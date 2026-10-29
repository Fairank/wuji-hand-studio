"""Independent public-cancel ordering challenges; no SDK, CLI or devices."""
from contextlib import contextmanager
import copy
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock, patch

from calibration_handover import CalibrationHandover
from connection_switch import ConnectionSwitch
from console_server import Controller

SN = 'WG_SYNTHETIC_REVIEW'
TOKEN = 'c' * 32
TEST_TEMP = Path(__file__).resolve().parents[2] / 'update-flow-0257/teleoperation-audit/test-temp'
TEST_TEMP.mkdir(parents=True, exist_ok=True)


class BeginReviewTests(unittest.TestCase):
    @contextmanager
    def environment(self, *, viewer=False, sdk=False):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as folder:
            calls = []
            calibration = N(lock=threading.RLock(), run={'running': False, 'status': 'idle'},
                            preflight_guided=Mock(), start=Mock(), cancel=Mock())
            def start(*args, **kw):
                kw['cancel_check']()
                calls.append('capture_started')
                calibration.run = {'running': True, 'status': 'starting', 'serial': SN, 'id': 'review-run'}
                return {'run': copy.deepcopy(calibration.run)}
            def cancel():
                calls.append('public_cancel')
                return {'run': copy.deepcopy(calibration.run)}
            calibration.start.side_effect = start
            calibration.cancel.side_effect = cancel
            viz_state = ({'active': True, 'busy': False, 'phase': 'running', 'session_id': TOKEN,
                          'reported_device': {'sn': SN}, 'backend': 'windows_native'} if viewer else
                         {'active': False, 'busy': False, 'phase': 'idle'})
            viz = N(folder=Path(folder), snapshot=Mock(side_effect=lambda: copy.deepcopy(viz_state)),
                    connection_record=Mock(return_value={'side': 'left', 'serial': SN, 'viewer_token': TOKEN}),
                    stop_owned=Mock())
            @contextmanager
            def priority(**kw):
                calls.append('priority_enter')
                try: yield
                finally: calls.append('priority_exit')
            viz.discovery_priority = priority
            c = N(lock=threading.RLock(), official_viz=viz, calibration=calibration,
                  state={'device_profile': {'id': 'hand2_left', 'side': 'left'}, 'hardware': {'active': False}},
                  group=N(active=False, internal=False), program=N(snapshot=lambda: {'active': False}, thread=None))
            c.calibration_devices = Mock(return_value=[{'sn': SN, 'model': 'Wuji Glove 1', 'handedness': 'left'}])
            for method in ('_action', '_coordinated_action', '_workflow_action', 'action'):
                setattr(c, method, lambda command, method=method: getattr(Controller, method)(c, command))
            service = CalibrationHandover(c, idle_check=lambda: None)
            c.calibration_handover = service
            glove = {'busy': sdk, 'connection': 'receiving' if sdk else 'disconnected', 'session_id': 4,
                     'glove_serial': SN, 'user': {'user_id': 'synthetic-owner'}, 'profile': 'hand2_left',
                     'hardware': {'active': False}}
            def snapshot():
                return {'connection': 'disconnected', 'hardware': {'active': False},
                        'device_profile': {'id': 'hand2_left'}, 'glove': copy.deepcopy(glove),
                        'official_viz': copy.deepcopy(viz_state), 'calibration': copy.deepcopy(calibration.run),
                        'calibration_connection': copy.deepcopy(service.state)}
            c.snapshot = snapshot
            switch = ConnectionSwitch(c)
            switch._official_preflight = Mock()
            switch._config = lambda: 'synthetic-config'
            switch.check_parent = Mock()
            c.connection_switch = switch
            c.glove = N(stop_session_confirmed=Mock(return_value={'cleanup_confirmed': True}))
            with patch('studio_component.enforce_session_owner'):
                yield N(c=c, service=service, switch=switch, calibration=calibration, viewer=viz,
                        calls=calls, glove=glove)

    def command(self):
        return {'name': 'calibration_begin', 'serial': SN, 'side': 'left', 'profile': 'Demo',
                'interface': 'guided', 'replace': False}

    def cancel_during(self, e, install_barrier):
        entered = threading.Event(); resume = threading.Event(); result = {}
        def hold():
            entered.set()
            if not resume.wait(3): raise AssertionError('test barrier timed out')
        install_barrier(hold)
        def begin():
            try: result['value'] = e.service.begin(self.command())
            except Exception as error: result['error'] = error
        thread = threading.Thread(target=begin, name='review-pending-begin')
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            self.assertTrue(e.service.state['busy'])
            e.c.action({'name': 'calibration_cancel'})
        finally:
            resume.set(); thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(result.get('error'), ValueError)
        self.assertIn('cancelled', str(result['error']))
        self.assertFalse(e.service.state['busy'])
        e.calibration.start.assert_not_called()
        e.c.glove.stop_session_confirmed.assert_not_called()
        return result

    def test_public_cancel_during_first_viewer_snapshot_never_starts(self):
        with self.environment() as e:
            original = e.viewer.snapshot.side_effect
            def install(hold):
                def snapshot(): hold(); return original()
                e.viewer.snapshot.side_effect = snapshot
            self.cancel_during(e, install)
            e.c.calibration_devices.assert_not_called()
            self.assertNotIn('priority_enter', e.calls)

    def test_public_cancel_during_sdk_release_first_snapshot_never_stops(self):
        with self.environment(sdk=True) as e:
            original = e.c.snapshot
            def install(hold):
                def snapshot(): hold(); return original()
                e.c.snapshot = snapshot
            self.cancel_during(e, install)
            e.switch._official_preflight.assert_not_called()
            e.c.calibration_devices.assert_not_called()
            self.assertEqual(e.calls, ['priority_enter', 'public_cancel', 'priority_exit'])

    def test_public_cancel_at_nested_viewer_start_is_preserved(self):
        with self.environment(viewer=True) as e:
            original = e.service.start
            def install(hold):
                def nested(command, **kw):
                    self.assertTrue(kw.get('_continuing'))
                    hold(); return original(command, **kw)
                e.service.start = nested
            self.cancel_during(e, install)
            e.viewer.connection_record.assert_not_called()
            e.viewer.stop_owned.assert_not_called()

    def test_sibling_preflight_failure_preserves_existing_sdk_owner(self):
        with self.environment(sdk=True) as e:
            e.switch.preflight_check = Mock(side_effect=ValueError('synthetic sibling unavailable'))
            with self.assertRaisesRegex(ValueError, 'sibling unavailable'):
                e.service.begin(self.command())
            e.c.glove.stop_session_confirmed.assert_not_called()
            e.calibration.start.assert_not_called()
            self.assertEqual(e.glove['connection'], 'receiving')
            self.assertFalse(e.switch.state['busy'])
            self.assertFalse(e.service.state['busy'])

    def test_direct_entry_priority_scope_covers_fresh_discovery_and_capture(self):
        with self.environment() as e:
            e.c.calibration_devices.side_effect = lambda **kw: (
                e.calls.append('fresh_discovery') or [{'sn': SN, 'model': 'Wuji Glove 1', 'side': 'left'}])
            e.service.begin(self.command())
            self.assertEqual(e.calls, ['priority_enter', 'fresh_discovery', 'capture_started', 'priority_exit'])
            self.assertEqual(e.calibration.start.call_args.kwargs['connected']['source'], 'explicit_calibration')


if __name__ == '__main__': unittest.main()
