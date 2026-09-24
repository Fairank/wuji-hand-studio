"""Adapter around the unmodified pinned official optimizer. No device access."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parent
VENDOR=ROOT/'official_data/solver'

def verify_vendor():
    manifest=json.loads((VENDOR/'manifest.json').read_text(encoding='utf-8'))
    for row in manifest['files']:
        path=(VENDOR/row['file']).resolve()
        if not path.is_relative_to(VENDOR.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:
            raise ValueError('Official solver source/model checksum mismatch')
    return manifest

class OfficialSolver:
    def __init__(self,profile_id,values):
        import numpy as np
        # Upstream imports Rotation lazily on its first frame (~0.5 s here).
        # Load it during preparation, before any live frame/freshness deadline.
        from scipy.spatial.transform import Rotation
        from device_profiles import profile
        from retarget_tuning import source,validate,values_hash
        self.values_sha256=values_hash(values)
        p=profile(profile_id);self.profile=p;self.np=np
        self.manifest=verify_vendor()
        if str(VENDOR) not in sys.path:sys.path.insert(0,str(VENDOR))
        from wuji_retargeting import Retargeter
        binding=dict(generation=p['generation'],side=p['side'],glove_serial='',hand_serial='',sdk_user='')
        config,_=source(binding);config=copy.deepcopy(config);config['retarget'].update(validate(values))
        # Replace upstream beta1 path with the exact beta2 asset revision already
        # used by the Workbench viewer. The original export template is unchanged.
        config['optimizer']['urdf_path']=str(VENDOR/'models'/f'{profile_id}.urdf')
        self.retargeter=Retargeter.from_config(config,hand_side=p['side'])
        pin_names=list(self.retargeter.optimizer.robot.dof_joint_names)
        model=ET.parse(ROOT/p['model']).getroot()
        self.joint_names=[x.get('joint') for x in model.findall('./actuator/position')]
        if len(pin_names)!=20 or len(set(pin_names))!=20 or set(pin_names)!=set(self.joint_names):
            raise ValueError('Official optimizer/viewer joint names do not match')
        self.order=[pin_names.index(x) for x in self.joint_names]
        urdf=ET.parse(VENDOR/'models'/f'{profile_id}.urdf').getroot()
        limits={x.get('name'):(float(x.find('limit').get('lower')),float(x.find('limit').get('upper')))
                for x in urdf.findall('joint') if x.get('type') in ('revolute','prismatic')}
        resolved={};self.limit_rounding_delta=0.
        for node in model.findall('./actuator/position'):
            exact=limits[node.get('joint')];display=list(map(float,node.get('ctrlrange').split()))
            delta=max(abs(a-b) for a,b in zip(exact,display));self.limit_rounding_delta=max(self.limit_rounding_delta,delta)
            # The pinned Hand 1 MJCF truncates some upper limits to 3 decimals;
            # its paired URDF retains 4. Keep their intersection, never widen it.
            if delta>(.00051 if p['generation']=='hand1' else 1e-7):
                raise ValueError('Official URDF and viewer joint limits differ')
            resolved[node.get('joint')]=(max(exact[0],display[0]),min(exact[1],display[1]))
        self.retargeter.optimizer.opt.set_lower_bounds([resolved[n][0] for n in pin_names])
        self.retargeter.optimizer.opt.set_upper_bounds([resolved[n][1] for n in pin_names])
        self.retargeter.optimizer.robot.model.lowerPositionLimit=np.array([resolved[n][0] for n in pin_names])
        self.retargeter.optimizer.robot.model.upperPositionLimit=np.array([resolved[n][1] for n in pin_names])
        self.count=0;self.last_ms=None

    def step(self,points):
        np=self.np;kp=np.asarray(points,dtype=np.float64)
        if kp.shape!=(21,3) or not np.isfinite(kp).all():raise ValueError('Expected 21 finite XYZ landmarks in meters')
        a,b=kp[5]-kp[0],kp[9]-kp[0]
        if np.linalg.norm(a)<1e-6 or np.linalg.norm(b)<1e-6 or np.linalg.norm(np.cross(a,b))<1e-9:
            raise ValueError('Degenerate palm landmarks; no valid wrist orientation')
        start=time.perf_counter();q=np.asarray(self.retargeter.retarget(kp),dtype=float)
        self.last_ms=(time.perf_counter()-start)*1000
        if q.shape!=(20,) or not np.isfinite(q).all():raise ValueError('Official solver returned invalid joint targets')
        q=q[self.order];self.count+=1
        return q.tolist()

    def info(self):
        return dict(engine='official_open',profile=self.profile['id'],model_version='beta2' if self.profile['generation']=='hand2' else 'hand1',
                    retarget_commit=self.manifest['retarget_commit'],model_commit=self.manifest['model_commit'],
                    frames=self.count,solve_ms=self.last_ms,joint_names=self.joint_names,values_sha256=self.values_sha256,limit_rounding_delta_rad=self.limit_rounding_delta)
