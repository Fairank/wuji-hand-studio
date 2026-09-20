"""Authored fixed-base choreography. Pure joint data, never device I/O.

Human tutorials inform rhythm only: these are not captured human trajectories,
official Wuji motions, certified signs, or physically validated performances.
"""
import math
from bisect import bisect_right
from functools import lru_cache

DANCES = {'dance_jellyfish': ('水母舒展', 'Jellyfish', 16.),
          'dance_wave': ('逐指波浪', 'Finger wave', 16.),
          'dance_ripple': ('指节涟漪', 'Knuckle ripple', 12.)}
PROGRAM_IDS = set(DANCES) | {'text_sequence', 'letter_J', 'letter_Z'}
REFERENCE = 'https://www.brambilabong.com/blogs/popping/learn-3-finger-tutting-dance-moves-tutorial'
SIGN_REFERENCE = 'https://www.lifeprint.com/asl101/pages-layout/fingerspelling.htm'


def smooth(x):
    return x*x*x*(10+x*(-15+6*x))


def _dance(action, t):
    from gesture_library import poses
    duration = DANCES[action][2]
    q = poses()['open'][:]
    v = [0.]*20
    # A 2 s quintic envelope gives zero velocity/acceleration at entry and exit.
    x = min(1., max(0., min(t, duration-t)/2.))
    env = smooth(x)
    denv = 30*x*x*(1-x)**2/2 * (1 if t<duration/2 else -1) if x<1 else 0.
    omega = 2*math.pi/4.
    for f in range(5):
        # Keep the thumb outside the palm; non-thumb flexion is more pronounced.
        for j, amount in ((0,.48),(2,.75),(3,.52)):
            if f == 0: amount *= .25
            delay = .13*f+.30*j if action=='dance_jellyfish' else .8*f+.13*j if action=='dance_wave' else .25*f+.8*j
            a = omega*t-delay
            pulse = .5-.5*math.cos(a)
            q[4*f+j] += amount*env*pulse
            v[4*f+j] = amount*(denv*pulse+env*.5*omega*math.sin(a))
        spread = (0.,.13,.04,-.04,-.13)[f]
        pulse = .5+.5*math.cos(omega*t)
        if action=='dance_jellyfish':
            q[4*f+1] += spread*env*pulse
            v[4*f+1] = spread*(denv*pulse-env*.5*omega*math.sin(omega*t))
    return q,v


def _symbol(c):
    from gesture_library import poses, letter, digit
    if c==' ':return [('单词停顿 / Word pause',poses()['open'][:],.8)]
    if c.isdigit():return [(c,digit(int(c)),.8)]
    if c not in 'JZ':return [(c,letter(c),.8)]
    # No wrist/forearm exists: explicitly named finger-only suggestions, NOT ASL.
    q=letter('I' if c=='J' else 'D')
    f=4 if c=='J' else 1
    path=[(0.,0.),(.12,.13),(.30,.16),(.42,-.10)] if c=='J' else [(0.,-.18),(0.,.18),(.28,-.18),(.28,.18)]
    out=[]
    for k,(flex,abd) in enumerate(path):
        p=q[:];p[f*4]+=flex;p[f*4+1]=abd
        out.append((f'{c} · 固定腕近似 / fixed-wrist approximation {k+1}/{len(path)}',p,.12 if k<len(path)-1 else .6))
    return out


@lru_cache(maxsize=48)
def program(action, text='WUJI TECH'):
    """Nominal timeline in seconds/radians, with repeated letters separated."""
    from gesture_library import poses
    from phrase_text import normalize_phrase
    if action not in PROGRAM_IDS:raise ValueError('Unknown performance')
    start=poses()['open'][:]
    points=[dict(t=0.,q=start,label='准备 / Ready',token_index=-1)]
    normalized=''
    if action in DANCES:
        duration=DANCES[action][2]
        # 100 Hz Hermite knots; the host evaluates the curve at its configured rate.
        for k in range(int(duration*100)+1):
            t=k/100.;q,v=_dance(action,t)
            p=dict(t=t,q=q,v=v,label=DANCES[action][0]+' / '+DANCES[action][1],interpolation='cubic_hermite',token_index=-1)
            if k==0:points[0]=p
            else:points.append(p)
    else:
        normalized=normalize_phrase(text if action=='text_sequence' else action[-1])
        previous=None
        for index,c in enumerate(normalized):
            steps=_symbol(c)
            if c==previous and c!=' ':steps=[('重复字母分隔 / Repeat separator',start,.15)]+steps
            for label,q,hold in steps:
                transition=.6 if c in 'JZ' else 1.
                points.append(dict(t=points[-1]['t']+transition,q=q,label=label,interpolation='minimum_jerk',token_index=index))
                points.append(dict(t=points[-1]['t']+hold,q=q[:],label=label,token_index=index))
            previous=c
        points.append(dict(t=points[-1]['t']+1.,q=start,label='恢复 / Return',interpolation='minimum_jerk',token_index=-1))
        points.append(dict(t=points[-1]['t']+.4,q=start[:],label='完成 / Complete',token_index=-1))
    return dict(schema='wuji-performance-v1',action=action,text=normalized,points=points,
        duration_s=points[-1]['t'],source='project_authored_human_inspired',
        source_url=REFERENCE if action in DANCES else SIGN_REFERENCE,
        fixed_base=True,hardware_validated=False,sign_language_certified=False)


@lru_cache(maxsize=48)
def _times(action,text):return tuple(p['t'] for p in program(action,text)['points'])


def sample(action,elapsed,text='WUJI TECH',*,loop=True):
    from motion_timing import segment_position
    data=program(action,text);points=data['points'];duration=data['duration_s']
    t=elapsed%duration if loop else max(0.,min(elapsed,duration))
    i=min(len(points)-2,max(0,bisect_right(_times(action,text),t)-1))
    a,b=points[i:i+2]
    return dict(q=segment_position(a,b,t),label=b['label'],token_index=b.get('token_index',-1),duration_s=duration,text=data['text'])


def segment_peak(a,b):
    """Exact peak absolute velocity of the interpolation polynomial."""
    dt=b['t']-a['t'];peak=0.
    for j,(x,y) in enumerate(zip(a['q'],b['q'])):
        if b.get('interpolation')=='cubic_hermite':
            va=a.get('v',[0.]*20)[j]*dt;vb=b.get('v',[0.]*20)[j]*dt
            A=2*x-2*y+va+vb;B=-3*x+3*y-2*va-vb;C=va
            candidates=[0.,1.]
            if abs(A)>1e-14 and 0<-B/(3*A)<1:candidates.append(-B/(3*A))
            peak=max(peak,*[abs(3*A*r*r+2*B*r+C)/dt for r in candidates])
        else:peak=max(peak,abs(y-x)/dt*(1.875 if b.get('interpolation')=='minimum_jerk' else 1.))
    return peak


def real_points(action,text,q,amplitude,limit,min_transition,hold):
    """Same choreography, uniformly retimed; entry/exit from measured SDK pose."""
    data=program(action,text)
    points=[]
    for p in data['points']:
        p=dict(p,q=[a+amplitude*(b-a) for a,b in zip(q,p['q'])])
        if 'v' in p:p['v']=[amplitude*v for v in p['v']]
        points.append(p)
    scale=max(1.,max(segment_peak(a,b) for a,b in zip(points,points[1:]))/limit)
    if action not in DANCES:
        scale=max(scale,max(min_transition/(b['t']-a['t']) for a,b in zip(points,points[1:]) if b.get('interpolation')=='minimum_jerk'))
    entry=max(min_transition,1.875*max(abs(x-y) for x,y in zip(q,points[0]['q']))/limit)
    for p in points:
        p['t']=entry+p['t']*scale
        if 'v' in p:p['v']=[v/scale for v in p['v']]
    points[0]['interpolation']='minimum_jerk'
    points.insert(0,dict(t=0.,q=q[:],v=[0.]*20,label='实测起点 / Measured start'))
    duration=max(min_transition,1.875*max(abs(x-y) for x,y in zip(q,points[-1]['q']))/limit)
    points.extend([dict(t=points[-1]['t']+duration,q=q[:],label='返回实测起点 / Return',interpolation='minimum_jerk'),
        dict(t=points[-1]['t']+duration+max(.01,hold),q=q[:],label='完成 / Complete')])
    return points,scale
