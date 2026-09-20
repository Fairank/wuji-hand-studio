"""Main-assistant integration checks; fake SDK only, no physical device."""
import math
import unittest
from types import SimpleNamespace
from hardware_trial import make_trial,LABELS
from official_replay import load_left_replay,path_indices,preview_pose,LEFT_SHA256
from demo_player import DemoPlayer
import test_hardware_showcase as fixtures
from test_hardware_showcase import feedback,diagnostics


class OfficialPathTests(unittest.TestCase):
    def test_retained_bends_reproduce_every_source_frame_within_error_bound(self):
        poses=load_left_replay();indices=path_indices()
        self.assertEqual(indices[0],0);self.assertEqual(indices[-1],len(poses)-1)
        for a,b in zip(indices,indices[1:]):
            for i in range(a,b+1):
                r=(i-a)/(b-a)
                error=max(abs(x-(u+(v-u)*r)) for x,u,v in zip(poses[i],poses[a],poses[b]))
                self.assertLessEqual(error,.0010000001)

    def test_exact_left_source_endpoints_and_provenance(self):
        data=load_left_replay();self.assertEqual(len(data),30000)
        start=[.02]*20;plan=make_trial(start,'official_opposition',.25,1)
        expected=[a+.25*(b-a) for a,b in zip(start,data[0])]
        self.assertEqual(plan['points'][1]['q'],expected)
        self.assertEqual(plan['points'][0]['q'],start);self.assertEqual(plan['points'][-1]['q'],start)
        self.assertEqual(plan['source_sha256'],LEFT_SHA256)
        self.assertEqual(plan['source'],'official_wuji_hand2_left_recording_retimed')
        self.assertFalse(plan['physical_contact_verified'])
        self.assertEqual(plan['current_limit_A'],.5)

    def test_full_amplitude_is_velocity_bounded_without_moving_targets(self):
        plan=make_trial([0.]*20,'official_opposition',1.,1)
        for a,b in zip(plan['points'],plan['points'][1:]):
            dt=b['t']-a['t'];self.assertGreater(dt,0)
            self.assertLessEqual(max(abs(x-y)/dt for x,y in zip(a['q'],b['q'])),.07500001)
        self.assertLess(plan['points'][-1]['t'],600)

    def test_preview_seam_and_source_are_explicit(self):
        self.assertLess(max(abs(a-b) for a,b in zip(preview_pose(30.999-1e-7),preview_pose(0))),1e-6)
        p=DemoPlayer();p.command(dict(name='demo_start',action='official_opposition',speed=.5,cycles=1))
        s=p.snapshot();self.assertEqual(s['source'],'official_left_recording_model_preview')
        self.assertFalse(s['hardware_motion'])


class OfficialExecutionTests(unittest.TestCase):
    setUp=fixtures.HardwareTests.setUp
    tearDown=fixtures.HardwareTests.tearDown
    driver=fixtures.HardwareTests.driver
    ready_diag=fixtures.HardwareTests.ready_diag

    def test_complete_official_path_requires_tracking_and_return(self):
        d=self.driver(False)
        self.hand.joints=lambda:[SimpleNamespace(index=i,label=x) for i,x in enumerate(LABELS)]
        d.command(dict(name='hardware_trial',action='official_opposition',amplitude=.25,cycles=1,
            workspace_clear=True,lease='a'*32),feedback(1.),self.ready_diag(1.),1.)
        self.assertEqual(len(d.holds),3)
        q=[0.]*20
        for k in range(1,math.ceil((d.period+4)/.025)):
            now=1+k*.025;row=feedback(now)
            for j,x in zip(row['joints'],q):j['position_rad']=x
            d.last_beat=now;d.tick(row,diagnostics(now),now)
            if not d.active:break
            q=d.target[:]
        result=d.trial_result
        self.assertTrue(result['accepted'],result)
        self.assertEqual(len(result['pose_checks']),3)
        self.assertEqual(result['finished_cycles'],1)
        self.assertGreater(result['tracking_samples'],0)
        self.assertEqual(result['source_sha256'],LEFT_SHA256)
        self.assertFalse(result['physical_contact_verified'])
        self.assertFalse(result['full_action_calibrated'])


if __name__=='__main__':unittest.main()
