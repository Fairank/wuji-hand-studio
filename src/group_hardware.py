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
    return dict(g,clock_id=clock_id(),clock_s=time.monotonic(),active=motion.active,
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
