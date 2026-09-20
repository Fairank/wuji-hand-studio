import math,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from device_profiles import PROFILES,load_native_model,require_identity,FirstGenerationPreview
from demo_player import PoseLibrary
from first_generation import serialize_first,target_at
from official_replay import load_replay
from bridge_config import validate,DEFAULT

class ProfilesTests(unittest.TestCase):
    def test_four_native_models_and_all_pose_ranges(self):
        import numpy as np
        for name in PROFILES:
            with self.subTest(name=name):
                m=load_native_model(name);self.assertEqual(m.nu,20)
                library=FirstGenerationPreview(m) if name.startswith('hand1') else PoseLibrary(m,PROFILES[name]['side'])
                for action in ('open','fist','opposition','wave','letter_A','digit_0'):
                    q=library.pose(action,1.2)
                    self.assertTrue(np.isfinite(q).all())
                    self.assertTrue((q>=m.actuator_ctrlrange[:,0]-.001).all())
                    self.assertTrue((q<=m.actuator_ctrlrange[:,1]+.001).all())

    def test_native_side_identity_before_motion(self):
        left=SimpleNamespace(handedness=lambda:SimpleNamespace(get=lambda:'left'))
        first_right=SimpleNamespace(handedness_name=lambda:'right')
        self.assertEqual(require_identity(left,PROFILES['hand2_left']),'left')
        self.assertEqual(require_identity(first_right,PROFILES['hand1_right']),'right')
        with self.assertRaises(RuntimeError):require_identity(left,PROFILES['hand2_right'])
        with self.assertRaises(RuntimeError):require_identity(first_right,PROFILES['hand1_left'])

    def test_right_recording_is_distinct_and_shape_valid(self):
        left,right=load_replay('left'),load_replay('right')
        self.assertEqual(len(left),30000);self.assertEqual(len(right),30000)
        self.assertNotEqual(left,right);self.assertEqual(len(right[0]),20)

    def test_hand1_missing_fields_are_not_fabricated_feedback(self):
        s=serialize_first(SimpleNamespace(position=[.1]*20),1.,4)
        self.assertIsNone(s['device_timestamp_us']);self.assertEqual(s['channel_id_source'],'array_index')
        self.assertTrue(all(j['effort_A'] is None and j['velocity_rad_s'] is None for j in s['joints']))
        with self.assertRaises(ValueError):serialize_first(SimpleNamespace(position=[math.nan]*20),1.,1)

    def test_first_gen_official_curl_and_measured_start_return(self):
        start=[.1]*20
        q,d=target_at(start,'fist',.25,1.,3,0);self.assertEqual(q,start)
        q,_=target_at(start,'fist',.25,1.,3,d);self.assertEqual(q,start)
        q,_=target_at(start,'fist',.25,1.,3,2.5)
        self.assertEqual(q[:4],[0.]*4);self.assertAlmostEqual(q[4],.4)
        self.assertEqual(q[5],0.)

    def test_connection_command_injection_is_rejected(self):
        for key,bad in [('host','localhost;shutdown'),('python','python;shutdown'),('agent_directory','/a/../x'),('username','a b')]:
            with self.subTest(key=key),self.assertRaises(ValueError):validate({**DEFAULT,key:bad})

if __name__=='__main__':unittest.main()
