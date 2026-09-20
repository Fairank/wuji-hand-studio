"""Bounded scripted MuJoCo pose previews; never writes to hardware."""
import math
import threading
import time
from datetime import datetime
from gesture_library import CATALOG as GESTURES,CUSTOM_IDS,route

CATALOG = {
    'all':'整套展示', 'open':'张开', 'fist':'握拳', 'opposition':'依次对指',
    'splay':'左右侧摆', 'wave':'逐指屈伸', 'joints':'逐关节活动',
    'thumb':'拇指活动', 'index':'食指活动', 'middle':'中指活动',
    'ring':'无名指活动', 'little':'小指活动',
    'official_opposition':'官方左手对指录制',
}
CATALOG.update({x['id']:x['zh'] for x in GESTURES if x['id'] in CUSTOM_IDS})
CATALOG['touch_flow']='轻触反应流程 · 编排预览'
CATALOG['sequence']='组合展示'
PERIOD = {key:6. for key in CATALOG}
PERIOD.update(opposition=20., wave=15., joints=60., all=53.)
PERIOD['official_opposition']=30.999
for _key in CUSTOM_IDS:PERIOD[_key]=max(6.,len(route(_key))*3.)
PERIOD['touch_flow']=8.
PERIOD['sequence']=53.
PERIOD['official_sweep']=6.


class DemoPlayer:
    def __init__(self):
        self.lock=threading.RLock()
        self.active=False
        self.running=False
        self.action='all'
        self.speed=.5
        self.cycles=1
        self.elapsed=0.
        self.tick=time.monotonic()
        self.clock_at=None

    def advance(self):
        now=time.monotonic()
        if self.active and self.running:self.elapsed+=(now-self.tick)*self.speed
        self.tick=now
        if self.cycles and self.elapsed>=PERIOD[self.action]*self.cycles:
            self.elapsed=PERIOD[self.action]*self.cycles
            self.running=False

    def command(self, data):
        with self.lock:
            self.advance()
            name=data['name']
            if name=='demo_start':
                action=data.get('action');speed=data.get('speed');cycles=data.get('cycles')
                if action not in CATALOG or type(speed) not in {int,float} or speed not in {.25,.5,1.}:
                    raise ValueError('无效动作或速度')
                if type(cycles) is not int or cycles not in {0,1,3,10}:
                    raise ValueError('播放次数只能选择1、3、10或循环')
                self.action,self.speed,self.cycles=action,speed,cycles
                self.clock_at=datetime.now().astimezone().isoformat()
                self.active=self.running=True;self.elapsed=0.;self.tick=time.monotonic()
            elif name=='demo_pause':self.running=False
            elif name=='demo_resume':
                if self.cycles and self.elapsed>=PERIOD[self.action]*self.cycles:self.elapsed=0.
                self.running=self.active
            elif name=='demo_stop':self.active=self.running=False
            else:raise ValueError('Unknown preview command')

    def snapshot(self):
        with self.lock:
            self.advance()
            display_elapsed=self.elapsed
            if self.cycles and self.elapsed>=PERIOD[self.action]*self.cycles:
                display_elapsed=max(0.,self.elapsed-1e-6)
            phase_label=CATALOG[self.action]
            if self.action in CUSTOM_IDS:
                steps=route(self.action,at=datetime.fromisoformat(self.clock_at) if self.clock_at else None)
                phase_label=steps[min(len(steps)-1,int((display_elapsed%PERIOD[self.action])/3.))][0]
            return dict(active=self.active,running=self.running,action=self.action,
                label=CATALOG[self.action]+' · '+phase_label if self.action in CUSTOM_IDS else phase_label,speed=self.speed,cycles=self.cycles,
                clock_at=self.clock_at,
                pose_elapsed_s=display_elapsed,
                elapsed_s=self.elapsed,cycle=min(int(self.elapsed/PERIOD[self.action])+1,self.cycles) if self.cycles else int(self.elapsed/PERIOD[self.action])+1,
                source='official_left_recording_model_preview' if self.action=='official_opposition' else 'scripted_mujoco_pose_preview',hardware_motion=False)


class PoseLibrary:
    def __init__(self, model,side="left"):
        self.side=side
        import mujoco as mj
        import numpy as np
        from scipy.optimize import least_squares
        self.np=np
        self.lo,self.hi=model.actuator_ctrlrange.T.copy()
        self.open=np.tile([.08,0,.12,.10],5);self.open[:4]=[.05,-.2,.15,.1]
        self.fist=self.open.copy()
        for f in range(1,5):self.fist[f*4:f*4+4]=[.85,0,1.2,.9]
        self.fist[:4]=[.7,-.35,.7,.45]
        self.pairs=[]
        data=mj.MjData(model)
        sites=[model.site(('l_' if side=='left' else 'r_')+f+'_tip').id for f in ('thumb','index_finger','middle_finger','ring_finger','pinky')]
        for finger in range(1,5):
            active=np.r_[np.arange(4),np.arange(finger*4,finger*4+4)]
            prior=np.array([.8,-.5,.8,.5,.6,0,.8,.5])
            def residual(x):
                q=self.open.copy();q[active]=x;data.qpos[:20]=q;mj.mj_forward(model,data)
                delta=data.site_xpos[sites[0]]-data.site_xpos[sites[finger]]
                return np.r_[delta,.001*(x-prior)]
            fit=least_squares(residual,np.clip(prior,self.lo[active]+.01,self.hi[active]-.01),
                              bounds=(self.lo[active]+.01,self.hi[active]-.01),max_nfev=120)
            q=self.open.copy();q[active]=fit.x;self.pairs.append(q)

    def pose(self, action, elapsed,clock_at=None):
        np=self.np
        if action=='sequence':action='all'
        if action=='official_sweep':
            t=elapsed%6.
            if t<2.:
                u=t/2.;s=u*u*u*(10+u*(-15+6*u));return self.open*(1-s)
            if t<=4.:
                q=np.zeros(20);q[0]=.01*(1-math.cos(math.pi*(t-2.)));return q
            u=(t-4.)/2.;s=u*u*u*(10+u*(-15+6*u));return self.open*s
        if action in CUSTOM_IDS:
            steps=route(action,at=datetime.fromisoformat(clock_at) if clock_at else None)
            t=elapsed%PERIOD[action];i=min(len(steps)-1,int(t/3.));u=(t%3.)/1.0
            a=self.open if i==0 else np.array(steps[i-1][1]);b=np.array(steps[i][1])
            x=min(1.,u);s=x*x*x*(10+x*(-15+6*x))
            return np.clip(a+(b-a)*s,self.lo+.005,self.hi-.005)
        if action=='touch_flow':
            t=elapsed%8.
            blend=0. if t<2 else min(1.,(t-2)/1.5) if t<3.5 else 1. if t<4 else max(0.,1-(t-4)/1.5)
            return self.open*(1-blend)+self.pairs[0]*blend
        if action=='official_opposition':
            from official_replay import preview_pose
            return np.array(preview_pose(elapsed,self.side))
        if action=='all':
            elapsed%=53.
            for key in ('open','splay','opposition','fist','wave'):
                if elapsed<PERIOD[key]:return self.pose(key,elapsed)
                elapsed-=PERIOD[key]
            return self.open.copy()
        phase=(elapsed%PERIOD[action])/PERIOD[action]
        pulse=.5-.5*math.cos(2*math.pi*phase)
        q=self.open.copy()
        if action=='fist':q=(1-pulse)*q+pulse*self.fist
        elif action=='open':
            q[1::4]+=np.array([.1,.22,.08,-.1,-.22])*pulse
            q[0::4]*=1-.8*pulse;q[2::4]*=1-.8*pulse;q[3::4]*=1-.8*pulse
        elif action=='splay':q[1::4]+=np.array([.15,.25,.12,-.12,-.25])*math.sin(2*math.pi*phase)
        elif action=='opposition':
            p=phase*4;index=min(3,int(p));blend=math.sin(math.pi*(p-index))**2
            q=(1-blend)*q+blend*self.pairs[index]
        elif action=='joints':
            p=phase*20;index=min(19,int(p));q[index]+=.3*math.sin(math.pi*(p-index))**2
        else:
            if action=='wave':
                p=phase*5;finger=min(4,int(p));blend=math.sin(math.pi*(p-finger))**2
            else:
                finger=['thumb','index','middle','ring','little'].index(action);blend=pulse
            sl=slice(finger*4,finger*4+4)
            q[sl]=(1-blend)*self.open[sl]+blend*self.fist[sl]
        return np.clip(q,self.lo+.005,self.hi-.005)
