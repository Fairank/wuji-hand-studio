"""Desktop shutdown ordering using actual entry points and in-memory sessions."""
import copy
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock, patch

from desktop_lifecycle import close_sessions
from native_desktop import NativeDesktop


def idle_state(**extra):
    return dict(connection='disconnected', hardware=dict(active=False, stop_confirmed=True),
                glove=dict(busy=False), official_viz={}, official_studio={},
                calibration={}, parameter_sync={}, **extra)


class DesktopClosePreflightTests(unittest.TestCase):
    def host(self, state, *, doctor_running=False):
        events = []
        controller = N(lock=threading.RLock(), desktop_closing=False,
                       snapshot=Mock(side_effect=lambda:copy.deepcopy(state)),
                       doctor=N(snapshot=Mock(return_value=dict(running=doctor_running))),
                       official_cli=N(idle_check=Mock(), closed=threading.Event(),
                                      cancel_and_wait=Mock(return_value=True)),
                       official_viz=N(stop=Mock(side_effect=lambda:events.append('viewer_stop'))),
                       studio=N(stop=Mock()), glove=N(busy=False),
                       player=N(command=Mock()), action=Mock())
        fleet = N(close=Mock(side_effect=lambda:events.append('fleet_close') or dict(ok=True)),
                  snapshot=Mock(return_value=[dict(connection='disconnected', hardware=False)]))
        updates = N(cancel_and_wait=Mock(side_effect=lambda:events.append('update_cancel') or True))
        host = NativeDesktop.__new__(NativeDesktop)
        host.session_lock = threading.Lock()
        host.allow_close = False
        host.server = N(controller=controller, _fleet=fleet, fleet=fleet,
                        _workbench_updates=updates, updates=updates)
        host.window = N(destroy=Mock())
        return host, events

    def blocked(self, state, *, doctor_running=False, require_idle=False):
        host, events = self.host(state, doctor_running=doctor_running)
        result = host._stop_and_close(require_idle=require_idle)
        self.assertFalse(result['ok'])
        self.assertEqual(events, [], 'A refused close must not terminate another workspace or cancel its download')
        host.server.controller.action.assert_not_called()
        host.server.controller.official_viz.stop.assert_not_called()
        self.assertFalse(host.server.controller.official_cli.closed.is_set())
        self.assertFalse(host.server.controller.desktop_closing)
        self.assertFalse(host.allow_close)
        return host

    def test_catalog_preparation_keeps_child_sessions_and_downloads(self):
        self.blocked(idle_state(maintenance=dict(catalog_busy=True)))

    def test_firmware_writer_keeps_child_sessions_and_downloads(self):
        self.blocked(idle_state(maintenance=dict(busy=True)))

    def test_pending_connection_transition_keeps_other_workspaces(self):
        for name in ('calibration_connection', 'teleoperation_connection', 'connection_switch'):
            with self.subTest(name=name):
                self.blocked(idle_state(**{name:dict(busy=True)}))

    def test_parameter_transfer_keeps_existing_viewer(self):
        state = idle_state()
        state['parameter_sync'] = dict(busy=True)
        state['official_viz'] = dict(active=True)
        self.blocked(state)

    def test_running_diagnostics_keep_existing_viewer(self):
        state = idle_state()
        state['official_viz'] = dict(active=True)
        self.blocked(state, doctor_running=True)

    def test_component_install_keeps_existing_workspaces(self):
        state = idle_state()
        state['official_studio'] = dict(busy=True, active=False, reserved=True)
        self.blocked(state)

    def test_idle_only_close_refuses_before_fleet_cleanup(self):
        self.blocked(idle_state(maintenance=dict(catalog_busy=True)), require_idle=True)

    def test_refused_close_can_retry_after_catalog_finishes(self):
        state = idle_state(maintenance=dict(catalog_busy=True))
        host, events = self.host(state)
        result = host._stop_and_close()
        self.assertFalse(result['ok'])
        self.assertEqual(events, [])
        state['maintenance']['catalog_busy'] = False
        def close(controller):
            self.assertTrue(controller.desktop_closing)
            events.append('root_close')
            return dict(ok=True)
        with patch('native_desktop.close_sessions', side_effect=close), patch('native_desktop.threading.Timer') as timer:
            self.assertTrue(host._stop_and_close()['ok'])
            timer.return_value.start.assert_called_once()
        self.assertEqual(events, ['update_cancel', 'fleet_close', 'root_close'])
        self.assertTrue(host.allow_close)

    def test_root_close_checks_diagnostics_before_viewer_stop(self):
        state = idle_state()
        state['official_viz'] = dict(active=True)
        host, events = self.host(state, doctor_running=True)
        self.assertFalse(close_sessions(host.server.controller, sleep=lambda _:None)['ok'])
        self.assertEqual(events, [])
        self.assertFalse(host.server.controller.official_cli.closed.is_set())

    def test_cleanup_failure_keeps_window_open_and_releases_close_intent(self):
        host, events = self.host(idle_state())
        host.server.fleet.close.side_effect = lambda:events.append('fleet_close') or dict(ok=False, error='synthetic cleanup unconfirmed')
        self.assertFalse(host._stop_and_close()['ok'])
        self.assertFalse(host.server.controller.desktop_closing)
        self.assertFalse(host.allow_close)
        host.window.destroy.assert_not_called()

    def test_other_close_owner_is_not_reset_or_disturbed(self):
        host, events = self.host(idle_state())
        host.server.controller.desktop_closing = True
        self.assertFalse(host._stop_and_close()['ok'])
        self.assertTrue(host.server.controller.desktop_closing)
        self.assertEqual(events, [])

    def test_close_intent_is_visible_before_preflight_reads(self):
        state = idle_state(maintenance=dict(catalog_busy=True))
        host, events = self.host(state)
        def read():
            self.assertTrue(host.server.controller.desktop_closing)
            return copy.deepcopy(state)
        host.server.controller.snapshot.side_effect = read
        self.assertFalse(host._stop_and_close()['ok'])
        self.assertFalse(host.server.controller.desktop_closing)
        self.assertEqual(events, [])

    def test_workspace_close_rejects_before_program_or_calibration_cancellation(self):
        from console_server import Controller
        host, events = self.host(idle_state(maintenance=dict(catalog_busy=True)))
        controller = host.server.controller
        controller.program = N(stop=Mock())
        controller.group = N(active=True, stop=Mock())
        controller.calibration = N(cancel=Mock())
        controller.calibration_handover = N(request_cancel=Mock(), workflow_lock=threading.RLock())
        controller._workflow_action = lambda command:Controller._workflow_action(controller, command)
        controller._coordinated_action = lambda command:Controller._coordinated_action(controller, command)
        self.assertFalse(Controller.action(controller, dict(name='session_close'))['ok'])
        controller.program.stop.assert_not_called()
        controller.group.stop.assert_not_called()
        controller.calibration.cancel.assert_not_called()
        controller.calibration_handover.request_cancel.assert_not_called()
        self.assertEqual(events, [])
        self.assertFalse(controller.desktop_closing)


if __name__ == '__main__':
    unittest.main()
