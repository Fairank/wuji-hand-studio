"""Native left MuJoCo geometry driven by validated display-only mappings.

mj_forward updates measured kinematics only. There is no physics integration,
actuator command, inverse control, contact estimation or invented motion.
"""
import io
from collections import deque
import math
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


class PoseView:
    def __init__(self, controller):
        self.controller = controller
        self.lock = threading.Lock()
        self.jpeg = None
        self.meta = dict(ready=False, mode='loading', message='正在载入原生左手模型', joints=[], render_hz=0)
        threading.Thread(target=self.run, daemon=True).start()

    def get(self):
        with self.lock:
            meta = dict(self.meta)
            if meta.get('ready') and time.monotonic()-meta.get('rendered_monotonic', 0) > 1:
                meta.update(ready=False, mode='stale', message='三维渲染已中断，画面不再代表当前姿态')
            meta.pop('rendered_monotonic', None)
            return self.jpeg, meta

    def run(self):
        renderer = None
        try:
            import mujoco as mj
            import numpy as np
            from PIL import Image
            from device_profiles import load_native_model,FirstGenerationPreview
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
                    active=0
                    for marker in markers:marker.update(status='demo',hz=None)
                elif state.get('view_source')=='preview':
                    d.qpos[:]=0;active=0;latest=None
                    for marker in markers:marker.update(status='unmapped',hz=None)
                glove_display=None
                if state.get('view_source')=='glove':
                    from glove_view import pose as glove_pose
                    gq,gmode,gfeedback=glove_pose(state.get('glove',{}))
                    d.qpos[:]=0;active=0;latest=None
                    if gq is not None:
                        for i,jid in enumerate(ids):d.qpos[m.jnt_qposadr[jid]]=gq[i]
                        if gmode=='glove_live':active=20;latest=gfeedback.get('latest')
                    rates={j['nid']:j for j in gfeedback.get('joint_rates') or []}
                    for i,marker in enumerate(markers):
                        marker.update(status='live' if active else 'demo' if gq is not None else 'unmapped',
                                      hz=rates.get(i//4*5+i%4+1,{}).get('host_hz') if active else None)
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
                        message=('手套遥操作 · 实际机械手反馈' if active else '手套映射预览 · 未驱动机械手' if gq is not None else '手套遥操作 · 等待新鲜数据'),
                        feedback=gfeedback if active else dict(latest=None,joint_rates=[],stale=True,metrics={}))
                with self.lock:
                    self.jpeg, self.meta = output.getvalue(), meta
                time.sleep(max(0, .05-(time.monotonic()-tick)))
        except Exception as error:
            with self.lock:
                self.meta = dict(ready=False, mode='error', message='三维模型加载失败：'+str(error), joints=[], render_hz=0)
        finally:
            if renderer:
                renderer.close()
