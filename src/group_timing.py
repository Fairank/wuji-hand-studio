"""Common-clock group schedules; no device I/O or clock synchronization claims."""
import math
import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def clock_id():
    # A common Linux boot ID means CLOCK_MONOTONIC is shared by both workers.
    # Never assume wall clocks on separate computers are synchronized.
    try:return Path('/proc/sys/kernel/random/boot_id').read_text().strip()+':'+os.readlink('/proc/self/ns/time')
    except OSError:return None


def number(value, lo, hi):
    if type(value) not in (int,float) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError('Invalid group timing value')
    return float(value)


def common_schedule(rows):
    if len(rows)<2:raise ValueError('Choose at least two hands / 至少选择两只手')
    ids={r.get('clock_id') for r in rows}
    if len(ids)!=1 or not next(iter(ids)):
        raise ValueError('同步实机需使用同一 Linux 控制端 / Synchronized hands must use the same Linux controller')
    return {key:max(number(r[key],.001,36000) for r in rows) for key in ('entry_s','body_s','exit_s','hold_s')}


def retime_sections(points, old, new):
    """Match entry, choreography, return and hold separately, preserving derivatives."""
    old_breaks=[0.,old['entry_s'],old['entry_s']+old['body_s'],old['entry_s']+old['body_s']+old['exit_s']]
    new_breaks=[0.,new['entry_s'],new['entry_s']+new['body_s'],new['entry_s']+new['body_s']+new['exit_s']]
    keys=('entry_s','body_s','exit_s','hold_s');out=[]
    for point in points:
        t=point['t'];i=next((k for k in range(3) if t < old_breaks[k+1]-1e-8),3)
        # A boundary knot's velocity is zero for entry / return; body endpoints
        # also use the zero-derivative envelope.
        scale=new[keys[i]]/old[keys[i]]
        p=dict(point,t=new_breaks[i]+(t-old_breaks[i])*scale,q=list(point['q']))
        if 'v' in p:p['v']=[v/scale for v in p['v']]
        out.append(p)
    return out
