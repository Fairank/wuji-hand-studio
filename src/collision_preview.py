"""Explicit display-only dynamics; never a device controller or target writer.

The validation factory is restricted to 5090. The product factory receives an
already loaded renderer runtime after an explicit fresh-target display choice.
Importing this module or using a fake engine never imports physics. Raw targets
and measured feedback must never be replaced by this separate display output.
"""
from dataclasses import dataclass
import hashlib
import importlib
import math
from pathlib import Path
import re
import socket
import struct
import time

PROFILES = frozenset(f'hand{generation}_{side}' for generation in (1, 2) for side in ('left', 'right'))
# Chinese COMPUTERNAME independently read via the already pinned SSH host/key.
PHYSICS_HOSTS = frozenset(('niangao-pc', '年糕'))
MUJOCO_VERSION = '3.3.7'
SOURCE = 'collision_constrained_preview'
MAX_FRAME_AGE_S = .5
MAX_SUBSTEPS = 25
DISPLAY_MAX_PENETRATION_M = .001
DISPLAY_MAX_LIMIT_EXCESS_RAD = .001


def _angles(raw):
    if type(raw) is not bytes or len(raw) != 160:
        raise ValueError('Expected immutable bytes containing twenty float64 angles')
    values = struct.unpack('<20d', raw)
    if not all(math.isfinite(x) for x in values):
        raise ValueError('Non-finite preview target')
    return values


def _finger(name):
    match = re.search(r'finger([1-5])_', name)
    if match:
        return ('thumb', 'index', 'middle', 'ring', 'pinky')[int(match[1]) - 1]
    match = re.match(r'^[lr]_(thumb|index_finger|middle_finger|ring_finger|pinky)(?:_|$)', name)
    return match[1].removesuffix('_finger') if match else None


@dataclass(frozen=True)
class TargetFrame:
    profile: str
    session_id: str
    seq: int
    received_at: float
    target_bytes: bytes
    source: str = 'mapped_target'
    fresh: bool = True


class CollisionPreview:
    """Bounded adapter. Engine injection permits software-only contract checks."""
    def __init__(self, profile, session_id, engine):
        if profile not in PROFILES or not isinstance(session_id, str) or not session_id:
            raise ValueError('Explicit supported profile and session are required')
        if getattr(engine, 'profile', None) != profile:
            raise ValueError('Preview engine profile mismatch')
        self.profile, self.session_id, self.engine = profile, session_id, engine
        self.last_seq = None
        self.closed = False

    def advance(self, frame, *, now=None, substeps=10):
        now = time.monotonic() if now is None else now
        if self.closed:
            raise ValueError('Preview is closed')
        if not isinstance(frame, TargetFrame) or frame.source != 'mapped_target':
            raise ValueError('Only mapped targets may enter a constrained preview; feedback is excluded')
        if frame.profile != self.profile or frame.session_id != self.session_id:
            raise ValueError('Preview source identity changed; create a new preview')
        if frame.fresh is not True or not math.isfinite(now) or not math.isfinite(frame.received_at):
            raise ValueError('Preview source is not fresh')
        if not 0 <= now - frame.received_at <= MAX_FRAME_AGE_S:
            raise ValueError('Preview frame is stale or from the future')
        if type(frame.seq) is not int or frame.seq < 0 or (self.last_seq is not None and frame.seq <= self.last_seq):
            raise ValueError('Preview requires a new source sequence')
        if type(substeps) is not int or not 1 <= substeps <= MAX_SUBSTEPS:
            raise ValueError('Preview substep budget exceeded')
        raw = frame.target_bytes
        target = _angles(raw)
        target_sha = hashlib.sha256(raw).hexdigest()
        start = time.perf_counter()
        try:
            snapshot = self.engine.step(target, substeps)
            display = tuple(float(x) for x in snapshot['display_q'])
            if len(display) != 20 or not all(math.isfinite(x) for x in display):
                raise ValueError('Preview engine returned invalid display angles')
            contacts = [dict(row) for row in snapshot.get('contacts', [])]
            for row in contacts:
                if type(row.get('distance_m')) not in (int, float) or not math.isfinite(row['distance_m']):
                    raise ValueError('Preview engine returned invalid contact distance')
            if hashlib.sha256(frame.target_bytes).hexdigest() != target_sha or frame.target_bytes != raw:
                raise ValueError('Preview input changed')
        except Exception:
            self.close()
            raise
        self.last_seq = frame.seq
        penetration = max([max(0., -row['distance_m']) for row in contacts if row.get('cross_finger') is True], default=0.)
        all_penetration = max([max(0., -row['distance_m']) for row in contacts], default=0.)
        return dict(source=SOURCE, preview_only=True, hardware_validated=False, target_modified=False,
            profile=self.profile, session_id=self.session_id, source_seq=frame.seq,
            source_target_sha256=target_sha, display_q=list(display), contacts=contacts,
            max_inter_finger_penetration_m=penetration,
            max_all_contact_penetration_m=all_penetration,
            max_target_error_rad=max(abs(a-b) for a,b in zip(display,target)),
            coverage=getattr(self.engine,'coverage','unknown'), model_sha256=getattr(self.engine,'model_sha256',None),
            limit_excess_rad=snapshot.get('limit_excess_rad'),
            stable=snapshot.get('stable'),native_warning_count=snapshot.get('native_warning_count'),
            wall_ms=(time.perf_counter()-start)*1000, substeps=substeps,
            note='Collision-constrained display only; not measured feedback or a device command')

    def close(self):
        if not self.closed:
            self.closed = True
            close = getattr(self.engine, 'close', None)
            if close:
                close()


def create_remote_mujoco_preview(profile, session_id, asset_root, *, allow_physics=False):
    """No physics import occurs until explicit authorization and actual host match.

    This is an execution-location guard, not an authentication/attestation API.
    The module is intentionally absent from product control/renderer imports.
    """
    if allow_physics is not True or socket.gethostname().casefold() not in PHYSICS_HOSTS:
        raise RuntimeError('MuJoCo preview computation is restricted to the authorized 5090 host')
    if profile not in PROFILES:
        raise ValueError('Unsupported preview profile')
    mj = importlib.import_module('mujoco')
    if mj.__version__ != MUJOCO_VERSION:
        raise RuntimeError('Existing MuJoCo 3.3.7 is required; do not install or upgrade automatically')
    engine = _MujocoEngine(mj, profile, asset_root)
    return CollisionPreview(profile, session_id, engine)


def create_display_preview(mj, profile, session_id, asset_root, *, mode=None, fresh_target=False):
    """Use the renderer's loaded runtime only after an explicit display choice.

    This factory deliberately has no SDK/publisher reference. Its model is
    loaded independently from native assets, never the portrait-rotated renderer.
    Software verification injects a fake engine; physical checks run on 5090.
    """
    if mode != 'constrained' or fresh_target is not True:
        raise ValueError('An explicit constrained display with a fresh target is required')
    if profile not in PROFILES or not isinstance(session_id,str) or not session_id:
        raise ValueError('Explicit supported profile and session are required')
    if getattr(mj,'__version__',None) != MUJOCO_VERSION:
        raise RuntimeError('Collision preview requires the bundled MuJoCo 3.3.7')
    return CollisionPreview(profile,session_id,_MujocoEngine(mj,profile,asset_root))


class _MujocoEngine:
    def __init__(self, mj, profile, asset_root):
        self.mj, self.profile = mj, profile
        generation, side = profile.split('_')
        root = Path(asset_root).resolve()
        path = (root/generation/'mjcf'/f'{side}.xml').resolve()
        if not path.is_relative_to(root):
            raise ValueError('Invalid model path')
        self.model_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        self.model = mj.MjModel.from_xml_path(str(path))
        self.data = mj.MjData(self.model)
        if self.model.nu != 20 or self.model.nq != 20:
            raise ValueError('Expected the fixed twenty-axis model')
        self.joint_ids = [int(j) for j in self.model.actuator_trnid[:,0]]
        self.qpos_ids = [int(self.model.jnt_qposadr[j]) for j in self.joint_ids]
        if len(set(self.qpos_ids)) != 20:
            raise ValueError('Preview actuator order is not a twenty-joint bijection')
        self.ranges = [tuple(map(float,self.model.jnt_range[j])) for j in self.joint_ids]
        # Initial state only. Subsequent frames write ctrl and integrate, never qpos.
        for i, (lo, hi) in zip(self.qpos_ids,self.ranges):
            self.data.qpos[i] = min(hi,max(lo,float(self.data.qpos[i])))
        self.data.ctrl[:] = [float(self.data.qpos[i]) for i in self.qpos_ids]
        mj.mj_forward(self.model,self.data)
        self.coverage = 'partial_original_hand1_masks' if generation == 'hand1' else 'original_hand2_masks_unvalidated'

    def step(self, target, substeps):
        if any(not lo <= value <= hi for value,(lo,hi) in zip(target,self.ranges)):
            raise ValueError('Preview target outside model joint limits; input was not clamped')
        self.data.ctrl[:] = target
        for _ in range(substeps):
            self.mj.mj_step(self.model,self.data)
            if not all(math.isfinite(float(x)) for x in self.data.qpos) or not all(math.isfinite(float(x)) for x in self.data.qvel):
                raise ValueError('Non-finite constrained preview state')
        self.mj.mj_forward(self.model,self.data)
        q=[float(self.data.qpos[i]) for i in self.qpos_ids]
        contacts=[]
        for index in range(self.data.ncon):
            c=self.data.contact[index]
            a=int(c.geom1); b=int(c.geom2)
            an=self.model.body(int(self.model.geom_bodyid[a])).name
            bn=self.model.body(int(self.model.geom_bodyid[b])).name
            af,bf=_finger(an),_finger(bn)
            contacts.append(dict(geom1=a,geom2=b,body1=an,body2=bn,distance_m=float(c.dist),
                                 cross_finger=af is not None and bf is not None and af != bf))
        return dict(display_q=q,contacts=contacts,
                    stable=all(int(w.number)==0 for w in self.data.warning),
                    native_warning_count=sum(int(w.number) for w in self.data.warning),
                    limit_excess_rad=max(max(lo-v,v-hi,0.) for v,(lo,hi) in zip(q,self.ranges)))
