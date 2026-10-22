"""Synthetic PTY transport checks. No real CLI, PTY, network or SDK runs."""
import io
import json
import queue
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import native_terminal_worker as worker


class HoldInput:
    def __init__(self):
        self.closed = threading.Event()

    def readline(self, _limit):
        self.closed.wait()
        return ''


class FakePTY:
    def __init__(self, output=(), status=0, *, natural=False, responsive=True):
        self.alive = True
        self.status = status
        self.responsive = responsive
        self.writes = []
        self.sizes = []
        self.queue = queue.Queue()
        for text in output:
            self.queue.put(text)
        if natural:
            self.queue.put(EOFError())

    def read(self, _size):
        item = self.queue.get(timeout=2)
        if isinstance(item, BaseException):
            if isinstance(item, EOFError):
                self.alive = False
            raise item
        return item

    def write(self, text):
        self.writes.append(text)
        if text == '\x03' and self.responsive:
            self.alive = False
            self.queue.put(EOFError())

    def setwinsize(self, rows, cols):
        self.sizes.append((rows, cols))

    def isalive(self):
        return self.alive

    @property
    def exitstatus(self):
        if self.alive:
            raise AssertionError('Exit status read before confirmed process exit')
        return self.status


def lines(*objects):
    return io.StringIO(''.join(json.dumps(obj) + '\n' for obj in objects))


class WorkerTests(unittest.TestCase):
    def execute(self, process, stream=None, timeout=.08, output=None):
        stream = stream if stream is not None else HoldInput()
        output = output if output is not None else io.StringIO()
        with patch.object(worker, '_spawn', return_value=process) as spawn:
            code = worker.run(['synthetic.exe', 'argument with spaces'], stdin=stream,
                              stdout=output, cancel_timeout=timeout)
        if isinstance(stream, HoldInput):
            stream.closed.set()
        process.alive = False
        process.queue.put(EOFError())
        events = [json.loads(row) for row in output.getvalue().splitlines()] if isinstance(output, io.StringIO) else []
        return code, events, spawn

    def test_pin_and_conpty_zero_survive_environment_override_without_shell(self):
        spawn = Mock(return_value=object())
        module = SimpleNamespace(__version__='3.1.0', Backend=SimpleNamespace(ConPTY=0),
                                 PtyProcess=SimpleNamespace(spawn=spawn))
        command = ['C:/verified/native.exe', 'a b', 'a&b', '"quoted"']
        with patch.dict(sys.modules, winpty=module), patch.dict('os.environ', PYWINPTY_BACKEND='1'):
            worker._spawn(command)
        spawn.assert_called_once_with(command, dimensions=(38, 100), backend='0')

    def test_other_dependency_version_or_backend_never_spawns(self):
        for version, backend in [('2.0.15', 0), ('3.1.0', 1)]:
            spawn = Mock()
            module = SimpleNamespace(__version__=version, Backend=SimpleNamespace(ConPTY=backend),
                                     PtyProcess=SimpleNamespace(spawn=spawn))
            with self.subTest(version=version, backend=backend), patch.dict(sys.modules, winpty=module):
                with self.assertRaises(RuntimeError):
                    worker._spawn(['synthetic.exe'])
                spawn.assert_not_called()

    def test_pinned_polling_mode_is_scoped_to_spawn_and_restored_on_failure(self):
        module = SimpleNamespace(__version__='3.1.0', Backend=SimpleNamespace(ConPTY=0))
        import os
        for previous in (None, '1', '0'):
            for fail in (False, True):
                def spawn(*_args, **_kwargs):
                    self.assertEqual(os.environ.get('PYWINPTY_BLOCK'), '0')
                    if fail:
                        raise OSError('synthetic spawn error')
                    return object()
                module.PtyProcess = SimpleNamespace(spawn=spawn)
                with self.subTest(previous=previous, fail=fail), patch.dict(sys.modules, winpty=module), patch.dict(os.environ):
                    if previous is None:
                        os.environ.pop('PYWINPTY_BLOCK', None)
                    else:
                        os.environ['PYWINPTY_BLOCK'] = previous
                    if fail:
                        with self.assertRaises(OSError):
                            worker._spawn(['synthetic.exe'])
                    else:
                        worker._spawn(['synthetic.exe'])
                    self.assertEqual(os.environ.get('PYWINPTY_BLOCK'), previous)

    def test_terminal_text_is_not_interpreted_and_nonzero_child_status_is_preserved(self):
        text = '\x1b[38;2;1;2;3m拇指\x1b[0m\r\n{"event":"result"}'
        process = FakePTY([text], 17, natural=True)
        code, events, spawn = self.execute(process)
        self.assertEqual(code, 0)
        self.assertEqual(events, [{'type': 'output', 'text': text}, {'type': 'exit', 'exitstatus': 17}])
        self.assertEqual(process.writes, [])
        spawn.assert_called_once_with(['synthetic.exe', 'argument with spaces'])

    def test_write_resize_and_cancel_are_forwarded_in_order(self):
        process = FakePTY(status=9)
        stream = lines({'type': 'write', 'text': '\x1b[A中文\r'},
                       {'type': 'resize', 'rows': 41, 'cols': 119}, {'type': 'cancel'})
        code, events, _ = self.execute(process, stream)
        self.assertEqual(code, 0)
        self.assertEqual(process.writes, ['\x1b[A中文\r', '\x03'])
        self.assertEqual(process.sizes, [(41, 119)])
        self.assertEqual(events[-1], {'type': 'exit', 'exitstatus': 9})

    def test_pipe_eof_requests_ctrl_c_once(self):
        process = FakePTY(status=9)
        code, events, _ = self.execute(process, io.StringIO(''))
        self.assertEqual(code, 0)
        self.assertEqual(process.writes, ['\x03'])
        self.assertEqual(events[-1]['exitstatus'], 9)

    def test_repeated_cancel_does_not_send_a_second_ctrl_c(self):
        process = FakePTY(status=9)
        code, _, _ = self.execute(process, lines({'type': 'cancel'}, {'type': 'cancel'}))
        self.assertEqual(code, 0)
        self.assertEqual(process.writes, ['\x03'])

    def test_unresponsive_child_has_bounded_wait_and_no_fabricated_exit(self):
        process = FakePTY(responsive=False)
        started = time.monotonic()
        code, events, _ = self.execute(process, lines({'type': 'cancel'}), timeout=.04)
        self.assertEqual(code, 1)
        self.assertLess(time.monotonic() - started, .5)
        self.assertEqual(process.writes, ['\x03'])
        self.assertEqual([e['type'] for e in events], ['error'])
        self.assertIn('unconfirmed', events[0]['message'])

    def test_unknown_child_exit_status_cannot_be_success(self):
        code, events, _ = self.execute(FakePTY(status=None, natural=True))
        self.assertEqual(code, 1)
        self.assertEqual(events[-1], {'type': 'exit', 'exitstatus': None})
        self.assertEqual(events[-2]['type'], 'error')

    def test_malformed_or_oversized_input_stops_without_forwarding_it(self):
        inputs = ['not json\n', '[]\n', 'x' * (worker.MAX_LINE + 1),
                  json.dumps({'type': 'write', 'text': 'x' * (worker.MAX_WRITE + 1)}) + '\n',
                  json.dumps({'type': 'resize', 'rows': True, 'cols': 100}) + '\n',
                  json.dumps({'type': 'resize', 'rows': 0, 'cols': 100}) + '\n',
                  json.dumps({'type': 'write', 'text': 'x', 'argv': ['bad']}) + '\n',
                  json.dumps({'type': 'spawn', 'argv': ['bad']}) + '\n']
        for value in inputs:
            with self.subTest(value=value[:90]):
                process = FakePTY(status=9)
                code, events, _ = self.execute(process, io.StringIO(value))
                self.assertEqual(code, 1)
                self.assertEqual(process.writes, ['\x03'])
                self.assertEqual(events[0]['type'], 'error')

    def test_bad_argv_or_timeout_never_imports_or_spawns_pty(self):
        for argv in ('command string', [], [''], ['a\0b'], ['x', object()]):
            with self.subTest(argv=repr(argv)), patch.object(worker, '_spawn') as spawn:
                output = io.StringIO()
                self.assertEqual(worker.run(argv, stdout=output), 1)
                spawn.assert_not_called()
                self.assertEqual(json.loads(output.getvalue())['type'], 'error')
        for value in (True, -1, float('inf'), 31):
            with self.subTest(timeout=value), patch.object(worker, '_spawn') as spawn:
                self.assertEqual(worker.run(['synthetic.exe'], stdout=io.StringIO(), cancel_timeout=value), 1)
                spawn.assert_not_called()

    def test_output_error_cancels_then_reports_actual_exit(self):
        process = FakePTY(status=9)
        process.queue.put(OSError('synthetic read failed'))
        code, events, _ = self.execute(process)
        self.assertEqual(code, 1)
        self.assertEqual(process.writes, ['\x03'])
        self.assertEqual(events[0]['type'], 'error')
        self.assertEqual(events[-1], {'type': 'exit', 'exitstatus': 9})

    def test_host_output_pipe_closure_still_interrupts_child(self):
        class ClosedOutput:
            def write(self, _text):
                raise BrokenPipeError('closed host pipe')
            def flush(self):
                pass
        process = FakePTY(['text'], status=9)
        code, _, _ = self.execute(process, output=ClosedOutput())
        self.assertEqual(code, 1)
        self.assertEqual(process.writes, ['\x03'])

    def test_main_passes_only_explicit_argv(self):
        with patch.object(worker, 'run', return_value=3) as run:
            self.assertEqual(worker.main(['synthetic.exe', '--help']), 3)
        run.assert_called_once_with(['synthetic.exe', '--help'])

    def test_dependency_specific_exception_is_a_transport_error(self):
        class SyntheticWinptyError(Exception):
            pass
        with patch.object(worker, '_spawn', side_effect=SyntheticWinptyError('synthetic PTY failure')):
            output = io.StringIO()
            self.assertEqual(worker.run(['synthetic.exe'], stdout=output), 1)
        self.assertEqual(json.loads(output.getvalue())['type'], 'error')

    def test_dead_child_cancels_pending_io_once_and_waits_for_actual_eof(self):
        process = FakePTY(status=0)
        process.alive = False
        def cancel_io():
            self.assertFalse(process.isalive())
            process.queue.put('last terminal bytes')
            process.queue.put(EOFError())
        process.pty = SimpleNamespace(cancel_io=Mock(side_effect=cancel_io))
        with patch.object(worker, 'OUTPUT_DRAIN_TIMEOUT', .01), patch.object(worker, 'IO_CANCEL_DRAIN_TIMEOUT', .1):
            code, events, _ = self.execute(process)
        self.assertEqual(code, 0)
        process.pty.cancel_io.assert_called_once_with()
        self.assertEqual(events, [{'type': 'output', 'text': 'last terminal bytes'},
                                  {'type': 'exit', 'exitstatus': 0}])
        self.assertEqual(process.writes, [])

    def test_dead_child_without_real_reader_eof_stays_unconfirmed(self):
        process = FakePTY(status=0)
        process.alive = False
        process.pty = SimpleNamespace(cancel_io=Mock())
        with patch.object(worker, 'OUTPUT_DRAIN_TIMEOUT', .01), patch.object(worker, 'IO_CANCEL_DRAIN_TIMEOUT', .02):
            code, events, _ = self.execute(process)
        self.assertEqual(code, 1)
        process.pty.cancel_io.assert_called_once_with()
        self.assertEqual([e['type'] for e in events], ['error'])
        self.assertIn('unconfirmed', events[0]['message'])

    def test_live_child_io_failure_does_not_use_dead_child_cleanup(self):
        process = FakePTY(status=9)
        process.queue.put(OSError('live child output error'))
        process.pty = SimpleNamespace(cancel_io=Mock())
        code, events, _ = self.execute(process)
        self.assertEqual(code, 1)
        self.assertEqual(process.writes, ['\x03'])
        self.assertEqual(events[0]['type'], 'error')
        process.pty.cancel_io.assert_not_called()


if __name__ == '__main__':
    unittest.main()
