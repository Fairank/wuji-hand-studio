"""User output adjustments after the SDK's immutable built-in retarget solver.

These are not the archived optimizer YAML weights. Identity defaults preserve
official output; smoothing is time-based and resets for each source/session.
"""
import json
import math


def defaults():
    return dict(gain=[1.] * 20, offset_deg=[0.] * 20, smoothing_ms=0.)


def validate(value):
    if not isinstance(value, dict) or set(value) != set(defaults()):
        raise ValueError('Invalid retarget settings fields')
    result = {}
    for key, low, high in (('gain', 0., 2.), ('offset_deg', -180., 180.)):
        vector = value[key]
        if not isinstance(vector, list) or len(vector) != 20:
            raise ValueError('Retarget settings require 20 joints')
        if any(type(x) not in (int, float) or not math.isfinite(x) or not low <= x <= high for x in vector):
            raise ValueError('Invalid retarget '+key)
        result[key] = list(map(float, vector))
    t = value['smoothing_ms']
    if type(t) not in (int, float) or not math.isfinite(t) or not 0 <= t <= 1000:
        raise ValueError('Smoothing must be 0–1000 ms')
    result['smoothing_ms'] = float(t)
    return result


def load(path):
    return validate(json.loads(path.read_text(encoding='utf-8'))) if path.exists() else defaults()


class OutputMapping:
    def __init__(self, settings):
        self.settings = validate(settings)
        self.previous = self.time = None

    def apply(self, q, now):
        if len(q) != 20 or any(not math.isfinite(x) for x in q):
            raise ValueError('Expected 20 finite SDK angles')
        desired = [x*g+math.radians(o) for x,g,o in zip(q,self.settings['gain'],self.settings['offset_deg'])]
        tau = self.settings['smoothing_ms']/1000
        alpha = 1 if self.previous is None or tau == 0 else -math.expm1(-max(0., now-self.time)/tau)
        result = desired if self.previous is None else [x+alpha*(y-x) for x,y in zip(self.previous,desired)]
        self.previous, self.time = result, now
        return result[:]
