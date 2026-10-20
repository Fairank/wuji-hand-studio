"""Window-size persistence with temporary data and a mocked pywebview backend."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import native_desktop


class MockWindow:
    uid = 'mock-window'

    def __init__(self, get_size):
        self.gui = SimpleNamespace(get_size=Mock(side_effect=get_size))
        self.resize = Mock()
        self.evaluate_js = Mock()

    @property
    def width(self):
        raise AssertionError('Read one backend size pair instead of width/height')

    @property
    def height(self):
        raise AssertionError('Read one backend size pair instead of width/height')


class NativeBoundsTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        data_patch = patch.object(native_desktop, 'DATA', self.data)
        data_patch.start()
        self.addCleanup(data_patch.stop)
        self.size = (1476, 880)
        self.host = native_desktop.NativeDesktop()
        self.host.server = SimpleNamespace(server_port=8783)
        self.window = MockWindow(lambda uid: self.size)
        self.host.window = self.window
        self.path = self.data / 'native-window.json'

    def saved(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def assert_no_window_actions(self):
        self.window.resize.assert_not_called()
        self.window.evaluate_js.assert_not_called()

    def test_reads_one_current_size_pair_and_preserves_limits(self):
        for actual, expected in [((1476, 880), (1476, 880)), ((640, 480), (960, 680)), ((3000, 1800), (2560, 1600))]:
            with self.subTest(actual=actual):
                self.size = actual
                self.window.gui.get_size.reset_mock()
                self.host.save_bounds()
                self.window.gui.get_size.assert_called_once_with('mock-window')
                self.assertEqual(self.saved(), dict(width=expected[0], height=expected[1], port=8783))
                self.assertEqual(list(self.data.glob('*.tmp')), [])
        self.assert_no_window_actions()

    def test_concurrent_callbacks_commit_the_current_pair_last(self):
        old_ready = threading.Event()
        new_waiting = threading.Event()
        release_old = threading.Event()
        mutex = self.host.bounds_lock

        class ObservedLock:
            def __enter__(self):
                if threading.current_thread().name == 'new-bounds':
                    new_waiting.set()
                mutex.acquire()

            def __exit__(self, *args):
                mutex.release()

        self.host.bounds_lock = ObservedLock()
        replace = native_desktop.os.replace

        def delayed_replace(source, target):
            if threading.current_thread().name == 'old-bounds':
                old_ready.set()
                if not release_old.wait(3):
                    raise TimeoutError('Test did not release the first save')
            replace(source, target)

        threads = []
        with patch.object(native_desktop.os, 'replace', side_effect=delayed_replace):
            try:
                old = threading.Thread(target=self.host.save_bounds, name='old-bounds')
                threads.append(old)
                old.start()
                self.assertTrue(old_ready.wait(3))
                self.size = (960, 680)
                new = threading.Thread(target=self.host.save_bounds, name='new-bounds')
                threads.append(new)
                new.start()
                self.assertTrue(new_waiting.wait(3))
                self.assertEqual(self.window.gui.get_size.call_count, 1, 'The second callback must read after acquiring the write lock')
            finally:
                release_old.set()
                for thread in threads:
                    thread.join(3)
                    self.assertFalse(thread.is_alive())
        self.assertEqual(self.window.gui.get_size.call_count, 2)
        self.assertEqual(self.saved(), dict(width=960, height=680, port=8783))
        self.assert_no_window_actions()

    def test_delayed_old_callback_reads_current_dimensions(self):
        callback_started = threading.Event()
        run_callback = threading.Event()

        def delayed_callback():
            callback_started.set()
            if run_callback.wait(3):
                self.host.save_bounds()

        old = threading.Thread(target=delayed_callback)
        old.start()
        try:
            self.assertTrue(callback_started.wait(3))
            self.size = (1322, 880)
            self.host.save_bounds()
        finally:
            run_callback.set()
            old.join(3)
        self.assertFalse(old.is_alive())
        self.assertEqual(self.window.gui.get_size.call_count, 2)
        self.assertEqual(self.saved(), dict(width=1322, height=880, port=8783))

    def test_partial_write_failure_keeps_original_json(self):
        original = '{"width":1476,"height":880,"port":8783}'
        self.path.write_text(original, encoding='utf-8')
        self.size = (960, 680)

        def failed_dump(state, stream):
            stream.write('{"width":')
            raise OSError('Injected full disk')

        with patch.object(native_desktop.json, 'dump', side_effect=failed_dump), self.assertLogs(level='ERROR'):
            self.host.save_bounds()
        self.assertEqual(self.path.read_text(encoding='utf-8'), original)
        self.assertEqual(list(self.data.glob('*.tmp')), [])
        self.host.save_bounds()
        self.assertEqual(self.saved(), dict(width=960, height=680, port=8783))

    def test_replace_failure_keeps_original_json_and_removes_temporary_file(self):
        original = '{"width":1476,"height":880,"port":8783}'
        self.path.write_text(original, encoding='utf-8')
        self.size = (960, 680)
        with patch.object(native_desktop.os, 'replace', side_effect=OSError('Injected replacement failure')), self.assertLogs(level='ERROR'):
            self.host.save_bounds()
        self.assertEqual(self.path.read_text(encoding='utf-8'), original)
        self.assertEqual(list(self.data.glob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
