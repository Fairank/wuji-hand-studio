"""Software-only ownership/stream/shutdown checks; no official CLI is run."""
from contextlib import contextmanager, ExitStack, nullcontext
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import official_viz_runner as runner


class FakeProcess:
    def __init__(self, *, code=None, end_on='SIGINT', stdout='', stderr=''):
        self.pid = 4321
        self.returncode = code
        self.end_on = end_on
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self.actions = []
        self.waits = []

    def poll(self):
        return self.returncode

    def send_signal(self, number):
        self.actions.append(('signal', number))
        if self.end_on == 'SIGINT':
            self.returncode = 9

    def terminate(self):
        self.actions.append(('terminate',))
        if self.end_on == 'SIGTERM':
            self.returncode = -15

    def kill(self):
        self.actions.append(('kill',))
        if self.end_on == 'SIGKILL':
            self.returncode = -9

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.returncode is not None:
            return self.returncode
        threading.Event().wait(.001)
        raise subprocess.TimeoutExpired('software child', timeout)


class RunnerTests(unittest.TestCase):
    @contextmanager
    def fixture(self, directory, process):
        calls = []

        @contextmanager
        def lease(name):
            calls.append('acquire:' + name)
            try:
                yield
            finally:
                calls.append('release:' + name)

        def profile(**kwargs):
            self.assertIn(kwargs, ({}, {'selection': True}))
            return lease('selection' if kwargs else 'profile')

        def ownership(serial):
            self.assertEqual(serial, 'WG_TEST')
            return lease('device')

        def gate(path):
            path.mkdir(parents=True, exist_ok=True)
            return lease('directory')

        with ExitStack() as stack:
            stack.enter_context(patch.object(runner.sys, 'platform', 'linux'))
            stack.enter_context(patch.object(runner, '_uid', return_value=0))
            stack.enter_context(patch.object(runner, '_starttime', side_effect=lambda pid: str(pid * 10)))
            stack.enter_context(patch.object(runner, '_session_gate', side_effect=gate))
            stack.enter_context(patch.object(runner, '_stop_signals', side_effect=lambda stop: nullcontext()))
            stack.enter_context(patch.object(runner, '_parent_death_signal', return_value=lambda: None))
            stack.enter_context(patch('sdk_session.ProfileLease', side_effect=profile))
            stack.enter_context(patch('device_ownership.DeviceOwnership', side_effect=ownership))
            spawn = stack.enter_context(patch.object(runner.subprocess, 'Popen', return_value=process))
            stack.enter_context(patch.dict(sys.modules, {'wuji_sdk': None}))
            yield calls, spawn

    def execute(self, directory, process, *, text='', output=None):
        return runner.run(str(directory / 'wuji'), 'WG_TEST', str(directory),
                          stdin=io.StringIO(text), stdout=output if output is not None else io.StringIO())

    def events(self, output):
        return [row for line in output.getvalue().splitlines()
                if isinstance(row := json.loads(line), dict) and row.get('workbench_event')]

    def test_stdout_is_not_reformatted_and_stderr_has_a_separate_namespace(self):
        official = ' { "status" : "running", "extra" : [1, 2] }\n{"unknown_official_field":true}\n'
        process = FakeProcess(stdout=official, stderr='Stream: 60 Hz\nstream disconnected\n')
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            output = io.StringIO()
            with self.fixture(directory, process) as (calls, spawn):
                self.assertEqual(self.execute(directory, process, output=output), 9)
            lines = output.getvalue().splitlines(keepends=True)
            for line in official.splitlines(keepends=True):
                self.assertIn(line, lines)
            messages = [x['message'] for x in self.events(output) if x['workbench_event'] == 'stderr']
            self.assertEqual(messages, ['Stream: 60 Hz', 'stream disconnected'])
            args, kwargs = spawn.call_args
            self.assertEqual(args[0], [str(directory / 'wuji'), 'viz', '--sn', 'WG_TEST', '--no-zero', '--no-open', '--jsonl'])
            self.assertIs(kwargs['stdin'], subprocess.DEVNULL)
            self.assertIs(kwargs['stdout'], subprocess.PIPE)
            self.assertIs(kwargs['stderr'], subprocess.PIPE)
            self.assertTrue(kwargs['start_new_session'])
            self.assertTrue(callable(kwargs['preexec_fn']))
            self.assertEqual(kwargs['env']['WUJI_NO_UPDATE_CHECK'], '1')
            self.assertEqual(kwargs['env']['WUJI_TELEMETRY_DISABLE'], '1')
            self.assertEqual(calls[-4:], ['release:directory', 'release:device', 'release:selection', 'release:profile'])
            self.assertFalse((directory / 'session.json').exists())

    def test_eof_or_stop_interrupts_only_the_owned_child(self):
        for text in ('', 'stop\n', 'ignored\nstop\n'):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder)
                process = FakeProcess()
                output = io.StringIO()
                with self.fixture(directory, process):
                    self.assertEqual(self.execute(directory, process, text=text, output=output), 9)
                self.assertEqual(process.actions, [('signal', signal.SIGINT)])
                self.assertIsNotNone(process.poll())
                event = self.events(output)[-1]
                self.assertEqual(event['workbench_event'], 'exited')
                self.assertTrue(event['cleanup_confirmed'])
                self.assertEqual(event['sn'], 'WG_TEST')
                self.assertEqual(event['returncode'], 9)

    def test_metadata_records_runner_and_child_identity_before_ready(self):
        captured = []
        publish = runner._publish

        def inspect(directory, row):
            captured.append(dict(row))
            publish(directory, row)
            self.assertEqual(json.loads((directory / 'session.json').read_text(encoding='utf-8')), row)

        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            with self.fixture(directory, FakeProcess()), patch.object(runner, '_publish', side_effect=inspect):
                self.execute(directory, None)
        row = captured[0]
        self.assertEqual(row['pid'], os.getpid())
        self.assertEqual(row['starttime'], str(os.getpid() * 10))
        self.assertEqual(row['child_pid'], 4321)
        self.assertEqual(row['child_starttime'], '43210')
        self.assertEqual(row['uid'], 0)
        self.assertEqual(row['kind'], 'official_viz_runner')
        self.assertEqual(Path(row['runner']).name, 'official_viz_runner.py')
        self.assertRegex(row['token'], r'^[0-9a-f]{32}$')

    def test_exited_confirmation_follows_wait_metadata_and_all_lease_releases(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            process = FakeProcess()
            with self.fixture(directory, process) as (calls, _):
                test = self

                class AuditOutput(io.StringIO):
                    def write(self, value):
                        if '"workbench_event": "exited"' in value:
                            test.assertIsNotNone(process.poll())
                            test.assertIn(0, process.waits)
                            test.assertFalse((directory / 'session.json').exists())
                            test.assertEqual(calls[-4:], ['release:directory', 'release:device', 'release:selection', 'release:profile'])
                        return super().write(value)

                output = AuditOutput()
                self.execute(directory, process, output=output)
                self.assertTrue(self.events(output)[-1]['cleanup_confirmed'])

    def test_normal_nonzero_exit_is_preserved_without_signalling(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            process = FakeProcess(code=7)
            output = io.StringIO()
            with self.fixture(directory, process):
                self.assertEqual(self.execute(directory, process, output=output), 7)
            self.assertFalse(process.actions)
            self.assertEqual(self.events(output)[-1]['returncode'], 7)
            self.assertTrue(self.events(output)[-1]['cleanup_confirmed'])

    def test_escalation_waits_for_each_preceding_timeout(self):
        for end_on, actions in [('SIGTERM', ['signal', 'terminate']), ('SIGKILL', ['signal', 'terminate', 'kill'])]:
            with self.subTest(end_on=end_on):
                process = FakeProcess(end_on=end_on)
                code, stage = runner._interrupt_child(process)
                self.assertEqual(stage, end_on)
                self.assertEqual([x[0] for x in process.actions], actions)
                self.assertEqual(process.waits, [5, 2] if end_on == 'SIGTERM' else [5, 2, 1])
                self.assertIsNotNone(code)
        self.assertLessEqual(runner.INTERRUPT_TIMEOUT + runner.TERMINATE_TIMEOUT + runner.KILL_TIMEOUT, 8)

    def test_unconfirmed_kill_retains_session_and_emits_no_cleanup_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            process = FakeProcess(end_on='never')
            output = io.StringIO()
            with self.fixture(directory, process) as (calls, _):
                with self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
                    self.execute(directory, process, output=output)
            self.assertTrue((directory / 'session.json').exists())
            self.assertFalse(any(x.get('cleanup_confirmed') for x in self.events(output)))
            self.assertEqual([x[0] for x in process.actions], ['signal', 'terminate', 'kill'])
            self.assertEqual(calls[-1], 'release:profile')

    def test_existing_session_is_never_replaced_or_spawned(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            marker = directory / 'session.json'
            original = '{"pid": 123, "token": "existing-session"}'
            marker.write_text(original)
            with self.fixture(directory, FakeProcess()) as (_, spawn):
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    self.execute(directory, None)
                spawn.assert_not_called()
            self.assertEqual(marker.read_text(), original)

    def test_publish_failure_stops_child_and_releases_every_lease(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            process = FakeProcess()
            output = io.StringIO()
            with self.fixture(directory, process) as (calls, _), patch.object(runner, '_publish', side_effect=OSError('disk unavailable')):
                with self.assertRaisesRegex(OSError, 'disk unavailable'):
                    self.execute(directory, process, output=output)
            self.assertIsNotNone(process.poll())
            self.assertEqual(calls[-1], 'release:profile')
            self.assertTrue(self.events(output)[-1]['cleanup_confirmed'])

    def test_foreign_token_is_not_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            path = directory / 'session.json'
            path.write_text('{"token":"other-session"}')
            self.assertFalse(runner._remove_own_record(directory, 'our-session'))
            self.assertTrue(path.exists())

    def test_atomic_publish_failure_leaves_no_partial_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            with patch.object(runner.os, 'replace', side_effect=OSError('interrupted publish')):
                with self.assertRaises(OSError):
                    runner._publish(directory, {'token': 'synthetic-token'})
            self.assertFalse(list(directory.iterdir()))

    def test_invalid_arguments_never_start_a_process(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            with self.fixture(directory, FakeProcess()) as (_, spawn):
                for cli, sn, session in [('relative', 'WG_TEST', str(directory)),
                                         (str(directory / 'wuji'), 'WG_TEST', 'relative'),
                                         (str(directory / 'wuji'), '--all', str(directory)),
                                         (str(directory / 'wuji'), 'WG;commands', str(directory)),
                                         (str(directory / 'wuji'), 'WG\nother', str(directory))]:
                    with self.subTest(cli=cli, sn=sn, session=session), self.assertRaises(ValueError):
                        runner.run(cli, sn, session, stdin=io.StringIO(), stdout=io.StringIO())
                spawn.assert_not_called()

    def test_profile_selection_lease_failure_prevents_child_start(self):
        calls = []

        @contextmanager
        def shared():
            calls.append('acquire')
            try:
                yield
            finally:
                calls.append('release')

        def profile(**kwargs):
            if kwargs:
                raise ValueError('selection busy')
            return shared()

        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            with self.fixture(directory, FakeProcess()) as (_, spawn), patch('sdk_session.ProfileLease', side_effect=profile):
                with self.assertRaisesRegex(ValueError, 'selection busy'):
                    self.execute(directory, None)
                spawn.assert_not_called()
        self.assertEqual(calls, ['acquire', 'release'])

    def test_proc_starttime_uses_final_comm_parenthesis(self):
        fields = ['S'] + ['0'] * 18 + ['987654'] + ['0'] * 4
        stat = '12 (runner ) with spaces) ' + ' '.join(fields)
        with patch.object(Path, 'read_text', return_value=stat):
            self.assertEqual(runner._starttime(12), '987654')

    def test_runner_signal_requests_stop_and_restores_previous_handlers(self):
        stop = threading.Event()
        previous = {signal.SIGINT: object(), signal.SIGTERM: object()}
        current = dict(previous)

        def install(number, handler):
            old = current[number]
            current[number] = handler
            return old

        with patch.object(runner.signal, 'signal', side_effect=install):
            with runner._stop_signals(stop):
                current[signal.SIGTERM](signal.SIGTERM, None)
                self.assertTrue(stop.is_set())
            self.assertEqual(current, previous)

    def test_parent_death_setup_preloads_libc_and_rejects_lost_parent(self):
        import ctypes
        for result, parent_matches in [(0, True), (0, False), (-1, True)]:
            with self.subTest(result=result, parent_matches=parent_matches):
                libc = Mock()
                libc.prctl.return_value = result
                libc.getppid.return_value = os.getpid() if parent_matches else os.getpid() + 1
                with patch.object(ctypes, 'CDLL', return_value=libc), patch.object(runner.signal, 'SIGKILL', 9, create=True):
                    prepare = runner._parent_death_signal()
                libc.prctl.assert_not_called()
                prepare()
                libc.prctl.assert_called_once_with(1, 9, 0, 0, 0)
                if result != 0 or not parent_matches:
                    libc._exit.assert_called_once_with(127)
                else:
                    libc._exit.assert_not_called()

    def test_eof_reaps_an_actual_python_child_before_confirmation(self):
        # The child is only a sleeping Python process. On Windows the signal
        # transport is translated to terminate; Linux uses the real SIGINT.
        real_popen = subprocess.Popen
        children = []
        code = 'import time; time.sleep(30)'
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            with self.fixture(directory, FakeProcess()) as (calls, spawn):
                def launch(argv, **kwargs):
                    kwargs.pop('preexec_fn')
                    if os.name == 'nt':
                        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
                    child = real_popen([sys.executable, '-u', '-c', code], **kwargs)
                    children.append(child)
                    if os.name == 'nt':
                        child.send_signal = lambda number: child.terminate()
                    return child

                spawn.side_effect = launch
                test = self

                class AuditOutput(io.StringIO):
                    def write(self, value):
                        if '"cleanup_confirmed": true' in value:
                            test.assertIsNotNone(children[0].poll())
                            test.assertEqual(calls[-1], 'release:profile')
                            test.assertFalse((directory / 'session.json').exists())
                        return super().write(value)

                try:
                    output = AuditOutput()
                    read_fd, write_fd = os.pipe()
                    os.close(write_fd)
                    with os.fdopen(read_fd, 'r', encoding='utf-8') as actual_eof:
                        runner.run(str(directory / 'wuji'), 'WG_TEST', str(directory),
                                   stdin=actual_eof, stdout=output)
                    self.assertEqual(len(children), 1)
                    self.assertIsNotNone(children[0].poll())
                    self.assertTrue(self.events(output)[-1]['cleanup_confirmed'])
                finally:
                    for child in children:
                        if child.poll() is None:
                            child.kill()
                        child.wait(timeout=3)

    def test_main_errors_are_workbench_records_with_message(self):
        output = io.StringIO()
        with patch.object(runner, 'run', side_effect=ValueError('synthetic failure')), patch.object(runner.sys, 'stdout', output):
            self.assertEqual(runner.main(['--cli', '/synthetic/wuji', '--sn', 'WG_TEST', '--session-dir', '/synthetic/session']), 1)
        self.assertEqual(json.loads(output.getvalue()), {'workbench_event': 'error', 'message': 'synthetic failure'})


if __name__ == '__main__':
    unittest.main()
