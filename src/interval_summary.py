"""Summary statistics for already-recorded interval durations.

Pure, offline helper: it performs no I/O, keeps no state between calls, and
only reads the container it is given (it never sorts or mutates it).
"""

import math
from typing import Dict, List, Tuple, Union

__all__ = ["summarize_intervals"]

Number = Union[int, float]
Intervals = Union[List[Number], Tuple[Number, ...]]
Summary = Dict[str, Union[int, float, None]]

_MS_PER_SECOND = 1000.0

# (output key, percentile as an integer percent, i.e. p = percent / 100)
_PERCENTILES: Tuple[Tuple[str, int], ...] = (("p50_ms", 50), ("p95_ms", 95))


def _nearest_rank_index(count: int, percent: int) -> int:
    """Return the zero-based nearest-rank index ``ceil(p * count) - 1``.

    ``p`` is ``percent / 100``.  The ceiling is taken in exact integer
    arithmetic, so binary floating-point error in ``p * count`` can never
    shift the rank.
    """
    rank = -((-percent * count) // 100)  # == ceil(percent * count / 100)
    return rank - 1


def _to_milliseconds(values: Intervals) -> List[float]:
    """Validate ``values`` and return a *new* list of durations in ms.

    Error messages never ``repr()`` a raw item: a huge ``int`` can exceed
    Python's int-to-str digit limit and would mask the real problem.
    """
    if not isinstance(values, (list, tuple)):
        raise ValueError(
            f"values must be a list or tuple, got {type(values).__name__}"
        )

    millis: List[float] = []
    for index, item in enumerate(values):
        # bool is a subclass of int, so it has to be excluded explicitly.
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError(
                f"values[{index}] must be an int or float (bool is rejected), "
                f"got {type(item).__name__}"
            )
        try:
            seconds = float(item)
        except OverflowError as exc:  # e.g. 10**400
            raise ValueError(
                f"values[{index}] is too large to represent as a float"
            ) from exc
        if not math.isfinite(seconds):
            raise ValueError(
                f"values[{index}] must be finite, got {seconds!r}"
            )
        if seconds < 0.0:
            raise ValueError(
                f"values[{index}] must be nonnegative, got {seconds!r}"
            )

        ms = seconds * _MS_PER_SECOND
        if not math.isfinite(ms):  # float multiply overflows to inf silently
            raise ValueError(
                f"values[{index}] overflows a float when converted to "
                "milliseconds"
            )
        millis.append(ms if ms != 0.0 else 0.0)  # normalise -0.0 to 0.0
    return millis


def summarize_intervals(values: Intervals) -> Summary:
    """Summarise already-recorded interval durations.

    Parameters
    ----------
    values:
        A ``list`` or ``tuple`` of finite, nonnegative ``int``/``float``
        durations in **seconds**.  The container is only read: it is never
        sorted, mutated, or retained.

    Returns
    -------
    dict
        ``count`` (int) plus ``mean_ms``, ``p50_ms``, ``p95_ms`` and
        ``max_ms`` (floats, in milliseconds).  For an empty input ``count``
        is 0 and every other field is ``None``.

        Percentiles use the nearest-rank convention on a sorted copy:
        ``sorted_copy[ceil(p * n) - 1]`` with ``p = 0.5`` and ``p = 0.95``.

    Raises
    ------
    ValueError
        If ``values`` is not a list/tuple; if any element is not an
        int/float (``bool`` is rejected), is NaN, infinite or negative; or
        if converting to float, scaling to milliseconds, or summing
        overflows a float.
    """
    millis = _to_milliseconds(values)
    count = len(millis)
    if count == 0:
        return {
            "count": 0,
            "mean_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "max_ms": None,
        }

    ordered = sorted(millis)  # sorted copy; the caller's container is untouched

    try:
        total = math.fsum(millis)
    except OverflowError:  # "intermediate overflow in fsum"
        total = math.inf
    if not math.isfinite(total):
        raise ValueError(
            "sum of intervals overflows a float; mean_ms cannot be computed"
        )

    # Rounding can leave total / count one ulp outside [min, max].  The true
    # mean always lies inside that range, so clamp to keep min <= mean <= max.
    mean_ms = min(max(total / count, ordered[0]), ordered[-1])

    summary: Summary = {"count": count, "mean_ms": mean_ms}
    for key, percent in _PERCENTILES:
        summary[key] = ordered[_nearest_rank_index(count, percent)]
    summary["max_ms"] = ordered[-1]
    return summary
