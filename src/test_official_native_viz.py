"""Native official-viewer contracts with fake components/processes/sockets.

These tests never prepare or execute an official CLI, scan a real device,
import an SDK, or start a real child process.
"""
from contextlib import contextmanager, ExitStack
import io
import json
from pathlib import Path
import sys
import tempfile
import time
from types import ModuleType
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qsl, urlencode, urlsplit

import calibration_cli
import cli_coordination as coordination
import official_device_discovery as discovery
import official_visualization as visualization


SERIAL = 'WG_SYNTHETIC_ONLY'
STREAM = 'rerun+http://127.0.0.1:19876/proxy'
VIEWER = 'http://127.0.0.1:19090/?' + urlencode({'url': STREAM, 'theme': 'dark'})
COMPONENT = dict(version='2026.10.12', binary='C:/synthetic-never-executed/wuji.exe')


def module(name, **values):
    result = ModuleType(name)
    result.__dict__.update(values)
    return result


class FakeProcess:
    def __init__(self, lines=(), exit_code=0):
        self.stdout = iter(lines)
        self.stderr = iter(())
        self.stdin = io.StringIO()
        self.returncode = None
        self.exit_code = exit_code

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = self.exit_code
        return self.returncode


class FakeJob:
    def __init__(self, process):
        self.process = process
        self.close = Mock(return_value=True)
        self.terminate_and_wait = Mock(return_value=True)


class DeferredThread:
    """Never run workers implicitly; tests invoke only the intended target."""
    created = []

    def __init__(self, target=None, args=(), **kwargs):
        self.target, self.args, self.started = target, args, False
        self.created.append(self)

    def start(self):
        self.started = True

    def is_alive(self):
        return False

    def join(self, timeout=None):
        pass


class NativeDiscoveryTests(unittest.TestCase):
    def test_only_default_windows_wsl_network_uses_native_cli(self):
        with patch.object(discovery.sys, 'platform', 'win32'):
            for cli in ('wuji', '/usr/local/bin/wuji'):
                self.assertTrue(discovery.use_native(dict(mode='wsl', cli=cli)))
            for config in (dict(mode='wsl', cli='/custom/wuji'), dict(mode='ssh', cli='wuji'),
                           dict(mode='local', cli='wuji'), dict(mode='macvm', cli='wuji')):
                self.assertFalse(discovery.use_native(config), config)
        with patch.object(discovery.sys, 'platform', 'linux'):
            self.assertFalse(discovery.use_native(dict(mode='wsl', cli='wuji')))

    def test_native_discovery_is_exact_json_devices_only(self):
        prepared = Mock(return_value=COMPONENT)
        result = Mock(returncode=0, stdout=json.dumps({'devices': [{'sn': SERIAL}]}), stderr='')
        with patch.dict(sys.modules, {'native_cli': module('native_cli', prepare=prepared)}), \
             patch.object(discovery.subprocess, 'run', return_value=result) as run:
            self.assertEqual(discovery.native_devices(), {'devices': [{'sn': SERIAL}]})
        self.assertEqual(run.call_args.args[0], [COMPONENT['binary'], '--json', 'devices'])
        self.assertFalse(run.call_args.kwargs.get('shell', False))
        self.assertEqual(run.call_args.kwargs['env']['WUJI_NO_UPDATE_CHECK'], '1')
        self.assertEqual(run.call_args.kwargs['env']['WUJI_TELEMETRY_DISABLE'], '1')

    def test_default_discovery_route_and_explicit_custom_controller_are_separate(self):
        config = dict(mode='wsl', cli='wuji')
        with patch.object(discovery.sys, 'platform', 'win32'), \
             patch.object(discovery, 'native_devices', return_value={'devices': [{'sn': SERIAL}]}) as native, \
             patch.object(calibration_cli, 'local_run', return_value=(0, '{"devices":[]}', '')) as local, \
             patch.object(calibration_cli, 'controller_args', side_effect=lambda config, args: list(args)):
            self.assertEqual(calibration_cli.cli_json(config, 'devices')['devices'], [{'sn': SERIAL}])
            local.assert_not_called()
            custom = dict(mode='wsl', cli='/explicit/custom/wuji')
            self.assertEqual(calibration_cli.cli_json(custom, 'devices'), {'devices': []})
            self.assertEqual(local.call_args.args[0], ['/explicit/custom/wuji', '--json', 'devices'])
            native.assert_called_once()

    def test_user_list_does_not_silently_switch_to_windows_profiles(self):
        with patch.object(discovery.sys, 'platform', 'win32'), \
             patch.object(discovery, 'native_devices', side_effect=AssertionError('No discovery/profile switch')) as native, \
             patch.object(calibration_cli, 'local_run', return_value=(0, '{"users":[]}', '')) as local, \
             patch.object(calibration_cli, 'controller_args', side_effect=lambda config, args: list(args)), \
             patch('official_cli.cli_path', return_value='/managed/linux/wuji'):
            self.assertEqual(calibration_cli.cli_json(dict(mode='wsl', cli='wuji'), 'user', 'list'), {'users': []})
            self.assertEqual(local.call_args.args[0], ['/managed/linux/wuji', '--json', 'user', 'list'])
            native.assert_not_called()

    def test_malformed_or_failed_discovery_does_not_become_empty_success(self):
        for result in (Mock(returncode=1, stdout='', stderr='synthetic failure'),
                       Mock(returncode=0, stdout='[]', stderr=''),
                       Mock(returncode=0, stdout='{"devices":{}}', stderr=''),
                       Mock(returncode=0, stdout='invalid', stderr='')):
            with self.subTest(stdout=result.stdout), \
                 patch.dict(sys.modules, {'native_cli': module('native_cli', prepare=Mock(return_value=COMPONENT))}), \
                 patch.object(discovery.subprocess, 'run', return_value=result), self.assertRaises(ValueError):
                discovery.native_devices()


class NativeVisualizationTests(unittest.TestCase):
    @contextmanager
    def fixture(self, lines=(), *, exit_code=0):
        DeferredThread.created = []
        with tempfile.TemporaryDirectory(prefix='.native-viz-qa-', dir=Path(__file__).resolve().parent) as directory, ExitStack() as stack:
            folder = Path(directory) / 'official-cli'
            folder.mkdir()
            process = FakeProcess(lines, exit_code)
            job = FakeJob(process)
            prepare = Mock(return_value=COMPONENT)
            factory = Mock(return_value=job)
            recover = Mock(return_value=True)
            config = dict(mode='wsl', cli='wuji')
            stack.enter_context(patch.dict(sys.modules, {
                'bridge_config': module('bridge_config', load_config=lambda: dict(config)),
                'native_cli': module('native_cli', prepare=prepare),
                'windows_process_job': module('windows_process_job', OwnedProcess=factory, recover_viz_job=recover),
            }))
            stack.enter_context(patch.object(coordination, 'identity', return_value='synthetic-owner-start'))
            stack.enter_context(patch.object(visualization, 'identity', return_value='synthetic-owner-start'))
            stack.enter_context(patch.object(visualization, 'native_network', return_value=True))
            stack.enter_context(patch.object(visualization.threading, 'Thread', DeferredThread))
            sockets = stack.enter_context(patch.object(visualization.socket, 'create_connection'))
            stack.enter_context(patch.object(visualization.subprocess, 'Popen', side_effect=AssertionError('No real subprocess')))
            stack.enter_context(patch.object(visualization.subprocess, 'run', side_effect=AssertionError('No external recovery command')))
            value = visualization.OfficialVisualization(folder, idle_check=Mock())
            value.state.update(supported=True, devices=[{'sn': SERIAL, 'model': 'Wuji Glove'}],scanned_at=time.time())
            yield value, job, factory, prepare, recover, sockets, config

    def marker(self, value):
        with coordination.gate(value.folder):
            token = coordination.reserve('viz', value.folder)
            row = coordination.reservation(value.folder)
            row.update(backend='windows_native', job_name='Local\\HandWorkbench-OfficialViz-' + token)
            coordination.atomic(value.folder / 'session.json', row)
        value.token = token
        value.state.update(phase='starting', busy=True, device={'sn': SERIAL, 'model': 'Wuji Glove'})
        return token

    def test_start_writes_backend_job_token_before_worker_registration(self):
        with self.fixture() as (value, job, factory, prepare, _, _, _):
            value.start(SERIAL, 'light')
            row = coordination.reservation(value.folder)
            self.assertEqual(row['backend'], 'windows_native')
            self.assertEqual(row['job_name'], 'Local\\HandWorkbench-OfficialViz-' + row['token'])
            self.assertEqual(row['token'], value.token)
            self.assertTrue(value.thread.started)
            factory.assert_not_called()
            prepare.assert_not_called()

    def test_native_start_requires_discovered_sn_and_supported_generation(self):
        with self.fixture() as (value, _, factory, _, _, _, _):
            with self.assertRaises(ValueError):
                value.start('WG_NOT_DISCOVERED')
            self.assertEqual(coordination.reservation(value.folder), {})
            value.state['devices'] = [{'sn': 'WH1_TEST', 'model': 'Wuji Hand'}]
            with self.assertRaises(ValueError):
                value.start('WH1_TEST')
            factory.assert_not_called()

    def test_running_requires_both_loopback_endpoints_and_is_not_freshness(self):
        observed = []
        with self.fixture() as (value, job, factory, _, _, sockets, _):
            token = self.marker(value)

            def lines():
                yield json.dumps(dict(status='running', device={'sn': SERIAL}, viewer={'url': VIEWER})) + '\n'
                observed.append(value.snapshot('light'))

            job.process.stdout = lines()
            value._run_native(SERIAL, 'light')
            self.assertEqual([call.args[0] for call in sockets.call_args_list], [('127.0.0.1', 19090), ('127.0.0.1', 19876)])
            self.assertTrue(observed[0]['active'])
            self.assertEqual(observed[0]['stream_freshness'], 'not_reported')
            self.assertFalse(observed[0]['zero_on_start'])
            self.assertEqual(dict(parse_qsl(urlsplit(observed[0]['viewer_url']).query))['theme'], 'light')
            self.assertEqual(factory.call_args.args[0], [COMPONENT['binary'], 'viz', '--sn', SERIAL, '--no-zero', '--no-open', '--jsonl'])
            self.assertEqual(factory.call_args.kwargs['job_name'], 'Local\\HandWorkbench-OfficialViz-' + token)
            self.assertEqual(coordination.reservation(value.folder), {})

    def test_mismatched_sn_or_remote_endpoint_never_becomes_active(self):
        events = [dict(status='running', device={'sn': 'WG_WRONG'}, viewer={'url': VIEWER}),
                  dict(status='running', device={'sn': SERIAL}, viewer={'url': VIEWER.replace('127.0.0.1:19090', '192.0.2.1:19090')}),
                  dict(status='running', device={'sn': SERIAL}, viewer={'url': VIEWER.replace('127.0.0.1%3A19876', '192.0.2.1%3A19876')})]
        for event in events:
            with self.subTest(event=event), self.fixture([json.dumps(event) + '\n']) as (value, _, _, _, _, _, _):
                self.marker(value)
                value._run_native(SERIAL, 'dark')
                self.assertFalse(value.snapshot()['active'])
                self.assertEqual(value.snapshot()['phase'], 'error')
                self.assertTrue(value.snapshot()['error'])

    def test_data_endpoint_failure_is_visible_and_never_active(self):
        event = json.dumps(dict(status='running', device={'sn': SERIAL}, viewer={'url': VIEWER})) + '\n'
        with self.fixture([event]) as (value, _, _, _, _, sockets, _):
            self.marker(value)
            sockets.side_effect = [Mock(__enter__=Mock(), __exit__=Mock()), OSError('synthetic data port unavailable')]
            value._run_native(SERIAL, 'dark')
            self.assertFalse(value.snapshot()['active'])
            self.assertIn('synthetic data port', value.snapshot()['error'])

    def test_stop_before_job_registration_never_spawns_a_child(self):
        with self.fixture() as (value, _, factory, _, recover, _, _):
            value.start(SERIAL)
            value.stop()
            value._run_native(SERIAL, 'light')
            factory.assert_not_called()
            self.assertTrue(value.native_stop.is_set())
            self.assertEqual(coordination.reservation(value.folder), {})
            self.assertEqual(value.snapshot()['phase'], 'idle')

    def test_cleanup_false_retains_marker_and_holder_as_visible_error(self):
        with self.fixture() as (value, job, _, _, _, _, _):
            token = self.marker(value)
            job.close.return_value = False
            value._run_native(SERIAL, 'dark')
            self.assertIs(value.native_job, job)
            self.assertEqual(coordination.reservation(value.folder)['token'], token)
            self.assertEqual(value.snapshot()['phase'], 'error')
            self.assertFalse(value.snapshot()['busy'])
            self.assertIn('cleanup', value.snapshot()['error'].lower())

    def test_startup_protocol_error_keeps_job_when_cleanup_is_unconfirmed(self):
        event = json.dumps(dict(status='running', device={'sn': 'WG_WRONG'}, viewer={'url': VIEWER})) + '\n'
        with self.fixture([event]) as (value, job, _, _, _, _, _):
            token = self.marker(value)
            job.close.return_value = False
            value._run_native(SERIAL, 'dark')
            self.assertIs(value.native_job, job)
            self.assertEqual(coordination.reservation(value.folder)['token'], token)
            self.assertEqual(value.snapshot()['phase'], 'error')
            self.assertFalse(value.snapshot()['busy'])

    def test_cleanup_exception_is_visible_and_does_not_escape_worker_finally(self):
        with self.fixture() as (value, job, _, _, _, _, _):
            token = self.marker(value)
            job.close.side_effect = OSError('synthetic job close failure')
            value._run_native(SERIAL, 'dark')
            self.assertIs(value.native_job, job)
            self.assertEqual(coordination.reservation(value.folder)['token'], token)
            self.assertEqual(value.snapshot()['phase'], 'error')
            self.assertFalse(value.snapshot()['busy'])
            self.assertTrue(value.snapshot()['error'])

    def test_startup_timeout_terminates_owned_job_and_retains_unconfirmed_cleanup(self):
        with self.fixture(exit_code=1) as (value, job, _, _, _, _, _):
            token = self.marker(value)
            job.close.return_value = False

            def lines():
                timer = next(thread for thread in DeferredThread.created if thread.target.__name__ == 'timeout')
                with patch.object(visualization.threading.Event, 'wait', return_value=False):
                    timer.target()
                return
                yield 'not reached'

            job.process.stdout = lines()
            value._run_native(SERIAL, 'dark')
            job.terminate_and_wait.assert_called_once_with(5)
            self.assertTrue(value.native_stop.is_set())
            self.assertIs(value.native_job, job)
            self.assertEqual(coordination.reservation(value.folder)['token'], token)
            self.assertEqual(value.snapshot()['phase'], 'error')

    def test_live_other_workspace_cannot_recover_or_terminate_holder(self):
        with self.fixture() as (value, job, _, _, recover, _, _):
            token = self.marker(value)
            value.token = None
            value.native_job = job
            with self.assertRaisesRegex(ValueError, 'owning workspace'):
                value.stop()
            recover.assert_not_called()
            job.close.assert_not_called()
            job.terminate_and_wait.assert_not_called()
            self.assertEqual(coordination.reservation(value.folder)['token'], token)

    def test_recovery_job_name_token_mismatch_is_rejected(self):
        with self.fixture() as (value, _, _, _, recover, _, _):
            token = self.marker(value)
            row = coordination.reservation(value.folder)
            row['job_name'] = 'Local\\HandWorkbench-OfficialViz-' + 'f' * 32
            coordination.atomic(value.folder / 'session.json', row)
            value.token = None
            with patch.object(coordination, 'identity', return_value=None), patch.object(visualization, 'identity', return_value=None):
                with self.assertRaisesRegex(ValueError, 'cleanup'):
                    value.stop()
            recover.assert_not_called()
            self.assertEqual(coordination.reservation(value.folder)['token'], token)

    def test_dead_owner_native_recovery_never_uses_linux_command(self):
        with self.fixture() as (value, _, _, _, recover, _, _):
            token = self.marker(value)
            value.token = None
            with patch.object(coordination, 'identity', return_value=None), patch.object(visualization, 'identity', return_value=None):
                value.stop()
            recover.assert_called_once_with('Local\\HandWorkbench-OfficialViz-' + token, timeout=7)
            self.assertEqual(coordination.reservation(value.folder), {})
            self.assertEqual(value.snapshot()['phase'], 'idle')

    def test_failed_native_recovery_retains_marker(self):
        with self.fixture() as (value, _, _, _, recover, _, _):
            token = self.marker(value)
            recover.return_value = False
            value.token = None
            with patch.object(coordination, 'identity', return_value=None), patch.object(visualization, 'identity', return_value=None):
                with self.assertRaisesRegex(ValueError, 'cleanup'):
                    value.stop()
            self.assertEqual(coordination.reservation(value.folder)['token'], token)

    def test_stop_holder_close_exception_leaves_visible_retryable_error(self):
        with self.fixture() as (value, job, _, _, recover, _, _):
            token = self.marker(value)
            value.native_job = job
            job.close.side_effect = OSError('synthetic holder close failure')
            with self.assertRaises((OSError, ValueError)):
                value.stop()
            self.assertEqual(coordination.reservation(value.folder)['token'], token)
            self.assertIs(value.native_job, job)
            recover.assert_not_called()
            self.assertEqual(value.snapshot()['phase'], 'error')
            self.assertFalse(value.snapshot()['busy'])
            self.assertTrue(value.snapshot()['error'])

    def test_stop_termination_exception_leaves_visible_retryable_error(self):
        with self.fixture() as (value, job, _, _, recover, _, _):
            token = self.marker(value)
            value.native_job = job
            job.terminate_and_wait.side_effect = OSError('synthetic holder terminate failure')
            with self.assertRaises((OSError, ValueError)):
                value.stop()
            self.assertEqual(coordination.reservation(value.folder)['token'], token)
            self.assertIs(value.native_job, job)
            recover.assert_not_called()
            self.assertEqual(value.snapshot()['phase'], 'error')
            self.assertFalse(value.snapshot()['busy'])
            self.assertTrue(value.snapshot()['error'])


if __name__ == '__main__':
    unittest.main()
