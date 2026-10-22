"""Pure injected-engine tests; no physics imports or simulation on this host."""
import dataclasses
import hashlib
import math
import struct
import sys
import unittest
from unittest.mock import patch

from collision_preview import CollisionPreview, TargetFrame, create_remote_mujoco_preview,create_display_preview, _finger


class FakeEngine:
    profile='hand2_right'
    coverage='synthetic'
    model_sha256='f'*64
    def __init__(self):self.calls=[];self.closed=False
    def step(self,target,substeps):
        self.calls.append((target,substeps))
        return dict(display_q=[0.]*20,contacts=[dict(distance_m=-.001,cross_finger=True)],limit_excess_rad=0.)
    def close(self):self.closed=True


class CollisionPreviewTests(unittest.TestCase):
    def setUp(self):
        self.engine=FakeEngine()
        self.preview=CollisionPreview('hand2_right','fixture-session',self.engine)
        self.raw=struct.pack('<20d',*[.1]*20)
        self.frame=TargetFrame('hand2_right','fixture-session',1,10.,self.raw)

    def test_input_bytes_unchanged_and_output_explicitly_separate(self):
        before=hashlib.sha256(self.raw).hexdigest()
        out=self.preview.advance(self.frame,now=10.1)
        self.assertEqual(out['source_target_sha256'],before)
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(),before)
        self.assertEqual(self.engine.calls,[(tuple([.1]*20),10)])
        self.assertTrue(out['preview_only']);self.assertFalse(out['hardware_validated'])
        self.assertFalse(out['target_modified']);self.assertEqual(out['source'],'collision_constrained_preview')
        self.assertEqual(out['max_inter_finger_penetration_m'],.001)
        self.assertEqual(out['display_q'],[0.]*20)

    def test_context_and_freshness_reject_without_engine(self):
        cases=[dict(source='measured_feedback'),dict(profile='hand2_left'),dict(session_id='other'),
               dict(fresh=False),dict(received_at=9.),dict(received_at=11.),dict(seq=-1),dict(seq=True)]
        for fields in cases:
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                self.preview.advance(dataclasses.replace(self.frame,**fields),now=10.1)
        self.assertEqual(self.engine.calls,[])

    def test_substep_budget_is_bounded(self):
        for count in (0,26,1000000,True,1.5):
            with self.subTest(count=count),self.assertRaises(ValueError):
                self.preview.advance(self.frame,now=10.,substeps=count)
        self.assertEqual(self.engine.calls,[])

    def test_duplicate_or_old_sequence_does_not_reintegrate(self):
        self.preview.advance(self.frame,now=10.)
        for seq in (1,0):
            with self.assertRaises(ValueError):
                self.preview.advance(dataclasses.replace(self.frame,seq=seq),now=10.)
        self.assertEqual(len(self.engine.calls),1)

    def test_only_immutable_finite_twenty_double_targets(self):
        for raw in (bytearray(self.raw),self.raw[:-1],struct.pack('<20d',*([math.nan]*20)),struct.pack('<20d',*([math.inf]*20))):
            with self.assertRaises(ValueError):
                self.preview.advance(dataclasses.replace(self.frame,target_bytes=raw),now=10.)
        self.assertEqual(self.engine.calls,[])

    def test_failed_engine_closes_no_automatic_retry(self):
        self.engine.step=lambda *_: {'display_q':[math.nan]*20}
        with self.assertRaises(ValueError):self.preview.advance(self.frame,now=10.)
        self.assertTrue(self.engine.closed);self.assertTrue(self.preview.closed)
        with self.assertRaises(ValueError):self.preview.advance(dataclasses.replace(self.frame,seq=2),now=10.)

    def test_malformed_contact_is_not_reported_as_clear(self):
        self.engine.step=lambda *_: {'display_q':[0.]*20,'contacts':[dict(distance_m=math.nan)]}
        with self.assertRaises(ValueError):self.preview.advance(self.frame,now=10.)
        self.assertTrue(self.preview.closed)

    def test_close_is_idempotent(self):
        self.preview.close();self.preview.close()
        self.assertTrue(self.engine.closed)
        with self.assertRaises(ValueError):self.preview.advance(self.frame,now=10.)

    def test_invalid_profile_or_missing_identity(self):
        for profile,session in (('hand3_right','s'),('hand2_right',''),('hand1_right','s')):
            with self.assertRaises(ValueError):CollisionPreview(profile,session,self.engine)

    def test_body_parts_are_associated_with_their_finger_prefix(self):
        self.assertEqual(_finger('r_pinky_middle'),'pinky')
        self.assertEqual(_finger('l_ring_finger_middle'),'ring')
        self.assertEqual(_finger('right_finger3_link3'),'middle')
        self.assertIsNone(_finger('r_wrist'))

    def test_non_interfinger_contacts_are_not_hidden_from_global_residual(self):
        self.engine.step=lambda *_: {'display_q':[0.]*20,'contacts':[dict(distance_m=-.002,cross_finger=False)]}
        out=self.preview.advance(self.frame,now=10.)
        self.assertEqual(out['max_inter_finger_penetration_m'],0.)
        self.assertEqual(out['max_all_contact_penetration_m'],.002)

    def test_local_factory_cannot_import_physics_even_if_requested(self):
        with patch('collision_preview.socket.gethostname',return_value='local-desktop'),patch('collision_preview.importlib.import_module') as load:
            with self.assertRaises(RuntimeError):create_remote_mujoco_preview('hand2_right','s','unused',allow_physics=True)
            load.assert_not_called()

    def test_host_name_alone_does_not_authorize_execution(self):
        with patch('collision_preview.socket.gethostname',return_value='NIANGAO-PC'),patch('collision_preview.importlib.import_module') as load:
            with self.assertRaises(RuntimeError):create_remote_mujoco_preview('hand2_right','s','unused')
            load.assert_not_called()

    def test_wrong_existing_version_is_rejected_without_installation(self):
        class Existing:__version__='3.3.6'
        with patch('collision_preview.socket.gethostname',return_value='NIANGAO-PC'),patch('collision_preview.importlib.import_module',return_value=Existing()):
            with self.assertRaisesRegex(RuntimeError,'3.3.7'):
                create_remote_mujoco_preview('hand2_right','s','unused',allow_physics=True)

    def test_display_factory_needs_explicit_mode_and_fresh_target_before_runtime_access(self):
        for mode,fresh in ((None,True),('target',True),('feedback',True),('constrained',False)):
            with patch('collision_preview._MujocoEngine') as engine,self.assertRaises(ValueError):
                create_display_preview(object(),'hand2_right','s','unused',mode=mode,fresh_target=fresh)
            engine.assert_not_called()

    def test_display_factory_uses_a_separate_native_asset_engine_without_a_host_lock(self):
        class LoadedRuntime:__version__='3.3.7'
        runtime=LoadedRuntime()
        with patch('collision_preview.socket.gethostname',return_value='customer-desktop'),patch('collision_preview._MujocoEngine',return_value=self.engine) as engine:
            result=create_display_preview(runtime,'hand2_right','s','native-assets',mode='constrained',fresh_target=True)
            engine.assert_called_once_with(runtime,'hand2_right','native-assets')
            self.assertIs(result.engine,self.engine)


if __name__=='__main__':unittest.main()
