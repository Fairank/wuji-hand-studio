"""Actual renderer/cache deadlines with synthetic clocks and graphics only."""
import copy
import threading
import unittest
from types import SimpleNamespace as N
from unittest.mock import Mock,patch

from pose_view import PoseView
import test_pose_collision_preview as renderer_fixture
fixture=renderer_fixture.fixture


class FrameDeadlineTests(unittest.TestCase):
    def rendered(self,*,timeout=250):
        state=fixture();state['teleoperation_view_mode']='automatic'
        state['glove']['stream']['timeout_ms']=timeout
        clock=Mock(return_value=100.)
        with patch('pose_view.time.monotonic',clock):
            view,*_=renderer_fixture.RendererContractTests().run_once(state)
        view.demand=threading.Event();view.requested_until=0.
        return view,state,clock

    def test_final_snapshot_wait_cannot_publish_a_frame_older_than_its_original_source_deadline(self):
        state=fixture();state['teleoperation_view_mode']='automatic'
        clock=Mock(return_value=100.);waiting=[False];original=copy.deepcopy
        def rendered(current):
            clock.return_value=100.02
            current['glove']['stream'].update(seq=2,q=[.2]*20,age_ms=0.,fresh=True)
            waiting[0]=True
        def snapshot_copy(value,*a,**kw):
            if value is state and waiting[0]:clock.return_value=100.32;waiting[0]=False
            return original(value,*a,**kw)
        with patch('pose_view.time.monotonic',clock),patch('copy.deepcopy',side_effect=snapshot_copy):
            view,*_=renderer_fixture.RendererContractTests().run_once(state,rendered)
        self.assertFalse(view.meta['ready']);self.assertEqual(view.jpeg,b'previous-image')
        self.assertEqual(view.meta['mode'],'glove_preview')

    def test_a_cached_target_frame_expires_at_source_timeout_not_the_generic_one_second_limit(self):
        view,state,clock=self.rendered()
        clock.return_value=100.1
        with patch('pose_view.time.monotonic',clock):jpeg,meta=view.get()
        self.assertTrue(meta['ready']);self.assertAlmostEqual(meta['frame_age_ms'],110.)
        self.assertEqual(meta['frame_timeout_ms'],250)
        self.assertFalse(any('monotonic' in key for key in meta))
        state['glove']['stream'].update(fresh=False,age_ms=310.)
        clock.return_value=100.3
        with patch('pose_view.time.monotonic',clock):old,meta=view.get()
        self.assertFalse(meta['ready']);self.assertEqual(old,jpeg)
        self.assertAlmostEqual(meta['frame_age_ms'],310.)

    def test_a_newer_fresh_sequence_does_not_starve_a_still_fresh_original_render(self):
        state=fixture();state['teleoperation_view_mode']='automatic';clock=Mock(return_value=100.)
        def newer(current):
            clock.return_value=100.02;current['glove']['stream'].update(seq=2,q=[.2]*20,age_ms=0.)
        with patch('pose_view.time.monotonic',clock):view,*_=renderer_fixture.RendererContractTests().run_once(state,newer)
        self.assertTrue(view.meta['ready']);self.assertEqual(view.meta['target_frame']['source_seq'],1)
        view.demand=threading.Event();clock.return_value=100.23
        with patch('pose_view.time.monotonic',clock):_,meta=view.get()
        self.assertTrue(meta['ready']);self.assertAlmostEqual(meta['frame_age_ms'],240.)
        clock.return_value=100.241
        with patch('pose_view.time.monotonic',clock):_,meta=view.get()
        self.assertFalse(meta['ready'])

    def test_each_original_timeout_and_the_existing_five_hundred_ms_cap_are_preserved(self):
        for timeout,inside,outside,limit in ((100,100.08,100.101,100),(2000,100.48,100.501,500)):
            with self.subTest(timeout=timeout):
                view,_,clock=self.rendered(timeout=timeout)
                clock.return_value=inside
                with patch('pose_view.time.monotonic',clock):_,meta=view.get()
                self.assertTrue(meta['ready']);self.assertEqual(meta['frame_timeout_ms'],limit)
                clock.return_value=outside
                with patch('pose_view.time.monotonic',clock):_,meta=view.get()
                self.assertFalse(meta['ready'])

    def test_the_last_image_lock_wait_cannot_publish_an_expired_target(self):
        state=fixture();state['teleoperation_view_mode']='automatic';clock=Mock(return_value=100.)
        class SlowLock:
            def __enter__(self):clock.return_value=100.4;return self
            def __exit__(self,*_):pass
        with patch('pose_view.time.monotonic',clock),patch('test_pose_collision_preview.threading.Lock',return_value=SlowLock()):
            view,*_=renderer_fixture.RendererContractTests().run_once(state)
        self.assertFalse(view.meta['ready']);self.assertEqual(view.jpeg,b'previous-image')

    def test_unattested_or_malformed_cached_target_timing_is_unready(self):
        for value in (None,True,float('nan'),100.2):
            with self.subTest(source_time=value):
                view,_,clock=self.rendered();view.meta['_frame_source_monotonic']=value
                clock.return_value=100.1
                with patch('pose_view.time.monotonic',clock):_,meta=view.get()
                self.assertFalse(meta['ready']);self.assertNotIn('_frame_source_monotonic',meta)

    def test_measured_demo_and_static_preview_keep_their_existing_cache_behavior(self):
        for mode in ('glove_live','live','demo','preview'):
            with self.subTest(mode=mode):
                view=PoseView.__new__(PoseView);view.controller=N(snapshot=Mock(side_effect=AssertionError('No new controller query')))
                view.lock=threading.Lock();view.demand=threading.Event();view.jpeg=b'unchanged'
                view.meta=dict(ready=True,mode=mode,rendered_monotonic=100.)
                with patch('pose_view.time.monotonic',return_value=100.3):jpeg,meta=view.get()
                self.assertTrue(meta['ready']);self.assertEqual(jpeg,b'unchanged')
                self.assertNotIn('frame_age_ms',meta);self.assertNotIn('frame_timeout_ms',meta)
