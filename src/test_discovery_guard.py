"""Synthetic proof that native discovery does not relax control or owner gates."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from discovery_guard import require_discovery_idle
from studio_component import require_idle
from official_visualization import OfficialVisualization


def feedback():
    return dict(connection='connected', device_id='SYNTHETIC-HAND', stale=False,
                device_profile=dict(id='hand2_right'),
                identified_device=dict(source='sdk_identity', serial='SYNTHETIC-HAND', profile='hand2_right'),
                hardware=dict(active=False), glove=dict(busy=False), unconfirmed_devices=[])


class DiscoveryGuardTests(unittest.TestCase):
    def test_verified_feedback_may_broadcast_without_mutating_or_loosening_full_idle(self):
        state = feedback(); before = copy.deepcopy(state)
        require_discovery_idle(state)
        self.assertEqual(state, before)
        with self.assertRaises(ValueError):require_idle(state)

    def test_every_uncertain_identity_or_other_operation_remains_blocked(self):
        changes = [dict(stale=True), dict(stale=None), dict(device_id='OTHER'),
                   dict(identified_device={}), dict(identified_device=[]),
                   dict(device_profile={}), dict(hardware=dict(active=True)),
                   dict(hardware=dict(active=None)), dict(glove=dict(busy=True)),
                   dict(calibration=dict(running=True)), dict(program=dict(active=True)),
                   dict(group=dict(active=True)), dict(recording=dict(active=True)),
                   dict(parameter_sync=dict(busy=True)),
                   dict(unconfirmed_devices=[dict(device_id='SYNTHETIC-HAND')])]
        for profile in ('not-a-hand', '', None, 2, [], {}):
            changes.append(dict(device_profile=dict(id=profile),
                                identified_device=dict(source='sdk_identity', serial='SYNTHETIC-HAND', profile=profile)))
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                require_discovery_idle({**feedback(), **change})
        with self.assertRaises(ValueError):require_discovery_idle(feedback(), dict(running=True))

    def test_connecting_error_and_unknown_connections_are_not_silently_disconnected(self):
        for connection in ('connecting', 'error', None):
            with self.subTest(connection=connection), self.assertRaises(ValueError):
                require_discovery_idle({**feedback(), 'connection': connection})
        require_discovery_idle(dict(connection='disconnected', hardware=dict(active=False)))

    def test_scan_only_callback_does_not_change_viewer_control_callback(self):
        with tempfile.TemporaryDirectory() as folder:
            full = Mock(side_effect=ValueError('full session still blocked'))
            scan = Mock()
            service = OfficialVisualization(Path(folder), idle_check=full, scan_check=scan)
            service.check_scan(); scan.assert_called_once(); full.assert_not_called()
            with self.assertRaisesRegex(ValueError, 'full session'):service.idle_check()
            fallback = OfficialVisualization(Path(folder), idle_check=full)
            with self.assertRaisesRegex(ValueError, 'full session'):fallback.check_scan()

    def test_server_exception_is_only_for_native_broadcast_and_checks_all_workspaces(self):
        from console_server import Controller, ConsoleHTTPServer
        with tempfile.TemporaryDirectory() as folder:
            controller = Controller(reports=Path(folder)/'reports')
            controller.official_viz = OfficialVisualization(Path(folder)/'cli')
            controller.snapshot = Mock(return_value=feedback())
            server = object.__new__(ConsoleHTTPServer); server.controller = controller
            with patch('managed_runtime.status', return_value=dict(busy=False)), \
                 patch('official_device_discovery.use_native', return_value=True), \
                 patch('bridge_config.load_config', return_value=dict(mode='wsl')):
                server.configure_official_cli(startup=False)
                controller.official_viz.check_scan()
                with self.assertRaises(ValueError):controller.official_viz.idle_check()
                with patch('official_device_discovery.use_native', return_value=False):
                    with self.assertRaises(ValueError):controller.official_viz.check_scan()
                server._fleet = Mock()
                server._fleet.lock = __import__('threading').RLock()
                server._fleet.children = {'child': dict(id='child')}
                server._fleet.get.side_effect = [feedback(), dict(running=False)]
                controller.official_viz.check_scan()
                server._fleet.get.side_effect = [{**feedback(), 'hardware': dict(active=True)}, dict(running=False)]
                with self.assertRaises(ValueError):controller.official_viz.check_scan()


if __name__ == '__main__':unittest.main()
