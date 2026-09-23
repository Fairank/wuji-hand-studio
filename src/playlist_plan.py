"""Pure playlist validation and order generation; hardware control lives elsewhere."""
import math
import random
from playback_rates import PLAYBACK_SPEEDS


def validate_plan(payload, allowed_actions):
    allowed=set(allowed_actions)
    if not isinstance(payload,dict) or set(payload)-{'entries','order','repeats','seed'}:
        raise ValueError('Invalid playlist fields')
    entries=payload.get('entries')
    if not isinstance(entries,list) or not 1<=len(entries)<=64:
        raise ValueError('Choose 1–64 actions')
    normalized=[]
    for row in entries:
        if not isinstance(row,dict) or set(row)-{'action','speed','cycles'} or 'action' not in row:
            raise ValueError('Invalid playlist entry')
        action,speed,cycles=row['action'],row.get('speed',1.),row.get('cycles',1)
        if not isinstance(action,str) or action not in allowed:
            raise ValueError('Action is unavailable in this playlist')
        if type(speed) not in (int,float) or not math.isfinite(speed) or speed not in PLAYBACK_SPEEDS:
            raise ValueError('Choose a supported playback speed')
        if type(cycles) is not int or not 1<=cycles<=20:
            raise ValueError('Entry repeat count must be 1–20')
        normalized.append(dict(action=action,speed=float(speed),cycles=cycles))
    order,repeats,seed=payload.get('order','sequence'),payload.get('repeats',1),payload.get('seed',0)
    if order not in ('sequence','shuffle') or type(order) is not str:
        raise ValueError('Unknown playlist order')
    if type(repeats) is not int or not 0<=repeats<=100:
        raise ValueError('Playlist repeats must be 0–100')
    if type(seed) is not int or not 0<=seed<=0xffffffff:
        raise ValueError('Invalid shuffle seed')
    return dict(entries=normalized,order=order,repeats=repeats,seed=seed)


def iter_plan(plan):
    # A running playlist must not change when the UI edits its copy.
    plan=validate_plan(plan,(row['action'] for row in plan['entries']))
    return _steps(plan)


def _steps(plan):
    entries=plan['entries'];rng=random.Random(plan['seed'])
    rounds=range(plan['repeats']) if plan['repeats'] else iter(int,1)
    previous=None
    for round_index in rounds:
        indices=list(range(len(entries)))
        if plan['order']=='shuffle':
            rng.shuffle(indices)
            if previous is not None and entries[indices[0]]['action']==previous:
                replacement=next((j for j in range(1,len(indices)) if entries[indices[j]]['action']!=previous),None)
                if replacement is not None:indices[0],indices[replacement]=indices[replacement],indices[0]
        for position,index in enumerate(indices):
            row=entries[index]
            yield dict(**row,round=round_index+1,index=index,total_in_round=len(indices),position=position+1)
            previous=row['action']
