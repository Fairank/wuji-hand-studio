"""Synthetic /proc and session recovery checks; never signal real processes."""
from contextlib import contextmanager, ExitStack, nullcontext
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import official_viz_recovery as recovery
import official_viz_runner as runner


class RecoveryTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        real_path = Path
        uid = os.getuid() if hasattr(os, 'getuid') else 0
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directory = root / 'session'
            directory.mkdir()
            proc = root / 'proc'
            proc.mkdir()
            row = dict(kind='official_viz_runner', pid=1234, starttime='100', uid=uid,
                       runner=str(Path(runner.__file__).resolve()),
                       cli=str(root / 'synthetic-cli'), sn='SYNTHETIC_QA',
                       session_dir=str(directory), token='a' * 32,
                       child_pid=1235, child_starttime='101')
            directory.joinpath('session.json').write_text(json.dumps(row), encoding='utf-8')
            signals = []
            descriptors = {}
            race = {'reuse': False, 'gone': False}

            def process(pid, start, argv, state='S'):
                target = proc / str(pid)
                target.mkdir(exist_ok=True)
                fields = [state] + ['0'] * 18 + [start] + ['0'] * 4
                (target / 'stat').write_text(str(pid) + ' (python with ) in name) ' + ' '.join(fields), encoding='utf-8')
                (target / 'cmdline').write_bytes(('\0'.join(argv) + '\0').encode('utf-8'))
                (target / 'status').write_text('Uid:\t' + '\t'.join([str(uid)] * 4) + '\n', encoding='utf-8')

            runner_argv = [sys.executable, '-u', row['runner'], '--cli', row['cli'],
                           '--sn', row['sn'], '--session-dir', row['session_dir']]
            child_argv = [row['cli'], 'viz', '--sn', row['sn'], '--no-zero', '--no-open', '--jsonl']
            process(row['pid'], row['starttime'], runner_argv)
            process(row['child_pid'], row['child_starttime'], child_argv)

            def dead(pid):
                process(pid, row['starttime'] if pid == row['pid'] else row['child_starttime'],
                        runner_argv if pid == row['pid'] else child_argv, 'Z')

            def deliver(pid, number):
                signals.append((pid, number))
                dead(pid)
                if pid == row['pid']:
                    dead(row['child_pid'])

            def numeric_signal(pid, number):
                if race['reuse']:
                    self.fail('A bare PID signal can hit a process reused after the identity check')
                if race['gone']:
                    dead(pid)
                    dead(row['child_pid'])
                    raise ProcessLookupError('synthetic process already exited')
                deliver(pid, number)

            def pidfd_open(pid, flags=0):
                if race['gone']:
                    dead(pid)
                    dead(row['child_pid'])
                    raise ProcessLookupError('synthetic process already exited')
                handle = os.open(os.devnull, os.O_RDONLY)
                descriptors[handle] = pid
                if race['reuse']:
                    argv = runner_argv if pid == row['pid'] else child_argv
                    process(pid, 'reused-start-time', argv)
                return handle

            def pidfd_signal(handle, number, *args):
                deliver(descriptors[handle], number)

            def path(value):
                return proc if str(value) == '/proc' else real_path(value)

            with ExitStack() as stack:
                stack.enter_context(patch.object(recovery, 'Path', side_effect=path))
                stack.enter_context(patch.object(recovery.os, 'getuid', return_value=uid, create=True))
                stack.enter_context(patch.object(recovery.os, 'kill', side_effect=numeric_signal))
                stack.enter_context(patch.object(recovery.os, 'pidfd_open', side_effect=pidfd_open, create=True))
                stack.enter_context(patch.object(recovery.signal, 'pidfd_send_signal', side_effect=pidfd_signal, create=True))
                stack.enter_context(patch.object(recovery.signal, 'SIGKILL', 9, create=True))
                stack.enter_context(patch.object(runner, '_session_gate', return_value=nullcontext()))
                try:
                    yield dict(root=root, directory=directory, row=row, proc=proc, uid=uid,
                               signals=signals, race=race, process=process, dead=dead,
                               runner_argv=runner_argv, child_argv=child_argv)
                finally:
                    for handle in descriptors:
                        try:
                            os.close(handle)
                        except OSError:
                            pass

    def test_process_identity_requires_pid_start_uid_and_non_zombie(self):
        with self.fixture() as f:
            row = f['row']
            self.assertTrue(recovery._process(row['pid'], row['starttime'], f['uid'], lambda argv: True))
            self.assertFalse(recovery._process(True, row['starttime'], f['uid'], lambda argv: True))
            self.assertFalse(recovery._process(1, row['starttime'], f['uid'], lambda argv: True))
            self.assertFalse(recovery._process(row['pid'], 'other', f['uid'], lambda argv: True))
            self.assertFalse(recovery._process(row['pid'], row['starttime'], f['uid'] + 1, lambda argv: True))
            f['dead'](row['pid'])
            self.assertFalse(recovery._process(row['pid'], row['starttime'], f['uid'], lambda argv: True))

    def test_runner_accepts_actual_python_prefix_and_exact_run_arguments(self):
        with self.fixture() as f:
            self.assertTrue(recovery._runner(f['row']))
            f['process'](f['row']['pid'], '100', f['runner_argv'] + ['--other'])
            self.assertFalse(recovery._runner(f['row']))

    def test_a_script_path_used_as_an_unrelated_program_argument_is_not_a_runner(self):
        with self.fixture() as f:
            for prefix in ([str(f['root'] / 'unrelated-executable')], [sys.executable, '-c']):
                with self.subTest(prefix=prefix):
                    argv = prefix + [f['row']['runner']] + f['runner_argv'][3:]
                    f['process'](f['row']['pid'], '100', argv)
                    self.assertFalse(recovery._runner(f['row']))

    def test_child_requires_the_exact_recorded_binary_argv(self):
        with self.fixture() as f:
            self.assertTrue(recovery._child(f['row']))
            for argv in (f['child_argv'] + ['--extra'], [sys.executable, *f['child_argv']]):
                f['process'](f['row']['child_pid'], '101', argv)
                self.assertFalse(recovery._child(f['row']))

    def test_read_rejects_wrong_owner_and_malformed_record(self):
        with self.fixture() as f:
            with patch.object(recovery.os, 'getuid', return_value=f['uid'] + 1):
                with self.assertRaises(ValueError):
                    recovery._read(f['directory'])

    def test_read_rejects_symlink_directory_or_record(self):
        with self.fixture() as f:
            for linked in (f['directory'], f['directory'] / 'session.json'):
                with self.subTest(linked=linked), patch.object(Path, 'is_symlink', new=lambda path: path == linked):
                    with self.assertRaises(ValueError):
                        recovery._read(f['directory'])
            for change in ({'token': 'short'}, {'runner': 'relative.py'}, {'cli': 'relative'}, {'kind': 'unrelated'}):
                row = {**f['row'], **change}
                (f['directory'] / 'session.json').write_text(json.dumps(row), encoding='utf-8')
                with self.subTest(change=change), self.assertRaises(ValueError):
                    recovery._read(f['directory'])

    def test_probe_of_a_running_session_never_sends_a_signal(self):
        with self.fixture() as f:
            value = recovery.inspect_session(f['directory'])
            self.assertFalse(value['cleanup_confirmed'])
            self.assertTrue(value['runner_alive'])
            self.assertTrue(value['child_alive'])
            self.assertFalse(f['signals'])

    def test_probe_without_metadata_does_not_confirm_a_busy_runner_gate(self):
        with self.fixture() as f:
            (f['directory'] / 'session.json').unlink()
            with patch.object(runner, '_session_gate', side_effect=ValueError('busy')):
                self.assertFalse(recovery.inspect_session(f['directory'])['cleanup_confirmed'])

    def test_probe_with_stale_metadata_also_requires_the_runner_gate(self):
        with self.fixture() as f:
            f['dead'](f['row']['pid'])
            f['dead'](f['row']['child_pid'])
            with patch.object(runner, '_session_gate', side_effect=ValueError('busy')):
                self.assertFalse(recovery.inspect_session(f['directory'])['cleanup_confirmed'])
            self.assertTrue((f['directory'] / 'session.json').exists())

    def test_stop_waits_for_owned_runner_and_deletes_its_record(self):
        with self.fixture() as f:
            value = recovery.stop_session(f['directory'])
            self.assertTrue(value['cleanup_confirmed'])
            self.assertEqual(f['signals'], [(f['row']['pid'], signal.SIGTERM)])
            self.assertFalse((f['directory'] / 'session.json').exists())

    def test_stop_can_recover_an_identity_verified_orphan_binary_child(self):
        with self.fixture() as f:
            f['dead'](f['row']['pid'])
            value = recovery.stop_session(f['directory'])
            self.assertTrue(value['cleanup_confirmed'])
            self.assertEqual(f['signals'], [(f['row']['child_pid'], signal.SIGINT)])

    def test_a_runner_that_disappears_before_signal_is_already_stopped(self):
        with self.fixture() as f:
            f['race']['gone'] = True
            value = recovery.stop_session(f['directory'])
            self.assertTrue(value['cleanup_confirmed'])
            self.assertFalse(f['signals'])

    def test_pid_reuse_between_verification_and_signal_never_hits_the_new_process(self):
        with self.fixture() as f:
            # Only the runner PID is made stale; the child was already reaped.
            f['dead'](f['row']['child_pid'])
            f['race']['reuse'] = True
            value = recovery.stop_session(f['directory'])
            self.assertTrue(value['cleanup_confirmed'])
            self.assertFalse(f['signals'])

    def test_missing_pidfd_support_never_falls_back_to_a_bare_pid_signal(self):
        with self.fixture() as f:
            numeric_kill = Mock()
            unsupported_os = SimpleNamespace(getuid=lambda: f['uid'], kill=numeric_kill)
            with patch.object(recovery, 'os', unsupported_os):
                with self.assertRaises(ValueError):
                    recovery.stop_session(f['directory'])
            numeric_kill.assert_not_called()
            self.assertTrue((f['directory'] / 'session.json').exists())

    def test_foreign_token_arriving_during_cleanup_is_preserved(self):
        with self.fixture() as f:
            f['dead'](f['row']['pid'])
            f['dead'](f['row']['child_pid'])

            @contextmanager
            def replace_record(directory):
                (directory / 'session.json').write_text(json.dumps({**f['row'], 'token': 'b' * 32}), encoding='utf-8')
                yield

            with patch.object(runner, '_session_gate', side_effect=replace_record):
                with self.assertRaisesRegex(ValueError, 'changed'):
                    recovery.stop_session(f['directory'])
            self.assertEqual(json.loads((f['directory'] / 'session.json').read_text(encoding='utf-8'))['token'], 'b' * 32)

    def test_missing_record_recovers_without_signalling_any_pid(self):
        with self.fixture() as f:
            (f['directory'] / 'session.json').unlink()
            self.assertTrue(recovery.stop_session(f['directory'])['cleanup_confirmed'])
            self.assertFalse(f['signals'])

    def test_a_record_published_before_gate_acquisition_is_not_ignored(self):
        for operation in (recovery.inspect_session, recovery.stop_session):
            with self.subTest(operation=operation.__name__), self.fixture() as f:
                marker = f['directory'] / 'session.json'
                marker.unlink()
                f['dead'](f['row']['pid'])

                @contextmanager
                def newly_published_orphan(directory):
                    marker.write_text(json.dumps(f['row']), encoding='utf-8')
                    yield

                with patch.object(runner, '_session_gate', side_effect=newly_published_orphan):
                    try:
                        value = operation(f['directory'])
                    except ValueError:
                        value = {'cleanup_confirmed': False}
                if value['cleanup_confirmed']:
                    self.assertFalse(recovery._child(f['row']), 'A newly published live orphan was ignored')
                if operation is recovery.inspect_session:
                    self.assertFalse(f['signals'])

    def test_main_recovery_modes_emit_the_real_confirmation_and_never_start_cli(self):
        for option in ('--probe', '--stop'):
            for confirmed in (True, False):
                output = io.StringIO()
                value = {'cleanup_confirmed': confirmed, 'runner_alive': not confirmed}
                target = 'inspect_session' if option == '--probe' else 'stop_session'
                with self.subTest(option=option, confirmed=confirmed):
                    with patch.object(recovery, target, return_value=value), patch.object(runner, 'run') as start, patch.object(runner.sys, 'stdout', output):
                        self.assertEqual(runner.main([option, '/synthetic/session']), 0 if confirmed else 1)
                        self.assertEqual(json.loads(output.getvalue()), value)
                        start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
