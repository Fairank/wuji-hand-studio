"""SDK preview -> explicit calibration release; all transports are synthetic."""
import copy
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock

import test_connection_switch as fixture


class CalibrationReleaseTests(unittest.TestCase):
    def command(self):
        return dict(name='calibration_begin',serial=fixture.SN,side='left',profile='Demo',
                    interface='guided',replace=False,create_profile=False)

    def setup_calibration(self,e):
        e.c.calibration=N(preflight_guided=Mock())

    def test_exact_sdk_session_is_released_without_opening_viewer_or_glove(self):
        with fixture.SwitchTests().environment() as e:
            self.setup_calibration(e);binding=copy.deepcopy(e.state['retarget_binding'])
            self.assertTrue(e.service.release_for_calibration(self.command()))
            e.c.calibration.preflight_guided.assert_called_once_with('Demo','left',False,create_profile=False)
            e.c.glove.stop_session_confirmed.assert_called_once_with(4,expected_serial=fixture.SN,
                expected_user=fixture.USER,expected_profile='hand2_left')
            self.assertIsNone(fixture.owner(e.c.snapshot()))
            self.assertEqual(e.service.resume['binding'],binding)
            self.assertFalse(e.service.state['busy']);self.assertEqual(e.service.state['phase'],'released_for_calibration')
            e.c.action.assert_not_called();e.c.official_viz.stop_owned.assert_not_called()

    def test_invalid_wearer_or_components_preserve_existing_stream(self):
        for failure in ('profile','component','workspace'):
            with self.subTest(failure=failure),fixture.SwitchTests().environment() as e:
                self.setup_calibration(e)
                if failure=='profile':e.c.calibration.preflight_guided.side_effect=ValueError('synthetic user invalid')
                elif failure=='component':e.service._official_preflight=Mock(side_effect=ValueError('synthetic component missing'))
                else:e.service.preflight_check.side_effect=ValueError('synthetic sibling offline')
                with self.assertRaises(ValueError):e.service.release_for_calibration(self.command())
                e.c.glove.stop_session_confirmed.assert_not_called()
                self.assertEqual(e.gs['connection'],'receiving');self.assertFalse(e.service.state['busy'])

    def test_another_glove_or_ambiguous_owner_is_never_stopped(self):
        for kind in ('other','unknown'):
            with self.subTest(kind=kind),fixture.SwitchTests().environment() as e:
                self.setup_calibration(e);command=self.command()
                if kind=='other':command['serial']=fixture.OTHER
                else:e.gs['session_id']=None
                with self.assertRaises(ValueError):e.service.release_for_calibration(command)
                e.c.glove.stop_session_confirmed.assert_not_called();e.c.action.assert_not_called()

    def test_missing_disable_ack_never_releases_or_reconnects(self):
        with fixture.SwitchTests().environment() as e:
            self.setup_calibration(e);e.gs.update(hardware={'active':True},feedback={'device_id':fixture.HAND})
            now=[0.];e.service.clock=lambda:now[0];e.service.sleep=lambda _:now.__setitem__(0,now[0]+7.)
            e.c.action.side_effect=lambda command:e.calls.append(command)
            with self.assertRaisesRegex(ValueError,'acknowledgement'):e.service.release_for_calibration(self.command())
            e.c.glove.stop_session_confirmed.assert_not_called()
            self.assertEqual([c['name'] for c in e.calls],['glove_stop'])
            self.assertFalse(e.service.state['source_released'])

    def test_cancel_after_confirmed_release_does_not_start_new_session(self):
        with fixture.SwitchTests().environment() as e:
            self.setup_calibration(e);original=e.c.glove.stop_session_confirmed.side_effect
            def stop(*args,**kw):
                result=original(*args,**kw);e.service.cancelled=True;return result
            e.c.glove.stop_session_confirmed.side_effect=stop
            with self.assertRaisesRegex(ValueError,'Switch stopped'):e.service.release_for_calibration(self.command())
            self.assertTrue(e.service.state['source_released']);e.c.action.assert_not_called()

    def test_empty_hardware_is_safe_only_when_no_hand_has_been_paired(self):
        for paired in (False,True):
            with self.subTest(paired=paired),fixture.SwitchTests().environment() as e:
                self.setup_calibration(e);e.gs['hardware']={}
                e.gs['feedback']={'device_id':fixture.HAND} if paired else {'stale':True}
                if paired:
                    with self.assertRaisesRegex(ValueError,'disable state is unknown'):
                        e.service.release_for_calibration(self.command())
                    e.c.glove.stop_session_confirmed.assert_not_called()
                else:self.assertTrue(e.service.release_for_calibration(self.command()))
                e.c.action.assert_not_called()

    def test_exact_cleanup_failure_can_be_retried_without_reopening_device(self):
        with fixture.SwitchTests().environment() as e:
            self.setup_calibration(e);original=e.c.glove.stop_session_confirmed.side_effect
            e.c.glove.stop_session_confirmed.side_effect=ValueError('synthetic cleanup failed')
            with self.assertRaisesRegex(ValueError,'cleanup'):e.service.release_for_calibration(self.command())
            self.assertEqual(e.gs['connection'],'receiving')
            e.c.glove.stop_session_confirmed.side_effect=original
            self.assertTrue(e.service.release_for_calibration(self.command()))
            self.assertEqual(e.c.glove.stop_session_confirmed.call_count,2);e.c.action.assert_not_called()


if __name__=='__main__':unittest.main()
