"""Rolling arrival-rate statistics for a timestamped stream (standard library only)."""
import math
from collections import deque


def _finite(value, name):
    """Return value as a float; raise ValueError unless it is a finite int/float."""
    try:
        ok = (isinstance(value, (int, float)) and not isinstance(value, bool)
              and math.isfinite(value))
    except OverflowError:  # int too large to represent as a float
        ok = False
    if not ok:
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return float(value)


class ArrivalRates:
    """Arrival stats on a caller-supplied monotonic clock (seconds). Not thread-safe.

    Sequence resets or wraparound are not detected: start a new instance instead.
    """

    def __init__(self, window_s: float = 2.0) -> None:
        self._window_s = _finite(window_s, "window_s")
        if self._window_s <= 0.0:
            raise ValueError(f"window_s must be > 0, got {window_s!r}")
        self._times = deque()  # accepted arrivals within window_s of the newest one
        self._frames = 0       # accepted lifetime count
        self._dropped = 0      # total of positive sequence gaps
        self._last_seq = None  # newest accepted int sequence
        self._last_now = None  # newest time given to add(), accepted or ignored

    def add(self, now: float, sequence: "int | None" = None) -> bool:
        """Record an arrival. Returns False when a stale/duplicate sequence is ignored."""
        now = _finite(now, "now")
        if isinstance(sequence, bool) or not isinstance(sequence, (int, type(None))):
            raise ValueError(f"sequence must be an int or None, got {sequence!r}")
        if self._last_now is not None and now < self._last_now:
            raise ValueError(f"arrival time decreased: {now} < {self._last_now}")
        self._last_now = now
        if sequence is not None:
            if self._last_seq is not None:
                if sequence <= self._last_seq:
                    return False
                self._dropped += sequence - self._last_seq - 1
            self._last_seq = sequence
        self._frames += 1
        self._times.append(now)
        cutoff = now - self._window_s
        while self._times[0] < cutoff:
            self._times.popleft()
        return True

    def snapshot(self, now: float) -> dict:
        """Return frames, rate_hz, age_ms and dropped as of `now` without changing state."""
        now = _finite(now, "now")
        if self._last_now is not None and now < self._last_now:
            raise ValueError(f"snapshot time {now} is before last add() time {self._last_now}")
        cutoff = now - self._window_s
        recent = [t for t in self._times if t >= cutoff]  # window is [now - window_s, now]
        span = recent[-1] - recent[0] if len(recent) >= 2 else 0.0
        rate_hz = (len(recent) - 1) / span if span > 0.0 else None
        if rate_hz is not None and not math.isfinite(rate_hz):
            rate_hz = None  # span too small for a representable rate
        age_ms = (now - self._times[-1]) * 1000.0 if self._times else None
        return {"frames": self._frames, "rate_hz": rate_hz,
                "age_ms": age_ms, "dropped": self._dropped}
