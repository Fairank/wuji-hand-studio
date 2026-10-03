"""Authored fixed-base choreography. Pure joint data, never device I/O.

Human tutorials inform rhythm only: these are not captured human trajectories,
official Wuji motions, certified signs, or physically validated performances.
"""
import math
from bisect import bisect_right
from functools import lru_cache

DANCES = {'dance_jellyfish': ('水母舒展', 'Jellyfish', 16.),
          'dance_wave': ('逐指波浪', 'Finger wave', 16.),
          'dance_ripple': ('指节涟漪', 'Knuckle ripple', 12.),
          'dance_reverse': ('逆向卷浪', 'Reverse roll', 16.),
          'dance_piano': ('空中钢琴', 'Air piano', 12.),
          'dance_alternate': ('交替律动', 'Alternating rhythm', 12.),
          'dance_fan': ('折扇开合', 'Folding fan', 16.),
          'dance_bloom': ('花苞绽放', 'Bloom', 16.),
          'dance_tutting': ('指节阶梯', 'Knuckle staircase', 12.),
          'preview_finger_limits': ('逐指屈伸大幅度预览 · 仅预览', 'Large-range finger flexion preview · preview only', 19.),
          'preview_lateral_limits': ('逐指侧摆大幅度预览 · 仅预览', 'Large-range finger splay preview · preview only', 19.),
          'preview_thumb_limits': ('拇指活动范围大幅度预览 · 仅预览', 'Large-range thumb preview · preview only', 12.)}
PREVIEW_ONLY_IDS = {'preview_finger_limits','preview_lateral_limits','preview_thumb_limits'}
NEW_DANCE_IDS = set(DANCES) - {'dance_jellyfish', 'dance_wave', 'dance_ripple'}
from bimanual_program import INTERNAL_IDS
PROGRAM_IDS = set(DANCES) | INTERNAL_IDS | {'text_sequence', 'letter_J', 'letter_Z'}
REFERENCE = 'https://www.brambilabong.com/blogs/popping/learn-3-finger-tutting-dance-moves-tutorial'
SIGN_REFERENCE = 'https://www.lifeprint.com/asl101/pages-layout/fingerspelling.htm'
WAVE_REFERENCE = 'https://howcast.com/videos/493866-how-to-do-waving-tutting/'
DANCE_REFERENCES = {key: WAVE_REFERENCE if key in {'dance_bloom', 'dance_tutting'} else REFERENCE for key in DANCES}


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
        for j, amount in ((0,.80),(2,1.10),(3,.85)):
            delay = .13*f+.30*j if action=='dance_jellyfish' else .8*f+.13*j if action=='dance_wave' else .25*f+.8*j
            power=1
            if action=='dance_reverse': delay=.8*(4-f)+.13*j
            elif action=='dance_piano':
                delay=1.25*f;amount={0:.50,2:.78,3:.52}[j];power=4
            elif action=='dance_alternate':
                delay=math.pi*(f%2)+.10*j;amount*=.8
            elif action=='dance_fan': delay=.55*f;amount*=.75
            elif action=='dance_bloom': delay=.35*j
            elif action=='dance_tutting':
                delay=.7*f+.9*j;amount={0:.22,2:1.15,3:.62}[j];power=3
            if f == 0: amount *= .30 if action=='dance_reverse' else .45
            a = omega*t-delay
            base = .5-.5*math.cos(a)
            pulse=base**power
            derivative=power*base**(power-1)*.5*omega*math.sin(a)
            q[4*f+j] += amount*env*pulse
            v[4*f+j] = amount*(denv*pulse+env*derivative)
        # S2 is active in EVERY routine. Index negative / little positive opens
        # the fan in the native models; the previous sign closed fingers inward.
        spread = (.24,-.40,-.14,.16,.43)[f]
        phase = {'dance_wave':.50*f,'dance_reverse':.50*(4-f),
                 'dance_ripple':.32*f,'dance_piano':.60*f,
                 'dance_alternate':math.pi*(f%2),'dance_tutting':.65*f}.get(action,0.)
        if action=='dance_fan':spread*=1.2
        elif action=='dance_piano':spread*=.8
        a=omega*t-phase
        pulse = .5+.5*math.cos(a)
        q[4*f+1] += spread*env*pulse
        v[4*f+1] = spread*(denv*pulse-env*.5*omega*math.sin(a))
    return q,v


def _range_preview(action, t):
    """High-amplitude, fixed-base display explorations; never a device plan."""
    from gesture_library import poses
    q=poses()['open'][:]
    v=[0.]*20

    def ease(x):
        x=min(1.,max(0.,x))
        return x*x*x*(10+x*(-15+6*x))

    def ease_rate(x):
        return 30*x*x*(1-x)*(1-x) if 0.<x<1. else 0.

    if action in {'preview_finger_limits','preview_lateral_limits'}:
        # Three-second windows with eased entry/exit and no concurrent fingers.
        # The last joint stays visibly flexed while each finger is isolated.
        for f in range(5):
            start=.7+3.35*f
            phase=t-start
            if action=='preview_finger_limits':
                if phase<0. or phase>=3.:continue
                if phase<1.:
                    amount=ease(phase);rate=ease_rate(phase)
                elif phase<2.:
                    amount=1.;rate=0.
                else:
                    amount=1.-ease(phase-2.);rate=-ease_rate(phase-2.)
                target=(1.15,.58,1.40,1.35) if f==0 else (1.40,0.,1.92,1.38)
                for j,value in enumerate(target):
                    index=f*4+j
                    delta=value-q[index]
                    q[index]+=delta*amount
                    v[index]=delta*rate
            else:
                if phase<0. or phase>=3.:continue
                # Broad but not hard-stop side-to-side exploration, with zero
                # velocity at each reversal and at the neutral return.
                joint=f*4+1
                if phase<1.:
                    amount=-.62*ease(phase);rate=-.62*ease_rate(phase)
                elif phase<2.:
                    x=phase-1.;amount=-.62+1.24*ease(x);rate=1.24*ease_rate(x)
                else:
                    x=phase-2.;amount=.62-.62*ease(x);rate=-.62*ease_rate(x)
                q[joint]=amount
                v[joint]=rate
        return q,v

    # A three-stop thumb arc samples both sides of its native travel with a
    # margin from the limits, then settles back at the open reference pose.
    targets=((1.15,.58,1.40,1.35),(-1.05,-1.30,-.90,-.90),(.05,-.20,.15,.10))
    segment=3.
    phase=min(3.,max(0.,t/segment))
    if phase>=3.:
        q[:4]=list(targets[-1])
        return q,v
    index=int(phase);u=phase-index
    weight=ease(u);rate=ease_rate(u)/segment
    source=(.05,-.20,.15,.10) if index==0 else targets[index-1]
    target=targets[index]
    for j,(a,b) in enumerate(zip(source,target)):
        q[j]=a+(b-a)*weight
        v[j]=(b-a)*rate
    return q,v


def _symbol(c):
    from gesture_library import poses, letter, digit
    if c==' ':return [('单词停顿 / Word pause',poses()['open'][:],.8)]
    if c.isdigit():return [('数字过渡 / Digit transition',poses()['open'][:],.12),(c,digit(int(c)),.8)]
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
    if action in INTERNAL_IDS:
        from bimanual_program import program as paired
        return paired(action)
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
            t=k/100.;q,v=_range_preview(action,t) if action in PREVIEW_ONLY_IDS else _dance(action,t)
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
        source_url=None if action in PREVIEW_ONLY_IDS else DANCE_REFERENCES[action] if action in DANCES else SIGN_REFERENCE,
        fixed_base=True,hardware_validated=False,hardware_preview_only=action in PREVIEW_ONLY_IDS,
        sign_language_certified=False)


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
    if action not in DANCES and action not in INTERNAL_IDS:
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
