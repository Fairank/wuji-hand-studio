"""Prepared group playback layered on the existing official SDK command path."""
import time
from group_timing import clock_id, number, retime_sections


def metadata(plan):
    p=plan['points']
    return dict(entry_s=p[1]['t'],body_s=p[-3]['t']-p[1]['t'],
                exit_s=p[-2]['t']-p[-3]['t'],hold_s=p[-1]['t']-p[-2]['t'])


def validate_spec(spec,action):
    from performance_program import DANCES
    from bimanual_program import INTERNAL_IDS
    from device_profiles import controller_profile
    if not isinstance(spec,dict) or set(spec)!={'token'} or not isinstance(spec['token'],str) or len(spec['token'])!=32:
        raise ValueError('Invalid group reservation')
    if action not in DANCES and action not in INTERNAL_IDS:raise ValueError('Unsupported group choreography')
    if controller_profile()['generation']!='hand2':raise ValueError('Hand 1 group routines are preview-only')
    if action in INTERNAL_IDS and not action.endswith('_'+controller_profile()['side']):raise ValueError('Group role differs from native hand identity')
    if not clock_id():raise ValueError('Group playback requires a shared Linux clock')


def status(motion):
    g=getattr(motion,'group_sync',None)
    if not g:return None
    return dict(g,control_version=1,clock_id=clock_id(),clock_s=time.monotonic(),active=motion.active,
                phase='ready' if motion.phase=='group_wait' and not g.get('start_s') else motion.phase,
                timing=metadata(motion.run_profile),elapsed_s=motion.elapsed,
                completed=bool((motion.trial_result or {}).get('completed')),
                stop_confirmed=not motion.owned,reason=motion.reason)


def commit(motion,command,now):
    g=getattr(motion,'group_sync',None)
    if not g or g['token']!=command.get('token') or motion.phase!='group_wait' or g.get('start_s'):
        raise ValueError('Group is not ready to commit')
    start=number(command.get('start_s'),now+.1,now+5.)
    old=metadata(motion.run_profile);new=command.get('timing')
    if not isinstance(new,dict) or set(new)!=set(old):raise ValueError('Incomplete group schedule')
    new={k:number(new[k],old[k]-1e-7,36000.) for k in old}
    from motion_parameters import MAX_TRIAL_DURATION_S
    if sum(new.values())*motion.cycles>MAX_TRIAL_DURATION_S:raise ValueError('Group duration exceeds configured playback duration')
    motion.points=retime_sections(motion.points,old,new)
    motion.run_profile['points']=motion.points;motion.index_path()
    motion.trial['deadline']=start+motion.period*motion.cycles*1.5+30
    g.update(start_s=start)


def control(motion,command,now):
    """Reserve one future pause/resume; only the worker tick applies it."""
    g=getattr(motion,'group_sync',None)
    name=command['name'].removeprefix('hardware_group_')
    epoch=command.get('epoch')
    if (not g or g['token']!=command.get('token') or name not in ('pause','resume')
            or type(epoch) is not int or epoch!=g.get('control_epoch',0)+1
            or g.get('pending_control')):
        raise ValueError('Expired or pending group control')
    expected='playing' if name=='pause' else 'paused'
    if motion.phase!=expected or motion.paused!=(name=='resume'):
        raise ValueError('Group cannot '+name+' in its current phase')
    at=number(command.get('at_s'),now+.1,now+5.)
    if name=='pause' and at-g['start_s']>=motion.period*motion.cycles:
        raise ValueError('Group is finishing; pause is no longer available')
    g.update(control_epoch=epoch,pending_control=dict(action=name,at_s=at,epoch=epoch))


def apply_control(motion,q,now):
    g=getattr(motion,'group_sync',None)
    pending=g.get('pending_control') if g else None
    if not pending or now<pending['at_s']:return
    at=pending['at_s']
    if now-at>.03:raise ValueError('Missed common '+pending['action']+' deadline; group stopped')
    if pending['action']=='pause':
        if motion.phase!='playing':raise ValueError('Group left playback before pause')
        motion.elapsed=max(0.,at-g['start_s'])
        motion.target=q[:];motion.paused=True;motion.phase='paused';g['paused_at_s']=at
        motion.reason='整组已暂停 / Group paused'
    else:
        if motion.phase!='paused' or not motion.paused:raise ValueError('Group is not paused')
        duration=at-g['paused_at_s']
        g['start_s']+=duration
        motion.trial['deadline']+=duration
        motion.paused=False;motion.phase='playing';motion.last_tick=at
        g['paused_at_s']=None;motion.reason='整组继续播放 / Group resumed'
    g.update(applied_epoch=pending['epoch'],pending_control=None)


def wait(motion,now):
    """Return true while publishing the measured preparation pose."""
    g=getattr(motion,'group_sync',None)
    if not g:return False
    if motion.phase=='approach':motion.phase='group_wait'
    if motion.phase!='group_wait':return False
    if not g.get('start_s'):
        if now>g['deadline']:raise ValueError('Group preparation expired')
    elif now>=g['start_s']:
        if now-g['start_s']>.03:raise ValueError('Missed common start deadline; group stopped')
        motion.phase='playing';motion.last_tick=g['start_s'];return False
    if motion.cadence.due(now):motion.publish(motion.target)
    return True
