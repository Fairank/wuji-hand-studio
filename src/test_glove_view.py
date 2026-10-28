"""Pure display decoding tests; no SDK, renderer or device session."""
import copy
import unittest

from glove_view import channel_ids,pose


class GloveViewTests(unittest.TestCase):
    def measured(self,generation,stale=False):
        channels=tuple(range(20)) if generation=='hand1' else (1,2,3,4,6,7,8,9,11,12,13,14,16,17,18,19,21,22,23,24)
        joints=[dict(nid=nid,position_rad=(i+1)/100) for i,nid in enumerate(channels)]
        return dict(stream=dict(q=[9.]*20,fresh=True),
                    feedback=dict(device_id='measured-hand',stale=stale,
                                  latest=dict(joints=list(reversed(joints)))))

    def test_both_generations_decode_measured_pose_in_model_order(self):
        for generation in ('hand1','hand2'):
            with self.subTest(generation=generation):
                glove=self.measured(generation)
                q,mode,feedback=pose(glove,generation)
                self.assertEqual(q,[(i+1)/100 for i in range(20)])
                self.assertEqual(mode,'glove_live')
                self.assertIs(feedback,glove['feedback'])

    def test_hand1_markers_use_array_indices_and_hand2_markers_use_native_ids(self):
        expected={'hand1':tuple(range(20)),
                  'hand2':(1,2,3,4,6,7,8,9,11,12,13,14,16,17,18,19,21,22,23,24)}
        for generation,channels in expected.items():
            with self.subTest(generation=generation):
                self.assertEqual(channel_ids(generation),channels)
                rates={nid:dict(host_hz=100+i) for i,nid in enumerate(channels)}
                self.assertEqual([rates[nid]['host_hz'] for nid in channel_ids(generation)],list(range(100,120)))

    def test_cross_generation_channels_do_not_become_a_measured_pose(self):
        for source,selected in (('hand1','hand2'),('hand2','hand1')):
            with self.subTest(source=source,selected=selected):
                q,mode,_=pose(self.measured(source),selected)
                self.assertIsNone(q)
                self.assertEqual(mode,'glove_live')

    def test_stale_measurements_do_not_fall_back_to_fresh_generated_targets(self):
        for generation in ('hand1','hand2'):
            with self.subTest(generation=generation):
                q,mode,_=pose(self.measured(generation,stale=True),generation)
                self.assertIsNone(q)
                self.assertEqual(mode,'glove_live')

    def test_incomplete_measurements_do_not_fall_back_to_generated_targets(self):
        for generation in ('hand1','hand2'):
            with self.subTest(generation=generation):
                glove=self.measured(generation)
                glove['feedback']['latest']['joints'].pop()
                self.assertIsNone(pose(glove,generation)[0])

    def test_unknown_or_missing_generation_cannot_infer_measured_channels(self):
        for generation in (None,'','hand3','hand1_left'):
            with self.subTest(generation=generation):
                self.assertIsNone(channel_ids(generation))
                q,mode,_=pose(self.measured('hand1'),generation)
                self.assertIsNone(q)
                self.assertEqual(mode,'glove_live')

    def test_default_mapping_display_cannot_bypass_missing_geometry_certificates(self):
        glove=dict(stream=dict(q=[.5]*20,fresh=True))
        for generation in ('hand1','hand2'):
            with self.subTest(generation=generation):
                q,mode,feedback=pose(glove,generation)
                self.assertIsNone(q)
                self.assertEqual(mode,'glove_constrained_unavailable')
        for generation in (None,'hand3'):
            self.assertIsNone(pose(glove,generation)[0])
        glove['stream']['fresh']=False
        self.assertIsNone(pose(glove,'hand1')[0])

    def test_legacy_target_choice_does_not_bypass_constraints_after_feedback_is_connected(self):
        for generation in ('hand1','hand2'):
            glove=self.measured(generation)
            before=copy.deepcopy(glove)
            self.assertIsNone(pose(glove,generation,'target')[0])
            self.assertEqual(pose(glove,generation,'target')[1],'glove_constrained_unavailable')
            self.assertEqual(glove,before)
            measured,mode,_=pose(glove,generation,'feedback')
            self.assertEqual(measured,[(i+1)/100 for i in range(20)])
            self.assertEqual(mode,'glove_live')

    def test_target_requires_a_fresh_complete_finite_vector(self):
        for q in ([1.]*19,[float('nan')]*20,[float('inf')]*20,[True]*20,['1']*20,None):
            self.assertIsNone(pose(dict(stream=dict(q=q,fresh=True)),'hand2','target')[0])
        for fresh,generation in ((False,'hand2'),(True,None),(True,'hand3')):
            self.assertIsNone(pose(dict(stream=dict(q=[1.]*20,fresh=fresh)),generation,'target')[0])

    def test_explicit_feedback_never_falls_back_to_generated_targets(self):
        glove=dict(stream=dict(q=[1.]*20,fresh=True),hardware={})
        self.assertEqual(pose(glove,'hand2','feedback'),(None,'glove_live',{}))
        glove=self.measured('hand2',stale=True)
        q,mode,_=pose(glove,'hand2','feedback')
        self.assertIsNone(q);self.assertEqual(mode,'glove_live')

    def test_unknown_display_selection_is_rejected(self):
        with self.assertRaises(ValueError):pose({},'hand2','invalid')

    def test_automatic_needs_geometry_before_feedback_and_preserves_actual_feedback(self):
        for generation in ('hand1','hand2'):
            glove=dict(stream=dict(q=[.5]*20,fresh=True),feedback={},hardware={})
            self.assertIsNone(pose(glove,generation,'automatic')[0])
            self.assertEqual(pose(glove,generation,'automatic')[1],'glove_constrained_unavailable')
            glove=self.measured(generation)
            actual,mode,feedback=pose(glove,generation,'automatic')
            self.assertEqual(actual,[(i+1)/100 for i in range(20)])
            self.assertEqual(mode,'glove_live');self.assertIs(feedback,glove['feedback'])

    def test_automatic_never_falls_back_after_feedback_becomes_stale_or_incomplete(self):
        for generation in ('hand1','hand2'):
            for invalid in ('stale','missing','generation'):
                with self.subTest(generation=generation,invalid=invalid):
                    glove=self.measured(generation,stale=invalid=='stale')
                    if invalid=='missing':glove['feedback']['latest']['joints'].pop()
                    selected=('hand2' if generation=='hand1' else 'hand1') if invalid=='generation' else generation
                    q,mode,_=pose(glove,selected,'automatic')
                    self.assertIsNone(q);self.assertEqual(mode,'glove_live')
            glove=dict(stream=dict(q=[.5]*20,fresh=True),hardware={'active':False},feedback={})
            self.assertEqual(pose(glove,generation,'automatic'),(None,'glove_live',{}))

    def test_automatic_and_legacy_preview_reject_nonfinite_incomplete_and_boolean_targets(self):
        for generation in ('hand1','hand2'):
            for mode in ('automatic',None):
                for q in ([1.]*19,[1.]*21,[float('nan')]*20,[float('inf')]*20,[True]*20,['1']*20,None):
                    with self.subTest(generation=generation,mode=mode,q=q):
                        result=pose(dict(stream=dict(q=q,fresh=True)),generation,mode)
                        self.assertIsNone(result[0])
                        self.assertEqual(result[1],'glove_constrained_unavailable')

    def test_actual_feedback_rejects_duplicate_or_malformed_channels_and_nonfinite_values(self):
        for generation in ('hand1','hand2'):
            original=self.measured(generation)
            rows=original['feedback']['latest']['joints']
            invalid=[rows+[rows[0]],rows[:-1],[None]*20,
                [dict(j,position_rad=float('nan')) for j in rows],
                [dict(j,position_rad=float('inf')) for j in rows],
                [dict(j,position_rad=True) for j in rows],
                [dict(j,nid=True) if i==0 else j for i,j in enumerate(rows)],
                [dict(j,nid=rows[0]['nid']) if i==1 else j for i,j in enumerate(rows)]]
            for mode in ('automatic','feedback',None):
                for malformed in invalid:
                    with self.subTest(generation=generation,mode=mode,rows=malformed):
                        glove=copy.deepcopy(original);glove['feedback']['latest']['joints']=malformed
                        self.assertIsNone(pose(glove,generation,mode)[0])
                        self.assertEqual(pose(glove,generation,mode)[1],'glove_live')
                glove=copy.deepcopy(original);glove['feedback']['latest']=['invalid']
                self.assertIsNone(pose(glove,generation,mode)[0])


if __name__=='__main__':unittest.main()
