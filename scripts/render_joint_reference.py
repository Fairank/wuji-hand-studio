"""Generate static anatomy reference images from bundled native models; no stepping."""
import hashlib
import json
import sys
from pathlib import Path
import numpy as np
import mujoco as mj
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from device_profiles import load_native_model, PROFILES
from joint_reference import image_path

def main():
    manifest = {}
    for pid, p in PROFILES.items():
        m = load_native_model(pid)
        if pid.startswith('hand2'):
            m.body_quat[1] = [0, 1, 0, 0]
        m.vis.global_.offwidth, m.vis.global_.offheight = 640, 480
        m.vis.headlight.ambient[:] = .65
        m.vis.headlight.diffuse[:] = .65
        d = mj.MjData(m)
        ids = m.actuator_trnid[:, 0].astype(int)
        for jid in ids:
            d.qpos[m.jnt_qposadr[jid]] = np.clip(0, *m.jnt_range[jid])
        mj.mj_forward(m, d)
        camera = mj.MjvCamera()
        camera.lookat[:] = [0, 0, .12]
        camera.distance = .34
        camera.azimuth = 75 if pid.startswith('hand2') else -15
        camera.elevation = -8
        opt = mj.MjvOption()
        opt.geomgroup[2] = 0
        with mj.Renderer(m, height=480, width=640) as renderer:
            for index, jid in enumerate(ids):
                renderer.update_scene(d, camera, opt)
                scene = renderer.scene
                # Highlight only this model joint's body and its world-space axis.
                for geom in scene.geoms[:scene.ngeom]:
                    if geom.objtype == mj.mjtObj.mjOBJ_GEOM and geom.objid >= 0:
                        body = m.geom_bodyid[geom.objid]
                        geom.rgba[:] = [.12, .46, .92, 1] if body == m.jnt_bodyid[jid] else [.62, .66, .73, 1]
                point, axis = d.xanchor[jid], d.xaxis[jid]
                geom = scene.geoms[scene.ngeom]
                mj.mjv_initGeom(geom, mj.mjtGeom.mjGEOM_SPHERE, [.006]*3, point, np.eye(3).ravel(), [1, .68, .12, 1])
                geom.label = str(index)
                scene.ngeom += 1
                geom = scene.geoms[scene.ngeom]
                mj.mjv_initGeom(geom, mj.mjtGeom.mjGEOM_ARROW, [0]*3, [0]*3, np.eye(3).ravel(), [1, .68, .12, 1])
                mj.mjv_connector(geom, mj.mjtGeom.mjGEOM_ARROW, .003, point - axis*.018, point + axis*.024)
                scene.ngeom += 1
                path = image_path(pid, index)
                path.parent.mkdir(parents=True, exist_ok=True)
                rgb = renderer.render()
                alpha = np.where(np.any(rgb != 0, axis=2), 255, 0).astype(np.uint8)
                Image.fromarray(np.dstack((rgb, alpha))).save(path, optimize=True)
        manifest[pid] = dict(model_sha256=hashlib.sha256((ROOT/'src'/p['model']).read_bytes()).hexdigest(), images=20)
    (ROOT/'src/web/joints/manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Rendered 80 model references. No simulation step or device command.')

if __name__ == '__main__':
    main()
