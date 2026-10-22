"""Public continuation cancellation must name the exact launched attempt."""
import unittest
from unittest.mock import Mock

from calibrated_teleoperation import CalibratedTeleoperation
from console_server import Controller
from glove_bridge import GloveBridge
from glove_protocol import CancellationNotDispatched


class ContinuationCancelAPITests(unittest.TestCase):
    def controller(self):
        controller = Controller.__new__(Controller)
        controller.calibrated_teleoperation = CalibratedTeleoperation(
            lambda: {}, Mock(), Mock(), lambda: {}, Mock(), Mock())
        controller.calibrated_teleoperation.state.update(
            busy=True, run_id='a' * 32, attempt_id='b' * 32, sdk_session=None)
        return controller

    def test_unscoped_and_extra_fields_cannot_cancel_any_attempt(self):
        for fields in ({}, {'run_id': 'a' * 32}, {'attempt_id': 'b' * 32},
                       {'run_id': 'a' * 32, 'attempt_id': 'b' * 32, 'serial': 'OTHER'}):
            with self.subTest(fields=fields):
                controller = self.controller()
                with self.assertRaisesRegex(ValueError, 'Invalid continuation cancellation'):
                    controller.action(dict(name='calibrated_teleoperation_cancel', **fields))
                self.assertFalse(controller.calibrated_teleoperation._cancel.is_set())
                controller.calibrated_teleoperation.stop_session.assert_not_called()

    def test_old_attempt_and_other_run_never_cancel_current_attempt(self):
        for run, attempt in (('c' * 32, 'b' * 32), ('a' * 32, 'c' * 32)):
            with self.subTest(run=run, attempt=attempt):
                controller = self.controller()
                with self.assertRaisesRegex(ValueError, 'attempt changed'):
                    controller.action(dict(name='calibrated_teleoperation_cancel',
                                           run_id=run, attempt_id=attempt))
                self.assertFalse(controller.calibrated_teleoperation._cancel.is_set())
                controller.calibrated_teleoperation.stop_session.assert_not_called()

    def test_exact_public_attempt_can_cancel_without_acquiring_worker_lock(self):
        controller = self.controller()
        result = controller.action(dict(name='calibrated_teleoperation_cancel',
                                        run_id='a' * 32, attempt_id='b' * 32))
        self.assertTrue(controller.calibrated_teleoperation._cancel.is_set())
        self.assertEqual(result['calibrated_teleoperation']['phase'], 'cancelling')
        controller.calibrated_teleoperation.stop_session.assert_not_called()


class PendingBridgeCancellationTests(unittest.TestCase):
    def test_pending_session_only_cancel_checks_stage_and_target_under_bridge_lock(self):
        cases = [({'connection': 'connecting'}, None, True),
                 ({'connection': 'ready'}, None, True),
                 ({'connection': 'receiving'}, None, False),
                 ({'connection': 'ready', 'hardware': {'active': True}}, None, False),
                 ({'connection': 'ready', 'hardware': {'active': None}}, None, False),
                 ({'connection': 'ready', 'feedback': {'device_id': 'WH-A'}}, None, False),
                 ({'connection': 'ready'}, 'lease', False)]
        for state, lease, allowed in cases:
            with self.subTest(state=state, lease=lease):
                bridge=GloveBridge(Mock());bridge.session=17;bridge.state.update(state)
                bridge.lease=lease;bridge.action=Mock()
                if allowed:
                    bridge.disconnect_session(17,pending_only=True)
                    bridge.action.assert_called_once_with({'name':'glove_disconnect'})
                else:
                    with self.assertRaisesRegex(CancellationNotDispatched,'Continuation completed'):
                        bridge.disconnect_session(17,pending_only=True)
                    bridge.action.assert_not_called()

    def test_trusted_cleanup_keeps_exact_session_behavior_after_preview(self):
        bridge=GloveBridge(Mock());bridge.session=17
        bridge.state.update(connection='receiving',hardware={'active':True})
        bridge.action=Mock()
        with self.assertRaisesRegex(CancellationNotDispatched,'session changed'):
            bridge.disconnect_session(16)
        bridge.action.assert_not_called()
        bridge.disconnect_session(17)
        bridge.action.assert_called_once_with({'name':'glove_disconnect'})


if __name__ == '__main__':
    unittest.main()
