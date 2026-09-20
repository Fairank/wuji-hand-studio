# timing_stats.py
"""Host-side arrival timing audit for a generic sensor sample stream.

Pure Python, standard library only. This is offline bookkeeping over rows that
were already received: no networking, SDK, hardware access or control logic.

Audit rules
-----------
* Rows are examined strictly in the order given (arrival order). Nothing is
  sorted, de-duplicated, interpolated or otherwise repaired, and the input is
  never modified.
* ``arrival_hz`` is the mean rate at which rows reached the host, derived only
  from host receive timestamps. It is NOT the physical sampling frequency of
  the device: host timestamps also contain transport, driver and scheduler
  effects. No device frequency is inferred anywhere in this module.
* Sequence accounting looks at adjacent pairs only. A late row is one
  ``backwards`` step, and the forward jumps on either side of it still add to
  ``missing``; no set-based reconciliation is attempted. Counter wraparound is
  not modelled, so a rollover shows up as a ``backwards`` step.
"""

from __future__ import annotations

import math
from typing import Any

__all__ = ["summarize_timing"]

_MS_PER_S = 1000.0


def _read_row(row: Any, index: int) -> tuple[int, float]:
    """Validate one row and return ``(seq, host_s)``; the row is not altered."""
    if not isinstance(row, dict):
        raise ValueError(
            f"row {index}: expected a dict, got {type(row).__name__}"
        )
    for key in ("seq", "host_s"):
        if key not in row:
            raise ValueError(f"row {index}: missing required key {key!r}")

    seq = row["seq"]
    # bool is a subclass of int, so it has to be rejected explicitly.
    if isinstance(seq, bool) or not isinstance(seq, int):
        raise ValueError(
            f"row {index}: seq must be an int (bool is not accepted), "
            f"got {type(seq).__name__}"
        )

    raw = row["host_s"]
    # No coercion from str/None/bool: parsing would be a silent repair.
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(
            f"row {index}: host_s must be a finite int or float (seconds), "
            f"got {type(raw).__name__}"
        )
    try:
        host_s = float(raw)
    except OverflowError:
        raise ValueError(f"row {index}: host_s is too large for a float") from None
    if not math.isfinite(host_s):
        raise ValueError(f"row {index}: host_s must be finite, got {host_s!r}")
    return seq, host_s


def summarize_timing(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize arrival timing and sequence continuity of ``rows``.

    ``rows`` is a list (or tuple) of dicts in arrival order. Each dict needs
    ``seq`` (int; bool is rejected) and ``host_s`` (finite int/float host
    receive time in seconds). Extra keys are ignored.

    Every field below is always present in the returned dict:

    n                  number of rows
    elapsed_s          last host_s minus first host_s in arrival order;
                       0.0 when n < 2; can be <= 0 if the host clock stepped back
    arrival_hz         (n - 1) / elapsed_s when n >= 2 and elapsed_s > 0,
                       otherwise None (host arrival rate, not a device rate)
    duplicates         adjacent pairs with seq == previous seq
    backwards          adjacent pairs with seq < previous seq
    missing            sum of max(0, seq - previous_seq - 1) over adjacent pairs
    max_gap_ms         largest signed host_s difference between adjacent rows,
                       in milliseconds (negative only if every difference is
                       negative); None when n < 2 because no gap was measured
    nonmonotonic_host  adjacent pairs with host_s difference <= 0

    Raises ValueError for a malformed container, row, seq or timestamp, and
    never returns a partial summary. ValueError is used for every rejection,
    wrong types included, so callers have a single exception to handle.
    """
    if not isinstance(rows, (list, tuple)):
        raise ValueError(f"rows must be a list of dicts, got {type(rows).__name__}")

    n = len(rows)
    duplicates = 0
    backwards = 0
    missing = 0
    nonmonotonic_host = 0
    max_gap_s = None
    first_host_s = 0.0
    prev_host_s = 0.0
    prev_seq = 0

    for index, row in enumerate(rows):
        seq, host_s = _read_row(row, index)
        if index == 0:
            first_host_s = host_s
        else:
            if seq == prev_seq:
                duplicates += 1
            elif seq < prev_seq:
                backwards += 1
            missing += max(0, seq - prev_seq - 1)

            gap_s = host_s - prev_host_s
            if gap_s <= 0:
                nonmonotonic_host += 1
            if max_gap_s is None or gap_s > max_gap_s:
                max_gap_s = gap_s
        prev_seq = seq
        prev_host_s = host_s

    elapsed_s = prev_host_s - first_host_s if n >= 2 else 0.0
    arrival_hz = (n - 1) / elapsed_s if n >= 2 and elapsed_s > 0 else None
    max_gap_ms = None if max_gap_s is None else max_gap_s * _MS_PER_S

    return {
        "n": n,
        "elapsed_s": elapsed_s,
        "arrival_hz": arrival_hz,
        "duplicates": duplicates,
        "backwards": backwards,
        "missing": missing,
        "max_gap_ms": max_gap_ms,
        "nonmonotonic_host": nonmonotonic_host,
    }
