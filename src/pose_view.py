"""Native left MuJoCo geometry driven by validated display-only mappings.

Measured and raw-target paths update kinematics only. Explicit constrained
display uses a separate native model/data; it never writes a device target.
"""
import io
import hashlib
import json
from collections import deque
import math
import struct
from pathlib import Path
import threading
import time
import xml.etree.ElementTree as ET
from view_camera import DEFAULT as DEFAULT_CAMERA, preview_selected

HERE = Path(__file__).resolve().parent
MODEL = HERE/'assets/mjcf/left.xml'
FINGERS = ['拇指', '食指', '中指', '无名指', '小指']


def load_left_model():
    from device_profiles import load_native_model
    return load_native_model('hand2_left')


def apply_measured_pose(model, data, state):
    """Map one feedback snapshot to qpos; never infer missing angles."""
    latest = state.get('latest')
    readings = {x['nid']:x for x in latest['joints']} if latest else {}
    rates = {x['nid']:x for x in state.get('joint_rates', [])}
    mapping = {e['index']:e for e in state.get('mapping', [])}
    device_matches = bool(state.get('device_id')) and state.get('mapping_device_id') == state.get('device_id')
    active, markers = 0, []
    data.qpos[:] = 0
    for index, joint_id in enumerate(model.actuator_trnid[:, 0]):
        jid = int(joint_id)
        entry = mapping.get(index)
        row = readings.get(entry['nid']) if entry and device_matches else None
        valid = bool(row) and not state['stale'] and rates.get(entry['nid'], {}).get('status') == 'fresh'
        status = 'unmapped'
        if valid:
            q = entry['sign'] * row['position_rad'] + entry['offset']
            if math.isfinite(q) and model.jnt_range[jid, 0]-.1 <= q <= model.jnt_range[jid, 1]+.1:
                data.qpos[model.jnt_qposadr[jid]] = q
                active += 1
                status = 'live' if entry.get('verified') else 'provisional'
            else:
                status = 'out_of_range'
        elif entry:
            status = 'stale'
        markers.append(dict(index=index, name=model.joint(jid).name,
            label=FINGERS[index//4]+f' J{index%4}', nid=entry['nid'] if entry else None,
            status=status, hz=rates.get(entry['nid'], {}).get('host_hz') if entry and valid else None))
    return active, markers


def validate_mapping(entries):
    if not isinstance(entries, list) or len(entries)>20:
        raise ValueError('最多配置20个关节')
    indices, nids = set(), set()
    output = []
    for entry in entries:
        index, nid = entry.get('index'), entry.get('nid')
        sign, offset = entry.get('sign'), entry.get('offset', 0.)
        if type(index) is not int or not 0<=index<20 or index in indices:
            raise ValueError('模型关节不能重复')
        if type(nid) is not int or not 0<=nid<=65535 or nid in nids:
            raise ValueError('设备关节编号不能重复')
        if type(sign) not in {int, float} or sign not in {-1, 1}:
            raise ValueError('方向只能选正向或反向')
        if type(offset) not in {int, float} or not math.isfinite(offset) or abs(offset)>7:
            raise ValueError('零位偏移无效')
        if entry.get('verified') is not True:
            raise ValueError('只保存已人工核对的显示映射')
        indices.add(index); nids.add(nid)
        output.append(dict(index=index, nid=nid, sign=sign, offset=offset, verified=True))
    return output


class GloveCollisionPreview:
    """Lazy display-only owner, independently testable without a physics import."""
    def __init__(self,factory):
        self.factory=factory
        self.reset()

    def _close_engine(self):
        if self.preview is not None:self.preview.close()
        self.preview=None;self.cached=None;self.last_seq=None;self.last_at=None

    def reset(self):
        if getattr(self,'preview',None) is not None:self.preview.close()
        self.preview=None;self.cached=None;self.last_seq=None;self.last_at=None
        self.key=None;self.error=None

    close=reset

    def update(self,state,*,now=None):
        from collision_preview import TargetFrame,SOURCE,DISPLAY_MAX_PENETRATION_M,DISPLAY_MAX_LIMIT_EXCESS_RAD,MAX_SUBSTEPS
        now=time.monotonic() if now is None else now
        if state.get('view_source')!='glove' or state.get('teleoperation_view_mode')!='constrained':
            self.reset();return None
        g=state.get('glove');g=g if isinstance(g,dict) else {}
        stream=g.get('stream');stream=stream if isinstance(stream,dict) else {}
        device=state.get('device_profile');device=device if isinstance(device,dict) else {}
        profile=device.get('id')
        base=dict(source=SOURCE,preview_only=True,hardware_validated=False,target_modified=False,
            profile=profile,bridge_session_id=g.get('session_id'),glove_serial=g.get('glove_serial'),
            source_seq=stream.get('seq'),status='blocked',
            message='碰撞约束预演 · 等待新鲜手套数据 / Collision-constrained preview requires fresh glove data')
        q=stream.get('q');seq=stream.get('seq');session=g.get('session_id');user=g.get('user') or {}
        age=stream.get('age_ms');timeout=stream.get('timeout_ms',250)
        finite=lambda values:isinstance(values,(list,tuple)) and len(values)==20 and all(type(x) in (int,float) and math.isfinite(x) for x in values)
        if not (type(now) in (int,float) and math.isfinite(now) and now>=0
                and g.get('connection')=='receiving' and g.get('busy') is True and stream.get('fresh') is True
                and finite(q) and type(seq) is int and seq>=0
                and type(age) in (int,float) and math.isfinite(age) and age>=0
                and type(timeout) in (int,float) and math.isfinite(timeout) and timeout>0 and age<=min(500,timeout)):
            self._close_engine();return base
        if not (g.get('profile')==profile and profile in ('hand1_left','hand1_right','hand2_left','hand2_right')
                and isinstance(g.get('glove_serial'),str) and g['glove_serial']
                and ((type(session) is int and session>=0) or (isinstance(session,str) and session))
                and isinstance(user,dict) and isinstance(user.get('user_id'),str)):
            self._close_engine();base['message']='预演来源身份不完整，未创建模型 / Preview source identity is incomplete';return base
        try:
            solver=stream.get('solver') or {};provenance=g.get('model_provenance') or {}
            scope=(profile,session,g['glove_serial'],user['user_id'],stream.get('retarget'),
                   solver.get('engine'),solver.get('values_sha256'),provenance.get('model_sha256'))
            key=json.dumps(scope,sort_keys=True,allow_nan=False,separators=(',',':'))
            raw=struct.pack('<20d',*q);sha=hashlib.sha256(raw).hexdigest()
        except Exception:
            self._close_engine();base['message']='预演来源设置无效 / Invalid preview source settings';return base
        if self.key!=key:
            self.reset();self.key=key
        if self.error:
            return dict(base,status='error',message=self.error)
        if self.last_seq is not None and seq<=self.last_seq:
            if seq==self.last_seq and self.cached is not None and self.cached.get('source_target_sha256')==sha:
                return dict(self.cached)
            self._close_engine();self.error='预演数据序号或目标冲突，请切换画面后重试 / Preview sequence or target changed unexpectedly; switch display source to retry'
            return dict(base,status='error',message=self.error)
        if self.last_at is not None and now-self.last_at>.5:self._close_engine()
        try:
            if self.preview is None:self.preview=self.factory(profile,hashlib.sha256(key.encode('utf-8')).hexdigest())
            elapsed=.02 if self.last_at is None else min(.05,max(.002,now-self.last_at))
            substeps=min(MAX_SUBSTEPS,max(1,round(elapsed/.002)))
            result=self.preview.advance(TargetFrame(profile,self.preview.session_id,seq,now-age/1000,raw),now=now,substeps=substeps)
            result=dict(result,bridge_session_id=session,glove_serial=g['glove_serial'])
            self.last_seq=seq;self.last_at=now
            required=('max_all_contact_penetration_m','limit_excess_rad')
            known=all(type(result.get(k)) in (int,float) and math.isfinite(result[k]) and result[k]>=0 for k in required)
            if result.get('stable') is not True or not known:
                raise ValueError('原生求解状态未确认 / Native preview state is unverified')
            if result.get('source_target_sha256')!=sha:raise ValueError('预演目标校验不一致 / Preview target checksum mismatch')
            if result['limit_excess_rad']>DISPLAY_MAX_LIMIT_EXCESS_RAD:
                result.update(status='blocked',message='预演超出模型行程，未更新画面 / Preview exceeded model limits; frame withheld')
            elif result['max_all_contact_penetration_m']>DISPLAY_MAX_PENETRATION_M:
                result.update(status='blocked',message='预演接触残余超过显示上限，未更新画面 / Contact residual exceeds display limit; frame withheld')
            elif result.get('coverage')=='partial_original_hand1_masks':
                result.update(status='partial',message='碰撞约束预演 · 非发送目标 · 一代覆盖有限 / Collision-constrained preview · Not sent to device · Hand 1 coverage limited')
            elif result.get('coverage')=='original_hand2_masks_unvalidated':
                result.update(status='ready',message='碰撞约束预演 · 非发送目标 / Collision-constrained preview · Not sent to device')
            else:raise ValueError('碰撞覆盖范围未确认 / Collision coverage is unverified')
            if result['status']=='blocked':result.pop('display_q',None)
            self.cached=result
            return dict(result)
        except Exception as error:
            self._close_engine();self.error='预演不可用：'+str(error)+' / Preview unavailable; switch display source to retry'
            return dict(base,status='error',message=self.error)


class PoseView:
    def __init__(self, controller):
        self.controller = controller
        self.lock = threading.Lock()
        self.jpeg = None
        self.demand=threading.Event();self.requested_until=0.
        self.meta = dict(ready=False, mode='loading', message='正在载入原生左手模型', joints=[], render_hz=0)
        threading.Thread(target=self.run, daemon=True).start()

    def get(self):
        with self.lock:
            self.requested_until=time.monotonic()+.5
            self.demand.set()
            meta = dict(self.meta)
            if meta.get('ready') and time.monotonic()-meta.get('rendered_monotonic', 0) > 1:
                meta.update(ready=False, mode='stale', message='三维渲染已中断，画面不再代表当前姿态')
            meta.pop('rendered_monotonic', None)
            return self.jpeg, meta

    def wait_for_demand(self):
        # Rendering alone follows viewer demand. SDK reception and control
        # heartbeats run in their own workers and are never suspended here.
        while True:
            with self.lock:
                if time.monotonic()<self.requested_until:return
                self.demand.clear()
            self.demand.wait()

    def run(self):
        renderer = None
        collision = None
        try:
            self.wait_for_demand()
            import mujoco as mj
            import numpy as np
            from PIL import Image
            from device_profiles import load_native_model,FirstGenerationPreview
            from collision_preview import create_display_preview
            collision=GloveCollisionPreview(lambda p,s:create_display_preview(mj,p,s,HERE/'assets',mode='constrained',fresh_target=True))
            profile_id=self.controller.snapshot().get('device_profile',{}).get('id','hand2_left')
            m = load_native_model(profile_id)
            d = mj.MjData(m)
            # Native model fingers point toward -Z; turn the entire display
            # upright without changing joint axes or native left meshes.
            if profile_id.startswith("hand2"):m.body_quat[1] = [0, 1, 0, 0]
            m.vis.global_.offwidth = 760
            m.vis.global_.offheight = 560
            m.vis.headlight.ambient[:] = [.65]*3
            m.vis.headlight.diffuse[:] = [.65]*3
            renderer = mj.Renderer(m, height=560, width=760)
            render_times=deque(maxlen=60)
            camera = mj.MjvCamera()
            camera.lookat[:] = [0, 0, .115]
            camera.distance = .38
            camera.azimuth = 90
            camera.elevation = -5
            option = mj.MjvOption()
            option.geomgroup[2] = 0
            ids = m.actuator_trnid[:, 0].astype(int)
            if len(ids)!=20 or not all(m.joint(int(j)).name for j in ids):
                raise ValueError('Expected native 20-axis left model')
            from demo_player import PoseLibrary
            library = FirstGenerationPreview(m) if profile_id.startswith('hand1') else PoseLibrary(m,profile_id.split('_')[1])
            starts = time.monotonic()
            frames = 0
            while True:
                self.wait_for_demand()
                tick = time.monotonic()
                state = self.controller.snapshot()
                new_profile=state.get('device_profile',{}).get('id','hand2_left')
                if new_profile!=profile_id:
                    renderer.close();profile_id=new_profile;m=load_native_model(profile_id);d=mj.MjData(m)
                    
                    if profile_id.startswith("hand2"):m.body_quat[1]=[0,1,0,0]
                    m.vis.global_.offwidth=760;m.vis.global_.offheight=560
                    m.vis.headlight.ambient[:]=[.65]*3;m.vis.headlight.diffuse[:]=[.65]*3
                    renderer=mj.Renderer(m,height=560,width=760);ids=m.actuator_trnid[:,0].astype(int)
                    library=FirstGenerationPreview(m) if profile_id.startswith('hand1') else PoseLibrary(m,profile_id.split('_')[1])
                view = state.get('camera', DEFAULT_CAMERA)
                camera.azimuth = view['azimuth']-(90 if profile_id.startswith('hand1') else 0)
                camera.elevation = view['elevation']
                camera.distance = view['distance']
                camera.lookat[:] = view['lookat']
                latest = state.get('latest')
                active, markers = apply_measured_pose(m, d, state)
                playback=state.get('playback', {})
                show_preview = preview_selected(state)
                if show_preview:
                    d.qpos[:20]=library.pose(playback['action'],playback.get('pose_elapsed_s',playback['elapsed_s']),playback.get('clock_at'),playback.get('text') or 'WUJI TECH')
                    amplitude=playback.get('amplitude',1.)
                    if amplitude!=1.:d.qpos[:20]=library.open+amplitude*(d.qpos[:20]-library.open)
                    active=0
                    for marker in markers:marker.update(status='demo',hz=None)
                elif state.get('view_source')=='preview':
                    d.qpos[:]=0;active=0;latest=None
                    for marker in markers:marker.update(status='unmapped',hz=None)
                glove_display=None
                collision_result=collision.update(state,now=tick)
                if state.get('view_source')=='glove':
                    from glove_view import pose as glove_pose,channel_ids
                    glove_generation=state.get('device_profile',{}).get('generation')
                    glove_channels=channel_ids(glove_generation)
                    gq,gmode,gfeedback=glove_pose(state.get('glove',{}),glove_generation,state.get('teleoperation_view_mode','automatic'),collision=collision_result)
                    if gmode in ('glove_live','glove_preview') and gq is None:
                        with self.lock:
                            # A pending/lost measured frame must never become
                            # a fresh zero pose or a generated target image.
                            self.meta=dict(ready=False,mode=gmode,
                                message=('实机反馈未更新 / Hand feedback is unavailable or stale' if gmode=='glove_live'
                                    else '手套预览数据无效或已过期 / Glove preview data is invalid or stale'),
                                active_joints=0,joints=[],render_hz=0,source_seq=None,
                                glove_seq=(state.get('glove',{}).get('stream') or {}).get('seq'),
                                feedback=gfeedback,no_motion_commands=True,no_physics_step=True,
                                device_profile=profile_id,view_source='glove')
                        time.sleep(max(0,.05-(time.monotonic()-tick)))
                        continue
                    if gmode=='glove_constrained_unavailable':
                        info=gfeedback.get('collision_preview') or {}
                        with self.lock:
                            # Keep the previous JPEG stale, never render a new
                            # attractive zero/raw pose for an invalid result.
                            self.meta=dict(ready=False,mode=gmode,message=info.get('message','预演不可用 / Preview unavailable'),
                                collision_preview=info,active_joints=0,joints=[],render_hz=0,
                                source_seq=None,glove_seq=(state.get('glove',{}).get('stream') or {}).get('seq'),
                                no_physics_step=not bool(info.get('substeps')),no_motion_commands=True,simulation_only=True,
                                device_profile=profile_id,view_source='glove')
                        time.sleep(max(0,.05-(time.monotonic()-tick)))
                        continue
                    d.qpos[:]=0;active=0;latest=None
                    if gq is not None:
                        for i,jid in enumerate(ids):d.qpos[m.jnt_qposadr[jid]]=gq[i]
                        if gmode=='glove_live':active=20;latest=gfeedback.get('latest')
                    rates={j['nid']:j for j in gfeedback.get('joint_rates') or []}
                    for i,marker in enumerate(markers):
                        marker.update(status='live' if active else 'demo' if gq is not None else 'unmapped',
                                      hz=rates.get(glove_channels[i],{}).get('host_hz') if active and glove_channels else None)
                    glove_display=(gq,gmode,gfeedback)
                mj.mj_forward(m, d)
                renderer.update_scene(d, camera=camera, scene_option=option)
                for index, jid in enumerate(ids):
                    if renderer.scene.ngeom >= renderer.scene.maxgeom:
                        break
                    geom = renderer.scene.geoms[renderer.scene.ngeom]
                    status = markers[index]['status']
                    color = [0.04, .72, .66, 1] if status=='live' else ([.95, .55, .15, 1] if status in {'out_of_range','provisional'} else [.6, .65, .7, 1])
                    mj.mjv_initGeom(geom, mj.mjtGeom.mjGEOM_SPHERE, np.array([.003]*3), d.xanchor[jid], np.eye(3).ravel(), np.array(color))
                    hz = markers[index]['hz']
                    if state.get('display_selected') == index:
                        geom.label = f'{index+1}: {hz:.0f} Hz' if hz is not None else f'{index+1}: --'
                    renderer.scene.ngeom += 1
                rgb = renderer.render()
                output = io.BytesIO()
                Image.fromarray(rgb).save(output, format='JPEG', quality=85)
                frames += 1
                rendered_at=time.monotonic();render_times.append(rendered_at)
                while render_times and render_times[0]<rendered_at-2.:render_times.popleft()
                meta = dict(ready=True, mode='live' if active else 'preview',
                    message=(f'{active}/20关节由实测反馈驱动' if state.get('mapping_verified') else f'{active}/20实机角度预览 · 关节对应待核对') if active else '模型预览 · 等待连接与关节对应核对',
                    active_joints=active, joints=markers,
                    source_seq=latest['seq'] if latest else None,
                    feedback=dict(latest=latest, joint_rates=state.get('joint_rates', []),
                                  stale=state['stale'], metrics=state['metrics']),
                    render_hz=(len(render_times)-1)/max(.001,render_times[-1]-render_times[0]) if len(render_times)>1 else None,
                    rendered_monotonic=time.monotonic(),
                    camera=view,camera_revision=state.get('camera_revision',0),
                    view_source=state.get('view_source','feedback'),
                    no_physics_step=True, no_motion_commands=True,device_profile=profile_id)
                if show_preview:
                    meta.update(mode='demo',message='仿真动作预览 · '+playback['label']+' · '+('播放中' if playback['running'] else '已暂停或完成'),
                                source_seq=None,playback=playback,feedback=dict(latest=None,joint_rates=[],stale=True,metrics={}))
                elif not active:
                    meta['message'] = ('实机同步 · 等待真实关节反馈' if state.get('view_source')=='feedback'
                                       else '动作预览 · 请先选择并播放动作')
                    if state.get('view_source')=='preview':
                        meta['feedback']=dict(latest=None,joint_rates=[],stale=True,metrics={})
                if glove_display is not None:
                    gq,gmode,gfeedback=glove_display
                    meta.update(mode=gmode,source_seq=latest['seq'] if latest else None,
                        glove_seq=state.get('glove',{}).get('stream',{}).get('seq'),
                        message=('手套遥操作 · 实际灵巧手反馈' if active else '手套映射目标 · 非实测反馈' if gq is not None else '手套遥操作 · 等待新鲜数据'),
                        feedback=gfeedback if active else dict(latest=None,joint_rates=[],stale=True,metrics={}))
                    if gmode=='glove_constrained':
                        info=gfeedback['collision_preview']
                        meta.update(message=info['message'],collision_preview=info,simulation_only=True,
                                    no_physics_step=False,source_seq=None,active_joints=0)
                with self.lock:
                    self.jpeg, self.meta = output.getvalue(), meta
                time.sleep(max(0, .05-(time.monotonic()-tick)))
        except Exception as error:
            with self.lock:
                self.meta = dict(ready=False, mode='error', message='三维模型加载失败：'+str(error), joints=[], render_hz=0)
        finally:
            if collision:collision.close()
            if renderer:
                renderer.close()
