"""Flatten Wuji CLI JSON diagnostic ("doctor") reports into rows for a UI.

Scope is deliberately small:
  * pure in-memory transformation of an already parsed JSON document
    (the caller runs ``json.loads``);
  * standard library only: no shell, networking, HTML, eval or file access;
  * never invents results: a missing status never becomes "pass", "skip" is
    never treated as success, and group rows are not rolled up from children.

Documented schema relied on:
  * top-level arrays ``env`` and ``device`` (older versions used ``system``
    and ``devices``);
  * nodes are objects with optional ``id``, ``label``, ``sn``, ``status``,
    ``summary``, ``tip`` and ``children``;
  * status values are ``pass``, ``warn``, ``fail`` and ``skip``.
"""

from typing import Any, Dict, List

__all__ = [
    "flatten_report",
    "MAX_DEPTH",
    "MAX_NODES",
    "KNOWN_STATUSES",
    "STATUS_UNKNOWN",
    "STATUS_GROUP",
]

MAX_DEPTH = 32    # deepest allowed node depth; top-level nodes are depth 0
MAX_NODES = 5000  # maximum number of nodes (= output rows) per report

KNOWN_STATUSES = ("pass", "warn", "fail", "skip")
STATUS_UNKNOWN = "unknown"  # leaf without status, or a value not documented
STATUS_GROUP = "group"      # parent node that carries no status of its own

# (normalized section name, accepted keys in order of preference)
_SECTIONS = (
    ("env", ("env", "system")),
    ("device", ("device", "devices")),
)


def _to_text(value: Any, field: str) -> str:
    """Convert a JSON scalar to stripped display text; reject containers."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):  # before int: bool is a subclass of int
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    raise ValueError(
        "field %r must be a string, number, boolean or null, got %s"
        % (field, type(value).__name__)
    )


def _normalize_status(value: Any, has_children: bool) -> str:
    """Return pass/warn/fail/skip, "group" or "unknown"; never guess "pass"."""
    if isinstance(value, str):
        value = value.strip()
        if value.isascii() and value.lower() in KNOWN_STATUSES:
            return value.lower()
    if value is None or value == "":
        # No status supplied: a parent is only a group, a leaf stays unknown.
        return STATUS_GROUP if has_children else STATUS_UNKNOWN
    return STATUS_UNKNOWN


def _walk(node: Any, section: str, depth: int,
          rows: List[Dict[str, Any]]) -> None:
    """Append ``node`` and then its children (pre-order) to ``rows``."""
    if depth > MAX_DEPTH:
        raise ValueError(
            "section %r is nested deeper than %d levels" % (section, MAX_DEPTH)
        )
    if not isinstance(node, dict):
        raise ValueError(
            "section %r: every node must be an object, got %s at depth %d"
            % (section, type(node).__name__, depth)
        )
    if len(rows) >= MAX_NODES:
        raise ValueError("report has more than %d nodes" % MAX_NODES)

    children = node.get("children")
    if children is None:
        children = []
    elif not isinstance(children, list):
        raise ValueError(
            "section %r: 'children' must be an array, got %s at depth %d"
            % (section, type(children).__name__, depth)
        )

    label = _to_text(node.get("label"), "label")
    node_id = _to_text(node.get("id"), "id")
    rows.append(
        {
            "section": section,
            "depth": depth,
            "label": label or node_id,
            "sn": _to_text(node.get("sn"), "sn"),
            "status": _normalize_status(node.get("status"), bool(children)),
            "summary": _to_text(node.get("summary"), "summary"),
            "tip": _to_text(node.get("tip"), "tip"),
        }
    )
    for child in children:
        _walk(child, section, depth + 1, rows)


def flatten_report(document: Any) -> List[Dict[str, Any]]:
    """Return one plain dict per report node, in display (pre-order) order.

    Each row is ``{"section", "depth", "label", "sn", "status", "summary",
    "tip"}``:

    * ``section`` is always "env" or "device", also for the legacy aliases
      ``system`` / ``devices``.  When a modern key is present (even if empty)
      its alias is ignored entirely, so nothing is listed twice.
    * ``depth`` is 0 for top-level nodes.
    * ``label`` falls back to ``id``; text fields are stripped strings, numbers
      and booleans are converted, null/missing becomes "".  Text is returned
      verbatim (no HTML escaping): render it as plain text.
    * ``status`` is "pass", "warn", "fail" or "skip" (ASCII case and outer
      whitespace ignored), "group" for a parent without a status, otherwise
      "unknown".

    Raises ValueError for: a non-object root, a root without any known
    section, a non-array section or ``children``, a node or child that is not
    an object, a container in a text field, depth > MAX_DEPTH, or more than
    MAX_NODES nodes.  No partial result is returned.
    """
    if not isinstance(document, dict):
        raise ValueError(
            "report root must be a JSON object, got %s"
            % type(document).__name__
        )

    rows: List[Dict[str, Any]] = []
    recognized = False
    for section, keys in _SECTIONS:
        for key in keys:  # modern key first, legacy alias only as a fallback
            if key not in document:
                continue
            recognized = True
            nodes = document[key]
            if not isinstance(nodes, list):
                raise ValueError(
                    "section %r must be an array, got %s"
                    % (key, type(nodes).__name__)
                )
            for node in nodes:
                _walk(node, section, 0, rows)
            break  # do not also read the alias: that would duplicate rows
    if not recognized:
        raise ValueError(
            "report root has none of the sections: env, device, system, devices"
        )
    return rows
