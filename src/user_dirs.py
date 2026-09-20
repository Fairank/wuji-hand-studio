r"""Per-user application data directory, standard library only.

    Windows : %LOCALAPPDATA%\<app_name>                (fallback: ~\AppData\Local)
    macOS   : ~/Library/Application Support/<app_name>
    other   : $XDG_DATA_HOME/<app_name>                (fallback: ~/.local/share)

Environment values that are empty or not absolute are ignored. The XDG Base
Directory spec requires this for XDG_DATA_HOME; the same rule is applied to
LOCALAPPDATA. Nothing is created on disk unless ``create=True``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

__all__ = ["user_data_dir"]

_MAX_NAME_CHARS = 64
_MAX_NAME_BYTES = 255  # common per-component limit on Linux/macOS filesystems
_FORBIDDEN_CHARS = frozenset('<>:"/\\|?*')
_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def _validate_app_name(app_name: str) -> str:
    """Return *app_name* if it is a safe single directory name on every platform."""
    if not isinstance(app_name, str):
        raise TypeError(f"app_name must be str, not {type(app_name).__name__}")
    if not app_name:
        raise ValueError("app_name must not be empty")
    if app_name != app_name.strip():
        raise ValueError("app_name must not start or end with whitespace")
    if app_name.endswith("."):  # also rejects "." and ".."
        raise ValueError("app_name must not end with a dot")
    if len(app_name) > _MAX_NAME_CHARS:
        raise ValueError(f"app_name must be at most {_MAX_NAME_CHARS} characters")
    try:
        encoded = app_name.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("app_name must be valid Unicode text") from None
    if len(encoded) > _MAX_NAME_BYTES:
        raise ValueError(f"app_name must be at most {_MAX_NAME_BYTES} UTF-8 bytes")
    for ch in app_name:
        if ch in _FORBIDDEN_CHARS or ord(ch) < 32 or ord(ch) == 127:
            raise ValueError(f"app_name contains a forbidden character: {ch!r}")
    stem = app_name.split(".", 1)[0].rstrip(" ").upper()
    if stem in _RESERVED_NAMES:
        raise ValueError(f"app_name uses a reserved Windows device name: {stem}")
    return app_name


def _absolute_dir_from_env(variable: str) -> Optional[Path]:
    """Return the variable's value as a Path if it is set, non-empty and absolute."""
    value = os.environ.get(variable, "")
    if value and "\x00" not in value and os.path.isabs(value):
        return Path(os.path.normpath(value))
    return None


def _home_dir() -> Path:
    home = os.path.expanduser("~")
    if home == "~" or "\x00" in home or not os.path.isabs(home):
        raise RuntimeError("cannot determine an absolute home directory")
    return Path(os.path.normpath(home))


def user_data_dir(
    app_name: str,
    *,
    platform: Optional[str] = None,
    create: bool = False,
) -> Path:
    """Return the absolute per-user data directory for *app_name*.

    ``platform`` overrides ``sys.platform`` (intended for tests). The directory
    is created (mode 0o700 on POSIX) only when ``create=True``.

    Raises TypeError / ValueError for an unsafe *app_name*, RuntimeError if no
    absolute home directory can be determined, OSError if creation fails.
    """
    name = _validate_app_name(app_name)
    current = sys.platform if platform is None else platform

    if current == "win32":
        base = _absolute_dir_from_env("LOCALAPPDATA")
        if base is None:
            base = _home_dir() / "AppData" / "Local"
    elif current == "darwin":
        base = _home_dir() / "Library" / "Application Support"
    else:
        base = _absolute_dir_from_env("XDG_DATA_HOME")
        if base is None:
            base = _home_dir() / ".local" / "share"

    target = base / name
    # Defence in depth: the result must be a direct child of the base directory.
    if target.parent != base or target.name != name:
        raise ValueError("app_name does not resolve to a direct child of the data directory")

    if create:
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
    return target
