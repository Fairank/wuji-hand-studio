"""Data-only adapter for Wuji's MIT-licensed Hand 2 left opposition recording.

The upstream device runner is deliberately not imported. This module has no SDK
or network calls. Physical playback still passes through HardwareShowcase.
"""
import hashlib
import math
import struct
from functools import lru_cache
from pathlib import Path
from motion_parameters import (PATH_SPEED_RAD_S,COMMAND_SPEED_RAD_S,MIN_TRANSITION_S,OFFICIAL_ENDPOINT_HOLD_S,
    OFFICIAL_RETURN_HOLD_S)
from motion_timing import SMOOTH_PEAK_RATIO

COMMIT='b0e48652dd94f4bc33df61cdc23a6d5dc598f93d'
SOURCE_URL=f'https://github.com/wuji-technology/wuji-sdk/tree/{COMMIT}/examples/python/wuji_hand_2/6_opposition'
LEFT_SHA256='696784c4a64c204a50b10919c9671b44844de6b3f8349819af8350e44403c32e'
HEADER=struct.Struct('<8sHBBI')
FRAME=struct.Struct('<I20f')
DATA=Path(__file__).with_name('official_data')/'left.replay'


def decode_replay(raw,expected_side='left'):
    if expected_side not in ('left','right') or len(raw)<HEADER.size:
        raise ValueError('Invalid replay side/header')
    magic,version,side,joints,count=HEADER.unpack_from(raw)
    if magic!=b'WJH2RPL\0' or version!=1 or side!=({'left':2,'right':1}[expected_side]) or joints!=20:
        raise ValueError('Replay format or handedness mismatch')
    if not 0<count<=1000000 or len(raw)!=HEADER.size+count*FRAME.size:
        raise ValueError('Invalid replay frame count/length')
    poses=[]
    for values in FRAME.iter_unpack(memoryview(raw)[HEADER.size:]):
        q=values[1:]
        if not all(math.isfinite(x) for x in q):raise ValueError('Nonfinite replay angle')
        poses.append(q)
    return poses


@lru_cache(maxsize=1)
def load_left_replay():
    return load_replay('left')

@lru_cache(maxsize=2)
def load_replay(side='left'):
    raw=(DATA if side=='left' else DATA.with_name('right.replay')).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=(LEFT_SHA256 if side=='left' else 'fcbbebd96c0080a141b96ebaaf3ce7107816cd1bc0a7152f277879a796ce23cb'):raise ValueError('官方左手动作文件校验失败')
    return tuple(decode_replay(raw,side))


@lru_cache(maxsize=4)
def validated_left(lower,upper,side="left"):
    poses=load_replay(side)
    if any(not lo<=x<=hi for pose in poses for x,lo,hi in zip(pose,lower,upper)):
        raise ValueError('官方轨迹超出当前左手已配置边界')
    return poses


@lru_cache(maxsize=1)
def path_indices(side="left"):
    """Retain 10 ms samples plus bends needed for <=0.001 rad source error."""
    poses=load_replay(side);knots=list(range(0,len(poses),10))+[len(poses)-1]
    keep=set(knots);todo=list(zip(knots,knots[1:]))
    while todo:
        a,b=todo.pop()
        worst=0.;index=None
        for i in range(a+1,b):
            r=(i-a)/(b-a)
            error=max(abs(x-(u+(v-u)*r)) for x,u,v in zip(poses[i],poses[a],poses[b]))
            if error>worst:worst,index=error,i
        if worst>.001:
            keep.add(index);todo.extend([(a,index),(index,b)])
    return tuple(sorted(keep))


def preview_pose(elapsed,side='left'):
    poses=load_replay(side);period=(len(poses)-1)*.001+1.
    t=max(0.,elapsed)%period
    if t<=(len(poses)-1)*.001:
        x=t*1000;i=min(len(poses)-2,int(x));r=x-i
        return [a+(b-a)*r for a,b in zip(poses[i],poses[i+1])]
    # Explicit smooth loop seam; upstream recording is not exactly cyclic.
    r=(t-(len(poses)-1)*.001)
    return [a+(b-a)*r for a,b in zip(poses[-1],poses[0])]


def build_points(q,amplitude,lower,upper):
    """Retain the SDK-order path within .001 rad and retime every edge.

    Amplitude scales from the real start. Never mirror right data, use simulated
    IK goals, jump to the first frame, or accelerate to catch up after a delay.
    """
    from device_profiles import controller_profile
    side=controller_profile()["side"]
    poses=validated_left(tuple(lower),tuple(upper),side)
    selected=path_indices(side)
    points=[dict(t=0.,q=q[:],label='实际起始姿态')]
    def append(goal,label,min_duration,verify=False,smooth=False):
        old=points[-1]
        factor=SMOOTH_PEAK_RATIO if smooth else 1.
        duration=max(min_duration,factor*max(abs(a-b) for a,b in zip(goal,old['q']))/min(PATH_SPEED_RAD_S,COMMAND_SPEED_RAD_S))
        points.append(dict(t=old['t']+duration,q=goal,label=label,verify_hold=verify,
            interpolation='minimum_jerk' if smooth else 'linear'))
    for k,i in enumerate(selected):
        goal=[a+amplitude*(b-a) for a,b in zip(q,poses[i])]
        append(goal,f'官方左手对指 · 原轨迹{i/1000:.2f}秒',MIN_TRANSITION_S if k==0 else (i-selected[k-1])*.001,smooth=k==0)
        if k==0:append(goal[:],'官方起点 · 稳定',OFFICIAL_ENDPOINT_HOLD_S,True)
    append(points[-1]['q'][:],'官方末点 · 稳定',OFFICIAL_ENDPOINT_HOLD_S,True)
    append(q[:],'返回本次实测起点',MIN_TRANSITION_S,smooth=True)
    append(q[:],'返回原姿态 · 稳定',OFFICIAL_RETURN_HOLD_S,True)
    return points
