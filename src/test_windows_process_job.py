"""Software-only process containment; never start an SDK or official CLI."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

import windows_process_job as job


class FakeProcess:
    pid = 1234
    _handle = 5678

    def __init__(self, events):
        self.events = events
        self.returncode = None

    def poll(self):
        return self.returncode

    def kill(self):
        self.events.append('kill')
        self.returncode = 1

    def wait(self, timeout=None):
        self.events.append('wait')
        return self.returncode


class UnitTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        events = []
        process = FakeProcess(events)
        api = Mock()
        api.create.side_effect = lambda name=None: events.append('create') or 777
        api.assign.side_effect = lambda *_: events.append('assign')
        api.resume_primary.side_effect = lambda *_: events.append('resume')
        api.active.return_value = 0
        api.terminate.side_effect = lambda *_: process.kill()
        api.close.side_effect = lambda *_: events.append('close')

        def popen(args, **kwargs):
            events.append('spawn')
            self.assertEqual(args, ['synthetic-child'])
            self.assertEqual(kwargs['creationflags'] & (job.CREATE_SUSPENDED | job.CREATE_NO_WINDOW),
                             job.CREATE_SUSPENDED | job.CREATE_NO_WINDOW)
            return process

        with patch.object(job, '_is_windows', return_value=True), patch.object(job, '_WindowsAPI', return_value=api), patch.object(job.subprocess, 'Popen', side_effect=popen):
            yield events, process, api

    def test_assignment_precedes_resume_and_flags_are_preserved(self):
        with self.fixture() as (events, process, api):
            owned = job.OwnedProcess(['synthetic-child'], creationflags=0x200)
            self.assertEqual(events[:4], ['create', 'spawn', 'assign', 'resume'])
            self.assertIs(owned.process, process)
            process.returncode = 0
            self.assertTrue(owned.close())

    def test_assign_failure_kills_and_reaps_before_closing_without_resume(self):
        with self.fixture() as (events, process, api):
            api.assign.side_effect = OSError('assignment rejected')
            with self.assertRaisesRegex(OSError, 'assignment rejected'):
                job.OwnedProcess(['synthetic-child'])
            self.assertNotIn('resume', events)
            self.assertEqual(events[-3:], ['kill', 'wait', 'close'])

    def test_resume_failure_also_reaps_the_child(self):
        with self.fixture() as (events, process, api):
            api.resume_primary.side_effect = OSError('bad thread count')
            with self.assertRaisesRegex(OSError, 'bad thread count'):
                job.OwnedProcess(['synthetic-child'])
            self.assertEqual(events[-3:], ['kill', 'wait', 'close'])

    def test_spawn_failure_closes_job_without_a_child(self):
        with self.fixture() as (events, _, api), patch.object(job.subprocess, 'Popen', side_effect=OSError('spawn failed')):
            with self.assertRaisesRegex(OSError, 'spawn failed'):
                job.OwnedProcess(['synthetic-child'])
            self.assertEqual(events, ['create', 'close'])
            api.assign.assert_not_called()

    def test_failed_start_waits_even_if_kill_reports_error(self):
        with self.fixture() as (events, process, api):
            api.assign.side_effect = OSError('assign failed')
            process.kill = Mock(side_effect=OSError('already gone'))
            with self.assertRaisesRegex(OSError, 'assign failed'):
                job.OwnedProcess(['synthetic-child'])
            self.assertEqual(events[-2:], ['wait', 'close'])

    def test_root_exit_does_not_mean_descendants_exited(self):
        with self.fixture() as (_, process, api):
            owned = job.OwnedProcess(['synthetic-child'])
            process.returncode = 0
            api.active.return_value = 1
            self.assertFalse(owned.close(timeout=0))
            self.assertIsNotNone(owned._job)
            self.assertIsNotNone(owned.last_error)
            api.active.return_value = 0
            self.assertTrue(owned.close())

    def test_job_query_failure_keeps_handle_for_retry(self):
        with self.fixture() as (_, process, api):
            owned = job.OwnedProcess(['synthetic-child'])
            api.active.side_effect = OSError('cannot query')
            self.assertFalse(owned.close(timeout=0))
            self.assertEqual(owned.last_error, 'cannot query')
            api.close.assert_not_called()
            api.active.side_effect = None
            api.active.return_value = 0
            process.returncode = 0
            self.assertTrue(owned.close())

    def test_empty_job_close_is_idempotent(self):
        with self.fixture() as (_, process, api):
            owned = job.OwnedProcess(['synthetic-child'])
            process.returncode = 0
            self.assertTrue(owned.terminate_and_wait())
            self.assertTrue(owned.close())
            self.assertTrue(owned.close())
            self.assertTrue(owned.terminate_and_wait())
            self.assertEqual(owned.active_processes(), 0)
            api.close.assert_called_once_with(777)

    def test_guard_rejects_non_windows_shell_and_breakaway(self):
        with patch.object(job, '_is_windows', return_value=False):
            with self.assertRaisesRegex(OSError, 'Windows'):
                job.OwnedProcess(['synthetic-child'])
        with self.fixture():
            for kwargs in ({'shell': True}, {'creationflags': 0x01000000}, {'start_new_session': True}, {'preexec_fn': lambda: None}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    job.OwnedProcess(['synthetic-child'], **kwargs)

    def test_timeout_validation(self):
        for value in (-1, float('nan'), float('inf'), True, '1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                job._timeout(value)

    def test_named_job_accepts_only_exact_workbench_token_name(self):
        good = 'Local\\HandWorkbench-OfficialViz-' + 'a' * 32
        with self.fixture() as (_, process, api):
            owned = job.OwnedProcess(['synthetic-child'], job_name=good)
            self.assertEqual(owned.job_name, good)
            api.create.assert_called_once_with(good)
            process.returncode = 0
            self.assertTrue(owned.close())
        for name in ('', 'Local\\Other-' + 'a' * 32, good + 'x', good.replace('Local', 'Global'), good.upper(), good + '\n'):
            with self.subTest(name=name), self.fixture(), self.assertRaises(ValueError):
                job.OwnedProcess(['synthetic-child'], job_name=name)

    def test_recovery_absent_job_is_confirmed_but_access_error_is_not(self):
        name = 'Local\\HandWorkbench-OfficialViz-' + 'a' * 32
        with self.fixture() as (_, _, api):
            api.open_named.return_value = None
            self.assertTrue(job.recover_viz_job(name))
            api.terminate.assert_not_called()
            api.close.assert_not_called()
            api.open_named.side_effect = OSError('access denied')
            self.assertFalse(job.recover_viz_job(name))

    def test_recovery_confirms_empty_tree_and_rejects_remaining_descendants(self):
        name = 'Local\\HandWorkbench-OfficialViz-' + 'a' * 32
        with self.fixture() as (_, _, api):
            api.open_named.return_value = 888
            api.active.side_effect = [1, 0]
            self.assertTrue(job.recover_viz_job(name))
            api.terminate.assert_called_once_with(888)
            api.close.assert_called_once_with(888)
        with self.fixture() as (_, _, api):
            api.open_named.return_value = 888
            api.active.return_value = 1
            self.assertFalse(job.recover_viz_job(name, timeout=0))
            api.close.assert_called_once_with(888)
        for value in ('Local\\Other-' + 'a' * 32, name + 'extra'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                job.recover_viz_job(value)

    def test_recovery_close_handle_failure_is_not_success(self):
        name = 'Local\\HandWorkbench-OfficialViz-' + 'a' * 32
        with self.fixture() as (_, _, api):
            api.open_named.return_value = 888
            api.active.return_value = 0
            api.close.side_effect = OSError('cannot close')
            self.assertFalse(job.recover_viz_job(name))


def read_line(process, timeout=10):
    result = queue.Queue()
    threading.Thread(target=lambda: result.put(process.stdout.readline()), daemon=True).start()
    try:
        line = result.get(timeout=timeout)
    except queue.Empty:
        raise AssertionError('Synthetic Python child did not report readiness') from None
    if not line:
        raise AssertionError('Synthetic Python child exited before readiness: ' + str(process.poll()))
    return line


@unittest.skipUnless(os.name == 'nt', 'Real Windows Job Object tests require Windows')
class WindowsProcessTests(unittest.TestCase):
    """Real OS tests use only sleeping Python children and a separate sentinel."""
    def root_code(self, exit_parent=False):
        child = 'import time; time.sleep(60)'
        return ('import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c",' + repr(child) + ']); '
                'print(p.pid,flush=True); ' + ('' if exit_parent else 'time.sleep(60)'))

    @contextmanager
    def process_handle(self, pid):
        import ctypes
        from ctypes import wintypes as w
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        k.OpenProcess.restype = w.HANDLE
        k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        k.WaitForSingleObject.restype = w.DWORD
        k.CloseHandle.argtypes = [w.HANDLE]
        k.CloseHandle.restype = w.BOOL
        handle = k.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        self.assertTrue(handle, 'Synthetic process unavailable before verification')
        try:
            yield lambda timeout_ms: k.WaitForSingleObject(handle, timeout_ms)
        finally:
            k.CloseHandle(handle)

    def test_normal_stop_kills_descendants_but_not_unrelated_sentinel(self):
        sentinel = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'], creationflags=job.CREATE_NO_WINDOW)
        owned = None
        try:
            owned = job.OwnedProcess([sys.executable, '-u', '-c', self.root_code()], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            child_pid = int(read_line(owned.process))
            with self.process_handle(child_pid) as child_wait:
                self.assertGreaterEqual(owned.active_processes(), 2)
                self.assertTrue(owned.close(timeout=5), owned.last_error)
                self.assertEqual(child_wait(5000), 0)
            self.assertIsNone(sentinel.poll())
            self.assertTrue(owned.close())
        finally:
            if owned:
                owned.close()
                owned.process.stdout.close()
                owned.process.stderr.close()
            sentinel.kill()
            sentinel.wait(timeout=5)

    def test_root_natural_exit_still_tracks_live_descendant(self):
        owned = job.OwnedProcess([sys.executable, '-u', '-c', self.root_code(True)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            child_pid = int(read_line(owned.process))
            with self.process_handle(child_pid) as child_wait:
                owned.process.wait(timeout=5)
                self.assertGreaterEqual(owned.active_processes(), 1)
                self.assertEqual(child_wait(0), 258)  # WAIT_TIMEOUT
                self.assertTrue(owned.close(timeout=5), owned.last_error)
                self.assertEqual(child_wait(5000), 0)
        finally:
            owned.close()
            owned.process.stdout.close()
            owned.process.stderr.close()

    def test_named_collision_does_not_modify_existing_job_and_exact_recovery_works(self):
        import secrets
        name = 'Local\\HandWorkbench-OfficialViz-' + secrets.token_hex(16)
        owned = job.OwnedProcess([sys.executable, '-u', '-c', self.root_code()], job_name=name,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            child_pid = int(read_line(owned.process))
            with self.process_handle(child_pid) as child_wait:
                with self.assertRaisesRegex(OSError, 'already exists'):
                    job.OwnedProcess([sys.executable, '-c', 'raise RuntimeError("must never start")'], job_name=name)
                self.assertIsNone(owned.process.poll())
                self.assertEqual(child_wait(0), 258)
                # A synthetic recovery exercises the mechanism; production
                # callers additionally verify their persisted owner marker.
                self.assertTrue(job.recover_viz_job(name, timeout=5))
                self.assertEqual(child_wait(5000), 0)
                self.assertTrue(owned.close())
                self.assertTrue(job.recover_viz_job(name, timeout=0))
        finally:
            owned.close()
            owned.process.stdout.close()
            owned.process.stderr.close()

    def wrapper(self, eof=False):
        source = str(Path(job.__file__).resolve().parent)
        code = ('import sys,json,time,subprocess;sys.path.insert(0,' + repr(source) + ');'
                'from windows_process_job import OwnedProcess;'
                'o=OwnedProcess([sys.executable,"-u","-c",' + repr(self.root_code()) + '],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);'
                'child=int(o.process.stdout.readline());print(json.dumps([o.process.pid,child]),flush=True);'
                + ('sys.stdin.read();sys.exit(0 if o.close(5) else 2)' if eof else 'time.sleep(60)'))
        return subprocess.Popen([sys.executable, '-u', '-c', code], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                creationflags=job.CREATE_NO_WINDOW)

    def test_parent_abrupt_exit_kernel_closes_job_and_kills_tree(self):
        wrapper = self.wrapper()
        try:
            root, child = json.loads(read_line(wrapper))
            with self.process_handle(root) as root_wait, self.process_handle(child) as child_wait:
                wrapper.kill()
                wrapper.wait(timeout=5)
                self.assertEqual(root_wait(5000), 0)
                self.assertEqual(child_wait(5000), 0)
        finally:
            if wrapper.poll() is None:
                wrapper.kill()
                wrapper.wait(timeout=5)
            wrapper.stdin.close()
            wrapper.stdout.close()
            wrapper.stderr.close()

    def test_control_pipe_eof_runs_normal_tree_cleanup(self):
        wrapper = self.wrapper(eof=True)
        try:
            root, child = json.loads(read_line(wrapper))
            with self.process_handle(root) as root_wait, self.process_handle(child) as child_wait:
                wrapper.stdin.close()
                self.assertEqual(wrapper.wait(timeout=10), 0)
                self.assertEqual(root_wait(5000), 0)
                self.assertEqual(child_wait(5000), 0)
        finally:
            if wrapper.poll() is None:
                wrapper.kill()
                wrapper.wait(timeout=5)
            wrapper.stdin.close()
            wrapper.stdout.close()
            wrapper.stderr.close()


if __name__ == '__main__':
    unittest.main()
