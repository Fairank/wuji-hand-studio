"""Bounded JSONL transport for a pre-authorized Windows ConPTY child.

The host must validate argv and establish its Windows Job before this worker
runs. This module never chooses a command, user, device or SDK environment.
It forwards terminal text unchanged; it does not interpret ANSI or CLI phases.

API pin: pywinpty 3.1.0, winpty/ptyprocess.py and winpty/enums.py at
https://github.com/andfoy/pywinpty/tree/v3.1.0 (MIT).
Input: type=write/text, resize/rows/cols, or cancel. EOF also requests cancel.
Output: type=output/text, exit/exitstatus, or error/message. A transport return
of zero is not CLI success: the host must inspect the actual exitstatus.
"""
import json
import math
import os
import queue
import sys
import threading
import time


PYWINPTY_VERSION = '3.1.0'
DIMENSIONS = (38, 100)  # pywinpty takes (rows, columns).
MAX_LINE = 65536
MAX_WRITE = 8192
READ_SIZE = 4096
QUEUE_LIMIT = 64
POLL_INTERVAL = .02
OUTPUT_DRAIN_TIMEOUT = 1.0
IO_CANCEL_DRAIN_TIMEOUT = 1.0


def _validate_argv(argv):
    if (not isinstance(argv, (list, tuple)) or not 1 <= len(argv) <= 128
            or not all(isinstance(arg, str) and '\0' not in arg for arg in argv)
            or not argv[0] or sum(map(len, argv)) > 32768):
        raise ValueError('Expected a bounded, already authorized command argv')
    return list(argv)


def _spawn(argv):
    # Import is deliberately lazy; importing the worker starts no process.
    from winpty import Backend, PtyProcess, __version__
    if __version__ != PYWINPTY_VERSION or Backend.ConPTY != 0:
        raise RuntimeError('The pinned pywinpty 3.1.0 ConPTY API is required')
    # In 3.1.0 spawn uses ``backend or PYWINPTY_BACKEND`` before int(). A
    # truthy numeric string preserves explicit ConPTY=0 against that fallback.
    # The pinned high-level reader otherwise blocks inside pty.read after a
    # child has died, never reaching its own iseof() check. Use its documented
    # polling mode; do not invent an EOF or interpret its terminal output.
    previous = os.environ.get('PYWINPTY_BLOCK')
    os.environ['PYWINPTY_BLOCK'] = '0'
    try:
        return PtyProcess.spawn(argv, dimensions=DIMENSIONS, backend=str(Backend.ConPTY))
    finally:
        if previous is None:
            os.environ.pop('PYWINPTY_BLOCK', None)
        else:
            os.environ['PYWINPTY_BLOCK'] = previous


def _command(line):
    if not isinstance(line, str) or len(line) > MAX_LINE:
        raise ValueError('Terminal input line exceeds its limit')
    try:
        value = json.loads(line)
    except (ValueError, TypeError) as error:
        raise ValueError('Invalid terminal input JSON') from error
    if not isinstance(value, dict):
        raise ValueError('Expected a terminal input object')
    kind = value.get('type')
    if kind == 'write' and set(value) == {'type', 'text'}:
        if not isinstance(value['text'], str) or len(value['text']) > MAX_WRITE:
            raise ValueError('Terminal write text exceeds its limit')
    elif kind == 'resize' and set(value) == {'type', 'rows', 'cols'}:
        if (type(value['rows']) is not int or type(value['cols']) is not int
                or not 1 <= value['rows'] <= 300 or not 1 <= value['cols'] <= 500):
            raise ValueError('Invalid terminal dimensions')
    elif kind != 'cancel' or set(value) != {'type'}:
        raise ValueError('Unsupported terminal input operation')
    return value


def _put(channel, value, finished):
    while not finished.is_set():
        try:
            channel.put(value, timeout=.05)
            return
        except queue.Full:
            pass


def _read_input(stream, channel, finished):
    try:
        while not finished.is_set():
            line = stream.readline(MAX_LINE + 1)
            if not line:
                _put(channel, ('cancel', None), finished)
                return
            command = _command(line)
            _put(channel, ('command', command), finished)
            if command['type'] == 'cancel':
                return
    except Exception as error:
        _put(channel, ('error', str(error)[:400]), finished)


def _read_output(process, channel, finished, drained):
    try:
        while not finished.is_set():
            text = process.read(READ_SIZE)
            if not isinstance(text, str) or len(text) > READ_SIZE + 4:
                raise ValueError('PTY returned invalid or oversized output')
            if text:
                _put(channel, ('output', text), finished)
    except EOFError:
        pass
    except Exception as error:
        _put(channel, ('error', str(error)[:400]), finished)
    finally:
        drained.set()


def run(argv, *, stdin=None, stdout=None, cancel_timeout=5.0):
    """Forward one PTY. Nonzero means transport/exit confirmation failed.

    On cancel/pipe EOF send one Ctrl+C, then wait at most cancel_timeout.
    An unresponsive child is left to the host's already-established Job; this
    worker never reports its exit, or invents an official cancellation result.
    The host must continuously drain stdout and contain a stalled pipe writer.
    """
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    process = None
    finished, drained = threading.Event(), threading.Event()
    commands, output = queue.Queue(QUEUE_LIMIT), queue.Queue(QUEUE_LIMIT)
    cancelling = None
    failed = False
    ctrl_c_sent = False
    dead_at = None
    io_cancel_at = None

    def emit(kind, **fields):
        # ASCII JSON safely carries Unicode/ANSI via escapes on any pipe codec.
        stdout.write(json.dumps(dict(type=kind, **fields), ensure_ascii=True) + '\n')
        stdout.flush()

    def error(message):
        nonlocal failed
        failed = True
        emit('error', message=message[:400])

    def cancel():
        nonlocal cancelling, ctrl_c_sent
        if cancelling is None:
            cancelling = time.monotonic()
        if not ctrl_c_sent:
            ctrl_c_sent = True
            try:
                process.write('\x03')
            except EOFError:
                pass
            except Exception as problem:
                error('Could not send terminal Ctrl+C: ' + str(problem))

    try:
        argv = _validate_argv(argv)
        if (type(cancel_timeout) not in (int, float)
                or not math.isfinite(cancel_timeout) or not 0 <= cancel_timeout <= 30):
            raise ValueError('Invalid terminal cancellation timeout')
        process = _spawn(argv)
        threading.Thread(target=_read_output, args=(process, output, finished, drained),
                         name='Native PTY output', daemon=True).start()
        threading.Thread(target=_read_input, args=(stdin, commands, finished),
                         name='Native PTY input', daemon=True).start()
        while True:
            alive = process.isalive()
            if not alive:
                if dead_at is None:
                    dead_at = time.monotonic()
                if drained.is_set() and output.empty():
                    status = process.exitstatus
                    if type(status) is not int:
                        error('PTY exited without a confirmed exit status')
                        status = None
                    emit('exit', exitstatus=status)
                    return 1 if failed else 0
                if time.monotonic() - dead_at >= OUTPUT_DRAIN_TIMEOUT:
                    if io_cancel_at is None:
                        # Ordinary process exit need not emit a terminal EOF
                        # sequence. After the original drain grace, cancel only
                        # pending I/O on the already-dead child. The library's
                        # real reader still has to close and report EOF.
                        io_cancel_at = time.monotonic()
                        try:
                            process.pty.cancel_io()
                        except Exception as problem:
                            error('Could not close exited PTY I/O: ' + str(problem))
                            return 1
                    elif time.monotonic() - io_cancel_at >= IO_CANCEL_DRAIN_TIMEOUT:
                        error('PTY output drain is unconfirmed after process exit')
                        return 1
            # Bound work per turn so a large output stream cannot starve cancel.
            for _ in range(16):
                try:
                    kind, value = commands.get_nowait()
                except queue.Empty:
                    break
                if not alive:
                    continue
                if kind == 'error':
                    error(value)
                    cancel()
                elif kind == 'cancel' or value['type'] == 'cancel':
                    cancel()
                elif cancelling is None:
                    if value['type'] == 'write':
                        process.write(value['text'])
                    else:
                        process.setwinsize(value['rows'], value['cols'])
            for _ in range(16):
                try:
                    kind, value = output.get_nowait()
                except queue.Empty:
                    break
                if kind == 'error':
                    error('PTY output failed: ' + value)
                    if alive:
                        cancel()
                else:
                    emit('output', text=value)
            if alive and drained.is_set() and cancelling is None:
                error('PTY output closed before process exit was confirmed')
                cancel()
            if alive and cancelling is not None and time.monotonic() - cancelling >= cancel_timeout:
                error('PTY exit is unconfirmed after Ctrl+C; host containment is required')
                return 1
            time.sleep(POLL_INTERVAL)
    except Exception as problem:
        # A dead host pipe must still request graceful interruption once.
        if process is not None and not ctrl_c_sent:
            try:
                process.write('\x03')
            except Exception:
                pass
        deadline = time.monotonic() + cancel_timeout if type(cancel_timeout) in (int, float) and math.isfinite(cancel_timeout) and 0 <= cancel_timeout <= 30 else time.monotonic()
        while process is not None and time.monotonic() < deadline:
            try:
                if not process.isalive():
                    break
            except Exception:
                break
            time.sleep(POLL_INTERVAL)
        try:
            emit('error', message=('Terminal transport failed: ' + str(problem))[:400])
        except (BrokenPipeError, OSError, ValueError):
            pass
        return 1
    finally:
        finished.set()
        # Never call terminate/close(force=True): those use process signals and
        # are not a claim of an official graceful calibration cancellation.


def main(argv=None):
    return run(sys.argv[1:] if argv is None else argv)


if __name__ == '__main__':
    # The worker has daemon readers that may be blocked in host/PTY pipes.
    # Avoid interpreter shutdown waiting on those pipe locks. run() already
    # flushes every JSONL message; the host owns final Job containment.
    os._exit(main())
