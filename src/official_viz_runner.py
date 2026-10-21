"""Own one Linux official Viewer process; never import the SDK or scan devices.

Official stdout is forwarded unchanged. Workbench lifecycle and stderr records
use a separate ``workbench_event`` key. EOF/``stop`` requests an interrupt of
this runner's child only; ownership is kept until that child exits.
"""
from contextlib import contextmanager, ExitStack
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import threading
import time


# Match the serial-number syntax already accepted by calibration_cli.py.
_SERIAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
INTERRUPT_TIMEOUT = 5
TERMINATE_TIMEOUT = 2
KILL_TIMEOUT = 1


def _absolute(value, label):
    if not isinstance(value, str) or '\0' in value or not Path(value).is_absolute():
        raise ValueError(label + ' must be an absolute path')
    return Path(value)


def _uid():
    return os.getuid()


def _starttime(pid):
    try:
        # /proc stat's comm can itself contain spaces and parentheses.
        return Path('/proc/' + str(pid) + '/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _parent_death_signal():
    """Resolve libc in the parent; the child only installs/verifies prctl."""
    import ctypes
    libc = ctypes.CDLL(None)
    prctl, getppid, exit_now = libc.prctl, libc.getppid, libc._exit
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    prctl.restype = ctypes.c_int
    getppid.argtypes = []
    getppid.restype = ctypes.c_int
    exit_now.argtypes = [ctypes.c_int]
    exit_now.restype = None
    parent_pid = os.getpid()
    death_signal = int(signal.SIGKILL)

    def prepare_child():
        # Covers both death after prctl and death between fork and prctl. No
        # imports, file operations or Python-side cleanup occur after fork.
        if prctl(1, death_signal, 0, 0, 0) != 0 or getppid() != parent_pid:
            exit_now(127)
    return prepare_child


@contextmanager
def _session_gate(directory):
    import fcntl
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != _uid():
        raise ValueError('Session directory must belong to the current Linux user')
    with (directory / '.runner.lock').open('a+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('Another official Viewer runner owns this session directory') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _publish(directory, row):
    path = directory / 'session.json'
    if path.exists() or path.is_symlink():
        # A dead runner can have a surviving child. Recovery must inspect that
        # record; a new launch must not silently replace it.
        raise ValueError('An official Viewer session record already exists; stop or recover it first')
    pending = directory / ('.session-' + row['token'] + '.pending')
    try:
        with pending.open('x', encoding='utf-8') as stream:
            json.dump(row, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def _remove_own_record(directory, token):
    path = directory / 'session.json'
    try:
        row = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return True
    if isinstance(row, dict) and row.get('token') == token:
        path.unlink(missing_ok=True)
        return True
    return False


class _Output:
    def __init__(self, stream, stop):
        self.stream = stream
        self.stop = stop
        self.lock = threading.Lock()
        self.line_ended = True

    def raw(self, line):
        with self.lock:
            try:
                self.stream.write(line)
                self.stream.flush()
                self.line_ended = line.endswith('\n')
            except (OSError, ValueError):
                self.stop.set()

    def event(self, name, **fields):
        with self.lock:
            try:
                # A truncated final official line must not swallow our event.
                if not self.line_ended:
                    self.stream.write('\n')
                self.stream.write(json.dumps(dict(workbench_event=name, **fields), ensure_ascii=False) + '\n')
                self.stream.flush()
                self.line_ended = True
            except (OSError, ValueError):
                self.stop.set()


def _forward(stream, output, stop, stderr=False):
    try:
        for line in stream:
            if stderr:
                output.event('stderr', message=line.rstrip('\r\n'))
            else:
                output.raw(line)
    except (OSError, ValueError):
        stop.set()


def _read_input(stream, stop):
    try:
        for line in stream:
            if line.strip() == 'stop':
                break
    except (OSError, ValueError):
        pass
    finally:
        stop.set()


def _interrupt_child(process):
    """Bounded shutdown of this Popen child, never a process-name/group kill."""
    code = process.poll()
    if code is not None:
        return process.wait(timeout=0), 'none'
    actions = ((lambda: process.send_signal(signal.SIGINT), INTERRUPT_TIMEOUT, 'SIGINT'),
               (process.terminate, TERMINATE_TIMEOUT, 'SIGTERM'),
               (process.kill, KILL_TIMEOUT, 'SIGKILL'))
    for action, timeout, stage in actions:
        try:
            action()
        except ProcessLookupError:
            pass
        try:
            return process.wait(timeout=timeout), stage
        except subprocess.TimeoutExpired:
            pass
    raise RuntimeError('Official Viewer child exit is unconfirmed after SIGKILL')


@contextmanager
def _stop_signals(stop):
    previous = {}
    try:
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, lambda *_: stop.set())
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def run(cli, serial, session_dir, *, stdin=None, stdout=None):
    """Run the official CLI, preserving its result and both stream contents."""
    if not sys.platform.startswith('linux'):
        raise ValueError('Official Viewer runner requires Linux')
    executable = _absolute(cli, 'CLI')
    directory = _absolute(session_dir, 'Session directory')
    if not isinstance(serial, str) or not _SERIAL.fullmatch(serial):
        raise ValueError('Invalid official device serial number')
    from sdk_session import ProfileLease
    from device_ownership import DeviceOwnership

    stop = threading.Event()
    output = _Output(sys.stdout if stdout is None else stdout, stop)
    input_stream = sys.stdin if stdin is None else stdin
    process = None
    readers = []
    stop_attempted = False
    code = None
    escalation = 'none'
    failure = None
    cleanup_confirmed = False
    token = secrets.token_hex(16)
    with ExitStack() as stack:
        stack.enter_context(ProfileLease())
        # selection=True is the existing exclusive user-selection lease; it
        # freezes the current selection without changing any SDK user.
        stack.enter_context(ProfileLease(selection=True))
        stack.enter_context(DeviceOwnership(serial))
        stack.enter_context(_session_gate(directory))
        try:
            if (directory / 'session.json').exists() or (directory / 'session.json').is_symlink():
                raise ValueError('An official Viewer session record already exists; stop or recover it first')
            started = _starttime(os.getpid())
            if started is None:
                raise ValueError('Cannot identify the Linux Viewer runner process')
            prepare_child = _parent_death_signal()
            with _stop_signals(stop):
                process = subprocess.Popen(
                    [str(executable), 'viz', '--sn', serial, '--no-zero', '--no-open', '--jsonl'],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding='utf-8', errors='replace', bufsize=1,
                    start_new_session=True, preexec_fn=prepare_child,
                    env={**os.environ, 'WUJI_NO_UPDATE_CHECK': '1', 'WUJI_TELEMETRY_DISABLE': '1'})
                row = dict(kind='official_viz_runner', pid=os.getpid(), starttime=started,
                           uid=_uid(), sn=serial, token=token,
                           runner=str(Path(__file__).resolve()), cli=str(executable),
                           python=sys.executable,
                           session_dir=str(directory), created_at=time.time(),
                           child_pid=process.pid, child_starttime=_starttime(process.pid))
                _publish(directory, row)
                output.event('started', runner_pid=row['pid'], child_pid=process.pid,
                             sn=serial, token=token)
                for stream, stderr in ((process.stdout, False), (process.stderr, True)):
                    thread = threading.Thread(target=_forward, args=(stream, output, stop, stderr), daemon=True)
                    readers.append(thread)
                    thread.start()
                threading.Thread(target=_read_input, args=(input_stream, stop), daemon=True).start()
                while True:
                    if stop.is_set():
                        stop_attempted = True
                        code, escalation = _interrupt_child(process)
                        break
                    try:
                        code = process.wait(timeout=.1)
                        break
                    except subprocess.TimeoutExpired:
                        pass
        except Exception as error:
            failure = error
        finally:
            try:
                if process is not None and process.poll() is None and not stop_attempted:
                    stop_attempted = True
                    code, escalation = _interrupt_child(process)
            finally:
                for thread in readers:
                    thread.join(timeout=.5)
                if process is not None:
                    for index, stream in enumerate((process.stdout, process.stderr)):
                        if index >= len(readers) or not readers[index].is_alive():
                            stream.close()
                # Retain the marker when shutdown is unconfirmed, so a later
                # launcher cannot claim the device session has disappeared.
                reaped = process is None or process.poll() is not None
                if process is not None and reaped:
                    code = process.wait(timeout=0)
                    cleanup_confirmed = _remove_own_record(directory, token)
                    if not cleanup_confirmed:
                        raise RuntimeError('Official Viewer session metadata changed; cleanup is unconfirmed')
    # ExitStack has now released the session gate, device lock and both profile
    # leases. This is the only positive cleanup confirmation emitted to the host.
    if cleanup_confirmed:
        output.event('exited', cleanup_confirmed=True, sn=serial, returncode=code,
                     stop_requested=stop.is_set(), escalation=escalation)
    if failure is not None:
        raise failure
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli')
    parser.add_argument('--sn')
    parser.add_argument('--session-dir')
    recovery=parser.add_mutually_exclusive_group()
    recovery.add_argument('--probe')
    recovery.add_argument('--stop')
    args = parser.parse_args(argv)
    try:
        if args.probe or args.stop:
            from official_viz_recovery import inspect_session,stop_session
            value=stop_session(args.stop) if args.stop else inspect_session(args.probe)
            print(json.dumps(value),flush=True)
            return 0 if value.get('cleanup_confirmed') else 1
        if not all((args.cli,args.sn,args.session_dir)):raise ValueError('Expected --cli, --sn and --session-dir')
        return run(args.cli, args.sn, args.session_dir)
    except Exception as error:
        print(json.dumps(dict(workbench_event='error', message=str(error))), flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
