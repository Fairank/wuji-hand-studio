"""Local candidate poses for explicitly requested low-current commissioning.

These poses are NOT reviewed full-action profiles or learned policies. No
display mapping or browser-supplied position can enter this path.
"""
import json
import math
from pathlib import Path
from official_policy import CURRENT_LIMIT_A,LOWER_RAD,UPPER_RAD,settings
from motion_parameters import (PATH_SPEED_RAD_S,COMMAND_SPEED_RAD_S,MIN_TRANSITION_S,
    POSE_HOLD_S,MAX_TRIAL_DURATION_S)
from motion_timing import SMOOTH_PEAK_RATIO,EXECUTION_VERSION
from playback_rates import PLAYBACK_SPEEDS
from gesture_library import CATALOG,CUSTOM_IDS,route as gesture_route

POSES=Path(__file__).with_name('trial_sdk_poses.json')
LABELS=[f'{finger}_S{j}' for finger in ('thumb','index','middle','ring','pinky') for j in range(1,5)]
NAMES={'open':'张开并返回','fist':'张开与轻握','opposition':'拇指依次对指姿态','sequence':'整套低力度展示',
       'official_opposition':'官方左手对指 · 限速适配'}
NAMES.update({x['id']:x['zh'] for x in CATALOG if x['id'] in CUSTOM_IDS})


def make_trial(q,action,amplitude,cycles,path=POSES,*,speed=1.,clock_at=None,text='WUJI TECH'):
    if action not in NAMES or type(amplitude) not in {int,float} or amplitude not in {.25,.5,.75,1.}:
        raise ValueError('请选择试运行动作和25/50/75/100%幅度')
    if type(cycles) is not int or cycles not in {1,3}:raise ValueError('试运行支持1或3轮')
    if type(speed) not in {int,float} or speed not in PLAYBACK_SPEEDS:
        raise ValueError('播放速度支持0.25/0.5/1/1.25/1.5/2倍')
    def retime(plan):
        for point in plan['points']:
            point['t']/=speed
            if 'v' in point:point['v']=[v*speed for v in point['v']]
        if plan['points'][-1]['t']*cycles>MAX_TRIAL_DURATION_S:
            raise ValueError(f'所选速度、幅度与循环超过{MAX_TRIAL_DURATION_S/60:g}分钟播放设置，请调整循环、幅度或配置')
        plan['speed_factor']=speed
        plan['max_velocity_rad_s']*=speed
        plan['control_policy']=settings()
        plan['execution_version']=EXECUTION_VERSION
        plan['clock_at']=clock_at
        return plan
    p=json.loads(path.read_text(encoding='utf-8'))
    if p.get('sdk_labels')!=LABELS or p.get('side')!='left' or p.get('schema')!=1:
        raise ValueError('低力度试运行姿态规格无效')
    lo,hi=LOWER_RAD[:],UPPER_RAD[:]
    def valid(values):
        return (isinstance(values,list) and len(values)==20 and all(type(x) in {int,float} and math.isfinite(x) for x in values))
    if not valid(lo) or not valid(hi) or not valid(q) or any(not a<=x<=b for x,a,b in zip(q,lo,hi)):
        raise ValueError('当前姿态超出候选试运行边界')
    from performance_program import PROGRAM_IDS,real_points,program
    if action in PROGRAM_IDS:
        data=program(action,text)
        points,scale=real_points(action,text,q,amplitude,min(PATH_SPEED_RAD_S,COMMAND_SPEED_RAD_S),MIN_TRANSITION_S,POSE_HOLD_S)
        if any(not valid(p['q']) or any(not a<=x<=b for x,a,b in zip(p['q'],lo,hi)) for p in points):
            raise ValueError('动作数据超出关节行程')
        return retime(dict(points=points,lower_rad=lo,upper_rad=hi,current_limit_A=CURRENT_LIMIT_A,
            max_velocity_rad_s=COMMAND_SPEED_RAD_S,cycles=cycles,action=action,label=NAMES[action],amplitude=amplitude,
            text=data['text'],source=data['source'],source_url=data['source_url'],choreography_schema=data['schema'],
            nominal_duration_s=data['duration_s'],time_scale=scale,physical_contact_verified=False,sign_language_certified=False))
    if action=='official_opposition':
        from official_replay import build_points,SOURCE_URL,COMMIT,LEFT_SHA256
        points=build_points(q,amplitude,lo,hi)
        if points[-1]['t']*cycles>MAX_TRIAL_DURATION_S:raise ValueError(f'官方动作限速后超过{MAX_TRIAL_DURATION_S/60:g}分钟播放设置')
        return retime(dict(points=points,lower_rad=lo,upper_rad=hi,current_limit_A=CURRENT_LIMIT_A,
            max_velocity_rad_s=COMMAND_SPEED_RAD_S,cycles=cycles,action=action,label=NAMES[action],amplitude=amplitude,
            source='official_wuji_hand2_'+__import__('device_profiles').controller_profile()['side']+'_recording_retimed',source_url=SOURCE_URL,
            source_commit=COMMIT,source_sha256=LEFT_SHA256 if __import__('device_profiles').controller_profile()['side']=='left' else 'fcbbebd96c0080a141b96ebaaf3ce7107816cd1bc0a7152f277879a796ce23cb',physical_contact_verified=False))
    if action in CUSTOM_IDS:
        from datetime import datetime
        stamp=datetime.fromisoformat(clock_at) if clock_at else None
        steps=gesture_route(action,at=stamp)
        points=[dict(t=0.,q=q[:],label='实际起始姿态 / Measured start')]
        for index,(label,pose,hold) in enumerate(steps+[('返回原姿态 / Return',q[:],POSE_HOLD_S)]):
            if not valid(pose) or any(not a<=x<=b for x,a,b in zip(pose,lo,hi)):
                raise ValueError('动作库姿态超出文档行程')
            goal=[x+amplitude*(y-x) for x,y in zip(q,pose)]
            interior=action=='official_sweep' and 0<index<len(steps)
            delta=max(abs(a-b) for a,b in zip(points[-1]['q'],goal))
            duration=max(.02 if interior else MIN_TRANSITION_S,
                (1. if interior else SMOOTH_PEAK_RATIO)*delta/min(PATH_SPEED_RAD_S,COMMAND_SPEED_RAD_S))
            if action in {'official_home','official_sweep'} and index==0:duration=max(1.5,duration)
            points.append(dict(t=points[-1]['t']+duration,q=goal,label=label,
                interpolation='linear' if interior else 'minimum_jerk'))
            if not interior or hold:
                points.append(dict(t=points[-1]['t']+max(hold,POSE_HOLD_S),q=goal[:],label=label+' · Hold'))
        meta=next(x for x in CATALOG if x['id']==action)
        return retime(dict(points=points,lower_rad=lo,upper_rad=hi,current_limit_A=CURRENT_LIMIT_A,
            max_velocity_rad_s=COMMAND_SPEED_RAD_S,cycles=cycles,action=action,label=NAMES[action],amplitude=amplitude,
            source='official_example_adapted' if meta['source']=='official' else 'project_scripted_gesture_candidate',
            source_url=meta['note'] or None,physical_contact_verified=False,
            sign_language_certified=False))
    def target(name):
        pose=p['poses'][name]
        if not valid(pose) or any(not a<=x<=b for x,a,b in zip(pose,lo,hi)):
            raise ValueError('候选姿态超出关节边界')
        return [x+amplitude*(y-x) for x,y in zip(q,pose)]
    route=[('张开','open')]
    if action in {'fist','sequence'}:route += [('轻握拳','fist'),('重新张开','open')]
    if action in {'opposition','sequence'}:
        for finger,label in [('index','食指'),('middle','中指'),('ring','无名指'),('pinky','小指')]:
            route += [(f'拇指与{label}对指姿态','pair_'+finger),('张开恢复','open')]
    points=[dict(t=0.,q=q[:],label='实际起始姿态')]
    for label,name in route+[('返回原姿态',None)]:
        goal=target(name) if name else q[:]
        duration=max(MIN_TRANSITION_S,SMOOTH_PEAK_RATIO*max(abs(a-b) for a,b in zip(points[-1]['q'],goal))/min(PATH_SPEED_RAD_S,COMMAND_SPEED_RAD_S))
        t=points[-1]['t']+duration
        points.append(dict(t=t,q=goal,label=label,interpolation='minimum_jerk'))
        points.append(dict(t=t+POSE_HOLD_S,q=goal[:],label=label+' · 稳定'))
    if points[-1]['t']*cycles>MAX_TRIAL_DURATION_S:raise ValueError(f'当前幅度与循环超过{MAX_TRIAL_DURATION_S/60:g}分钟播放设置')
    return retime(dict(points=points,lower_rad=lo,upper_rad=hi,current_limit_A=CURRENT_LIMIT_A,
        max_velocity_rad_s=COMMAND_SPEED_RAD_S,cycles=cycles,action=action,label=NAMES[action],amplitude=amplitude,
        source=p['source'],physical_contact_verified=False))
