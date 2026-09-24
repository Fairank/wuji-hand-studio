"""Persistence for user-selected post-retarget output settings.

``MappingLibrary`` stores per-binding settings and named presets chosen in an
unofficial robotics desktop UI. It never connects to, commands or drives
hardware. It does not infer motor limits or physical validity. It only
enforces the documented schema and numeric ranges.

File layout (JSON, at most 4 MiB)::

    {"schema_version": 1,
     "entries": {"<sha256 hex of canonical binding JSON>":
                 {"binding": {...}, "settings": {...}, "revision": 1}},
     "presets": [{"name": "...", "settings": {...}}]}

Every public method re-reads the file while holding a process-local
``threading.RLock``. All instances that use the same path share that lock.
There is no cross-process locking, so give each workspace its own directory.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import math
import os
import re
import tempfile
import threading
import unicodedata
from collections.abc import Mapping
from typing import Any

__all__ = [
    "BINDING_FIELDS",
    "CorruptLibraryError",
    "JOINT_COUNT",
    "MAX_ENTRIES",
    "MAX_FILE_BYTES",
    "MAX_PRESETS",
    "MappingLibrary",
    "RevisionConflictError",
    "SCHEMA_VERSION",
    "binding_key",
    "default_settings",
    "normalize_preset_name",
    "validate_binding",
    "validate_settings",
]

SCHEMA_VERSION = 1
JOINT_COUNT = 20
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_ENTRIES = 512
MAX_PRESETS = 64
MAX_PRESET_NAME_LENGTH = 48
MAX_IDENTIFIER_LENGTH = 100

GAIN_RANGE = (0.0, 2.0)
OFFSET_DEG_RANGE = (-180.0, 180.0)
SMOOTHING_MS_RANGE = (0.0, 1000.0)

GENERATIONS = ("hand1", "hand2")
SIDES = ("left", "right")
BINDING_FIELDS = ("generation", "side", "glove_serial", "sdk_user", "hand_serial")
SETTINGS_FIELDS = ("gain", "offset_deg", "smoothing_ms")

_IDENTIFIER_FIELDS = ("glove_serial", "sdk_user", "hand_serial")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9_.\-]{0,%d}" % MAX_IDENTIFIER_LENGTH)
_DOTTED_QUAD_RE = re.compile(r"[0-9]{1,3}(?:\.[0-9]{1,3}){3}")
_ENTRY_KEY_RE = re.compile(r"[0-9a-f]{64}")
_ENTRY_FIELDS = frozenset({"binding", "settings", "revision"})
_PRESET_FIELDS = frozenset({"name", "settings"})
_TOP_LEVEL_REQUIRED = frozenset({"schema_version", "entries"})
_TOP_LEVEL_ALLOWED = _TOP_LEVEL_REQUIRED | {"presets"}

_PATH_LOCKS: dict[str, Any] = {}
_PATH_LOCKS_GUARD = threading.Lock()


class CorruptLibraryError(ValueError):
    """The library file is unreadable or violates the schema; it was not modified."""


class RevisionConflictError(ValueError):
    """``expected_revision`` did not match the stored revision."""


# ----------------------------------------------------------------- validation


def default_settings() -> dict[str, Any]:
    """Return fresh neutral settings: gain 1.0, offset 0.0 deg, smoothing 0.0 ms."""
    return {
        "gain": [1.0] * JOINT_COUNT,
        "offset_deg": [0.0] * JOINT_COUNT,
        "smoothing_ms": 0.0,
    }


def _finite_number(value: Any, bounds: tuple[float, float], label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be an int or float, not {type(value).__name__}")
    try:
        number = float(value)
    except OverflowError:
        raise ValueError(f"{label} is out of range") from None
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    low, high = bounds
    if not low <= number <= high:
        raise ValueError(f"{label} must be between {low} and {high}")
    return number


def _number_list(value: Any, bounds: tuple[float, float], label: str) -> list[float]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a list of {JOINT_COUNT} numbers")
    if len(value) != JOINT_COUNT:
        raise ValueError(f"{label} must contain exactly {JOINT_COUNT} values, got {len(value)}")
    return [_finite_number(item, bounds, f"{label}[{index}]") for index, item in enumerate(value)]


def validate_settings(settings: Any) -> dict[str, Any]:
    """Validate per-joint output settings and return a normalized copy (floats only)."""
    if not isinstance(settings, Mapping):
        raise ValueError("settings must be a mapping")
    if set(settings.keys()) != set(SETTINGS_FIELDS):
        raise ValueError("settings must have exactly the keys " + ", ".join(SETTINGS_FIELDS))
    return {
        "gain": _number_list(settings["gain"], GAIN_RANGE, "gain"),
        "offset_deg": _number_list(settings["offset_deg"], OFFSET_DEG_RANGE, "offset_deg"),
        "smoothing_ms": _finite_number(settings["smoothing_ms"], SMOOTHING_MS_RANGE, "smoothing_ms"),
    }


def validate_binding(binding: Any) -> dict[str, str]:
    """Validate a binding and return a normalized copy holding exactly the five str fields."""
    if not isinstance(binding, Mapping):
        raise ValueError("binding must be a mapping")
    if set(binding.keys()) != set(BINDING_FIELDS):
        raise ValueError("binding must have exactly the keys " + ", ".join(BINDING_FIELDS))
    normalized: dict[str, str] = {}
    for field in BINDING_FIELDS:
        value = binding[field]
        if type(value) is not str:
            raise ValueError(f"binding {field} must be a str, not {type(value).__name__}")
        normalized[field] = value
    if normalized["generation"] not in GENERATIONS:
        raise ValueError("binding generation must be 'hand1' or 'hand2'")
    if normalized["side"] not in SIDES:
        raise ValueError("binding side must be 'left' or 'right'")
    for field in _IDENTIFIER_FIELDS:
        value = normalized[field]
        if _IDENTIFIER_RE.fullmatch(value) is None:
            raise ValueError(
                f"binding {field} must be empty or up to {MAX_IDENTIFIER_LENGTH} "
                "ASCII letters, digits, '_', '.' or '-'"
            )
        if _DOTTED_QUAD_RE.fullmatch(value) is not None:
            raise ValueError(f"binding {field} looks like a network address, which is not allowed")
    return normalized


def _key_for(normalized: dict[str, str]) -> str:
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def binding_key(binding: Any) -> str:
    """Return the stable storage key: sha256 hex digest of the canonical binding JSON."""
    return _key_for(validate_binding(binding))


def normalize_preset_name(name: Any) -> str:
    """Strip surrounding whitespace and require 1..48 printable characters with no control characters."""
    if type(name) is not str:
        raise ValueError("preset name must be a str")
    if any(unicodedata.category(char) == "Cc" for char in name):
        raise ValueError("preset name must not contain control characters")
    stripped = name.strip()
    if not 1 <= len(stripped) <= MAX_PRESET_NAME_LENGTH:
        raise ValueError(
            f"preset name must be 1..{MAX_PRESET_NAME_LENGTH} characters after stripping whitespace"
        )
    if not stripped.isprintable():
        raise ValueError("preset name must contain only printable characters")
    return stripped


# ---------------------------------------------------------------- file format


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite JSON number {name} is not allowed")


def _parse_document(document: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(document, dict):
        raise ValueError("top level must be a JSON object")
    keys = set(document)
    if not _TOP_LEVEL_REQUIRED <= keys or not keys <= _TOP_LEVEL_ALLOWED:
        raise ValueError("top-level keys must be schema_version, entries and presets")
    version = document["schema_version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {version!r}")

    raw_entries = document["entries"]
    if not isinstance(raw_entries, dict):
        raise ValueError("entries must be a JSON object")
    if len(raw_entries) > MAX_ENTRIES:
        raise ValueError(f"more than {MAX_ENTRIES} entries")
    entries: dict[str, Any] = {}
    for key, raw in raw_entries.items():
        if _ENTRY_KEY_RE.fullmatch(key) is None:
            raise ValueError(f"invalid entry key {key!r}")
        if not isinstance(raw, dict) or set(raw) != _ENTRY_FIELDS:
            raise ValueError(f"entry {key} must have exactly binding, settings, revision")
        binding = validate_binding(raw["binding"])
        if _key_for(binding) != key:
            raise ValueError(f"entry {key} does not match its binding")
        revision = raw["revision"]
        if type(revision) is not int or revision < 1:
            raise ValueError(f"entry {key} has an invalid revision")
        entries[key] = {
            "binding": binding,
            "settings": validate_settings(raw["settings"]),
            "revision": revision,
        }

    raw_presets = document.get("presets", [])  # absent only in pre-preset v1 files
    if not isinstance(raw_presets, list):
        raise ValueError("presets must be a JSON array")
    if len(raw_presets) > MAX_PRESETS:
        raise ValueError(f"more than {MAX_PRESETS} presets")
    presets: dict[str, Any] = {}
    for raw in raw_presets:
        if not isinstance(raw, dict) or set(raw) != _PRESET_FIELDS:
            raise ValueError("each preset must have exactly name and settings")
        name = normalize_preset_name(raw["name"])
        if name != raw["name"]:
            raise ValueError(f"preset name {raw['name']!r} is not normalized")
        if name in presets:
            raise ValueError(f"duplicate preset name {name!r}")
        presets[name] = validate_settings(raw["settings"])
    return {"entries": entries, "presets": presets}


def _snapshot(
    binding: dict[str, str], settings: dict[str, Any], saved: bool, revision: int
) -> dict[str, Any]:
    return {
        "binding": dict(binding),
        "settings": copy.deepcopy(settings),
        "saved": saved,
        "revision": revision,
    }


def _lock_for(path: str) -> threading.RLock:
    key = os.path.normcase(path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = _PATH_LOCKS[key] = threading.RLock()
        return lock


def _fsync_directory(directory: str) -> None:
    """Make the rename durable on a best-effort basis (POSIX only)."""
    if os.name != "posix":
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


# -------------------------------------------------------------------- library


class MappingLibrary:
    """JSON-file store of per-binding output settings and named presets."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        raw = os.fspath(path)
        if not isinstance(raw, str) or not raw:
            raise ValueError("path must be a non-empty str or os.PathLike[str]")
        # No filesystem access here, so constructing never creates or rewrites data.
        self._path = os.path.abspath(raw)
        self._lock = _lock_for(self._path)

    @property
    def path(self) -> str:
        return self._path

    def snapshot(self, binding: Mapping[str, Any]) -> dict[str, Any]:
        """Return {binding, settings, saved, revision}; unsaved bindings get the defaults."""
        normalized = validate_binding(binding)
        key = _key_for(normalized)
        with self._lock:
            entry = self._load_state()["entries"].get(key)
        if entry is None:
            return _snapshot(normalized, default_settings(), False, 0)
        return _snapshot(entry["binding"], entry["settings"], True, entry["revision"])

    def save(
        self,
        binding: Mapping[str, Any],
        settings: Mapping[str, Any],
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Store settings for a binding and return the new snapshot.

        If expected_revision is given, it must equal the stored revision
        (0 when nothing is stored). Otherwise RevisionConflictError is raised.
        """
        normalized = validate_binding(binding)
        clean = validate_settings(settings)
        if expected_revision is not None and (
            type(expected_revision) is not int or expected_revision < 0
        ):
            raise ValueError("expected_revision must be None or a non-negative int (not bool)")
        key = _key_for(normalized)
        with self._lock:
            state = self._load_state()
            entries = state["entries"]
            current = entries.get(key)
            current_revision = 0 if current is None else current["revision"]
            if expected_revision is not None and expected_revision != current_revision:
                raise RevisionConflictError(
                    f"expected revision {expected_revision}, stored revision is {current_revision}"
                )
            if current is None and len(entries) >= MAX_ENTRIES:
                raise ValueError(f"library already holds the maximum of {MAX_ENTRIES} bindings")
            entry = {"binding": normalized, "settings": clean, "revision": current_revision + 1}
            entries[key] = entry
            self._store_state(state)
        return _snapshot(entry["binding"], entry["settings"], True, entry["revision"])

    def list_entries(self) -> list[dict[str, Any]]:
        """Return snapshots of every stored binding, sorted by the binding fields."""
        with self._lock:
            entries = list(self._load_state()["entries"].values())
        entries.sort(key=lambda item: tuple(item["binding"][field] for field in BINDING_FIELDS))
        return [_snapshot(e["binding"], e["settings"], True, e["revision"]) for e in entries]

    def save_preset(self, name: str, settings: Mapping[str, Any]) -> dict[str, Any]:
        """Create a preset, or replace the preset with the same exact (stripped) name."""
        clean_name = normalize_preset_name(name)
        clean = validate_settings(settings)
        with self._lock:
            state = self._load_state()
            presets = state["presets"]
            if clean_name not in presets and len(presets) >= MAX_PRESETS:
                raise ValueError(f"at most {MAX_PRESETS} presets are allowed")
            presets[clean_name] = clean
            self._store_state(state)
        return {"name": clean_name, "settings": copy.deepcopy(clean)}

    def list_presets(self) -> list[dict[str, Any]]:
        """Return all presets as [{name, settings}], sorted by name in code point order."""
        with self._lock:
            presets = self._load_state()["presets"]
        return [{"name": name, "settings": copy.deepcopy(presets[name])} for name in sorted(presets)]

    def delete_preset(self, name: str) -> None:
        """Delete a preset by its exact (stripped) name. Raise KeyError if it does not exist."""
        clean_name = normalize_preset_name(name)
        with self._lock:
            state = self._load_state()
            if clean_name not in state["presets"]:
                raise KeyError(clean_name)
            del state["presets"][clean_name]
            self._store_state(state)

    # ------------------------------------------------------------ disk I/O

    def _load_state(self) -> dict[str, dict[str, Any]]:
        try:
            with open(self._path, "rb") as handle:
                data = handle.read(MAX_FILE_BYTES + 1)
        except FileNotFoundError:
            return {"entries": {}, "presets": {}}
        if len(data) > MAX_FILE_BYTES:
            raise CorruptLibraryError(
                f"{self._path}: larger than {MAX_FILE_BYTES} bytes; file left unchanged"
            )
        try:
            document = json.loads(
                data.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_constant,
            )
            return _parse_document(document)
        except (ValueError, RecursionError) as exc:
            raise CorruptLibraryError(
                f"{self._path}: invalid mapping library ({exc}); file left unchanged"
            ) from exc

    def _store_state(self, state: dict[str, dict[str, Any]]) -> None:
        presets = state["presets"]
        document = {
            "schema_version": SCHEMA_VERSION,
            "entries": state["entries"],
            "presets": [{"name": name, "settings": presets[name]} for name in sorted(presets)],
        }
        payload = json.dumps(
            document, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False
        ).encode("ascii") + b"\n"
        if len(payload) > MAX_FILE_BYTES:
            raise ValueError(f"library would exceed {MAX_FILE_BYTES} bytes; nothing was written")
        directory = os.path.dirname(self._path)
        fd, temp_path = tempfile.mkstemp(
            prefix="." + os.path.basename(self._path) + ".", suffix=".tmp", dir=directory
        )
        try:
            with open(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp_path)
            raise
        _fsync_directory(directory)
