"""Independent CLI handover/viewer contracts, with isolated data and fake I/O."""
from contextlib import contextmanager
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qsl, urlencode, urlsplit

import cli_coordination as coordination
import official_cli
import official_visualization as visualization


CURRENT = '2026.10.12'
PREVIOUS = '2026.9.22'
NEXT = '2026.10.13'
SERIAL = 'WH2_TEST_ONLY'
STREAM = 'rerun+http://127.0.0.1:9876/proxy'
VIEWER = 'http://127.0.0.1:9090/?' + urlencode([('url', STREAM), ('theme', 'dark'), ('persist', '1')])


def module(name, **values):
    value = ModuleType(name)
    value.__dict__.update(values)
    return value


class FakeProcess:
    def __init__(self, lines=(), *, exit_code=0, stuck=False):
        self.stdout = iter(lines)
        self.stderr = iter(())
        self.stdin = io.StringIO()
        self.exit_code = exit_code
        self.returncode = None
        self.stuck = stuck

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        if self.stuck:
            raise subprocess.TimeoutExpired('mock-viewer-runner', timeout)
        self.returncode = self.exit_code
        return self.returncode


class OfficialCLIRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.folder = self.data/'official-cli'
        self.folder.mkdir()
        self.config = dict(mode='local', cli='wuji', python='mock-python', agent_directory='/mock/agent')
        self.idle = Mock()
        self.store = SimpleNamespace(fetch_release=Mock(return_value={'tag_name': 'v' + NEXT}), prepare=Mock())
        self.owner = official_cli.OfficialCLI(self.folder, idle_check=self.idle, store=self.store)
        self.selection = self.folder/'selection.json'
        self.modules = {
            'managed_runtime': module('managed_runtime', image_manifest=lambda: {'cli_version': CURRENT}, check_ready=Mock()),
            'bridge_config': module('bridge_config', load_config=lambda: copy.deepcopy(self.config)),
            'doctor': module('doctor', local_run=Mock(side_effect=AssertionError('No real compatibility command is allowed'))),
            'calibration_cli': module('calibration_cli', controller_args=lambda config, args: list(args), cli_json=Mock(side_effect=AssertionError('No real device discovery is allowed'))),
        }
        for target in (patch.dict(sys.modules, self.modules), patch.object(coordination, 'identity', return_value='synthetic-start'),
                       patch.object(coordination, 'root_folder', return_value=self.folder), patch.object(official_cli, 'root_folder', return_value=self.folder),
                       patch.object(visualization, 'root_folder', return_value=self.folder),
                       patch.dict(os.environ, {'WUJI_STUDIO_DATA': str(self.data/'data'), 'WUJI_OFFICIAL_CLI_DATA': str(self.folder)})):
            target.start()
            self.addCleanup(target.stop)

    def candidate(self, version=CURRENT, mode='local'):
        binary = self.folder/'versions'/version/'payload/wuji'
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b'synthetic candidate; never executed')
        path = str(binary) if mode == 'local' else '/opt/hand-workbench/components/cli/versions/' + version + '/wuji'
        return dict(version=version, binary=path, mode=mode, validated=True, commit='a' * 40, archive_sha256='b' * 64, validation='software-help-only')

    def save_selection(self, current=None, previous=None):
        value = dict(current=current or self.candidate(), previous=previous)
        coordination.atomic(self.selection, value)
        return value

    def saved(self):
        return json.loads(self.selection.read_text(encoding='utf-8'))

    def viewer(self):
        value = visualization.OfficialVisualization(self.folder, idle_check=self.idle)
        value.config = copy.deepcopy(self.config)
        value.state.update(supported=True, device={'sn': SERIAL, 'model': 'Wuji Hand 2'}, devices=[{'sn': SERIAL, 'model': 'Wuji Hand 2'}])
        return value

    def own_marker(self, value, kind='viz'):
        with coordination.gate(self.folder):
            value.token = coordination.reserve(kind, self.folder)
        return value.token

    @contextmanager
    def runner(self, value, process, socket_side_effect=None):
        # Suppress only helper threads; stdout events are consumed synchronously.
        # No worker survives the test or touches an actual socket/process.
        with patch.object(value, '_command', return_value=['mock-python', 'mock-viz-runner']) as command, \
             patch.object(self.modules['doctor'], 'local_run', return_value=(0, 'wuji ' + CURRENT, '')), \
             patch.object(visualization.subprocess, 'Popen', return_value=process) as popen, \
             patch.object(visualization.subprocess, 'run', side_effect=AssertionError('Unexpected external recovery command')), \
             patch.object(visualization.socket, 'create_connection', side_effect=socket_side_effect) as connect, \
             patch.object(visualization.threading, 'Thread'):
            yield SimpleNamespace(command=command, popen=popen, connect=connect)

    def test_selected_local_path_preserves_explicit_custom_and_ssh_configuration(self):
        candidate = self.candidate()
        self.save_selection(candidate)
        for configured in ('wuji', '/usr/local/bin/wuji'):
            self.assertEqual(official_cli.cli_path(dict(mode='local', cli=configured), self.folder), candidate['binary'])
        for config in (dict(mode='ssh', cli='wuji'), dict(mode='ssh', cli='/remote/custom/wuji'), dict(mode='local', cli='/local/custom/wuji'), dict(mode='wsl', cli='/opt/custom/wuji')):
            with self.subTest(config=config):
                self.assertEqual(official_cli.cli_path(config, self.folder), config['cli'])

    def test_wsl_selection_requires_exact_managed_path_and_matching_mode(self):
        candidate = self.candidate(mode='wsl')
        self.save_selection(candidate)
        self.assertEqual(official_cli.cli_path(dict(mode='wsl', cli='wuji'), self.folder), candidate['binary'])
        candidate['binary'] = candidate['binary'] + '/../../outside'
        self.save_selection(candidate)
        self.assertEqual(official_cli.cli_path(dict(mode='wsl', cli='wuji'), self.folder), 'wuji')

    def test_unvalidated_or_outside_local_selection_is_not_used(self):
        candidate = self.candidate()
        candidate['validated'] = False
        self.save_selection(candidate)
        self.assertEqual(official_cli.cli_path(self.config, self.folder), 'wuji')
        outside = self.data/'outside-wuji'
        outside.write_bytes(b'never executed')
        candidate.update(validated=True, binary=str(outside))
        self.save_selection(candidate)
        self.assertEqual(official_cli.cli_path(self.config, self.folder), 'wuji')

    def test_viewer_theme_override_preserves_data_endpoint_and_other_query(self):
        original = dict(parse_qsl(urlsplit(visualization.viewer_url(VIEWER)).query))
        changed = dict(parse_qsl(urlsplit(visualization.viewer_url(VIEWER, 'light')).query))
        self.assertEqual(original['theme'], 'dark')
        self.assertEqual(changed, dict(original, theme='light'))
        self.assertEqual(changed['url'], STREAM)
        self.assertEqual(changed['persist'], '1')

    def test_viewer_rejects_invalid_outer_and_data_endpoints(self):
        invalid = ['https://127.0.0.1:9090/?' + urlencode({'url': STREAM}),
                   'http://example.com:9090/?' + urlencode({'url': STREAM}),
                   'http://user:secret@127.0.0.1:9090/?' + urlencode({'url': STREAM}),
                   VIEWER + '#fragment', 'http://127.0.0.1:9090/unowned?' + urlencode({'url': STREAM}),
                   'http://127.0.0.1:9090/?' + urlencode([('url', STREAM), ('url', STREAM)])]
        invalid += ['http://127.0.0.1:9090/?' + urlencode({'url': value}) for value in
                    ('rerun+http://example.com:9876/proxy', 'http://127.0.0.1:9876/proxy',
                     'rerun+http://127.0.0.1:9876/other', 'rerun+http://127.0.0.1:9876/proxy?remote=1',
                     'rerun+http://user:secret@localhost:9876/proxy', 'rerun+http://localhost/proxy')]
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(ValueError):
                visualization.viewer_url(url)

    def test_prepare_failure_keeps_current_pointer_and_old_binary(self):
        original = self.save_selection()
        before = self.selection.read_bytes()
        self.store.prepare.side_effect = RuntimeError('Synthetic download failure')
        with patch.object(self.owner, '_deploy') as deploy:
            self.owner._work(False)
        self.assertEqual(self.selection.read_bytes(), before)
        self.assertTrue(Path(original['current']['binary']).is_file())
        self.assertEqual(self.owner.snapshot()['update']['phase'], 'error')
        deploy.assert_not_called()
        self.assertEqual(coordination.reservation(self.folder), {})

    def test_compatibility_failure_never_switches_pointer(self):
        self.save_selection()
        before = self.selection.read_bytes()
        with patch.object(self.owner, '_deploy', side_effect=ValueError('Synthetic incompatible CLI')):
            self.owner._work(False)
        self.assertEqual(self.selection.read_bytes(), before)
        self.assertEqual(self.owner.snapshot()['update']['phase'], 'error')

    def test_successful_update_retains_previous_selection(self):
        original = self.save_selection()
        candidate = self.candidate(NEXT)
        with patch.object(self.owner, '_deploy', return_value=candidate):
            self.owner._work(False)
        self.assertEqual(self.saved()['current'], candidate)
        self.assertEqual(self.saved()['previous'], original['current'])
        self.assertEqual(self.owner.snapshot()['update']['phase'], 'ready')
        self.assertEqual(coordination.reservation(self.folder), {})

    def test_startup_respects_a_user_rollback_hold(self):
        self.save_selection()
        saved=self.saved();saved['hold_version']=NEXT
        coordination.atomic(self.selection,saved);before=self.selection.read_bytes()
        with patch.object(self.owner,'_deploy') as deploy:
            self.owner._work(False,automatic=True)
        self.assertEqual(self.selection.read_bytes(),before)
        self.store.prepare.assert_not_called();deploy.assert_not_called()

    def test_cancelled_preparation_never_deploys_or_switches(self):
        self.save_selection();before=self.selection.read_bytes()
        def prepared(*args,**kwargs):
            self.owner.closed.set()
            return {'synthetic':'prepared'}
        self.store.prepare.side_effect=prepared
        with patch.object(self.owner,'_deploy') as deploy:self.owner._work(False)
        self.assertEqual(self.selection.read_bytes(),before);deploy.assert_not_called()
        self.assertEqual(self.owner.snapshot()['update']['phase'],'cancelled')
        self.assertIsNone(self.owner.snapshot()['update']['error'])

    def test_cancelling_before_start_performs_no_network_work(self):
        self.owner.closed.set();self.owner._work(False)
        self.store.fetch_release.assert_not_called();self.store.prepare.assert_not_called()
        self.assertEqual(self.owner.snapshot()['update']['phase'],'cancelled')

    def test_explicit_update_can_restore_the_managed_cli_after_rollback(self):
        baseline=dict(version=NEXT,source='bundled',validated=False)
        coordination.atomic(self.selection,dict(current=baseline,hold_version=NEXT))
        candidate=self.candidate(NEXT)
        with patch.object(self.owner,'_deploy',return_value=candidate):
            self.owner._work(False,automatic=False)
        self.assertEqual(self.saved()['current'],candidate)
        self.assertEqual(self.saved()['previous'],baseline)
        self.assertEqual(self.owner.snapshot()['update']['phase'],'ready')

    def test_ssh_config_is_not_automatically_deployed(self):
        self.config.update(mode='ssh', cli='/remote/custom/wuji')
        self.save_selection()
        before = self.selection.read_bytes()
        self.owner._work(False)
        self.store.prepare.assert_not_called()
        self.assertEqual(self.selection.read_bytes(), before)

    def test_successful_software_check_survives_unavailable_runtime_status(self):
        from official_tools import OfficialTools
        runtime = self.modules['managed_runtime']
        runtime.PYTHON = '/mock/python'
        runtime.CLI = '/usr/local/bin/wuji'
        runtime.args = lambda args: list(args)
        runtime._decode = lambda raw: raw.decode('utf-8').strip()
        # A version target is deliberately not a full runtime-image manifest.
        runtime.image_manifest = Mock(return_value=dict(cli_version=CURRENT, sdk_version=CURRENT))
        runtime.status = Mock(side_effect=KeyError('runtime_version'))
        results = [SimpleNamespace(returncode=0, stdout=json.dumps(dict(sdk_version=CURRENT, sdk_imported=True)).encode()),
                   SimpleNamespace(returncode=0, stdout=('wuji ' + CURRENT).encode())]
        tools = OfficialTools()
        with patch('official_tools.subprocess.run', side_effect=results) as run:
            tools.run()
        self.assertEqual(run.call_count, 2)
        self.assertTrue(tools.check['available'])
        self.assertTrue(tools.check['compatible'])
        self.assertIsNone(tools.check['error'])
        runtime.status.assert_not_called()

    def test_selected_new_cli_and_unchanged_sdk_are_compatible(self):
        from official_tools import OfficialTools
        selected = self.candidate(NEXT, mode='wsl')
        self.save_selection(selected)
        runtime = self.modules['managed_runtime']
        runtime.PYTHON = '/mock/python'
        runtime.CLI = '/usr/local/bin/wuji'
        runtime.args = lambda args: list(args)
        runtime._decode = lambda raw: raw.decode('utf-8').strip()
        runtime.image_manifest = Mock(return_value=dict(cli_version=CURRENT, sdk_version=CURRENT))
        runtime.status = Mock(return_value=dict(ready=True, installed=True))
        results = [SimpleNamespace(returncode=0, stdout=json.dumps(dict(sdk_version=CURRENT, sdk_imported=True)).encode()),
                   SimpleNamespace(returncode=0, stdout=('wuji ' + NEXT).encode())]
        tools = OfficialTools()
        with patch('official_tools.subprocess.run', side_effect=results) as run:
            tools.run()
            snapshot = tools.snapshot()
        self.assertEqual(snapshot['target'], dict(cli_version=NEXT, sdk_version=CURRENT))
        self.assertEqual(tools.check['cli_version'], NEXT)
        self.assertEqual(tools.check['sdk_version'], CURRENT)
        self.assertTrue(tools.check['available'])
        self.assertTrue(tools.check['compatible'], 'A verified independent CLI update must not request a downgrade to the bundled CLI')
        self.assertIsNone(tools.check['error'])
        self.assertIn(selected['binary'], run.call_args_list[1].args[0])

    def test_automatic_latest_check_does_not_downgrade_a_newer_selection(self):
        newer = self.candidate(NEXT)
        self.save_selection(newer)
        self.store.fetch_release.return_value = {'tag_name': 'v' + CURRENT}
        with patch.object(self.owner, '_deploy', return_value=self.candidate(CURRENT)):
            self.owner._work(False)
        self.assertEqual(self.saved()['current'], newer, 'Automatic latest checking must not roll a newer selection back')

    def test_idle_rejection_does_not_switch_and_releases_only_own_marker(self):
        self.save_selection()
        before = self.selection.read_bytes()
        self.idle.side_effect = ValueError('Synthetic hardware session remains active')
        with self.assertRaises(ValueError):
            self.owner._activate(self.candidate(NEXT))
        self.assertEqual(self.selection.read_bytes(), before)
        self.assertEqual(coordination.reservation(self.folder), {})

    def test_rollback_swaps_current_and_previous_as_one_record(self):
        old, current = self.candidate(PREVIOUS), self.candidate(CURRENT)
        self.save_selection(current, old)
        self.owner.rollback()
        self.assertEqual(self.saved()['current'], old)
        self.assertEqual(self.saved()['previous'], current)
        self.assertEqual(coordination.reservation(self.folder), {})

    def test_failed_atomic_rollback_preserves_original_record(self):
        self.save_selection(self.candidate(), self.candidate(PREVIOUS))
        before = self.selection.read_bytes()
        replace = coordination.os.replace

        def failed_selection(source, destination):
            if Path(destination) == self.selection:
                raise OSError('Synthetic pointer replacement failure')
            replace(source, destination)

        with patch.object(coordination.os, 'replace', side_effect=failed_selection), self.assertRaises(OSError):
            self.owner.rollback()
        self.assertEqual(self.selection.read_bytes(), before)
        self.assertEqual(list(self.folder.glob('*.pending')), [])
        self.assertEqual(coordination.reservation(self.folder), {})

    def test_rollback_selects_previous_inside_the_handover_lock(self):
        old, current, new = self.candidate(PREVIOUS), self.candidate(CURRENT), self.candidate(NEXT)
        self.save_selection(current, old)
        attempted = threading.Event()
        errors = []
        real_gate = coordination.gate

        @contextmanager
        def announced_gate(folder=None):
            attempted.set()
            with real_gate(folder):
                yield

        def rollback():
            try:
                self.owner.rollback()
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=rollback)
        with patch.object(official_cli, 'gate', announced_gate):
            with real_gate(self.folder):
                worker.start()
                self.assertTrue(attempted.wait(3))
                # Another updater commits while rollback waits for the same gate.
                coordination.atomic(self.selection, dict(current=new, previous=current))
            worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.saved()['current'], current, 'Rollback must use the previous version of the locked current record')
        self.assertEqual(self.saved()['previous'], new)

    def test_viewer_idle_rejection_releases_its_unstarted_reservation(self):
        value = self.viewer()
        self.idle.side_effect = ValueError('Synthetic busy controller')
        with patch.object(visualization.subprocess, 'Popen') as popen, self.assertRaises(ValueError):
            value.start(SERIAL)
        popen.assert_not_called()
        self.assertEqual(coordination.reservation(self.folder), {})

    def test_viewer_requires_a_discovered_device_before_launch(self):
        value = self.viewer()
        with patch.object(visualization.subprocess, 'Popen') as popen, self.assertRaises(ValueError):
            value.start('WH2_NOT_DISCOVERED')
        popen.assert_not_called()
        self.assertEqual(coordination.reservation(self.folder), {})

    def test_running_viewer_probes_both_endpoints_and_keeps_theme(self):
        value = self.viewer()
        self.own_marker(value)
        observed = []
        event = dict(status='running', device={'sn': SERIAL}, viewer={'url': VIEWER})

        def lines():
            yield json.dumps(event) + '\n'
            observed.append(value.snapshot('light'))
            yield json.dumps(dict(workbench_event='exited', sn=SERIAL, cleanup_confirmed=True)) + '\n'

        process = FakeProcess(lines())
        with self.runner(value, process) as mocked:
            value._run(self.config, SERIAL, 'light')
        self.assertEqual([call.args[0] for call in mocked.connect.call_args_list], [('127.0.0.1', 9090), ('127.0.0.1', 9876)])
        self.assertTrue(observed[0]['active'])
        query = dict(parse_qsl(urlsplit(observed[0]['viewer_url']).query))
        self.assertEqual(query, {'url': STREAM, 'theme': 'light', 'persist': '1'})
        self.assertEqual(observed[0]['stream_freshness'], 'not_reported')
        self.assertFalse(observed[0]['zero_on_start'])
        self.assertEqual(coordination.reservation(self.folder), {})

    def test_unreachable_data_endpoint_is_never_exposed_as_active(self):
        value = self.viewer()
        self.own_marker(value)
        event = dict(status='running', device={'sn': SERIAL}, viewer={'url': VIEWER})
        observed = []

        def lines():
            yield json.dumps(event) + '\n'
            observed.append(value.snapshot())

        calls = 0
        def connect(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('Synthetic data endpoint unreachable')
            return Mock(__enter__=Mock(return_value=None), __exit__=Mock(return_value=False))

        with self.runner(value, FakeProcess(lines()), connect):
            value._run(self.config, SERIAL, 'dark')
        self.assertEqual(calls, 2)
        self.assertFalse(value.snapshot()['active'])
        self.assertIsNone(value.snapshot()['viewer_url'])
        self.assertFalse(any(state['active'] for state in observed))

    def test_abnormal_launcher_exit_without_child_confirmation_keeps_marker(self):
        value = self.viewer()
        token = self.own_marker(value)
        with self.runner(value, FakeProcess(exit_code=1)):
            value._run(self.config, SERIAL, 'dark')
        self.assertEqual(coordination.reservation(self.folder).get('token'), token,
                         'A dead launcher alone does not confirm its Linux child exited')

    def test_stop_timeout_retains_marker_for_explicit_recovery(self):
        value = self.viewer()
        token = self.own_marker(value)
        with self.runner(value, FakeProcess(stuck=True)):
            value._run(self.config, SERIAL, 'dark')
        self.assertEqual(coordination.reservation(self.folder).get('token'), token)
        self.assertFalse(value.snapshot()['active'])

    def test_cleanup_ack_for_another_device_does_not_release_marker(self):
        value = self.viewer()
        token = self.own_marker(value)
        event = json.dumps(dict(workbench_event='exited', sn='WH2_OTHER_DEVICE', cleanup_confirmed=True)) + '\n'
        with self.runner(value, FakeProcess([event], exit_code=1)):
            value._run(self.config, SERIAL, 'dark')
        self.assertEqual(coordination.reservation(self.folder).get('token'), token)

    def test_stop_does_not_clear_another_switch_or_scan_marker(self):
        for kind in ('switch', 'scan'):
            with self.subTest(kind=kind):
                value = self.viewer()
                with coordination.gate(self.folder):
                    token = coordination.reserve(kind, self.folder)
                with patch.object(value, '_command', return_value=['mock-viz-stop']), patch.object(visualization.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=b'{}', stderr=b'')) as run:
                    try:
                        value.stop()
                    except ValueError:
                        pass
                self.assertEqual(coordination.reservation(self.folder).get('token'), token)
                run.assert_not_called()
                with coordination.gate(self.folder):
                    coordination.release(token, self.folder)

    def test_stop_during_worker_registration_cannot_launch_without_marker(self):
        value = self.viewer()
        real_thread = threading.Thread
        registering = threading.Event()
        continue_registration = threading.Event()
        stop_finished = threading.Event()
        launch_reservations, errors = [], []

        class PendingWorker:
            def __init__(self, **kwargs):
                registering.set()
                if not continue_registration.wait(3):
                    raise TimeoutError('Test did not release worker registration')

            def start(self):
                launch_reservations.append(coordination.reservation(value.folder).get('token'))

            def is_alive(self):
                return False

            def join(self, timeout=None):
                pass

        def start():
            try:
                value.start(SERIAL)
            except Exception as error:
                errors.append(error)

        def stop():
            try:
                value.stop()
            except ValueError:
                pass
            except Exception as error:
                errors.append(error)
            finally:
                stop_finished.set()

        starter, stopper = real_thread(target=start), real_thread(target=stop)
        acknowledged = dict(cleanup_confirmed=True, status='stopped', active=False)
        with patch.object(visualization.threading, 'Thread', PendingWorker), \
             patch.object(value, '_command', return_value=['mock-viz-stop']), \
             patch.object(visualization.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(acknowledged).encode(), stderr=b'')):
            starter.start()
            try:
                self.assertTrue(registering.wait(3))
                stopper.start()
                # A correctly serialized stop may wait for registration. The
                # assertion concerns the lease at launch, not elapsed time.
                stop_finished.wait(.15)
            finally:
                continue_registration.set()
                for worker in (starter, stopper):
                    if worker.ident is not None:
                        worker.join(4)
                        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(all(launch_reservations), 'Stop must not release the lease before a pending worker launches')

    def test_session_release_requires_the_matching_token(self):
        with coordination.gate(self.folder):
            token = coordination.reserve('viz', self.folder)
            coordination.release('another-token', self.folder)
            self.assertEqual(coordination.reservation(self.folder).get('token'), token)
            coordination.release(token, self.folder)
        self.assertEqual(coordination.reservation(self.folder), {})

    def test_dead_viz_owner_stays_reserved_but_dead_switch_does_not(self):
        with coordination.gate(self.folder):
            coordination.atomic(self.folder/'session.json', dict(kind='viz', pid=987654, start='gone', token='unconfirmed-child'))
        with patch.object(coordination, 'identity', return_value=None):
            self.assertEqual(coordination.reservation(self.folder).get('token'), 'unconfirmed-child')
            self.assertFalse(self.viewer().snapshot()['can_start'])
            coordination.atomic(self.folder/'session.json', dict(kind='switch', pid=987654, start='gone', token='dead-switch'))
            self.assertEqual(coordination.reservation(self.folder), {})

    def test_corrupt_marker_blocks_new_session_instead_of_becoming_idle(self):
        (self.folder/'session.json').write_text('{"kind":', encoding='utf-8')
        self.assertEqual(coordination.reservation(self.folder).get('kind'), 'unknown')
        with coordination.gate(self.folder), self.assertRaises(ValueError):
            coordination.reserve('viz', self.folder)

    def test_marker_blocks_new_control_but_allows_explicit_stops(self):
        with coordination.gate(self.folder):
            coordination.reserve('viz', self.folder)
        for name in ('connect', 'hardware_start', 'calibration_start', 'glove_open', 'runtime_select'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                coordination.enforce({'name': name}, self.folder)
        for name in ('disconnect', 'hardware_stop', 'calibration_cancel', 'glove_disconnect', 'group_stop'):
            coordination.enforce({'name': name}, self.folder)


if __name__ == '__main__':
    unittest.main()
