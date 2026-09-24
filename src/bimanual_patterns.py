# -*- coding: utf-8 -*-
"""Generic bimanual choreography math helper (pure Python, standard library only).

The eight CATALOG actions are authored, fixed-base visual rhythms for two
five-finger hands. They are NOT official recorded motions and NOT validated
physical movements. sample() returns normalized curl/spread values in [0, 1]
plus their analytic time derivatives (per second). These are not joint angles,
actuator commands or safety decisions. Mapping to native joint limits and clock
synchronization belong to the caller.

Layout: palms toward the viewer, thumbs inward, so the spatial chain is
left little, ring, middle, index, thumb | right thumb, index, middle, ring, little.
Per-hand finger order is thumb, index, middle, ring, little (f = 0..4). The
chain index is g = 4 - f on the left hand and g = 5 + f on the right hand.

Model: value = E(t) * shape(theta), theta = 2*pi*t/4 - phase(g, t). E is a
quintic smootherstep of min(t, 16 - t) / 2 capped at 1, so E, dE/dt and
d2E/dt2 vanish at t = 0 and t = 16. Time is clamped to [0, 16] and never wraps.
"""
import math
import numbers
from collections import OrderedDict

__all__ = ["CATALOG", "DURATION_S", "PERIOD_S", "SIDES", "FINGERS", "global_index", "sample"]

DURATION_S = 16.0
PERIOD_S = 4.0
RAMP_S = 2.0
OMEGA = 2.0 * math.pi / PERIOD_S
BOUNCE_OMEGA = 2.0 * math.pi / 8.0
SIDES = ("left", "right")
FINGERS = ("thumb", "index", "middle", "ring", "little")

CATALOG = OrderedDict(
    (action_id, {"zh": zh, "en": en, "duration_s": DURATION_S})
    for action_id, zh, en in (
        ("pair_wave", "跨手波浪", "Cross-hand wave"),
        ("pair_reverse", "反向波浪", "Reverse wave"),
        ("pair_bounce", "往返波浪", "Out-and-back wave"),
        ("pair_ripple", "中心向外涟漪", "Center-outward ripple"),
        ("pair_alternate", "双手交替", "Alternating hands"),
        ("pair_bloom", "同步绽放", "Synchronous bloom"),
        ("pair_piano", "十指钢琴", "Ten-finger piano"),
        ("pair_chase", "双波追逐", "Two chasing waves"),
    )
)


def _check_side(side):
    if not isinstance(side, str) or side not in SIDES:
        raise ValueError("side must be exactly 'left' or 'right', got %r" % (side,))


def global_index(side, finger):
    """Chain index of a per-hand finger (0=thumb .. 4=little): 4 - f left, 5 + f right."""
    _check_side(side)
    valid = isinstance(finger, numbers.Integral) and not isinstance(finger, bool)
    if not valid or not 0 <= finger <= 4:
        raise ValueError("finger must be an integer in 0..4, got %r" % (finger,))
    return 4 - int(finger) if side == "left" else 5 + int(finger)


def _clamped_time(elapsed_s):
    if isinstance(elapsed_s, bool) or not isinstance(elapsed_s, numbers.Real):
        raise TypeError("elapsed_s must be a real number, got %s" % type(elapsed_s).__name__)
    try:
        t = float(elapsed_s)
    except OverflowError:  # huge but finite int/Fraction: clamp by sign
        return DURATION_S if elapsed_s > 0 else 0.0
    if not math.isfinite(t):
        raise ValueError("elapsed_s must be finite, got %r" % (elapsed_s,))
    return min(DURATION_S, max(0.0, t))


def _envelope(t):
    """Quintic smootherstep of u = min(t, 16 - t) / 2 capped at 1. Returns (E, dE/dt)."""
    if t <= DURATION_S - t:
        u, du_dt = t / RAMP_S, 1.0 / RAMP_S
    else:
        u, du_dt = (DURATION_S - t) / RAMP_S, -1.0 / RAMP_S
    if u >= 1.0:
        return 1.0, 0.0
    value = u * u * u * (u * (6.0 * u - 15.0) + 10.0)
    return min(1.0, value), 30.0 * u * u * (1.0 - u) ** 2 * du_dt


def _phase(action, g, t):
    """Spatial phase delay (rad) of chain index g and its time derivative."""
    if action == "pair_wave":
        return 0.52 * g, 0.0
    if action == "pair_reverse":
        return 0.52 * (9 - g), 0.0
    if action == "pair_bounce":  # travel direction reverses smoothly every 4 s
        k = 0.52 * (g - 4.5)
        return k * math.cos(BOUNCE_OMEGA * t), -k * BOUNCE_OMEGA * math.sin(BOUNCE_OMEGA * t)
    if action == "pair_ripple":
        return 0.75 * abs(g - 4.5), 0.0
    if action == "pair_alternate":
        return (0.0 if g <= 4 else math.pi), 0.0
    if action == "pair_bloom":
        return 0.0, 0.0
    if action == "pair_piano":
        return 0.65 * g, 0.0
    return 0.7 * g, 0.0  # pair_chase


def _curl_shape(action, theta):
    """Curl pulse in [0, 1] and its derivative with respect to theta."""
    r, dr = 0.5 - 0.5 * math.cos(theta), 0.5 * math.sin(theta)
    if action == "pair_piano":  # sharp key strike
        return r ** 4, 4.0 * r ** 3 * dr
    if action == "pair_chase":  # leader pulse chased half a cycle later by a 0.6 follower
        q = 1.0 - r
        return r ** 4 + 0.6 * q ** 4, (4.0 * r ** 3 - 2.4 * q ** 3) * dr
    return r, dr


def _spread_shape(theta):
    """Separate spread rhythm in [0, 1], peaking a quarter cycle before the curl pulse."""
    return 0.5 + 0.5 * math.sin(theta), 0.5 * math.cos(theta)


def sample(action, elapsed_s, side):
    """Sample one hand of `action` at clip time `elapsed_s` (seconds, clamped to 0..16).

    Returns a new dict {"curl", "spread", "curl_velocity", "spread_velocity"}.
    Each value is a list of 5 floats ordered thumb, index, middle, ring, little.
    Raises ValueError for an unknown action or side, or for a non-finite time.
    Raises TypeError for a bool or non-real time.
    """
    if not isinstance(action, str) or action not in CATALOG:
        raise ValueError("unknown action %r" % (action,))
    _check_side(side)
    t = _clamped_time(elapsed_s)
    env, denv = _envelope(t)
    out = {"curl": [], "spread": [], "curl_velocity": [], "spread_velocity": []}
    for f in range(5):
        phi, dphi = _phase(action, global_index(side, f), t)
        theta, dtheta = OMEGA * t - phi, OMEGA - dphi
        for key, (value, dvalue) in (("curl", _curl_shape(action, theta)),
                                     ("spread", _spread_shape(theta))):
            out[key].append(env * value)
            out[key + "_velocity"].append(denv * value + env * dvalue * dtheta)
    return out
