"""Reviewed, deterministic LEFT SDK-order demo poses. No device I/O.

Letters are approximate finger shapes, not certified ASL: a fixed wrist cannot
reproduce palm orientations or the moving J/Z. Never infer touch from a pose.
"""
from datetime import datetime
import json
import math
from pathlib import Path

LETTERS = 'ABCDEFGHIKLMNOPQRSTUVWXY'
COMMIT = 'b0e48652dd94f4bc33df61cdc23a6d5dc598f93d'
SDK = f'https://github.com/wuji-technology/wuji-sdk/tree/{COMMIT}/examples/python/wuji_hand_2/'
_POSES = None


def entry(key, zh, en, group, source='project', note=''):
    return dict(id=key, zh=zh, en=en, group=group, source=source, note=note,
                hardware=True, physical_contact_verified=False)


CATALOG = [entry('official_opposition','官方左手对指录制','Official left opposition replay','official','official',SDK+'6_opposition'),
    entry('official_home','官方归零示例 · 返回起点适配','Official home example · return adaptation','official','official',SDK+'2.publish.py'),
    entry('official_sweep','官方0号关节余弦扫动 · 适配','Official joint-0 cosine sweep · adapted','official','official',SDK+'7.mit_sweep.py')]
CATALOG += [entry(k,z,e,'basic') for k,z,e in [
    ('open','张开并返回','Open and return'),('fist','张开与握拳','Open and fist'),
    ('opposition','依次对指','Finger opposition'),('sequence','组合展示','Combined demo'),
    ('splay','左右侧摆','Finger spread'),('wave','逐指屈伸','Finger wave')]]
CATALOG += [entry('digit_'+str(i),'数字 '+str(i),'Digit '+str(i),'numbers') for i in range(10)]
CATALOG += [entry('count_digits','依次报数 0–9','Count 0–9','numbers'),
    entry('clock','当前时间 · HH:MM','Current time · HH:MM','numbers')]
CATALOG += [entry('letter_'+c,'字母 '+c+' · 近似造型','Letter '+c+' · approximate','letters') for c in LETTERS]
CATALOG += [entry('alphabet','24个静态字母 · 近似造型','24 static letters · approximate','letters')]
CUSTOM_IDS = {x['id'] for x in CATALOG} - {'open','fist','opposition','sequence','official_opposition'}

OFFICIAL_INVENTORY = [
    dict(name='Hand 2 · 6_opposition',status='adapted',detail='Left recording integrated; entry/return, amplitude and timing adapted.',url=SDK+'6_opposition'),
    dict(name='Hand 2 · 2.publish',status='adapted',detail='Smooth home/hold integrated; returns to measured starting pose.',url=SDK+'2.publish.py'),
    dict(name='Hand 2 · 7.mit_sweep',status='adapted',detail='101 cosine targets on joint 0 integrated; entry/return and timing adapted.',url=SDK+'7.mit_sweep.py'),
    dict(name='Hand 2 · 0/1/3/4/5',status='not_motion',detail='Feedback, fingertip stream, stream rate and flash log examples; not gesture recordings.',url=SDK),
    dict(name='Retargeting / teleoperation',status='requires_input',detail='Requires glove/keypoint input and calibration; not a bundled gesture library.',url=f'https://github.com/wuji-technology/wuji-sdk/tree/{COMMIT}/examples/python/retargeting'),
    dict(name='ROS2 wave / first-generation grasp',status='different_device',detail='First-generation model/driver examples; not directly executable on Hand 2.',url='https://github.com/wuji-technology/wujihandros2')]


def poses():
    global _POSES
    if _POSES is None:
        _POSES = json.loads(Path(__file__).with_name('trial_sdk_poses.json').read_text(encoding='utf-8'))['poses']
    return _POSES


def shape(extended=(), thumb=False):
    p=poses();q=p['fist'][:]
    for f in extended:q[f*4:f*4+4]=p['open'][f*4:f*4+4]
    # Thumb rests outside the fingers; these shapes do not claim opposition.
    q[:4]=[-.18,-.30,.08,.08] if thumb else [.12,-.25,.50,.48]
    return q


def digit(number):
    if number==0:
        q=shape(range(1,5));q[:4]=[.50,-.45,.6,.45]
        for f in range(1,5):q[4*f:4*f+4]=[.42,0,.70,.40]
        return q
    if number<=5:return shape(range(1,number+1) if number<4 else range(1,5),thumb=number in {3,5}) if number!=3 else shape((1,2),thumb=True)
    paired={6:'pinky',7:'ring',8:'middle',9:'index'}[number]
    q=poses()['pair_'+paired][:]
    f={'index':1,'middle':2,'ring':3,'pinky':4}[paired]
    for other in range(1,5):
        if other!=f:q[other*4:other*4+4]=poses()['open'][other*4:other*4+4]
    return q


def letter(c):
    open_fingers={'A':(), 'B':(1,2,3,4),'C':(1,2,3,4),'D':(1,), 'E':(), 'F':(2,3,4),
        'G':(1,), 'H':(1,2), 'I':(4,), 'K':(1,2), 'L':(1,), 'M':(), 'N':(), 'O':(),
        'P':(1,2),'Q':(1,),'R':(1,2),'S':(),'T':(),'U':(1,2),'V':(1,2),
        'W':(1,2,3),'X':(1,),'Y':(4,)}[c]
    q=shape(open_fingers,thumb=c in 'AKLY')
    if c=='F':return digit(9)
    if c=='O':return digit(0)
    if c=='C':
        for f in range(1,5):q[4*f:4*f+4]=[.40,0,.60,.35]
        q[:4]=[.15,-.45,.40,.25]
    if c=='D':
        for f in (2,3,4):q[4*f:4*f+4]=[.5,0,.8,.55]
        q[:4]=[.6,-.4,.7,.5]
    if c=='E':
        for f in range(1,5):q[4*f:4*f+4]=[.35,0,1.2,.9]
    if c in 'GH':
        for f in open_fingers:q[4*f]=.6
        q[:4]=[-.1,-.5,.2,.15]
    if c in 'KP':q[9]=-.16;q[:4]=[.35,-.4,.3,.3]
    if c in 'PQ':
        for f in open_fingers:q[4*f]=1.0
    if c=='R':q[5]=-.13;q[9]=.13
    if c in 'VW':q[5]=.18;q[9]=-.06
    if c=='W':q[13]=-.18
    if c=='X':q[6]=.85;q[7]=.3
    if c in 'MNST':
        q[:4]={'M':[.45,-.35,.68,.55],'N':[.4,-.30,.60,.45],
               'S':[.55,-.20,.5,.4],'T':[.3,-.15,.35,.3]}[c]
    return q


def route(action, *, at=None):
    """(label, target, minimum hold seconds) pairs; timestamp frozen per run."""
    p=poses()
    if action.startswith('digit_') and action in CUSTOM_IDS:return [(action[6:],digit(int(action[6:])),1.)]
    if action.startswith('letter_') and action in CUSTOM_IDS:return [(action[7:],letter(action[7:]),1.)]
    if action=='count_digits':return [(str(i),digit(i),1.) for i in range(10)]
    if action=='alphabet':return [(c,letter(c),1.) for c in LETTERS]
    if action=='clock':
        now=at or datetime.now();digits=f'{now.hour:02d}{now.minute:02d}'
        result=[]
        for label,c in zip(('小时十位 / Hour tens','小时个位 / Hour units','分钟十位 / Minute tens','分钟个位 / Minute units'),digits):
            result.append((f'{now:%H:%M} · {label} = {c}',digit(int(c)),1.2))
            result.append(('分隔 / Separator',p['open'][:],.3))
        return result
    if action=='splay':
        out=[]
        for sign in (1,-1,0):
            q=p['open'][:]
            for f,x in enumerate((.15,.25,.12,-.12,-.25)):q[f*4+1]+=sign*x
            out.append(('侧摆 / Spread',q,.15))
        return out
    if action=='wave':
        out=[]
        for f in range(5):
            q=p['open'][:];q[f*4:f*4+4]=p['fist'][f*4:f*4+4] if f else [.5,-.3,.65,.5]
            out.extend([(f'手指 / Finger {f+1}',q,.15),('张开 / Open',p['open'][:],.15)])
        return out
    if action=='official_home':return [('官方归零 / Home',[0.]*20,5.)]
    if action=='official_sweep':
        # The original sample commands all other joints at zero, not current q.
        return [(f'余弦扫动 / Cosine {i}/100',[.01*(1-math.cos(2*math.pi*i/100))]+[0.]*19,0.) for i in range(101)]
    raise ValueError('Unknown gesture')


def catalog():
    return dict(actions=CATALOG,official_inventory=OFFICIAL_INVENTORY,
        letters_note='Approximate fixed-wrist finger shapes, not certified sign language. J/Z require motion and are excluded.',
        clock_note='Local server time captured once at start, HH:MM, 24-hour format.',
        interaction=dict(recognition_ready=False,hardware_grasp_ready=False,
            reason='Real-device current-to-load calibration and withdrawal validation have not passed. No real grasp is enabled.'))
