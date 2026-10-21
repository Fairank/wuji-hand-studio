"""Own a Windows child process tree with a kill-on-close Job Object.

No process is resumed before it belongs to this job. This module does not
discover devices, run a shell, or import an SDK. It is importable on Linux.
Job termination is an OS lifecycle operation, not graceful application cleanup.
"""
import math
import os
import re
import subprocess
import threading
import time


CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
_VIZ_JOB = re.compile(r'Local\\HandWorkbench-OfficialViz-[0-9a-f]{32}\Z')


def _is_windows():
    return os.name == 'nt'


class _WindowsAPI:
    """Small, lazy ctypes boundary; all handles are local and non-inherited."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        self.ctypes = ctypes
        self.w = w
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        size_t = ctypes.c_size_t

        class BasicLimits(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                        ('LimitFlags', w.DWORD), ('MinimumWorkingSetSize', size_t),
                        ('MaximumWorkingSetSize', size_t), ('ActiveProcessLimit', w.DWORD),
                        ('Affinity', size_t), ('PriorityClass', w.DWORD), ('SchedulingClass', w.DWORD)]

        class IOCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                         'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', BasicLimits), ('IoInfo', IOCounters),
                        ('ProcessMemoryLimit', size_t), ('JobMemoryLimit', size_t),
                        ('PeakProcessMemoryUsed', size_t), ('PeakJobMemoryUsed', size_t)]

        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int64) for name in
                        ('TotalUserTime', 'TotalKernelTime', 'ThisPeriodTotalUserTime', 'ThisPeriodTotalKernelTime')]
            _fields_ += [(name, w.DWORD) for name in
                         ('TotalPageFaultCount', 'TotalProcesses', 'ActiveProcesses', 'TotalTerminatedProcesses')]

        class ThreadEntry(ctypes.Structure):
            _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('th32ThreadID', w.DWORD),
                        ('th32OwnerProcessID', w.DWORD), ('tpBasePri', w.LONG),
                        ('tpDeltaPri', w.LONG), ('dwFlags', w.DWORD)]

        self.ExtendedLimits, self.Accounting, self.ThreadEntry = ExtendedLimits, Accounting, ThreadEntry
        k = self.kernel
        prototypes = {
            'CreateJobObjectW': ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            'OpenJobObjectW': ([w.DWORD, w.BOOL, w.LPCWSTR], w.HANDLE),
            'SetInformationJobObject': ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            'QueryInformationJobObject': ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p], w.BOOL),
            'AssignProcessToJobObject': ([w.HANDLE, w.HANDLE], w.BOOL),
            'TerminateJobObject': ([w.HANDLE, w.UINT], w.BOOL),
            'SetHandleInformation': ([w.HANDLE, w.DWORD, w.DWORD], w.BOOL),
            'CloseHandle': ([w.HANDLE], w.BOOL),
            'CreateToolhelp32Snapshot': ([w.DWORD, w.DWORD], w.HANDLE),
            'Thread32First': ([w.HANDLE, ctypes.POINTER(ThreadEntry)], w.BOOL),
            'Thread32Next': ([w.HANDLE, ctypes.POINTER(ThreadEntry)], w.BOOL),
            'OpenThread': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            'ResumeThread': ([w.HANDLE], w.DWORD),
        }
        for name, (args, result) in prototypes.items():
            getattr(k, name).argtypes = args
            getattr(k, name).restype = result

    def _check(self, result):
        if not result:
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        return result

    def create(self, name=None):
        self.ctypes.set_last_error(0)
        handle = self._check(self.kernel.CreateJobObjectW(None, name))
        try:
            if name is not None and self.ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
                raise OSError('Named process job already exists')
            self._check(self.kernel.SetHandleInformation(handle, 1, 0))
            limits = self.ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
            self._check(self.kernel.SetInformationJobObject(handle, 9, self.ctypes.byref(limits), self.ctypes.sizeof(limits)))
            return handle
        except BaseException:
            self.close(handle)
            raise

    def open_named(self, name):
        handle = self.kernel.OpenJobObjectW(0x0004 | 0x0008, False, name)  # QUERY | TERMINATE
        if not handle:
            error = self.ctypes.get_last_error()
            if error == 2:  # ERROR_FILE_NOT_FOUND: not an access-denied/malformed-name success.
                return None
            raise self.ctypes.WinError(error)
        return handle

    def assign(self, job, process):
        self._check(self.kernel.AssignProcessToJobObject(job, int(process._handle)))

    def resume_primary(self, pid):
        c, k = self.ctypes, self.kernel
        snapshot = k.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
        if snapshot == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            entry = self.ThreadEntry()
            entry.dwSize = c.sizeof(entry)
            threads = []
            ok = k.Thread32First(snapshot, c.byref(entry))
            if not ok and c.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                raise c.WinError(c.get_last_error())
            while ok:
                if entry.th32OwnerProcessID == pid:
                    threads.append(entry.th32ThreadID)
                entry.dwSize = c.sizeof(entry)
                ok = k.Thread32Next(snapshot, c.byref(entry))
            if c.get_last_error() != 18:
                raise c.WinError(c.get_last_error())
            if len(threads) != 1:
                raise OSError('Suspended child must have exactly one primary thread')
            thread = self._check(k.OpenThread(0x0002, False, threads[0]))  # THREAD_SUSPEND_RESUME
            try:
                previous = k.ResumeThread(thread)
                if previous == 0xffffffff:
                    raise c.WinError(c.get_last_error())
                if previous != 1:
                    raise OSError('Unexpected primary-thread suspend count: ' + str(previous))
            finally:
                self.close(thread)
        finally:
            self.close(snapshot)

    def active(self, job):
        info = self.Accounting()
        self._check(self.kernel.QueryInformationJobObject(job, 1, self.ctypes.byref(info), self.ctypes.sizeof(info), None))
        return int(info.ActiveProcesses)

    def terminate(self, job):
        self._check(self.kernel.TerminateJobObject(job, 1))

    def close(self, handle):
        self._check(self.kernel.CloseHandle(handle))


def _timeout(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('Timeout must be a finite nonnegative number')
    return value


def _job_name(value):
    if not isinstance(value, str) or not _VIZ_JOB.fullmatch(value):
        raise ValueError('Expected an exact Workbench official-visualization job name')
    return value


def recover_viz_job(name, timeout=5):
    """Terminate only an identity-checked stale Workbench visualization job.

    The caller must hold the session gate and verify that the recorded owner's
    PID/start identity is dead and that this name matches its recorded token.
    This function never selects jobs by prefix, PID, executable name or path.
    """
    name = _job_name(name)
    deadline = time.monotonic() + _timeout(timeout)
    if not _is_windows():
        raise OSError('Visualization job recovery requires Windows')
    api = _WindowsAPI()
    handle = None
    confirmed = False
    try:
        handle = api.open_named(name)
        if handle is None:
            return True
        if api.active(handle):
            api.terminate(handle)
        while True:
            if api.active(handle) == 0:
                confirmed = True
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(.02, remaining))
    except OSError:
        confirmed = False
    finally:
        if handle is not None:
            try:
                api.close(handle)
            except OSError:
                confirmed = False
    return confirmed


class OwnedProcess:
    """A Popen child and all descendants, isolated in a private unnamed job.

    ``process`` exposes the Popen streams/returncode. Call ``close`` explicitly.
    A False return retains the job handle for retry and is not confirmation of
    application cleanup. Exiting the parent process closes the non-inherited
    job handle in the kernel, which kills every remaining descendant.
    """
    def __init__(self, args, *, job_name=None, **popen_kwargs):
        if not _is_windows():
            raise OSError('OwnedProcess requires Windows')
        if popen_kwargs.get('shell'):
            raise ValueError('OwnedProcess does not run a shell')
        flags = popen_kwargs.get('creationflags', 0)
        if type(flags) is not int or flags & 0x01000000:  # CREATE_BREAKAWAY_FROM_JOB
            raise ValueError('Unsupported process creation flags')
        if popen_kwargs.get('start_new_session') or popen_kwargs.get('preexec_fn') is not None:
            raise ValueError('Use Windows process creation semantics')
        if job_name is not None:
            _job_name(job_name)
        self.job_name = job_name
        popen_kwargs['creationflags'] = flags | CREATE_SUSPENDED | CREATE_NO_WINDOW
        self._api = _WindowsAPI()
        self._lock = threading.RLock()
        self._job = self._api.create(job_name)
        self.process = None
        self.last_error = None
        try:
            self.process = subprocess.Popen(args, **popen_kwargs)
            self._api.assign(self._job, self.process)
            if self.process.poll() is not None:
                raise OSError('Suspended child exited before resume')
            self._api.resume_primary(self.process.pid)
        except BaseException:
            # A failed assignment must never fall back to an uncontained child.
            # At this point no user code has been intentionally resumed.
            reap_error = None
            try:
                if self.process is not None:
                    try:
                        self.process.kill()
                    except OSError:
                        pass  # Still wait on the exact process handle below.
                    try:
                        self.process.wait(timeout=5)
                    except (OSError, subprocess.TimeoutExpired) as error:
                        reap_error = error
            finally:
                self._api.close(self._job)
                self._job = None
            if reap_error is not None:
                # Closing an assigned job is a second containment guarantee;
                # an unassigned failed spawn still must be positively reaped.
                try:
                    self.process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired) as error:
                    raise RuntimeError('Failed startup child exit is unconfirmed') from error
            raise

    def active_processes(self):
        with self._lock:
            return 0 if self._job is None else self._api.active(self._job)

    def terminate_and_wait(self, timeout=5):
        deadline = time.monotonic() + _timeout(timeout)
        with self._lock:
            if self._job is None:
                return True
            try:
                if self._api.active(self._job):
                    self._api.terminate(self._job)
                while True:
                    empty = self._api.active(self._job) == 0
                    reaped = self.process.poll() is not None
                    if empty and reaped:
                        self.process.wait(timeout=0)
                        self.last_error = None
                        return True
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self.last_error = 'Process job termination is unconfirmed'
                        return False
                    time.sleep(min(.02, remaining))
            except OSError as error:
                self.last_error = str(error)
                return False

    def close(self, timeout=5):
        with self._lock:
            if self._job is None:
                return True
            if not self.terminate_and_wait(timeout):
                return False
            try:
                self._api.close(self._job)
            except OSError as error:
                self.last_error = str(error)
                return False
            self._job = None
            return True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if not self.close():
            raise RuntimeError(self.last_error or 'Process job cleanup is unconfirmed')
