"""Fully mocked native CLI/Job contract tests; never touch a device or network."""
from contextlib import contextmanager, ExitStack
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import calibration_cli
import cli_coordination
import native_calibration as native
import native_cli


SN = 'WG_SYNTHETIC_LEFT'
TOKEN = 'a' * 32
COMPONENT = {'version': '2026.10.12', 'binary': 'C:/synthetic/wuji.exe'}
PROFILE = {'name': 'Demo', 'is_default': False, 'left_hand': {'calibrated': False},
           'right_hand': {'calibrated': False}}


@contextmanager
def temporary_folder():
    root = Path(__file__).resolve().parents[3]/'outputs/cli-workspace-0230/backend-review/test-temp'
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as folder:
        yield folder


class DeferredThread:
    def __init__(self, target=None, args=(), **_kwargs):
        self.target, self.args, self.started = target, args, False
    def start(self):
        self.started = True
    def is_alive(self):
        return self.started
    def join(self, timeout=None):
        pass


class FakeProcess:
    def __init__(self, events=(), worker_code=0):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(''.join(json.dumps(event) + '\n' for event in events))
        self.stderr = io.StringIO()
        self.worker_code = worker_code
        self.returncode = None
    def poll(self):
        return self.returncode
    def wait(self, timeout=None):
        self.returncode = self.worker_code
        return self.worker_code


class NativeTests(unittest.TestCase):
    @contextmanager
    def environment(self, profile=None, events=(), worker_code=0, clean=True):
        with temporary_folder() as folder, ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, WUJI_OFFICIAL_CLI_DATA=folder))
            # Both implementations must agree on owner identity, without asking
            # Windows to inspect processes or ever opening a real process Job.
            identity = lambda pid: 'live' if pid == os.getpid() else None
            stack.enter_context(patch.object(cli_coordination, 'identity', side_effect=identity))
            stack.enter_context(patch.object(native, 'identity', side_effect=identity))
            prepare = stack.enter_context(patch.object(native_cli, 'prepare', return_value=copy.deepcopy(COMPONENT)))
            query = stack.enter_context(patch.object(native_cli, 'json_command', return_value=copy.deepcopy(PROFILE if profile is None else profile)))
            process = FakeProcess(events, worker_code)
            job = SimpleNamespace(process=process, close=Mock(return_value=clean),
                                  terminate_and_wait=Mock(return_value=clean))
            spawn = stack.enter_context(patch('windows_process_job.OwnedProcess', return_value=job))
            stack.enter_context(patch.object(native.threading, 'Thread', DeferredThread))
            owner = SimpleNamespace(lock=threading.RLock(), metadata_check=Mock(),
                                    run={'running': False, 'status': 'idle'}, process=None,
                                    thread=None, cache=None, reports=Path(folder)/'reports')
            owner.snapshot = Mock(side_effect=lambda **_: copy.deepcopy(owner.run))
            service = native.NativeCalibration(owner)
            yield SimpleNamespace(folder=Path(folder), owner=owner, service=service, job=job,
                                  process=process, prepare=prepare, query=query, spawn=spawn)

    def test_host_preflight_starts_original_human_journey_without_profile_calls(self):
        with self.environment() as env:
            state = env.service.start_human(SN, 'left', 900)
            env.prepare.assert_called_once_with()
            env.query.assert_not_called()
            env.owner.metadata_check.assert_called_once_with()
            marker = cli_coordination.reservation(env.folder)
            args, kwargs = env.spawn.call_args
            self.assertEqual(marker['kind'], 'calibration')
            self.assertEqual(marker['serial'], SN)
            self.assertEqual(kwargs['job_name'], 'Local\\HandWorkbench-OfficialCalibration-' + marker['token'])
            self.assertEqual(args[0][-5:], ['--native-calibration-worker', marker['token'], SN, 'left', '900'])
            self.assertEqual(state['source'], 'official_tty')
            self.assertTrue(state['terminal'])
            self.assertEqual(state['status'], 'starting')
            self.assertIsNone(state['result'])
            self.assertIsNone(state['user'])
            self.assertIsNone(state['side'])

    def test_full_idle_failure_never_prepares_or_spawns(self):
        with self.environment() as env:
            env.owner.metadata_check.side_effect = ValueError('another workspace is active')
            with self.assertRaisesRegex(ValueError, 'active'):
                env.service.start_human(SN, 'left', 900)
            env.prepare.assert_not_called(); env.query.assert_not_called(); env.spawn.assert_not_called()

    def test_default_unknown_and_existing_models_are_owned_by_original_human_prompts(self):
        for profile in ({'name':'Default','is_default':True}, {}, None,
                        {'name':'Demo','left_hand':{'calibrated':True}}):
            with self.subTest(profile=profile), self.environment(profile=profile) as env:
                env.service.start_human(SN)
                env.query.assert_not_called()
                self.assertEqual(env.spawn.call_args.args[0][-5:],
                    ['--native-calibration-worker', env.service.token, SN, 'auto', '900'])
                self.assertIsNone(env.owner.run['user'])
                self.assertIsNone(env.owner.run['side'])

    def test_invalid_human_identity_options_never_prepare_or_reserve(self):
        for args in [(None,), ('',), ('bad serial',), (SN,'unknown'), (SN,'LEFT'),
                     (SN,'left',True), (SN,None,59), (SN,None,1801)]:
            with self.subTest(args=args), self.environment() as env:
                with self.assertRaises(ValueError):env.service.start_human(*args)
                env.prepare.assert_not_called();env.query.assert_not_called();env.spawn.assert_not_called()
                self.assertEqual(cli_coordination.reservation(env.folder), {})

    def test_ready_displays_verified_side_without_claiming_user_or_model_success(self):
        with self.environment(events=[{'type':'ready','source':'official_tty','serial':SN,'side':'right'},
                                      {'type':'exit','exitstatus':0}]) as env:
            env.service.start_human(SN)
            env.service._collect()
            self.assertEqual(env.owner.run['side'], 'right')
            self.assertIsNone(env.owner.run['user'])
            self.assertIsNone(env.owner.run['result'])
            self.assertEqual(env.owner.run['status'], 'session_ended')

    def test_wrong_ready_identity_is_error_not_a_verified_session(self):
        for event in [{'type':'ready','serial':'WG_OTHER','side':'left'},
                      {'type':'ready','serial':SN,'side':'right'},
                      {'type':'ready','serial':SN,'side':None}]:
            with self.subTest(event=event), self.environment(events=[event]) as env:
                env.service.start_human(SN, 'left')
                env.service._collect()
                self.assertEqual(env.owner.run['status'], 'error')
                self.assertIsNone(env.owner.run['side'])
                self.assertIsNone(env.owner.run['user'])

    def test_original_default_user_exit_is_failure_without_retry_or_invented_owner(self):
        with self.environment(events=[{'type':'output','text':'Default user protection'},
                                      {'type':'exit','exitstatus':5}]) as env:
            env.service.start_human(SN)
            env.service._collect()
            self.assertEqual(env.owner.run['status'], 'error')
            self.assertEqual(env.owner.run['exit_code'], 5)
            self.assertFalse(env.owner.run['running'])
            self.assertIsNone(env.owner.run['user'])
            self.assertIsNone(env.owner.run['result'])
            self.assertEqual(cli_coordination.reservation(env.folder), {})
            env.prepare.assert_called_once()
            env.query.assert_not_called()
            env.spawn.assert_called_once()

    def test_spawn_error_releases_only_own_reservation(self):
        with self.environment() as env:
            env.spawn.side_effect = OSError('synthetic job assignment failure')
            with self.assertRaisesRegex(OSError, 'assignment'):
                env.service.start_human(SN, 'left', 900)
            self.assertEqual(cli_coordination.reservation(env.folder), {})
            self.assertIsNone(env.service.token)

    def test_tty_zero_exit_is_session_ended_and_never_calibration_success(self):
        with self.environment(events=[{'type': 'output', 'text': '\x1b[31mCalibration finished\x1b[0m'},
                                      {'type': 'exit', 'exitstatus': 0}]) as env:
            env.service.start_human(SN, 'left', 900)
            env.service._collect()
            self.assertEqual(env.owner.run['status'], 'session_ended')
            self.assertFalse(env.owner.run['running'])
            self.assertIsNone(env.owner.run['result'])
            self.assertTrue(env.owner.run['cleanup_confirmed'])
            self.assertEqual(cli_coordination.reservation(env.folder), {})
            report = json.loads(next(env.owner.reports.glob('*.json')).read_text())
            self.assertEqual(report['status'], 'session_ended')

    def test_final_report_keeps_completed_run_and_transcript_if_next_run_starts(self):
        with self.environment(events=[{'type': 'output', 'text': 'OLD_SESSION_TERMINAL'},
                                      {'type': 'exit', 'exitstatus': 0}]) as env:
            env.service.start_human(SN, 'left', 900)
            old_id = env.owner.run['id']
            mkdir = Path.mkdir

            def next_run_before_report_io(path, *args, **kwargs):
                result = mkdir(path, *args, **kwargs)
                if path == env.owner.reports:
                    # This interleaving is reachable after the old session's
                    # marker is released and its running flag is false.
                    self.assertEqual(cli_coordination.reservation(env.folder), {})
                    self.assertFalse(env.owner.run['running'])
                    env.owner.run = {'running': True, 'status': 'starting',
                                     'id': 'NEW_SESSION_ID', 'result': None}
                    env.service.frames.clear()
                    env.service.frames.append((99, 'NEW_SESSION_TERMINAL'))
                return result

            with patch.object(Path, 'mkdir', next_run_before_report_io):
                env.service._collect()
            files = {path.name for path in env.owner.reports.iterdir()}
            self.assertEqual(files, {old_id + '.json', old_id + '-terminal.ansi'})
            report = json.loads((env.owner.reports/(old_id + '.json')).read_text(encoding='utf-8'))
            self.assertEqual(report['id'], old_id)
            self.assertEqual(report['status'], 'session_ended')
            self.assertFalse(report['running'])
            self.assertIsNone(report['result'])
            self.assertEqual((env.owner.reports/(old_id + '-terminal.ansi')).read_text(encoding='utf-8'),
                             'OLD_SESSION_TERMINAL')
            self.assertEqual(env.owner.run['id'], 'NEW_SESSION_ID')

    def test_eof_error_missing_status_and_worker_failure_never_complete(self):
        cases = [([], 0), ([{'type': 'exit', 'exitstatus': None}], 0),
                 ([{'type': 'error', 'message': 'synthetic transport failure'}, {'type': 'exit', 'exitstatus': 0}], 0),
                 ([{'type': 'exit', 'exitstatus': 0}], 1),
                 ([{'type': 'exit', 'exitstatus': True}], 0)]
        for events, code in cases:
            with self.subTest(events=events, code=code), self.environment(events=events, worker_code=code) as env:
                env.service.start_human(SN, 'left', 900)
                env.service._collect()
                self.assertEqual(env.owner.run['status'], 'error')
                self.assertIsNone(env.owner.run['result'])

    def test_unconfirmed_job_cleanup_keeps_session_reserved_and_running(self):
        with self.environment(events=[{'type': 'exit', 'exitstatus': 0}], clean=False) as env:
            env.service.start_human(SN, 'left', 900)
            token = env.service.token
            env.service._collect()
            self.assertTrue(env.owner.run['running'])
            self.assertFalse(env.owner.run['cleanup_confirmed'])
            self.assertEqual(env.owner.run['status'], 'error')
            self.assertEqual(cli_coordination.reservation(env.folder)['token'], token)

    def test_input_rejects_old_run_and_changed_owner_without_writing(self):
        with self.environment() as env:
            env.service.start_human(SN, 'left', 900)
            with self.assertRaisesRegex(ValueError, 'no longer active'):
                env.service.input({'run_id': 'old-run', 'type': 'write', 'text': 'x'})
            self.assertEqual(env.process.stdin.getvalue(), '')
            marker = cli_coordination.reservation(env.folder)
            marker['token'] = 'b' * 32
            cli_coordination.atomic(env.folder/'session.json', marker)
            with self.assertRaisesRegex(ValueError, 'ownership changed'):
                env.service.input({'run_id': env.owner.run['id'], 'type': 'write', 'text': 'x'})
            self.assertEqual(env.process.stdin.getvalue(), '')

    def test_input_bounds_and_unicode_forwarding(self):
        with self.environment() as env:
            env.service.start_human(SN, 'left', 900)
            run_id = env.owner.run['id']
            env.service.input({'run_id': run_id, 'type': 'write', 'text': '\x1b[A中文\r'})
            env.service.input({'run_id': run_id, 'type': 'resize', 'rows': 38, 'cols': 100})
            records = [json.loads(x) for x in env.process.stdin.getvalue().splitlines()]
            self.assertEqual(records, [{'type': 'write', 'text': '\x1b[A中文\r'},
                                       {'type': 'resize', 'rows': 38, 'cols': 100}])
            for payload in [{'type': 'write', 'text': ''}, {'type': 'write', 'text': '\0'},
                            {'type': 'write', 'text': 'x' * 4097}, {'type': 'resize', 'rows': True, 'cols': 100},
                            {'type': 'resize', 'rows': 37, 'cols': 100}, {'type': 'resize', 'rows': 38, 'cols': 241},
                            {'type': 'cancel'}]:
                with self.subTest(payload=str(payload)[:80]), self.assertRaises(ValueError):
                    env.service.input(dict(run_id=run_id, **payload))

    def test_recovery_is_exact_job_and_never_kills_live_owner(self):
        with self.environment() as env, patch('windows_process_job.recover_viz_job', return_value=True) as recover:
            marker = {'kind': 'calibration', 'backend': 'windows_native', 'pid': 987654,
                      'start': 'old', 'token': TOKEN,
                      'job_name': 'Local\\HandWorkbench-OfficialCalibration-' + TOKEN}
            cli_coordination.atomic(env.folder/'session.json', marker)
            env.service.recover()
            recover.assert_called_once_with(marker['job_name'], timeout=7)
            self.assertEqual(cli_coordination.reservation(env.folder), {})
            recover.reset_mock()
            marker.update(pid=os.getpid(), start='live')
            cli_coordination.atomic(env.folder/'session.json', marker)
            with self.assertRaisesRegex(ValueError, 'owning workspace'):
                env.service.recover()
            recover.assert_not_called()

    def test_recovery_rejects_wrong_job_or_unconfirmed_cleanup(self):
        for wrong, result in [(True, True), (False, False)]:
            with self.subTest(wrong=wrong), self.environment() as env, \
                    patch('windows_process_job.recover_viz_job', return_value=result) as recover:
                marker = {'kind': 'calibration', 'backend': 'windows_native', 'pid': 987654,
                          'start': 'old', 'token': TOKEN,
                          'job_name': 'Local\\HandWorkbench-OfficialCalibration-' + ('b' * 32 if wrong else TOKEN)}
                cli_coordination.atomic(env.folder/'session.json', marker)
                with self.assertRaisesRegex(ValueError, 'unconfirmed'):
                    env.service.recover()
                self.assertEqual(cli_coordination.reservation(env.folder)['token'], TOKEN)
                if wrong:recover.assert_not_called()

    def test_cancel_after_completed_native_session_recovers_dead_foreign_owner(self):
        with self.environment(events=[{'type': 'exit', 'exitstatus': 0}]) as env:
            env.service.start_human(SN, 'left', 900)
            env.service._collect()
            env.owner.native = env.service
            self.assertIsNone(env.service.token)
            self.assertFalse(env.owner.run['running'])
            marker = {'kind': 'calibration', 'backend': 'windows_native', 'pid': 987654,
                      'start': 'dead-foreign-owner', 'token': 'b' * 32,
                      'job_name': 'Local\\HandWorkbench-OfficialCalibration-' + 'b' * 32}
            cli_coordination.atomic(env.folder/'session.json', marker)
            with patch('windows_process_job.recover_viz_job', return_value=True) as recover, \
                    patch.object(env.service, 'cancel') as old_cancel:
                calibration_cli.CalibrationCLI.cancel(env.owner)
            old_cancel.assert_not_called()
            recover.assert_called_once_with(marker['job_name'], timeout=7)
            self.assertEqual(cli_coordination.reservation(env.folder), {})

    def test_cancel_after_completed_native_session_cannot_kill_live_foreign_owner(self):
        with self.environment(events=[{'type': 'exit', 'exitstatus': 0}]) as env:
            env.service.start_human(SN, 'left', 900)
            env.service._collect()
            env.owner.native = env.service
            marker = {'kind': 'calibration', 'backend': 'windows_native', 'pid': 987654,
                      'start': 'live-foreign-owner', 'token': 'b' * 32,
                      'job_name': 'Local\\HandWorkbench-OfficialCalibration-' + 'b' * 32}
            cli_coordination.atomic(env.folder/'session.json', marker)
            with patch.object(native, 'identity', return_value='live-foreign-owner'), \
                    patch('windows_process_job.recover_viz_job') as recover, \
                    patch.object(env.service, 'cancel') as old_cancel:
                with self.assertRaisesRegex(ValueError, 'owning workspace'):
                    calibration_cli.CalibrationCLI.cancel(env.owner)
            old_cancel.assert_not_called()
            recover.assert_not_called()
            self.assertEqual(cli_coordination.reservation(env.folder), marker)

    def test_cancel_after_completed_native_session_retains_unconfirmed_dead_job(self):
        with self.environment(events=[{'type': 'exit', 'exitstatus': 0}]) as env:
            env.service.start_human(SN, 'left', 900)
            env.service._collect()
            env.owner.native = env.service
            marker = {'kind': 'calibration', 'backend': 'windows_native', 'pid': 987654,
                      'start': 'dead-foreign-owner', 'token': 'b' * 32,
                      'job_name': 'Local\\HandWorkbench-OfficialCalibration-' + 'b' * 32}
            cli_coordination.atomic(env.folder/'session.json', marker)
            with patch('windows_process_job.recover_viz_job', return_value=False) as recover:
                with self.assertRaisesRegex(ValueError, 'unconfirmed'):
                    calibration_cli.CalibrationCLI.cancel(env.owner)
            recover.assert_called_once_with(marker['job_name'], timeout=7)
            self.assertEqual(cli_coordination.reservation(env.folder), marker)


class WorkerPreflightTests(unittest.TestCase):
    @contextmanager
    def environment(self, hand=None, contained=True, marker_override=None):
        with temporary_folder() as folder, ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, WUJI_OFFICIAL_CLI_DATA=folder))
            output = io.StringIO()
            stack.enter_context(patch.object(native, '_attach_pipes'))
            stack.enter_context(patch.object(native.sys, 'stdout', output))
            job = stack.enter_context(patch('windows_process_job.current_in_named_job', return_value=contained))
            prepare = stack.enter_context(patch.object(native_cli, 'prepare', return_value=copy.deepcopy(COMPONENT)))
            query = stack.enter_context(patch.object(native_cli, 'json_command', return_value=
                {'value':'left','sn':SN} if hand is None else hand))
            tty = stack.enter_context(patch('native_terminal_worker.run', return_value=0))
            stack.enter_context(patch.object(native, 'identity', return_value='live'))
            marker = dict(kind='calibration', token=TOKEN, pid=os.getpid(), start='live',
                          job_name='Local\\HandWorkbench-OfficialCalibration-' + TOKEN,
                          cli_version='2026.10.12',serial=SN,side='left')
            marker.update(marker_override or {})
            cli_coordination.atomic(Path(folder)/'session.json', marker)
            yield SimpleNamespace(output=output, job=job, query=query, prepare=prepare, tty=tty)

    def arguments(self, side='left'):
        return [TOKEN, SN, side, '900']

    def test_job_and_typed_side_precede_unmodified_original_tty_with_one_sn(self):
        with self.environment() as env:
            self.assertEqual(native.worker_main(self.arguments()), 0)
            env.job.assert_called_once_with('Local\\HandWorkbench-OfficialCalibration-' + TOKEN)
            env.query.assert_called_once_with('get', 'hand_side', '--sn', SN)
            env.tty.assert_called_once_with([COMPONENT['binary'], 'calib', 'hand-model', '--sn', SN, '--timeout-s', '900'])
            ready = json.loads(env.output.getvalue())
            self.assertEqual(ready, {'type':'ready','source':'official_tty','serial':SN,'side':'left'})
            self.assertNotIn('user', ready)
            argv=env.tty.call_args.args[0]
            self.assertEqual(argv.count('--sn'), 1)
            for flag in ['--json','--jsonl','--handedness','--non-interactive','--yes','--force']:
                self.assertNotIn(flag, argv)

    def test_worker_outside_job_or_changed_ownership_never_starts_tty(self):
        for contained, override in [(False, {}), (True, {'token': 'b' * 32}),
                                     (True, {'job_name': 'Local\\Other'}), (True, {'start': 'old'}),
                                     (True, {'serial':'WG_OTHER'}), (True, {'side':None})]:
            with self.subTest(contained=contained, override=override), \
                    self.environment(contained=contained, marker_override=override) as env:
                self.assertEqual(native.worker_main(self.arguments()), 1)
                env.query.assert_not_called(); env.prepare.assert_not_called(); env.tty.assert_not_called()
                self.assertEqual(json.loads(env.output.getvalue())['type'], 'error')

    def test_wrong_missing_numeric_nested_and_unknown_side_never_start_tty(self):
        for hand in [{}, {'value': 'right'}, {'value': 0}, {'value': True}, {'value': 'LEFT'},
                     {'value': 'Handedness.LEFT'}, {'data': {'value': 'left'}},
                     {'value': 'left', 'sn': 'WG_OTHER'}, {'value': 'left', 'device': {'sn': 'WG_OTHER'}},
                     {'value':'left','device':['unexpected']}]:
            with self.subTest(hand=hand), self.environment(hand=hand) as env:
                self.assertEqual(native.worker_main(self.arguments()), 1)
                env.prepare.assert_not_called(); env.tty.assert_not_called()

    def test_unknown_discovery_side_accepts_only_actual_typed_left_or_right(self):
        for side in ['left','right']:
            with self.subTest(side=side), self.environment(hand={'value':side,'sn':SN},marker_override={'side':None}) as env:
                self.assertEqual(native.worker_main(self.arguments('auto')), 0)
                self.assertEqual(json.loads(env.output.getvalue())['side'], side)
                env.query.assert_called_once_with('get','hand_side','--sn',SN)
                env.tty.assert_called_once()
        for value in [None,0,1,True,'LEFT','Handedness.RIGHT','unknown']:
            with self.subTest(value=value), self.environment(hand={'value':value},marker_override={'side':None}) as env:
                self.assertEqual(native.worker_main(self.arguments('auto')), 1)
                env.tty.assert_not_called()

    def test_pinned_cli_version_must_match_recorded_host_before_tty(self):
        with self.environment(marker_override={'cli_version': '2099.1.1'}) as env:
            self.assertEqual(native.worker_main(self.arguments()), 1)
            env.tty.assert_not_called()

    def test_bad_internal_envelope_never_queries_or_starts_anything(self):
        for args in [[], self.arguments()+['extra'], ['bad-token', *self.arguments()[1:]],
                     [TOKEN,SN,'unknown','900'], [TOKEN,SN,'left','Demo','keep','900']]:
            with self.subTest(args=args), self.environment() as env:
                self.assertEqual(native.worker_main(args), 1)
                env.job.assert_not_called();env.query.assert_not_called();env.prepare.assert_not_called();env.tty.assert_not_called()


class NativeMetadataTests(unittest.TestCase):
    def test_human_metadata_snapshot_never_starts_a_parallel_user_journey(self):
        config = {'mode': 'wsl', 'cli': 'wuji'}
        def query(*args):
            if args == ('user', 'list'):
                return {'users': [{'name': 'Demo', 'is_default': False}]}
            if args == ('user', 'show'):
                return copy.deepcopy(PROFILE)
            self.fail('Unexpected native command: ' + repr(args))
        with temporary_folder() as folder, patch.dict(os.environ, WUJI_OFFICIAL_CLI_DATA=folder), \
                patch.object(calibration_cli, 'load_config', return_value=config), \
                patch('official_device_discovery.use_native', return_value=True), \
                patch.object(native_cli, 'prepare', return_value=COMPONENT), \
                patch.object(native_cli, 'json_command', side_effect=query) as call, \
                patch.object(calibration_cli, 'local_run') as linux, \
                patch.object(calibration_cli, 'runner_command') as wrapper:
            service = calibration_cli.CalibrationCLI(device_provider=lambda fresh=False: [])
            state = service.snapshot(refresh=True)
            self.assertTrue(state['metadata_current'])
            self.assertEqual(state['runtime']['backend'], 'windows_native')
            self.assertEqual(state['runtime']['version'], '2026.10.12')
            self.assertEqual(state['guide'], 'official_tty')
            self.assertTrue(state['human_tty'])
            self.assertIsNone(state['current'])
            self.assertEqual(state['users'], [])
            call.assert_not_called()
            linux.assert_not_called(); wrapper.assert_not_called()

    def test_native_json_metadata_uses_verified_binary_and_keeps_profile_environment(self):
        process = Mock()
        process.communicate.return_value = (json.dumps(PROFILE), '')
        process.returncode = 0
        context = Mock()
        context.__enter__ = Mock(return_value=SimpleNamespace(process=process))
        context.__exit__ = Mock(return_value=False)
        with temporary_folder() as folder, patch.dict(os.environ, WUJI_OFFICIAL_CLI_DATA=folder,
                HOME='C:/synthetic-profile-home', USERPROFILE='C:/synthetic-profile-home'), \
                patch.object(native_cli, 'prepare', return_value=COMPONENT) as prepare, \
                patch('windows_process_job.OwnedProcess', return_value=context) as owned:
            self.assertEqual(native_cli.json_command('user', 'show'), PROFILE)
            prepare.assert_called_once_with()
            args, kwargs = owned.call_args
            self.assertEqual(args[0], [COMPONENT['binary'], '--json', 'user', 'show'])
            self.assertEqual(kwargs['env']['HOME'], 'C:/synthetic-profile-home')
            self.assertEqual(kwargs['env']['USERPROFILE'], 'C:/synthetic-profile-home')
            self.assertEqual(kwargs['env']['WUJI_NO_UPDATE_CHECK'], '1')
            process.communicate.assert_called_once_with(timeout=20)


if __name__ == '__main__':
    unittest.main()
