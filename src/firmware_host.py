"""Host transport for one explicitly chosen official firmware workflow.

Firmware uses the official interactive confirmation. It never shares the
calibration kill-on-close job, sends Ctrl+C, or invents progress from ANSI.
"""
from collections import deque
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid

from cli_coordination import atomic, gate, identity, release, reservation, reserve, root_folder

SERIAL = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z')
KINDS = {'firmware_check', 'firmware', 'sdk_check', 'sdk_update'}
SAFE_ACTIONS = {'disconnect', 'hardware_stop', 'glove_stop', 'glove_disconnect',
    'calibration_cancel', 'program_stop', 'group_stop', 'demo_stop', 'official_viz_stop',
    'session_keepalive', 'hardware_keepalive', 'glove_keepalive', 'program_keepalive', 'group_beat'}


def firmware_environment():
    """Compatibility entry for the shared native CLI environment."""
    from native_environment import official_cli_environment
    return official_cli_environment()


def process_state(pid, expected_start):
    """Three states: permission/query failures are not evidence of process death."""
    if sys.platform!='win32':return 'unknown'
    import ctypes
    from ctypes import wintypes as w
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.CloseHandle.argtypes=[w.HANDLE]
    kernel.GetExitCodeProcess.argtypes=[w.HANDLE,ctypes.POINTER(w.DWORD)]
    kernel.GetProcessTimes.argtypes=[w.HANDLE,*([ctypes.POINTER(w.FILETIME)]*4)]
    ctypes.set_last_error(0)
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return 'dead' if ctypes.get_last_error()==87 else 'unknown'
    try:
        code=w.DWORD()
        if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)):return 'unknown'
        if code.value!=259:return 'dead'
        if not isinstance(expected_start, str) or not expected_start:return 'unknown'
        values=[w.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle,*map(ctypes.byref,values)):return 'unknown'
        actual=str((values[0].dwHighDateTime<<32)|values[0].dwLowDateTime)
        return 'live' if actual==expected_start else 'dead'
    finally:kernel.CloseHandle(handle)


def enforce_maintenance(command, folder=None):
    """Every workspace honours the same maintenance marker, including starts.

    Reads use GET. Stops remain available; session_close must not interrupt a
    firmware process, so desktop_lifecycle handles it with an explicit refusal.
    """
    if not isinstance(command, dict):return
    marker = reservation(folder)
    if marker.get('kind') in KINDS | {'unknown'} and command.get('name') not in SAFE_ACTIONS:
        raise ValueError('设备维护正在进行，请等待完成 / Device maintenance is in progress; wait for it to finish')


class FirmwareHost:
    def __init__(self, controller, *, folder=None, spawn=None, runner=None):
        self.controller = controller
        self.folder = Path(folder or root_folder())
        self.spawn = spawn
        self.runner = runner
        self.lock = threading.RLock()
        self.process = None
        self.session = None
        self.session_token = None
        self.pending_serial = None
        self.chunks = deque(maxlen=128)
        self.sequence = 0
        self.query_process = None
        self.query_job = None
        self.query_cancel = None
        self.query_automatic = False
        self.query_token = None
        self.query_serial = None
        self.query_exit_confirmed = False
        self._read_not_started = None
        self._reader_done = None
        self.package_validator = None
        self.package_releaser = None
        self._package_claim = None

    def validate_package_claim(self, archive):
        """Pin a private verified ZIP to this exact firmware reservation."""
        from firmware_service import FirmwareNotStarted, FirmwareWriterUnconfirmed
        no_writer = False
        try:
            if not callable(self.package_validator):raise ValueError('Official package validation is unavailable')
            with gate(self.folder):
                marker = reservation(self.folder)
                with self.lock:
                    no_writer = (marker.get('writer_stage') == 'reserved' and 'child_pid' not in marker
                        and 'child_start' not in marker and self.process is None and self.query_process is None
                        and self.query_job is None and self.query_cancel is None)
                if not no_writer:raise FirmwareWriterUnconfirmed('Official writer launch or exit is already unconfirmed')
                if (type(archive) is not dict or marker.get('kind') != 'firmware'
                        or marker.get('serial') != archive.get('serial') or not marker.get('token')
                        or archive.get('reservation_token') != marker['token']):
                    raise ValueError('Official package firmware ownership changed')
                verified = self.package_validator(archive)
                if verified != archive:raise ValueError('Official package changed')
                with self.lock:
                    previous = self._package_claim
                    if (previous and previous['reservation_token'] == marker['token']
                            and previous['archive'] != archive):
                        raise ValueError('Official package target changed')
                    self._package_claim = dict(reservation_token=marker['token'], archive=copy.deepcopy(archive))
                marker.update(package_mode='official_file_confirmation', package_version=archive['version'], package_sha256=archive['sha256'])
                atomic(self.folder/'session.json', marker)
                return copy.deepcopy(verified)
        except Exception as error:
            if no_writer:raise FirmwareNotStarted(str(error)) from error
            raise

    def file_readback(self, serial, *, reservation_token):
        """Read exact Glove identity/version after a confirmed writer exit."""
        from device_versions import parse_official_resource_json
        if not isinstance(serial, str) or not re.fullmatch(r'WG1[JK][A-Z0-9]{12}', serial):
            raise ValueError('Official package readback supports Glove 1 only')
        with gate(self.folder):
            marker = reservation(self.folder)
            if (marker.get('kind') != 'firmware' or marker.get('token') != reservation_token
                    or marker.get('serial') != serial or marker.get('command') != 'official-interactive-file-upgrade'):
                raise ValueError('Official package readback ownership changed')
            with self.lock:
                if self.process is not None and self.process.isalive():raise ValueError('Official writer is still running')
                known_exit = (self.session_token == reservation_token and self.process is None
                    and isinstance(self.session, dict) and self.session.get('serial') == serial
                    and self.session.get('running') is False and self.session.get('status') == 'finished'
                    and type(self.session.get('exitstatus')) is int)
            pid, start = marker.get('child_pid'), marker.get('child_start')
            if not known_exit and (type(pid) is not int or pid <= 0 or process_state(pid, start) != 'dead'):
                raise ValueError('Original official writer exit is unconfirmed')
            before = self.cli_identity()
            reports = {}
            for path in ('serial_number', 'firmware_version'):
                component = self._component()
                with self.lock:
                    self._read_not_started = None
                    self.query_token, self.query_serial = reservation_token, serial
                    self.query_exit_confirmed, self.query_automatic = False, False
                command = [component['binary'], 'get', path, '--sn', serial, '--json']
                if self.runner is None:
                    response = self._background_query(command, firmware_environment(), lambda:False)
                else:
                    try:
                        run = self.runner(command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=40, env=firmware_environment(),
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                        response = dict(exited=True, returncode=run.returncode, stdout=run.stdout, stderr=run.stderr)
                    finally:
                        with self.lock:self.query_exit_confirmed=True
                if response.get('exited') is not True or type(response.get('returncode')) is not int:
                    from firmware_service import ReadOnlyQueryUnconfirmed
                    raise ReadOnlyQueryUnconfirmed('Official resource query exit is unconfirmed')
                if response['returncode'] != 0:
                    raise ValueError('Official resource read did not succeed')
                value = parse_official_resource_json(response.get('stdout'), path)
                if value is None or path == 'serial_number' and value != serial:
                    raise ValueError('Official resource identity or format is unverified')
                reports[path] = dict(value=value, report=response)
            if self.cli_identity() != before or reservation(self.folder).get('token') != reservation_token:
                raise ValueError('Official readback identity changed')
            self._retire_recovered_writer(serial, reservation_token)
            return dict(process_exited=True, identity_verified=True, sn=serial,
                reservation_token=reservation_token, firmware_version=reports['firmware_version']['value'],
                source='official_resource_readback', resources=reports)

    def _component(self):
        # Until another OS has the same verified interactive transport, do not
        # route USB/network flashing through an unverified remote terminal.
        from bridge_config import load_config
        from official_device_discovery import use_native
        if not use_native(load_config()):
            raise ValueError('固件升级请使用 Windows 内置控制组件 / Use the Windows built-in controller for firmware updates')
        from native_cli import prepare
        return prepare(strict=True)

    def cli_identity(self):
        # Reject a bad home before the service reserves/launches a writer;
        # a configuration error must not become an unknown flash process.
        firmware_environment()
        component = self._component()
        return {'version': component['version'], 'sha256': component['binary_sha256'],
                'platform': 'windows-native'}

    def device(self, serial):
        if not isinstance(serial, str) or not SERIAL.fullmatch(serial):
            raise ValueError('Select a discovered device')
        rows = [row for row in self.controller.calibration_devices()
                if row.get('serial') == serial and not row.get('conflict')]
        if len(rows) != 1:
            raise ValueError('请重新扫描并选择设备 / Scan again and select the device')
        with self.lock:self.pending_serial=serial
        return copy.deepcopy(rows[0])

    def acquire(self, kind):
        if kind not in KINDS:raise ValueError('Invalid maintenance operation')
        with gate(self.folder):
            metadata=None
            if kind in {'firmware','firmware_check'}:
                with self.lock:serial=self.pending_serial
                if not serial:raise ValueError('Select the exact device before reserving firmware maintenance')
                metadata=dict(serial=serial)
                if kind == 'firmware':metadata['writer_stage'] = 'reserved'
            elif kind=='sdk_update':metadata=dict(stage='pending')
            # Reserve before checking other workspaces: a concurrent start
            # then sees the shared marker before it can reach a device.
            token = reserve(kind, self.folder,metadata=metadata)
            with self.lock:
                # This private proof is created before any read dispatch and
                # invalidated by every query entry. It only covers this exact
                # never-started reservation, including an idle/release failure.
                self._read_not_started = ((token, serial) if kind == 'firmware_check'
                    and self.process is None and self.query_process is None
                    and self.query_job is None and self.query_cancel is None else None)
            try:self.controller.official_cli.idle_check()
            except BaseException:
                try:release(token, self.folder)
                except Exception:
                    from firmware_service import FirmwareReservationHeld
                    raise FirmwareReservationHeld(token) from None
                raise
            return token

    def release(self, token):
        with gate(self.folder):
            current=reservation(self.folder)
            if current and current.get('token')!=token:raise ValueError('Maintenance ownership changed')
            release(token, self.folder)
            if callable(self.package_releaser):self.package_releaser(token)
            with self.lock:
                if self._package_claim and self._package_claim['reservation_token'] == token:self._package_claim=None
                if self._read_not_started and self._read_not_started[0] == token:self._read_not_started=None

    def _retire_recovered_writer(self, serial, reservation_token):
        """Retire only our exact dead PTY after its reader has also finished.

        A resource read can prove recovery while the old output channel failed.
        Keeping that dead process as a possible new writer strands the next
        task. A live/unknown process, changed owner or pending reader stays held.
        Caller holds the same filesystem gate as recovery/readback.
        """
        with self.lock:
            process, done = self.process, self._reader_done
            if process is None:return
            if (self.session_token != reservation_token or not isinstance(self.session,dict)
                    or self.session.get('serial') != serial):
                raise ValueError('Original firmware transport owner changed')
        marker = reservation(self.folder)
        pid = getattr(process, 'pid', None)
        if (marker.get('kind') != 'firmware' or marker.get('token') != reservation_token
                or marker.get('serial') != serial or type(pid) is not int
                or marker.get('child_pid') != pid or process.isalive()
                or process_state(pid, marker.get('child_start')) != 'dead'):
            raise ValueError('Original firmware transport exit is unconfirmed')
        if done is not None and not done.is_set():
            # The writer is already proven dead. Cancel only pending pipe I/O;
            # never terminate a firmware process or discard an active reader.
            process.pty.cancel_io()
            if not done.wait(1):raise ValueError('Original firmware output reader has not finished')
        with self.lock:
            if self.process is not process or self.session_token != reservation_token:
                raise ValueError('Original firmware transport owner changed')
            self.process = None
            self._reader_done = None
            self.session.update(running=False, status='recovered')

    def recovery_check(self, serial, *, reservation_token, read_only=False):
        """Confirm exact process exit and actual firmware readback, never retry."""
        with gate(self.folder):
            marker=reservation(self.folder)
            if read_only:
                if marker.get('kind')!='firmware_check' or marker.get('token')!=reservation_token or marker.get('serial')!=serial:
                    raise ValueError('Version-check recovery ownership changed')
                with self.lock:
                    if (self._read_not_started == (reservation_token, serial) and self.process is None
                            and self.query_process is None and self.query_job is None and self.query_cancel is None):
                        return dict(read_only=True, no_read_started=True, process_exited=True,
                            sn=serial, reservation_token=reservation_token)
                    if self.query_token!=reservation_token or self.query_serial!=serial:
                        raise ValueError('Original version-check identity is unavailable')
                    if self.query_job is not None and not self.query_job.close(timeout=1):
                        raise ValueError('版本检查进程尚未结束 / Version-check process is still running')
                    if self.query_process is not None:
                        if type(self.query_process.poll()) is not int:
                            raise ValueError('版本检查进程尚未结束 / Version-check process is still running')
                        self.query_exit_confirmed=True
                        self.query_process=None;self.query_job=None;self.query_cancel=None
                    if not self.query_exit_confirmed:
                        raise ValueError('版本检查进程尚未确认退出 / Version-check process exit remains unconfirmed')
                return dict(read_only=True,process_exited=True,sn=serial,reservation_token=reservation_token)
            if marker.get('kind')!='firmware' or marker.get('token')!=reservation_token or marker.get('serial')!=serial:
                raise ValueError('Firmware recovery ownership changed')
            with self.lock:
                never_started = (marker.get('writer_stage') == 'reserved' and 'child_pid' not in marker
                    and 'child_start' not in marker and self.process is None and self.query_process is None
                    and self.query_job is None and self.query_cancel is None)
            if never_started:
                return dict(no_write_started=True, process_exited=True, sn=serial, reservation_token=reservation_token)
            with self.lock:
                if self.session and self.session.get('running') and self.process is not None:
                    if self.process.isalive():raise ValueError('官方升级进程仍在运行 / The official update process is still running')
            pid,start=marker.get('child_pid'),marker.get('child_start')
            file_mode = marker.get('command') == 'official-interactive-file-upgrade'
            if not file_mode:
                if type(pid) is not int or pid<=0 or not isinstance(start,str) or not start:
                    raise ValueError('原升级进程标识未保存，暂不能自动解除维护 / The original update process identity is unavailable; maintenance cannot be cleared automatically')
                observation=process_state(pid,start)
                if observation!='dead':
                    raise ValueError('原升级进程尚未确认退出 / The original update process exit is not verified')
            # Reuse the exact-SN JSON contract captured from the pinned CLI.
            # `get` help documents a value, but does not establish an identity
            # envelope; guessing sn/device/value could strand real recovery.
            # Keep the firmware lease while this bounded read runs. It is not
            # a second flash, a catalog target, or proof the target installed.
            with self.lock:
                if self.query_cancel is not None:
                    if self.query_token!=reservation_token or self.query_serial!=serial:
                        raise ValueError('Firmware version-read ownership changed')
                    if self.query_job is not None and not self.query_job.close(timeout=1):
                        raise ValueError('版本核验进程尚未结束 / Version-verification process is still running')
                    if self.query_process is None or type(self.query_process.poll()) is not int:
                        raise ValueError('版本核验进程退出尚未确认 / Version-verification process exit remains unconfirmed')
                    self.query_process=None;self.query_job=None;self.query_cancel=None
            if file_mode:
                return self.file_readback(serial, reservation_token=reservation_token)
            report=self(['upgrade','--sn',serial,'--check','--json'])
            from device_versions import parse_official_check_json
            observed=(parse_official_check_json(report.get('stdout'),serial)
                if type(report.get('returncode')) is int and report['returncode']==0 and report.get('exited') is True else None)
            if observed is None:raise ValueError('设备身份与固件版本尚未读回 / Device identity and firmware version are not yet verified')
            version=observed['current']
            if reservation(self.folder).get('token')!=reservation_token:raise ValueError('Firmware recovery ownership changed')
            self._retire_recovered_writer(serial, reservation_token)
            return dict(process_exited=True,identity_verified=True,sn=serial,
                        reservation_token=reservation_token,firmware_version=version,
                        verification_report=report)

    def run_snapshot(self):
        with self.lock:return copy.deepcopy(self.session)

    def terminal_read(self, run_id, after):
        if not isinstance(run_id, str) or not re.fullmatch('[0-9a-f]{32}', run_id):raise ValueError('Invalid firmware run')
        if type(after) is not int or after < 0:raise ValueError('Invalid firmware cursor')
        with self.lock:
            if not self.session or self.session['id'] != run_id:raise ValueError('Firmware session changed')
            rows = list(self.chunks)
            return dict(id=run_id, chunks=[copy.deepcopy(row) for row in rows if row['sequence'] > after],
                        gap=bool(rows and after < rows[0]['sequence'] - 1))

    def terminal_input(self, command):
        expected = {'name', 'run_id', 'type', 'text'} if command.get('type') == 'write' else {'name', 'run_id', 'type', 'rows', 'cols'}
        if set(command) != expected:raise ValueError('Invalid firmware terminal request')
        if command['type'] == 'write':
            text = command['text']
            # Only the official confirmation/menu input. No Ctrl+C/Ctrl+D,
            # paste control sequences, shell escapes, or arbitrary PTY control.
            if (not isinstance(text, str) or not text or len(text) > 64
                    or not (text in ('\x1b[A', '\x1b[B', '\r', '\n')
                            or all(32 <= ord(c) <= 126 for c in text))):
                raise ValueError('Only confirmation input is accepted during firmware updates')
        elif (command['type'] != 'resize' or type(command['rows']) is not int
                or type(command['cols']) is not int or not 1 <= command['rows'] <= 100
                or not 81 <= command['cols'] <= 240):raise ValueError('Invalid terminal dimensions')
        with gate(self.folder),self.lock:
            marker=reservation(self.folder)
            if (not self.session or self.session['id'] != command['run_id']
                    or self.session['running'] is not True or self.session.get('status')!='running'
                    or self.process is None or not self.session_token or marker.get('kind')!='firmware'
                    or marker.get('token')!=self.session_token or marker.get('serial')!=self.session.get('serial')):
                raise ValueError('Official firmware terminal ownership is unverified')
            if command['type'] == 'write':self.process.write(text)
            else:self.process.setwinsize(command['rows'], command['cols'])
        return dict(submitted=True)

    def _append(self, text, run_id=None):
        if not isinstance(text, str):raise ValueError('Invalid official terminal output')
        with self.lock:
            if run_id is not None and (not self.session or self.session.get('id') != run_id):return
            for at in range(0, len(text), 4096):
                self.sequence += 1
                self.chunks.append(dict(sequence=self.sequence, text=text[at:at+4096]))

    def cancel_background_check(self):
        """Request cancellation of only our finite read-only --check process.

        The owning query thread confirms its actual process exit before the
        service releases the shared reservation. The firmware PTY is unrelated.
        """
        with self.lock:
            if self.query_cancel is None or not self.query_automatic:return False
            self.query_cancel.set()
            return True

    def _spawn_background(self,command,env):
        # Read-only checks, unlike the interactive flash PTY, must never outlive
        # a crashing root. Reuse the existing suspended-before-assignment,
        # non-inherited kill-on-close Job rather than a naked Popen child.
        from windows_process_job import OwnedProcess
        return OwnedProcess(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            text=True,encoding='utf-8',errors='replace',env=env)

    def _background_query(self, command, env, external_cancelled):
        cancelled=threading.Event()
        with self.lock:
            if self.query_cancel is not None:raise ValueError('A background firmware check is already running')
            self._read_not_started=None
            self.query_cancel=cancelled
        process=None;owned=None;startup_unknown=False
        def stop_read():
            try:process.terminate()
            except OSError:pass
            try:return process.communicate(timeout=1)
            except (subprocess.TimeoutExpired,OSError):
                process.kill()
                return process.communicate(timeout=1)
        try:
            if cancelled.is_set() or external_cancelled():return dict(returncode=1,stdout='',stderr='Background check deferred',exited=True)
            try:owned=self._spawn_background(command,env)
            except RuntimeError:
                startup_unknown=True
                from firmware_service import ReadOnlyQueryUnconfirmed
                raise ReadOnlyQueryUnconfirmed('版本检查子进程启动状态尚未确认 / Version-check child startup is unconfirmed') from None
            process=owned.process
            with self.lock:self.query_process=process;self.query_job=owned
            deadline=time.monotonic()+40
            while True:
                if cancelled.is_set() or external_cancelled() or time.monotonic()>=deadline:
                    # This path is reachable only for the fixed noninteractive
                    # --check argv below. Never terminate the firmware PTY.
                    out,err=stop_read()
                    return dict(returncode=1,stdout=out,stderr='Background check deferred' if cancelled.is_set() or external_cancelled() else 'Firmware check timed out',exited=process.returncode is not None)
                try:
                    out,err=process.communicate(timeout=.1)
                    if len(out or '')+len(err or '')>2*1024*1024:raise ValueError('Official firmware report exceeds its limit')
                    return dict(returncode=process.returncode,stdout=out,stderr=err,exited=True)
                except subprocess.TimeoutExpired:continue
        except BaseException:
            if process is not None and process.poll() is None:
                try:stop_read()
                except Exception:
                    from firmware_service import ReadOnlyQueryUnconfirmed
                    raise ReadOnlyQueryUnconfirmed('后台版本检查进程尚未确认退出，请稍后重试 / Background version-check process exit is unconfirmed') from None
            raise
        finally:
            # A root crash closes this private non-inherited handle in the
            # kernel, killing the read subtree. Normal completion also proves
            # the entire read tree empty before releasing its shared lease.
            clean=not startup_unknown
            if owned is not None:
                try:clean=owned.close(timeout=1)
                except Exception:clean=False
            with self.lock:
                # Do not clear a reference to a child whose exit is uncertain.
                if clean and (process is None or process.poll() is not None):
                    self.query_exit_confirmed=True
                    self.query_process=None;self.query_job=None;self.query_cancel=None
            if not clean:
                from firmware_service import ReadOnlyQueryUnconfirmed
                raise ReadOnlyQueryUnconfirmed('版本检查进程组退出尚未确认 / Version-check process tree exit is unconfirmed') from None

    def __call__(self, argv, *, mutating=False, interactive=False, on_event=None, background=False, cancelled=None):
        if (type(background) is not bool or not isinstance(argv, list) or not 3 <= len(argv) <= 8
                or argv[0] != 'upgrade' or argv[1] != '--sn'
                or not isinstance(argv[2], str) or not SERIAL.fullmatch(argv[2])
                or (cancelled is not None and (not background or not callable(cancelled)))):
            raise ValueError('Unsupported official firmware command')
        serial = argv[2]
        file_mode = interactive and len(argv) == 5 and argv[3] == '--file'
        if interactive:
            if background or not mutating or not (argv == ['upgrade', '--sn', serial] or file_mode):raise ValueError('Firmware confirmation must remain with the official CLI')
            if file_mode:
                from firmware_service import FirmwareNotStarted, FirmwareWriterUnconfirmed
                with gate(self.folder), self.lock:
                    marker, claim = reservation(self.folder), self._package_claim
                    if (marker.get('writer_stage') != 'reserved' or 'child_pid' in marker
                            or 'child_start' in marker or self.process is not None):
                        raise FirmwareWriterUnconfirmed('Official writer launch or exit is already unconfirmed')
                    if (not claim or marker.get('kind') != 'firmware' or marker.get('serial') != serial
                            or marker.get('token') != claim['reservation_token']
                            or claim['archive']['serial'] != serial or claim['archive']['path'] != argv[4]):
                        raise FirmwareNotStarted('Official package is not owned by this firmware task')
                    self.validate_package_claim(claim['archive'])
        elif mutating or argv[3:] not in (['--check', '--json'], ['--check']):
            raise ValueError('Unsupported firmware read operation')
        if not interactive:
            marker=reservation(self.folder)
            with self.lock:
                self._read_not_started=None
                self.query_token=marker.get('token') if marker.get('kind') in {'firmware_check','firmware'} and marker.get('serial')==serial else None
                self.query_serial=serial;self.query_exit_confirmed=False;self.query_automatic=background
        try:component = self._component()
        except Exception:
            if not interactive:
                with self.lock:self.query_exit_confirmed=True
            if file_mode:
                from firmware_service import FirmwareNotStarted
                raise FirmwareNotStarted('Official CLI preparation failed') from None
            raise
        command = [component['binary'], *argv]
        env = firmware_environment()
        if not interactive:
            cancelled=cancelled or (lambda:False)
            if self.runner is None:return self._background_query(command,env,cancelled)
            if background and cancelled():
                with self.lock:self.query_exit_confirmed=True
                return dict(returncode=1,stdout='',stderr='Background check deferred',exited=True)
            try:
                result = (self.runner or subprocess.run)(command, stdin=subprocess.DEVNULL, capture_output=True,
                    text=True, encoding='utf-8', errors='replace', timeout=40, env=env,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            finally:
                # subprocess.run owns finite child cleanup before propagating.
                with self.lock:self.query_exit_confirmed=True
            if len(result.stdout or '') + len(result.stderr or '') > 2 * 1024 * 1024:
                raise ValueError('Official firmware report exceeds its limit')
            return dict(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr, exited=True)
        return self._interactive(command, serial, on_event, env=env)

    def _interactive(self, command, serial, on_event, *, env=None):
        # Keep a durable exact device identity before launching. On a crash a
        # surviving/uncertain firmware marker must never be auto-cleared.
        with gate(self.folder):
            marker = reservation(self.folder)
            if marker.get('kind') != 'firmware':raise ValueError('Firmware ownership is unverified')
            file_mode = len(command) == 6 and command[-2] == '--file'
            if file_mode:
                claim = self._package_claim
                if (marker.get('writer_stage') != 'reserved' or 'child_pid' in marker
                        or 'child_start' in marker or self.process is not None):
                    from firmware_service import FirmwareWriterUnconfirmed
                    raise FirmwareWriterUnconfirmed('Official writer launch or exit is already unconfirmed')
                if (not claim or claim['reservation_token'] != marker.get('token')
                        or claim['archive']['serial'] != serial or claim['archive']['path'] != command[-1]):
                    from firmware_service import FirmwareNotStarted
                    raise FirmwareNotStarted('Official package ownership changed before spawn')
                self.validate_package_claim(claim['archive'])
                marker.update(package_sha256=claim['archive']['sha256'], package_version=claim['archive']['version'])
            marker.update(serial=serial, command='official-interactive-file-upgrade' if file_mode else 'official-interactive-upgrade', writer_stage='starting')
            atomic(self.folder / 'session.json', marker)
        from native_terminal_worker import _spawn
        process = (self.spawn or _spawn)(command,env=firmware_environment() if env is None else env)
        run_id = uuid.uuid4().hex
        # pywinpty's destructor terminates a live PTY. Retain it before *any*
        # post-spawn I/O/identity operation can fail and unwind this stack.
        with self.lock:
            self.process = process
            self.session_token = marker['token']
            self.sequence = 0
            self.chunks.clear()
            self.session = dict(id=run_id, source='official_tty', running=True, status='unknown', serial=serial)
        with gate(self.folder):
            current=reservation(self.folder)
            if current.get('token')!=marker.get('token'):
                # Never interrupt a child which may already be writing.
                raise ValueError('Firmware ownership changed after launch')
            pid=getattr(process,'pid',None)
            current.update(child_pid=pid,child_start=identity(pid) if type(pid) is int else None, writer_stage='running')
            atomic(self.folder / 'session.json',current)
        with self.lock:
            self.session['status']='running'
        done = threading.Event()
        with self.lock:self._reader_done = done
        errors = []
        def read():
            try:
                while True:
                    value = process.read(4096)
                    if value:self._append(value, run_id)
            except EOFError:pass
            except Exception as error:errors.append(str(error)[:400])
            finally:done.set()
        reader=threading.Thread(target=read, name='Official firmware output', daemon=True)
        try:reader.start()
        except BaseException:
            if reader.ident is None:done.set()
            raise
        try:
            while process.isalive():time.sleep(.05)
            # Cancel only pending I/O after real process exit, never the flash.
            if not done.wait(1):
                process.pty.cancel_io()
                done.wait(1)
            status = process.exitstatus
            exited = type(status) is int and done.is_set() and not errors
        except Exception as error:
            errors.append(str(error)[:400]);status = None;exited = False
        with self.lock:
            self.session.update(running=not exited, status='finished' if exited else 'unknown', exitstatus=status)
            output=''.join(row['text'] for row in self.chunks)
            if exited:self.process = None
        return dict(returncode=status, stdout=output, stderr='; '.join(errors), exited=exited,
                    official_interactive=True, run_id=run_id)
