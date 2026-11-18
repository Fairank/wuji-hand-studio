"""Atomic commit contention and ownership; every path is synthetic and temporary."""
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import filesystem_commit as commit


def denied(code=5):
    error = PermissionError(13, 'Synthetic Windows commit denial')
    error.winerror = code
    return error


class FilesystemCommitTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source, self.target = self.root / 'candidate', self.root / 'current'
        self.source.write_bytes(b'NEW BYTES')
        self.target.write_bytes(b'OLD BYTES')
        self.windows = patch.object(commit, '_WINDOWS', True)
        self.windows.start()
        self.addCleanup(self.windows.stop)

    def assert_originals(self):
        self.assertEqual(self.source.read_bytes(), b'NEW BYTES')
        self.assertEqual(self.target.read_bytes(), b'OLD BYTES')

    def test_permanent_denial_preserves_original_error_and_bounds_every_wait(self):
        for code in (5, 32):
            with self.subTest(code=code):
                error = denied(code)
                move = Mock(side_effect=error)
                with patch.object(commit.time, 'sleep') as sleep:
                    with self.assertRaises(PermissionError) as caught:
                        commit.move_with_retry(self.source, self.target, move)
                self.assertIs(caught.exception, error)
                self.assertEqual(move.call_count, 10)
                self.assertEqual(sleep.call_count, 9)
                self.assertTrue(all(c.args == (.01,) for c in sleep.call_args_list))
                self.assert_originals()

    def test_unlisted_errors_are_never_retried(self):
        for error in (OSError('unclassified'), denied(2), denied(6), denied(112), denied(183)):
            with self.subTest(error=repr(error)):
                move = Mock(side_effect=error)
                with patch.object(commit.time, 'sleep') as sleep:
                    with self.assertRaises(OSError) as caught:
                        commit.move_with_retry(self.source, self.target, move)
                self.assertIs(caught.exception, error)
                move.assert_called_once_with(self.source, self.target)
                sleep.assert_not_called()
                self.assert_originals()

    def test_cancel_after_denial_prevents_second_move(self):
        checks = []

        def check():
            checks.append(True)
            if len(checks) > 1:
                raise ValueError('cancelled')

        move = Mock(side_effect=denied())
        with patch.object(commit.time, 'sleep'), self.assertRaisesRegex(ValueError, 'cancelled'):
            commit.move_with_retry(self.source, self.target, move, check_cancel=check)
        self.assertEqual(move.call_count, 1)
        self.assert_originals()

    def test_source_and_destination_replacements_are_never_touched(self):
        for operand in ('source', 'target'):
            with self.subTest(operand=operand):
                path = getattr(self, operand)
                retained = self.root / ('retained-' + operand)
                replacement = self.root / ('replacement-' + operand)
                replacement.write_bytes(b'OTHER OWNER')

                def replace_during_wait(_):
                    os.replace(path, retained)
                    os.replace(replacement, path)

                move = Mock(side_effect=denied())
                with patch.object(commit.time, 'sleep', side_effect=replace_during_wait):
                    with self.assertRaises(commit.CommitConflict):
                        commit.move_with_retry(self.source, self.target, move)
                self.assertEqual(move.call_count, 1)
                self.assertEqual(path.read_bytes(), b'OTHER OWNER')
                os.replace(retained, path)
                self.assert_originals()

    def test_same_file_edited_during_wait_is_not_committed(self):
        before = self.source.stat()

        def edit_during_wait(_):
            self.source.write_bytes(b'EDITED!!!')
            os.utime(self.source, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))

        move = Mock(side_effect=denied())
        with patch.object(commit.time, 'sleep', side_effect=edit_during_wait):
            with self.assertRaises(commit.CommitConflict):
                commit.move_with_retry(self.source, self.target, move)
        self.assertEqual(move.call_count, 1)
        self.assertEqual(self.source.read_bytes(), b'EDITED!!!')
        self.assertEqual(self.target.read_bytes(), b'OLD BYTES')

    def test_changed_parent_is_detected_even_when_operands_keep_their_ids(self):
        parent = self.root / 'parent'
        parent.mkdir()
        source, target = parent / 'candidate', parent / 'current'
        os.replace(self.source, source)
        os.replace(self.target, target)
        ids = commit.path_identity(source), commit.path_identity(target)
        retained = self.root / 'retained-parent'

        def replace_parent(_):
            parent.rename(retained)
            parent.mkdir()
            os.replace(retained / source.name, source)
            os.replace(retained / target.name, target)

        move = Mock(side_effect=denied())
        with patch.object(commit.time, 'sleep', side_effect=replace_parent):
            with self.assertRaises(commit.CommitConflict):
                commit.move_with_retry(source, target, move)
        self.assertEqual((commit.path_identity(source), commit.path_identity(target)), ids)
        self.assertEqual(move.call_count, 1)
        self.assertEqual(target.read_bytes(), b'OLD BYTES')

    def test_completed_move_is_reconciled_before_late_cancellation(self):
        identity = commit.path_identity(self.source)
        cancelled = False
        checks = []

        def check():
            checks.append(True)
            if cancelled:
                raise ValueError('cancelled too late')

        def move(source, target):
            nonlocal cancelled
            os.replace(source, target)
            cancelled = True
            raise denied()

        primitive = Mock(side_effect=move)
        commit.move_with_retry(self.source, self.target, primitive, check_cancel=check)
        primitive.assert_called_once_with(self.source, self.target)
        self.assertEqual(len(checks), 1)
        self.assertEqual(commit.path_identity(self.target), identity)
        self.assertEqual(self.target.read_bytes(), b'NEW BYTES')

    def test_unknown_observations_allow_first_attempt_but_never_authorize_retry(self):
        with patch.object(commit, '_snapshot', return_value=commit._UNKNOWN):
            commit.move_with_retry(self.source, self.target, os.replace)
        self.assertEqual(self.target.read_bytes(), b'NEW BYTES')
        self.source.write_bytes(b'NEXT BYTES')
        error = denied()
        move = Mock(side_effect=error)
        with patch.object(commit, '_snapshot', return_value=commit._UNKNOWN), \
                patch.object(commit.time, 'sleep') as sleep:
            with self.assertRaises(PermissionError) as caught:
                commit.move_with_retry(self.source, self.target, move)
            self.assertIsNone(commit.path_identity(self.source))
            self.assertFalse(commit.same_identity(self.source, None))
        self.assertIs(caught.exception, error)
        self.assertEqual(move.call_count, 1)
        sleep.assert_not_called()

    def test_non_windows_keeps_exactly_one_original_primitive_call(self):
        move = Mock(side_effect=denied())
        with patch.object(commit, '_WINDOWS', False), patch.object(commit.time, 'sleep') as sleep:
            with self.assertRaises(PermissionError):
                commit.move_with_retry(self.source, self.target, move)
        move.assert_called_once_with(self.source, self.target)
        sleep.assert_not_called()
        self.assert_originals()


@unittest.skipUnless(os.name == 'nt', 'Real Windows sharing semantics')
class RealWindowsReaderTests(unittest.TestCase):
    @contextmanager
    def reader(self, path, *, share=3):
        from ctypes import wintypes
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        api.CreateFileW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        handle = api.CreateFileW(str(path), 1, share, None, 3, 0x02000000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        closed = False

        def close():
            nonlocal closed
            if not closed:
                if not api.CloseHandle(handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                closed = True
        try:
            yield close
        finally:
            close()

    def test_actual_child_reader_denies_directory_publish_then_release_commits_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'stage', Path(tmp) / 'final'
            source.mkdir()
            child = source / 'payload.py'
            child.write_bytes(b'SYNTHETIC; NEVER EXECUTED')
            identity = commit.path_identity(source)
            errors = []
            with self.reader(child) as release:
                with self.assertRaises(PermissionError) as raw:
                    os.replace(source, target)
                self.assertIn(raw.exception.winerror, (5, 32))
                self.assertEqual(child.read_bytes(), b'SYNTHETIC; NEVER EXECUTED')

                def move(a, b):
                    try:
                        return os.replace(a, b)
                    except PermissionError as error:
                        errors.append(error.winerror)
                        release()  # Release only after the actual denial, no timing guess.
                        raise

                primitive = Mock(side_effect=move)
                commit.move_with_retry(source, target, primitive)
            self.assertEqual(len(errors), 1)
            self.assertEqual(primitive.call_count, 2)
            self.assertEqual(commit.path_identity(target), identity)
            self.assertEqual((target / child.name).read_bytes(), b'SYNTHETIC; NEVER EXECUTED')

    def test_actual_persistent_target_reader_fails_bounded_and_keeps_both_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'candidate', Path(tmp) / 'current'
            source.write_bytes(b'NEW')
            target.write_bytes(b'OLD')
            primitive = Mock(wraps=os.replace)
            with self.reader(target), self.assertRaises(PermissionError) as caught:
                commit.move_with_retry(source, target, primitive)
            self.assertIn(caught.exception.winerror, (5, 32))
            self.assertEqual(primitive.call_count, 10)
            self.assertEqual(source.read_bytes(), b'NEW')
            self.assertEqual(target.read_bytes(), b'OLD')

    def test_actual_child_directory_scan_blocks_parent_even_with_delete_sharing(self):
        # Matches the captured workspace scanner: a child directory enumeration,
        # not a direct handle lacking FILE_SHARE_DELETE on the renamed object.
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'stage', Path(tmp) / 'final'
            child = source / 'lib'
            child.mkdir(parents=True)
            (child / 'payload').write_bytes(b'KEEP')
            errors = []
            with self.reader(child, share=7) as release:
                def move(a, b):
                    try:
                        return os.replace(a, b)
                    except PermissionError as error:
                        errors.append(error.winerror)
                        release()
                        raise
                primitive = Mock(side_effect=move)
                commit.move_with_retry(source, target, primitive)
            self.assertEqual(errors, [5])
            self.assertEqual(primitive.call_count, 2)
            self.assertEqual((target / 'lib/payload').read_bytes(), b'KEEP')

        # The equivalent shared handle on the source itself permits the move;
        # this distinguishes descendant enumeration from a direct sharing lock.
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'stage', Path(tmp) / 'final'
            source.mkdir()
            with self.reader(source, share=7):
                os.replace(source, target)
            self.assertTrue(target.is_dir())


if __name__ == '__main__':
    unittest.main()
