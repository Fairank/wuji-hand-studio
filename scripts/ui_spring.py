# ui_spring.py
"""Underdamped spring (mass = 1) unit step response as a CSS ``linear()`` easing.

Solves x'' + 2*z*w*x' + w**2*x = w**2 from rest (stiffness w**2, damping
2*z*w). Dependency-free pure functions for static presentation easings:
no file I/O, network access, subprocesses or timers.
"""

from __future__ import annotations

import math
import numbers
from collections.abc import Iterable

__all__ = ["spring_samples", "css_linear"]

MIN_COUNT = 3
MAX_COUNT = 1001
# Normalization divides by x(duration_s). Values this small only arise from
# degenerate settings (duration far shorter than the natural period, or an
# almost undamped spring ending near a trough) and would amplify rounding error.
MIN_DENOMINATOR = 1e-6


def _real(name: str, value: object) -> float:
    """Return ``value`` as a finite float; reject bools and non-real types."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise TypeError(f"{name} must be a real number, not {type(value).__name__}")
    try:
        result = float(value)
    except OverflowError:  # e.g. a huge int or Fraction
        result = math.inf
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _count(value: object) -> int:
    """Return ``value`` as an int in [MIN_COUNT, MAX_COUNT]; reject bools."""
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise TypeError(f"count must be an integer, not {type(value).__name__}")
    if not MIN_COUNT <= value <= MAX_COUNT:
        raise ValueError(f"count must be between {MIN_COUNT} and {MAX_COUNT}")
    return int(value)


def spring_samples(
    damping_ratio: float = 0.72,
    frequency_hz: float = 3.5,
    duration_s: float = 0.45,
    count: int = 61,
) -> list[float]:
    """Return ``count`` evenly spaced, normalized step-response samples.

    Samples cover t in [0, duration_s] of

        x(t) = 1 - exp(-z*w*t) * (cos(wd*t) + z/sqrt(1 - z**2) * sin(wd*t))

    with z = damping_ratio, w = 2*pi*frequency_hz and wd = w*sqrt(1 - z**2),
    divided by x(duration_s): the first sample is exactly 0.0, the last is
    exactly 1.0, and overshoot above 1 is preserved (never clamped).

    Raises TypeError for non-real or bool arguments or a non-integer count, and
    ValueError for non-finite or out-of-range arguments, or when x(duration_s)
    is too close to zero to normalize by.
    """
    zeta = _real("damping_ratio", damping_ratio)
    freq = _real("frequency_hz", frequency_hz)
    duration = _real("duration_s", duration_s)
    n = _count(count)
    if not 0.0 < zeta < 1.0:
        raise ValueError("damping_ratio must satisfy 0 < damping_ratio < 1")
    if freq <= 0.0:
        raise ValueError("frequency_hz must be > 0")
    if duration <= 0.0:
        raise ValueError("duration_s must be > 0")
    omega = 2.0 * math.pi * freq
    if not math.isfinite(omega * duration):
        raise ValueError("frequency_hz * duration_s is too large")
    root = math.sqrt((1.0 - zeta) * (1.0 + zeta))  # sqrt(1 - z**2), accurate near 1
    decay, wd, ratio = zeta * omega, omega * root, zeta / root

    def response(t: float) -> float:
        envelope = math.exp(-decay * t)
        return 1.0 - envelope * (math.cos(wd * t) + ratio * math.sin(wd * t))

    # i / (n - 1) is exactly 1.0 for the last index, so the last t is duration.
    raw = [response(duration * (i / (n - 1))) for i in range(n)]
    final = raw[-1]
    if not final > MIN_DENOMINATOR:  # x(t) > 0 for t > 0; this also rejects NaN
        raise ValueError("x(duration_s) is too close to zero to normalize by")
    samples = [value / final for value in raw]
    samples[0], samples[-1] = 0.0, 1.0
    return samples


def _num(value: float) -> str:
    """Plain decimal text: at most 6 decimal places, no exponent, no -0."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def css_linear(samples: Iterable[float]) -> str:
    """Return a CSS ``linear()`` easing with explicit, evenly spaced percentages.

    ``samples`` must yield 3..1001 finite real numbers (bools are rejected);
    sample ``i`` of ``n`` is placed at ``100 * i / (n - 1)`` percent.
    """
    items = list(samples)
    if not MIN_COUNT <= len(items) <= MAX_COUNT:
        raise ValueError(f"need between {MIN_COUNT} and {MAX_COUNT} samples")
    values = [_real(f"samples[{i}]", item) for i, item in enumerate(items)]
    last = len(values) - 1
    stops = (f"{_num(v)} {_num(100 * i / last)}%" for i, v in enumerate(values))
    return "linear(" + ", ".join(stops) + ")"
