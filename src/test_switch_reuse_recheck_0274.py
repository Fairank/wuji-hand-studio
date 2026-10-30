"""Recheck the live owner after a potentially slow target-directory read."""
import copy
import unittest
from unittest.mock import Mock

from cli_coordination import release
from test_connection_switch_0264 import PurposeSwitchTests, SN
from test_switch_stale_retry_0271 import StaleRetryTests


class ReuseRecheckTests(unittest.TestCase):
    environment = PurposeSwitchTests.environment
    command = PurposeSwitchTests.command
    saved = StaleRetryTests.saved

    def during_inventory(self, e, edit):
        inventory = e.c.device_inventory.side_effect
        fired = []
        def read():
            if not fired:
                fired.append(True)
                edit()
            return inventory()
        e.c.device_inventory.side_effect = read

    def test_fresh_source_expiring_during_lookup_uses_exact_saved_preflight_before_release(self):
        with self.environment() as e:
            order = self.saved(e)
            old = e.gs['session_id']
            self.during_inventory(e, lambda: e.gs['stream'].update(fresh=False))
            result = e.service.start(self.command(e, 'teleoperation'))
            self.assertFalse(result['connection_switch']['unchanged'])
            self.assertEqual(order, ['preflight', 'stop', 'prepare', 'open'])
            self.assertEqual(e.gs['session_id'], old + 1)
            self.assertTrue(e.gs['stream']['fresh'])
            self.assertEqual(e.calls, [])

    def test_exact_saved_preflight_failure_after_expiry_preserves_the_original_owner(self):
        with self.environment() as e:
            self.saved(e)
            old = e.gs['session_id']
            self.during_inventory(e, lambda: e.gs['stream'].update(fresh=False))
            e.c._saved_profile_preflight.side_effect = ValueError('Saved wearer unavailable')
            with self.assertRaisesRegex(ValueError, 'Saved wearer unavailable'):
                e.service.start(self.command(e, 'teleoperation'))
            self.assertEqual(e.gs['session_id'], old)
            self.assertTrue(e.gs['busy'])
            e.c.glove.stop_session_confirmed.assert_not_called()
            e.c._start_saved_profile_session.assert_not_called()

    def test_a_still_fresh_owner_reuses_without_saved_reads_or_device_commands(self):
        with self.environment() as e:
            order = self.saved(e)
            self.during_inventory(e, lambda: e.gs['stream'].update(seq=900))
            before = copy.deepcopy(e.gs)
            result = e.service.start(self.command(e, 'teleoperation'))
            self.assertTrue(result['connection_switch']['unchanged'])
            self.assertEqual(order, [])
            self.assertEqual(e.gs['session_id'], before['session_id'])
            self.assertEqual(e.gs['stream']['seq'], 900)
            self.assertEqual(e.calls, [])

    def test_changed_sdk_epoch_or_action_revision_never_reuses_or_stops_the_replacement(self):
        for field in ('session_id', 'action_revision'):
            with self.subTest(field=field), self.environment() as e:
                self.saved(e)
                self.during_inventory(e, lambda: e.gs.update({field: e.gs.get(field, 0) + 1}))
                with self.assertRaisesRegex(ValueError, 'owner changed'):
                    e.service.start(self.command(e, 'teleoperation'))
                e.c.glove.stop_session_confirmed.assert_not_called()
                e.c._start_saved_profile_session.assert_not_called()
                self.assertEqual(e.calls, [])

    def test_paired_hand_becoming_unknown_during_lookup_is_not_claimed_reusable(self):
        with self.environment() as e:
            self.saved(e)
            e.gs.update(feedback={'device_id': 'SYNTHETIC-HAND'},
                        hardware={'active': False, 'stop_confirmed': True})
            self.during_inventory(e, lambda: e.gs['hardware'].update(
                active=None, stop_confirmed=False))
            with self.assertRaises(ValueError):
                e.service.start(self.command(e, 'teleoperation'))
            e.c.glove.stop_session_confirmed.assert_not_called()
            e.c._start_saved_profile_session.assert_not_called()
            self.assertEqual(e.calls, [])

    def test_ended_viewer_or_hand_during_lookup_does_not_return_a_historical_owner(self):
        for mode in ('official', 'hand'):
            with self.subTest(mode=mode), self.environment(mode) as e:
                def ended():
                    if mode == 'official':
                        token = e.vs['session_id']
                        e.vs.clear()
                        e.vs.update(phase='exited', active=False, busy=False)
                        release(token, e.folder)
                    else:
                        e.state.update(connection='disconnected', device_id=None,
                                       identified_device=None,
                                       session_generation=e.state['session_generation'] + 1)
                self.during_inventory(e, ended)
                from test_connection_switch import HAND
                with self.assertRaisesRegex(ValueError, 'owner changed'):
                    e.service.start(self.command(e, mode, HAND if mode == 'hand' else SN))
                self.assertEqual(e.calls, [])
                e.c.glove.stop_session_confirmed.assert_not_called()
                e.c.official_viz.stop_owned.assert_not_called()
                e.c.stop_feedback_confirmed.assert_not_called()

    def test_cancel_during_lookup_remains_a_zero_command_refusal(self):
        with self.environment() as e:
            order = self.saved(e)
            self.during_inventory(e, lambda: setattr(e.service, 'cancelled', True))
            with self.assertRaisesRegex(ValueError, 'cancellation requested'):
                e.service.start(self.command(e, 'teleoperation'))
            self.assertEqual(order, [])
            self.assertEqual(e.calls, [])
            e.c.glove.stop_session_confirmed.assert_not_called()


del PurposeSwitchTests, StaleRetryTests

if __name__ == '__main__':
    unittest.main()
