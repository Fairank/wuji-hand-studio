"""Native SDK joint-order mapping of authored two-hand rhythms, not mirrored images."""
from functools import lru_cache
from bimanual_patterns import CATALOG, sample as rhythm

INTERNAL_IDS={key+'_'+side for key in CATALOG for side in ('left','right')}


def resolve_action(kind,action,side):
    from performance_program import DANCES
    if kind=='same' and action in DANCES:return action
    if kind=='pair' and action in CATALOG and side in ('left','right'):return action+'_'+side
    raise ValueError('Choose a group routine / 请选择编组动作')


@lru_cache(maxsize=16)
def program(action):
    if action not in INTERNAL_IDS:raise ValueError('Unknown paired performance')
    name,side=action.rsplit('_',1)
    from gesture_library import poses
    opened=poses()['open'];points=[];duration=CATALOG[name]['duration_s']
    for k in range(round(duration*100)+1):
        t=k/100;r=rhythm(name,t,side);q=opened[:];v=[0.]*20
        for f in range(5):
            for j,amount in ((0,.8),(2,1.1),(3,.85)):
                if f==0:amount*=.45
                q[4*f+j]+=amount*r['curl'][f];v[4*f+j]=amount*r['curl_velocity'][f]
            amount=(.24,-.40,-.14,.16,.43)[f]
            q[4*f+1]+=amount*r['spread'][f];v[4*f+1]=amount*r['spread_velocity'][f]
        points.append(dict(t=t,q=q,v=v,interpolation='cubic_hermite',label=CATALOG[name]['zh']+' / '+CATALOG[name]['en'],token_index=-1))
    return dict(schema='wuji-bimanual-v1',action=action,text='',points=points,duration_s=duration,
                source='project_authored_bimanual',source_url=None,fixed_base=True,
                arrangement='palms_forward_thumbs_inward',side=side,
                hardware_validated=False,sign_language_certified=False)


def catalog():
    from performance_program import DANCES
    return ([dict(id=k,zh=z,en=e,kind='same') for k,(z,e,_) in DANCES.items()]+
            [dict(id=k,zh=x['zh'],en=x['en'],kind='pair') for k,x in CATALOG.items()])
