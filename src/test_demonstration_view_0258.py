"""Read-only per-model display routing; no SDK or renderer execution."""
import unittest
import time
from types import SimpleNamespace as N
from unittest.mock import Mock,patch

from console_server import Handler
from pose_view import DemonstrationPoseSource,PoseView,VirtualPoseSource
from device_profiles import PROFILES


class DisplayRouteTests(unittest.TestCase):
    def test_new_request_cannot_publish_previous_device_cached_frame(self):
        current=dict(device_profile={'id':'hand2_left'},device_id='WH_SYNTHETIC_B',
                     demonstration_display='feedback',view_source='feedback')
        controller=N(demonstration_snapshot=Mock(return_value=current),action=Mock())
        with patch('pose_view.threading.Thread'):
            view=PoseView(DemonstrationPoseSource(controller,'hand2_left','feedback'))
            view.jpeg=b'synthetic-only-image'
            view.meta=dict(ready=True,mode='live',device_profile='hand2_left',device_id='WH_SYNTHETIC_A',
                           demonstration_display='feedback',view_source='feedback',rendered_monotonic=time.monotonic())
            self.assertFalse(view.get()[1]['ready'])
            view.meta['device_id']='WH_SYNTHETIC_B'
            self.assertTrue(view.get()[1]['ready'])
            current.update(demonstration_display='preview',view_source='preview',device_id=None)
            self.assertFalse(view.get()[1]['ready'])
            view.meta.update(demonstration_display='preview',view_source='preview',device_id=None)
            self.assertTrue(view.get()[1]['ready'])
            current['device_profile']={'id':'hand1_right'}
            self.assertFalse(view.get()[1]['ready'])
        controller.action.assert_not_called()

    def test_new_request_rejects_old_run_pause_and_stop_cached_frames(self):
        current=dict(device_profile={'id':'hand2_left'},device_id=None,
                     demonstration_display='preview',view_source='preview',
                     playback=dict(run_id='new-run',active=True,running=True))
        controller=N(demonstration_snapshot=Mock(return_value=current),action=Mock())
        with patch('pose_view.threading.Thread'):
            view=PoseView(DemonstrationPoseSource(controller,'hand2_left','preview'))
            view.meta=dict(ready=True,mode='demo',device_profile='hand2_left',device_id=None,
                           demonstration_display='preview',view_source='preview',
                           playback=dict(run_id='old-run',active=True,running=True),rendered_monotonic=time.monotonic())
            self.assertFalse(view.get()[1]['ready'])
            view.meta['playback']['run_id']='new-run'
            self.assertTrue(view.get()[1]['ready'])
            current['playback']['running']=False
            self.assertFalse(view.get()[1]['ready'])
            view.meta['playback']['running']=False
            self.assertTrue(view.get()[1]['ready'])
            current['playback'].update(active=False,run_id=None)
            self.assertFalse(view.get()[1]['ready'])
            view.meta.pop('playback');view.meta['mode']='preview'
            self.assertTrue(view.get()[1]['ready'])
        controller.action.assert_not_called()

    def test_source_only_reads_exact_local_profile(self):
        state={'device_profile':{'id':'hand1_right'},'latest':None}
        controller=N(demonstration_snapshot=Mock(return_value=state),action=Mock())
        source=DemonstrationPoseSource(controller,'hand1_right')
        self.assertIs(source.snapshot(),state)
        controller.demonstration_snapshot.assert_called_once_with('hand1_right')
        controller.action.assert_not_called()
        self.assertFalse(hasattr(source,'action'))
        explicit=DemonstrationPoseSource(controller,'hand1_right',display='feedback')
        explicit.snapshot()
        controller.demonstration_snapshot.assert_called_with('hand1_right',display='feedback')
        for value in ('hand2','hand2_other','../hand2_left','',None):
            with self.subTest(value=value),self.assertRaises((ValueError,TypeError)):
                DemonstrationPoseSource(controller,value)

    def test_bounded_lazy_renderers_do_not_share_model_mode_or_glove_source(self):
        with patch('pose_view.threading.Thread') as thread:
            controller=N(snapshot=Mock(),action=Mock())
            view=PoseView(controller)
            self.assertEqual(view.demonstration_views,{})
            for profile in PROFILES:
                view.get(profile=profile)
                child=view.demonstration_views[(profile,None)]
                view.get(profile=profile)
                self.assertIs(view.demonstration_views[(profile,None)],child)
                self.assertIsInstance(child.controller,DemonstrationPoseSource)
                self.assertEqual(child.controller.profile_id,profile)
            self.assertEqual(len(view.demonstration_views),4)
            self.assertEqual(thread.call_count,5)
            for profile in PROFILES:
                for display in ('feedback','preview'):
                    view.get(profile=profile,display=display)
                    child=view.demonstration_views[(profile,display)]
                    self.assertEqual(child.controller.display,display)
                    view.get(profile=profile,display=display)
                    self.assertIs(view.demonstration_views[(profile,display)],child)
            self.assertEqual(len(view.demonstration_views),12)
            self.assertEqual(thread.call_count,13)
            view.get(teleoperation='preview')
            self.assertIsInstance(view.virtual_view.controller,VirtualPoseSource)
            self.assertNotIn(view.virtual_view,view.demonstration_views.values())
            self.assertEqual(thread.call_count,14)
            for kwargs in ({'profile':'wrong'},{'profile':'hand2_left','teleoperation':'preview'},
                           {'display':'feedback'},{'profile':'hand1_left','display':'other'}):
                with self.assertRaises(ValueError):view.get(**kwargs)
            self.assertEqual(thread.call_count,14)
            controller.action.assert_not_called()

    def test_http_model_selector_is_single_validated_and_never_an_action(self):
        handler=object.__new__(Handler);handler.allowed_host=Mock(return_value=True);handler.reply=Mock()
        pose=N(get=Mock(return_value=(None,{'ready':False})))
        controller=N(action=Mock())
        handler.server=N(pose=pose,controller=controller)
        for profile in PROFILES:
            pose.get.reset_mock();handler.reply.reset_mock()
            handler.path='/api/view?profile='+profile;handler.do_GET()
            pose.get.assert_called_once_with(profile=profile)
            self.assertEqual(handler.reply.call_args.args[0],200)
            for display in ('feedback','preview'):
                pose.get.reset_mock();handler.path='/api/view?profile='+profile+'&display='+display;handler.do_GET()
                pose.get.assert_called_once_with(profile=profile,display=display)
                self.assertEqual(handler.reply.call_args.args[0],200)
        for query in ('profile=wrong','profile=','profile=hand2_left&profile=hand2_right',
                      'profile=hand2_left&teleoperation=preview','profile=hand2_left&other=1',
                      'display=preview','profile=hand2_left&display=',
                      'profile=hand2_left&display=preview&display=feedback','profile=hand2_left&display=other'):
            pose.get.reset_mock();handler.reply.reset_mock()
            handler.path='/api/view?'+query;handler.do_GET()
            self.assertEqual(handler.reply.call_args.args[0],400)
            pose.get.assert_not_called()
        controller.action.assert_not_called()


if __name__=='__main__':unittest.main()
