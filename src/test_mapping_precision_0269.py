"""Numerical display fidelity and existing geometry refusals, no physics/SDK."""
import struct
import unittest
import numpy as np

from collision_preview import CollisionPreview,TargetFrame
from glove_view import pose
from mapping_collision import GeometryBudgetExceeded,MIN_CLEARANCE_M,PAIR_COUNT
from test_mapping_collision import synthetic_engine


class MappingPrecisionTests(unittest.TestCase):
    def test_a_certified_large_target_arrives_in_the_same_frame_without_an_angle_ramp(self):
        for substeps in (1,25):
            with self.subTest(substeps=substeps):
                engine=synthetic_engine();engine.pair_radii[:,2]=.001
                raw=np.array([0.]*20);raw[2]=1.;raw[8]=-.7;before=raw.tobytes()
                result=engine.step(raw,substeps)
                self.assertEqual(result['display_q'],raw.tolist())
                self.assertEqual(raw.tobytes(),before)
                self.assertFalse(result['display_modified']);self.assertTrue(result['path_certified'])
                self.assertEqual(result['geometry_pairs_verified'],PAIR_COUNT)

    def test_a_collision_bound_still_blocks_the_affected_finger_without_lagging_an_unrelated_one(self):
        engine=synthetic_engine();engine.pair_radii[0,2]=.01
        engine.pairs=[({'geom':0},{'geom':1},'cross_finger')]*PAIR_COUNT
        def clear(q,deadline=None):
            distances=np.full(PAIR_COUNT,.01);distances[0]=.001-.01*q[2]
            return distances
        engine._clearances=clear
        raw=np.array([0.]*20);raw[2]=1.;raw[8]=.7;before=raw.tobytes()
        result=engine.step(raw,25)
        self.assertLessEqual(result['display_q'][2],.09+1e-12)
        self.assertEqual(result['display_q'][8],.7)
        self.assertTrue(result['display_modified']);self.assertTrue(result['path_certified'])
        for q in engine.last_path:self.assertGreaterEqual(float(clear(np.array(q)).min()),MIN_CLEARANCE_M)
        self.assertEqual(raw.tobytes(),before)

    def test_partial_large_candidate_never_replaces_a_verified_pose(self):
        engine=synthetic_engine();calls=[]
        def clear(q,deadline=None):
            calls.append(1)
            if len(calls)>1:
                engine.separators[:]=99.
                raise GeometryBudgetExceeded()
            return np.full(PAIR_COUNT,.01)
        engine._clearances=clear
        result=engine.step([1.]*20,25)
        self.assertEqual(result['display_q'],[0.]*20)
        self.assertTrue(result['frame_verified']);self.assertTrue(result['budget_limited'])
        self.assertFalse(result['progressed']);self.assertTrue(np.all(engine.separators==0.))

    def test_invalid_limits_or_expired_source_never_publish_a_new_pose(self):
        engine=synthetic_engine();engine.profile='hand2_right'
        with self.assertRaises(ValueError):engine.step([1.1]+[0.]*19,25)
        self.assertTrue(np.all(engine.q==0.))
        preview=CollisionPreview('hand2_right','synthetic',engine)
        frame=TargetFrame('hand2_right','synthetic',1,1.,struct.pack('<20d',*([1.]*20)))
        with self.assertRaisesRegex(ValueError,'stale'):preview.advance(frame,now=2.,substeps=25)
        self.assertTrue(np.all(engine.q==0.))

    def test_a_budget_hold_or_stale_stream_is_not_presented_as_fresh_sdk_motion(self):
        engine=synthetic_engine();engine.profile='hand2_right';ticks=iter((0.,1.))
        engine.clock=lambda:next(ticks,1.)
        raw=[1.]*20;preview=CollisionPreview('hand2_right','synthetic',engine)
        result=preview.advance(TargetFrame('hand2_right','synthetic',1,1.,struct.pack('<20d',*raw)),now=1.,substeps=25)
        result.update(bridge_session_id=3,glove_serial='WG-SYNTHETIC')
        glove=dict(profile='hand2_right',session_id=3,glove_serial='WG-SYNTHETIC',busy=True,
                   connection='receiving',stream=dict(q=raw,seq=1,fresh=True))
        q,mode,_=pose(glove,'hand2','target',collision=result)
        self.assertIsNone(q);self.assertEqual(mode,'glove_constrained_unavailable')
        glove['stream']['fresh']=False
        q,mode,_=pose(glove,'hand2','target',collision=result)
        self.assertIsNone(q);self.assertEqual(mode,'glove_constrained_unavailable')


if __name__=='__main__':unittest.main()
