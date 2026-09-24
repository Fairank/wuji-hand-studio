import math,os,tempfile,unittest
import queue,sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from device_profiles import PROFILES,load_native_model,require_identity,FirstGenerationPreview
from demo_player import PoseLibrary
from first_generation import serialize_first,target_at
from official_replay import load_replay
from bridge_config import validate,DEFAULT

class ProfilesTests(unittest.TestCase):
    def sdk_fixture(self,side='left',fail_enable=False):
        calls=[]
        frames=iter([SimpleNamespace(position=[.1]*20),None])
        subscription=SimpleNamespace(recv=lambda:next(frames,None),close=lambda:calls.append('sub.close'))
        def enable():
            calls.append('enable')
            if fail_enable:raise RuntimeError('synthetic enable fault')
        hand=SimpleNamespace(serial_number='TEST',handedness_name=lambda:side,
            joint_states=lambda:SimpleNamespace(subscribe=lambda:subscription),
            set_all_effort_limit=lambda value:calls.append('limit'),enable=enable,
            disable=lambda:calls.append('disable'),disconnect=lambda:calls.append('disconnect'))
        manager=SimpleNamespace(scan=lambda:[SimpleNamespace(device_type='Hand1',address='127.0.0.1',sn='TEST')],
            connect=lambda **kwargs:hand,disconnect_all=lambda:calls.append('disconnect'))
        sdk=SimpleNamespace(SdkManager=SimpleNamespace(instance=lambda:manager),DeviceType=SimpleNamespace(WujiHand='Hand1'),JointCommand=object,LowPass=object)
        return sdk,calls

    def test_hand1_side_mismatch_never_enables(self):
        from first_generation import worker_first
        sdk,calls=self.sdk_fixture(side='right')
        with patch.dict(sys.modules,{'wuji_sdk':sdk}),patch.dict(os.environ,{'WUJI_HAND_PROFILE':'hand1_left'}):
            worker_first('',queue.Queue(),queue.Queue())
        self.assertNotIn('enable',calls);self.assertIn('disconnect',calls)

    def test_hand1_partial_enable_failure_is_disabled(self):
        from first_generation import worker_first
        sdk,calls=self.sdk_fixture(fail_enable=True);requests=queue.Queue()
        requests.put(dict(name='hardware_trial',action='fist',workspace_clear=True,lease='a'*32,amplitude=.25,speed=.5,cycles=1))
        requests.put(dict(name='disconnect'))
        with patch.dict(sys.modules,{'wuji_sdk':sdk}),patch.dict(os.environ,{'WUJI_HAND_PROFILE':'hand1_left'}):
            worker_first('',requests,queue.Queue())
        self.assertEqual(calls.count('enable'),1);self.assertEqual(calls.count('disable'),1)
        self.assertIn('sub.close',calls);self.assertIn('disconnect',calls)

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

    def test_first_gen_recording_without_device_clock(self):
        from console_agent import Recorder
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'WUJI_HAND_PROFILE':'hand1_right'}):
            r=Recorder(directory);r.start('baseline',10,1.)
            r.add(serialize_first(SimpleNamespace(position=[0.]*20),1.01,1))
            summary=r.finish('test',1.1)
            self.assertEqual(summary['side'],'right')
            self.assertEqual(summary['units']['effort'],'unavailable')
            self.assertEqual(summary['device_timestamp_intervals']['n'],0)

if __name__=='__main__':unittest.main()
