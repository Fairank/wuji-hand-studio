"""Pure display decoding tests; no SDK, renderer or device session."""
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

    def test_preview_remains_distinct_and_requires_known_generation_and_freshness(self):
        glove=dict(stream=dict(q=[.5]*20,fresh=True))
        for generation in ('hand1','hand2'):
            with self.subTest(generation=generation):
                q,mode,feedback=pose(glove,generation)
                self.assertEqual(q,[.5]*20)
                self.assertEqual((mode,feedback),('glove_preview',{}))
        for generation in (None,'hand3'):
            self.assertIsNone(pose(glove,generation)[0])
        glove['stream']['fresh']=False
        self.assertIsNone(pose(glove,'hand1')[0])


if __name__=='__main__':unittest.main()
