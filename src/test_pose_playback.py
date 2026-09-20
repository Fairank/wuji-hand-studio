"""Offline checks: synthetic feedback never enters the running hardware service."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import mujoco as mj
import numpy as np

from console_server import Controller, ConsoleHTTPServer, Handler
from demo_player import DemoPlayer, PoseLibrary, CATALOG, PERIOD
from pose_view import load_left_model, apply_measured_pose


class PosePlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = load_left_model()
        cls.library = PoseLibrary(cls.model)

    def test_observed_node_groups_drive_native_index_without_claiming_calibration(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = Controller(reports=Path(tmp)/'reports')
            nids = [f*5+j+1 for f in range(5) for j in range(4)]
            c.state.update(connection='connected', device_id='synthetic-left',
                latest=dict(seq=1, joints=[dict(nid=n,position_rad=0.) for n in reversed(nids)]),
                joint_rates=[dict(nid=n,status='fresh',host_hz=1000.) for n in nids],
                metrics=dict(age_ms=0.))
            import time
            c.last_update = time.monotonic()
            state = c.snapshot()
        self.assertFalse(state['mapping_verified'])
        self.assertFalse(state['motion_enabled'])
        d = mj.MjData(self.model)
        apply_measured_pose(self.model, d, state); mj.mj_forward(self.model, d)
        start = d.site_xpos[self.model.site('l_index_finger_tip').id].copy()
        next(j for j in state['latest']['joints'] if j['nid']==6)['position_rad'] = .4
        active, markers = apply_measured_pose(self.model, d, state); mj.mj_forward(self.model, d)
        self.assertEqual(active,20)
        self.assertEqual(markers[4]['status'],'provisional')
        self.assertAlmostEqual(d.qpos[4],.4)
        self.assertGreater(np.linalg.norm(d.site_xpos[self.model.site('l_index_finger_tip').id]-start),.005)
        self.assertEqual(np.count_nonzero(d.qpos),1)

    def test_stale_wrong_device_and_invalid_angles_do_not_drive_pose(self):
        base=dict(latest=dict(joints=[dict(nid=6,position_rad=.4)]),
            joint_rates=[dict(nid=6,status='fresh',host_hz=1000.)],
            mapping=[dict(index=4,nid=6,sign=1,offset=0.,verified=False)],
            device_id='test',mapping_device_id='test',stale=False)
        for update in [dict(stale=True),dict(mapping_device_id='other'),
                       dict(latest=dict(joints=[dict(nid=6,position_rad=float('nan'))])),
                       dict(latest=dict(joints=[dict(nid=6,position_rad=20.)]))]:
            state=copy.deepcopy(base);state.update(update)
            data=mj.MjData(self.model)
            active,_=apply_measured_pose(self.model,data,state)
            self.assertEqual(active,0)
            self.assertTrue(np.all(data.qpos==0))

    def test_every_scripted_action_moves_within_native_limits(self):
        for name in CATALOG:
            samples=np.array([self.library.pose(name,t) for t in np.linspace(0,PERIOD[name],101)])
            self.assertTrue(np.isfinite(samples).all(),name)
            self.assertTrue((samples>=self.library.lo).all(),name)
            self.assertTrue((samples<=self.library.hi).all(),name)
            self.assertGreater(np.max(np.ptp(samples,axis=0)),.01,name)

    def test_pause_resume_finite_and_infinite_cycles(self):
        with patch('demo_player.time.monotonic',return_value=0.) as now:
            p=DemoPlayer();p.command(dict(name='demo_start',action='fist',speed=.5,cycles=1))
            now.return_value=2.;self.assertAlmostEqual(p.snapshot()['elapsed_s'],1.)
            p.command(dict(name='demo_pause'));now.return_value=7.
            self.assertAlmostEqual(p.snapshot()['elapsed_s'],1.)
            p.command(dict(name='demo_resume'));now.return_value=27.
            self.assertFalse(p.snapshot()['running'])
            self.assertEqual(p.snapshot()['elapsed_s'],6.)
            p.command(dict(name='demo_start',action='fist',speed=1.,cycles=0))
            now.return_value=627.;self.assertTrue(p.snapshot()['running'])
            self.assertFalse(p.snapshot()['hardware_motion'])
            p.command(dict(name='demo_stop'));self.assertFalse(p.snapshot()['active'])

    def test_second_server_cannot_share_port(self):
        first=ConsoleHTTPServer(('127.0.0.1',0),Handler)
        try:
            with self.assertRaises(OSError):
                ConsoleHTTPServer(first.server_address,Handler)
        finally:first.server_close()


if __name__=='__main__':unittest.main()
