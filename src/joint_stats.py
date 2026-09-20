"""Per-nid observed feedback rates, independent of the browser/render rate.

One stream frame includes all online joints. These counters measure received
entries; they cannot establish the sensor's internal acquisition rate.
"""
from collections import deque


class JointRates:
    def __init__(self, window=1.):
        self.window = window
        self.frames = deque()
        self.rows = {}
        self.seen = set()

    def add(self, row):
        now = row['host_s']
        self.frames.append((now, row['seq'], row['device_timestamp_us']))
        for j in row['joints']:
            nid = j['nid']
            self.seen.add(nid)
            self.rows.setdefault(nid, deque()).append((now, row['seq'], row['device_timestamp_us']))
        self.trim(now)

    def trim(self, now):
        for history in [self.frames, *self.rows.values()]:
            while history and history[0][0] < now-self.window:
                history.popleft()

    def snapshot(self, now):
        self.trim(now)
        span = self.frames[-1][0]-self.frames[0][0] if len(self.frames)>1 else 0
        device_span = (self.frames[-1][2]-self.frames[0][2])/1e6 if len(self.frames)>1 else 0
        frames_n = len(self.frames)
        stream_span = self.frames[-1][1]-self.frames[0][1]+1 if frames_n else 0
        frame_list=list(self.frames)
        seq_ok = all(b[1]>a[1] for a,b in zip(frame_list,frame_list[1:]))
        result = []
        for nid in sorted(self.seen):
            h = self.rows[nid]
            # Shared stream window exposes an absent joint instead of measuring
            # only its surviving burst and claiming a normal frequency.
            count = len(h)
            host_hz = max(0, count-1)/span if span>=.2 else None
            device_hz = max(0, count-1)/device_span if device_span>0 and span>=.2 else None
            unique = len({v[1] for v in h})
            gaps = max(0, stream_span-unique) if seq_ok and stream_span>0 else None
            result.append(dict(nid=nid, host_hz=host_hz, device_hz=device_hz,
                age_ms=(now-h[-1][0])*1000 if h else None,
                missing_in_received_frames=frames_n-count,
                missing_stream_slots=gaps, samples=count,
                status='fresh' if h and now-h[-1][0]<=.1 else 'stale'))
        return result
