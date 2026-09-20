"""Trajectory interpolation and command cadence. No SDK or network access."""
import math
from collections import deque
from motion_parameters import COMMAND_RATE_HZ

# Host publish target, not a claim about measured hardware timing. The device
# supports up to 1 kHz. User requested 1 kHz; actual timing is reported.
COMMAND_HZ = float(COMMAND_RATE_HZ)
SMOOTH_PEAK_RATIO = 1.875
EXECUTION_VERSION = 'smooth-cadence-v1'


def segment_position(a, b, t):
    r = min(1., max(0., (t-a['t'])/(b['t']-a['t'])))
    if b.get('interpolation') == 'minimum_jerk':
        # Zero first and second derivatives at both ends; convex/no overshoot.
        r = r*r*r*(10.+r*(-15.+6.*r))
    return [x+(y-x)*r for x,y in zip(a['q'],b['q'])]


class CommandCadence:
    """Absolute deadlines; late calls emit at most one command, never a burst."""
    def __init__(self, now, hz=COMMAND_HZ):
        if not math.isfinite(hz) or not 0 < hz <= 1000:
            raise ValueError('Command frequency must be in (0, 1000] Hz')
        self.period = 1./hz
        self.next_due = now+self.period
        self.missed = 0

    def due(self, now):
        if now+1e-10 < self.next_due:
            return False
        skipped = max(0, math.floor((now-self.next_due+1e-10)/self.period))
        self.missed += skipped
        self.next_due += (skipped+1)*self.period
        # Do not send a near-immediate catch-up packet after a late callback.
        if self.next_due-now < self.period*.5-1e-10:
            self.next_due = now+self.period
        return True


class PublishTiming:
    """Successful host PUB calls only; never claims device receipt/actuation."""
    def __init__(self):
        self.starts=deque(maxlen=2000)
        self.intervals=deque(maxlen=100000)
        self.costs=deque(maxlen=100000)
        self.count=0;self.first=None;self.last=None;self.max_gap=0.

    def add(self,start,end):
        if self.last is not None:
            gap=start-self.last
            self.intervals.append(gap);self.max_gap=max(self.max_gap,gap)
        if self.first is None:self.first=start
        self.last=start;self.count+=1
        self.starts.append(start);self.costs.append(max(0.,end-start))

    def snapshot(self):
        duration=self.starts[-1]-self.starts[0] if len(self.starts)>1 else 0.
        elapsed=self.last-self.first if self.count>1 else 0.
        return dict(target_hz=COMMAND_HZ,host_publish_hz=(len(self.starts)-1)/duration if duration>0 else None,
            mean_publish_hz=(self.count-1)/elapsed if elapsed>0 else None,
            commands=self.count,max_interval_ms=self.max_gap*1000 if self.count>1 else None,
            window_commands=len(self.starts),basis='successful_host_sdk_publish_not_device_receipt',
            execution_version=EXECUTION_VERSION)

    def report(self):
        from interval_summary import summarize_intervals
        return dict(self.snapshot(),intervals=summarize_intervals(list(self.intervals)),
            sdk_call=summarize_intervals(list(self.costs)),sample_scope='last_100000_calls',
            retained_intervals_s=list(self.intervals))
