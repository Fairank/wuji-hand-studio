"""Native geometry and explicit generation/side identity, never image mirroring."""
import json,os
from pathlib import Path

PROFILES={f'{generation}_{side}':dict(id=f'{generation}_{side}',generation=generation,side=side,
    zh=f'舞肌{1 if generation=="hand1" else 2}代 · {"左" if side=="left" else "右"}手',
    en=f'Wuji Hand {1 if generation=="hand1" else 2} · {side.title()}',
    motion_adapter='official_lowpass' if generation=='hand1' else 'hand2_joint_publisher',
    model=f'assets/{generation}/mjcf/{side}.xml',hardware_tested=False)
    for generation in ('hand2','hand1') for side in ('left','right')}

def profile(name):
    if name not in PROFILES:raise ValueError('Unknown hand generation/side')
    return dict(PROFILES[name])

def controller_profile():return profile(os.environ.get('WUJI_HAND_PROFILE','hand2_left'))

def selected_profile():
    from runtime_paths import DATA
    path=DATA/'device_profile.json'
    return profile(json.loads(path.read_text())['id'] if path.exists() else 'hand2_left')

def save_profile(name):
    from runtime_paths import DATA
    p=profile(name);DATA.mkdir(parents=True,exist_ok=True)
    (DATA/'device_profile.json').write_text(json.dumps({'id':p['id']}),encoding='utf-8')
    return p

def require_identity(hand,p=None):
    p=p or controller_profile()
    actual=str(hand.handedness_name() if p['generation']=='hand1' else hand.handedness().get()).lower()
    if actual not in (p['side'],'handedness.'+p['side']):raise RuntimeError('Connected hand side differs from selected profile; no motion sent')
    return p['side']

def load_native_model(name):
    import mujoco as mj
    import xml.etree.ElementTree as ET
    p=profile(name);path=Path(__file__).resolve().parent/p['model']
    root=ET.parse(path).getroot();meshdir=root.find('compiler').get('meshdir')
    source=(path.parent/meshdir).resolve();root.find('compiler').set('meshdir','')
    files={m.get('file'): (source/m.get('file')).read_bytes() for m in root.findall('./asset/mesh')}
    model=mj.MjModel.from_xml_string(ET.tostring(root,encoding='unicode'),assets=files)
    if model.nu!=20 or model.nq!=20:raise ValueError('Expected 20 native axes')
    return model

class FirstGenerationPreview:
    """Preview poses use first-generation joint ranges; no actuator API."""
    def __init__(self,model):
        import numpy as np
        self.np=np;self.lo,self.hi=model.actuator_ctrlrange.T.copy()
        self.open=np.clip(np.zeros(20),self.lo,self.hi)
    def pose(self,action,elapsed,clock_at=None):
        import math
        from gesture_library import route,CUSTOM_IDS
        from datetime import datetime
        np=self.np;q=self.open.copy();pulse=.5-.5*math.cos(math.pi*(elapsed%2.))
        if action in {'fist','sequence','all'}:
            for f in range(1,5):q[[f*4,f*4+2,f*4+3]]=1.6*pulse
        elif action in {'wave','thumb','index','middle','ring','little','joints'}:
            f=int(elapsed/2)%5 if action in {'wave','joints'} else ['thumb','index','middle','ring','little'].index(action)
            q[[f*4,f*4+2,f*4+3]]=.8*pulse
        elif action=='splay':q[1::4]+=.15*math.sin(elapsed)
        elif action in CUSTOM_IDS:
            steps=route(action,at=datetime.fromisoformat(clock_at) if clock_at else None)
            target=np.array(steps[int(elapsed/3)%len(steps)][1]);target[1]=abs(target[1])
            u=(elapsed%3)/3;s=u*u*u*(10+u*(-15+6*u));q=(1-s)*q+s*target
        elif action in {'opposition','official_opposition','touch_flow'}:
            # Candidate only: not an official first-generation recording or IK solution.
            f=1+int(elapsed/4)%4;q[:4]=[.9,.65,.8,.6];q[f*4:f*4+4]=[.9,0,1.1,.8]
            q=self.open+(q-self.open)*pulse
        return np.clip(q,self.lo,self.hi)
