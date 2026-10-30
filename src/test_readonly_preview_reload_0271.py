"""Reload only the web UI while an exactly owned, unpaired SDK preview runs."""
import copy
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock

from desktop_tools import dispatch


def preview_state():
    return dict(connection='disconnected', hardware={'active': False},
        device_profile={'id': 'hand2_right'},
        glove=dict(busy=True, connection='receiving', glove_serial='WG_QA_RIGHT',
            session_id=17, action_revision=3, user={'user_id': 'wearer-qa'},
            profile='hand2_right', follow_pending=False, follow_unconfirmed=False,
            open_pending=False, hand_unpairing=False, hardware={},
            feedback={'device_id': None, 'connection': 'disconnected'},
            session_cleanup={'error': None}, stream={'fresh': False}),
        connection_switch=dict(busy=False, current_owner=dict(mode='teleoperation',
            kind='glove', serial='WG_QA_RIGHT', session=17,
            user_id='wearer-qa', profile='hand2_right'), owner_error=None))


def fixture(state=None):
    state = preview_state() if state is None else state
    controller = N(lock=threading.RLock(),
        calibration_handover=N(workflow_lock=threading.Lock()),
        snapshot=Mock(side_effect=lambda: copy.deepcopy(state)),
        doctor=N(snapshot=Mock(return_value={'running': False})),
        action=Mock(side_effect=AssertionError('Reload must not dispatch a device action')))
    host = N(server=N(controller=controller), reload_ui=Mock(return_value={'ok': True}),
        stop_and_close=Mock(), data_dir=None, edition={'name': 'workbench', 'version': '0.2.70'})
    return host, controller, state


class ReadonlyPreviewReloadTests(unittest.TestCase):
    def test_stale_unpaired_preview_reload_preserves_owner_and_never_touches_sdk(self):
        host, controller, state = fixture(); before = copy.deepcopy(state)
        for _ in range(2):
            self.assertEqual(dispatch(host, {'operation': 'reload_ui'}), {'ok': True})
        self.assertEqual(host.reload_ui.call_count, 2)
        self.assertEqual(state, before)
        controller.action.assert_not_called(); host.stop_and_close.assert_not_called()

    def test_inexact_paired_pending_unknown_or_maintenance_preview_is_not_reloadable(self):
        changes = [
            ('no canonical owner', lambda s: s['connection_switch'].pop('current_owner')),
            ('owner SN', lambda s: s['connection_switch']['current_owner'].update(serial='WG_QA_OTHER')),
            ('owner epoch', lambda s: s['connection_switch']['current_owner'].update(session=18)),
            ('owner user', lambda s: s['connection_switch']['current_owner'].update(user_id='other')),
            ('owner profile', lambda s: s['connection_switch']['current_owner'].update(profile='hand2_left')),
            ('owner error', lambda s: s['connection_switch'].update(owner_error='unconfirmed')),
            ('nonpositive epoch', lambda s: (s['glove'].update(session_id=0), s['connection_switch']['current_owner'].update(session=0))),
            ('busy false receiving', lambda s: s['glove'].update(busy=False)),
            ('opening', lambda s: s['glove'].update(open_pending=True)),
            ('follow pending', lambda s: s['glove'].update(follow_pending=True)),
            ('follow unknown', lambda s: s['glove'].update(follow_unconfirmed=True)),
            ('follow field unknown', lambda s: s['glove'].pop('follow_pending')),
            ('paired inactive hand', lambda s: (s['glove']['feedback'].update(device_id='WH_QA'), s['glove'].update(hardware={'active': False}))),
            ('hand connecting', lambda s: s['glove']['feedback'].update(connection='connecting')),
            ('hand unpairing', lambda s: s['glove'].update(hand_unpairing=True)),
            ('hardware active', lambda s: s['glove'].update(hardware={'active': True})),
            ('hardware unknown', lambda s: s['glove'].update(hardware={'active': None})),
            ('stop unknown', lambda s: s['glove'].update(hardware={'active': False, 'stop_confirmed': False})),
            ('global hardware unknown', lambda s: s.update(hardware={})),
            ('global stop unknown', lambda s: s.update(hardware={'active': False, 'stop_confirmed': False})),
            ('cleanup error', lambda s: s['glove']['session_cleanup'].update(error='not released')),
            ('switch busy', lambda s: s['connection_switch'].update(busy=True)),
            ('maintenance busy', lambda s: s.update(maintenance={'busy': True})),
            ('catalog busy', lambda s: s.update(maintenance={'catalog_busy': True})),
            ('calibration cleanup', lambda s: s.update(calibration={'cleanup_required': True})),
            ('CLI cleanup', lambda s: s.update(official_cli={'update': {'recovery_required': True}})),
        ]
        for label, change in changes:
            with self.subTest(label=label):
                host, controller, state = fixture(); change(state)
                with self.assertRaises(ValueError): dispatch(host, {'operation': 'reload_ui'})
                host.reload_ui.assert_not_called(); controller.action.assert_not_called()
        host, controller, _ = fixture(); controller.doctor.snapshot.return_value = {'running': True}
        with self.assertRaises(ValueError): dispatch(host, {'operation': 'reload_ui'})
        host.reload_ui.assert_not_called()

    def test_preview_exception_does_not_allow_close_update_or_code_preview(self):
        host, controller, _ = fixture()
        for operation in ('close_idle', 'prepare_update', 'preview'):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                dispatch(host, {'operation': operation, 'action': 'open', 'speed': 1})
        host.stop_and_close.assert_not_called(); host.reload_ui.assert_not_called()
        controller.action.assert_not_called()

    def test_owner_changes_during_final_read_are_rejected_without_reload(self):
        for key, value in (('session', 18), ('user_id', 'other'), ('serial', 'WG_QA_OTHER')):
            with self.subTest(key=key):
                host, controller, state = fixture(); changed = copy.deepcopy(state)
                changed['connection_switch']['current_owner'][key] = value
                if key == 'session': changed['glove']['session_id'] = value
                elif key == 'user_id': changed['glove']['user']['user_id'] = value
                else: changed['glove']['glove_serial'] = value
                controller.snapshot.side_effect = [copy.deepcopy(state), changed]
                with self.assertRaises(ValueError): dispatch(host, {'operation': 'reload_ui'})
                host.reload_ui.assert_not_called()

    def test_workflow_busy_refuses_even_idle_then_explicit_retry_works(self):
        host, controller, _ = fixture(dict(connection='disconnected', hardware={'active': False}))
        lock = controller.calibration_handover.workflow_lock; lock.acquire()
        try:
            with self.assertRaises(ValueError): dispatch(host, {'operation': 'reload_ui'})
            host.reload_ui.assert_not_called()
        finally: lock.release()
        self.assertEqual(dispatch(host, {'operation': 'reload_ui'}), {'ok': True})

    def test_reload_serializes_workflow_but_does_not_hold_controller_lock(self):
        host, controller, _ = fixture(); observations = []
        def reload_ui():
            def contender():
                workflow = controller.calibration_handover.workflow_lock
                acquired = workflow.acquire(blocking=False); observations.append(('workflow', acquired))
                if acquired: workflow.release()
                acquired = controller.lock.acquire(blocking=False); observations.append(('controller', acquired))
                if acquired: controller.lock.release()
            thread = threading.Thread(target=contender); thread.start(); thread.join(1)
            self.assertFalse(thread.is_alive())
            return {'ok': True}
        host.reload_ui.side_effect = reload_ui
        self.assertEqual(dispatch(host, {'operation': 'reload_ui'}), {'ok': True})
        self.assertEqual(observations, [('workflow', False), ('controller', True)])
        self.assertTrue(controller.calibration_handover.workflow_lock.acquire(blocking=False))
        controller.calibration_handover.workflow_lock.release()

    def test_failed_webview_call_releases_workflow_for_explicit_retry(self):
        host, controller, state = fixture(); before = copy.deepcopy(state)
        host.reload_ui.side_effect = [RuntimeError('synthetic WebView unavailable'), {'ok': True}]
        with self.assertRaisesRegex(RuntimeError, 'WebView'): dispatch(host, {'operation': 'reload_ui'})
        self.assertEqual(dispatch(host, {'operation': 'reload_ui'}), {'ok': True})
        self.assertEqual(state, before); controller.action.assert_not_called()

    def test_native_host_only_evaluates_location_reload_and_requires_workflow(self):
        from native_desktop import NativeDesktop
        host, controller, state = fixture(); before = copy.deepcopy(state)
        native = NativeDesktop(); native.server = host.server; native.window = Mock()
        self.assertEqual(dispatch(native, {'operation': 'reload_ui'}), {'ok': True})
        native.window.evaluate_js.assert_called_once_with('location.reload()')
        self.assertEqual(state, before); controller.action.assert_not_called()
        del controller.calibration_handover
        with self.assertRaises(ValueError): dispatch(host, {'operation': 'reload_ui'})
        host.reload_ui.assert_not_called()


if __name__ == '__main__': unittest.main()
