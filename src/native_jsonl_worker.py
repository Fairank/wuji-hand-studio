"""Raw pipes for official JSONL, with Ctrl+C in a private hidden console.

ConPTY is for interactive artwork; it may render/wrap machine records. This
transport never sends structured output through a terminal renderer. The caller
must already be inside its exact private kill-on-close Job. No command selection
or SDK logic lives here. Parent EOF requests one graceful Ctrl+C, then the host
Job contains an unresponsive child. No external console is ever attached.
"""
import ctypes
from ctypes import wintypes as w
import json
import os
import queue
import subprocess
import sys
import threading
import time

from native_terminal_worker import _validate_argv, _read_input


class PrivateConsole:
    def __init__(self):
        if sys.platform!='win32':raise OSError('Windows private console required')
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.GetConsoleWindow.restype=w.HWND
        self.kernel.FreeConsole.restype=w.BOOL
        self.kernel.AllocConsole.restype=w.BOOL
        self.kernel.GenerateConsoleCtrlEvent.argtypes=[w.DWORD,w.DWORD]
        self.kernel.GenerateConsoleCtrlEvent.restype=w.BOOL
        # Detach only this worker, never attach to any parent/user console.
        self.kernel.FreeConsole()
        if not self.kernel.AllocConsole():raise ctypes.WinError(ctypes.get_last_error())
        window=self.kernel.GetConsoleWindow()
        if window:
            user=ctypes.WinDLL('user32',use_last_error=True)
            user.ShowWindow.argtypes=[w.HWND,ctypes.c_int]
            user.ShowWindow(window,0)
        self.callback=ctypes.WINFUNCTYPE(w.BOOL,w.DWORD)(lambda event:event in (0,1))
        self.kernel.SetConsoleCtrlHandler.argtypes=[ctypes.c_void_p,w.BOOL]
        self.kernel.SetConsoleCtrlHandler.restype=w.BOOL
        # A callback is process-local; do not set the inheritable IGNORE flag.
        if not self.kernel.SetConsoleCtrlHandler(None,False) or not self.kernel.SetConsoleCtrlHandler(self.callback,True):
            self.close();raise ctypes.WinError(ctypes.get_last_error())

    def cancel(self):
        if not self.kernel.GenerateConsoleCtrlEvent(0,0):raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        self.kernel.FreeConsole()


def run(argv, *, stdin=None, stdout=None, cancel_timeout=5.0):
    stdin=sys.stdin if stdin is None else stdin
    stdout=sys.stdout if stdout is None else stdout
    argv=_validate_argv(argv)
    if type(cancel_timeout) not in (int,float) or not 0<=cancel_timeout<=30:raise ValueError('Invalid cancellation timeout')
    console=None;process=None;cancel_at=None;failed=False
    finished=threading.Event();commands=queue.Queue(64);output=queue.Queue(64)
    drained=[threading.Event(),threading.Event()]

    def emit(kind,**fields):
        stdout.write(json.dumps(dict(type=kind,**fields),ensure_ascii=True)+'\n');stdout.flush()

    def put(value):
        while not finished.is_set():
            try:output.put(value,timeout=.05);return
            except queue.Full:pass

    def read(stream,index):
        try:
            while not finished.is_set():
                line=stream.readline(65537)
                if not line:break
                if len(line)>65536:raise ValueError('Official JSONL pipe record exceeds its limit')
                # Bound escaped transport records even for all-Unicode JSONL.
                # The host's incremental decoder owns logical line boundaries.
                for offset in range(0,len(line),4096):
                    put(('output' if index==0 else 'diagnostic',line[offset:offset+4096]))
        except Exception as error:put(('error',str(error)[:500]))
        finally:drained[index].set()

    def cancel():
        nonlocal cancel_at
        if cancel_at is None:
            cancel_at=time.monotonic();console.cancel()

    try:
        console=PrivateConsole()
        startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
        # No NEW_PROCESS_GROUP: that would disable Ctrl+C in the child. Its
        # console is exclusively this worker's, and Job membership is inherited.
        process=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            text=True,encoding='utf-8',errors='strict',bufsize=1,startupinfo=startup)
        for index,stream in enumerate((process.stdout,process.stderr)):
            threading.Thread(target=read,args=(stream,index),daemon=True).start()
        threading.Thread(target=_read_input,args=(stdin,commands,finished),daemon=True).start()
        dead_at=None
        while True:
            code=process.poll()
            if code is not None:
                if dead_at is None:dead_at=time.monotonic()
                if all(event.is_set() for event in drained) and output.empty():
                    emit('exit',exitstatus=code);return 1 if failed else 0
                if time.monotonic()-dead_at>2:
                    emit('error',message='Official JSONL pipe drain is unconfirmed');return 1
            for _ in range(16):
                try:kind,value=commands.get_nowait()
                except queue.Empty:break
                if code is not None:continue
                if kind=='cancel' or kind=='command' and value.get('type')=='cancel':cancel()
                else:
                    failed=True;emit('error',message='Guided calibration accepts cancellation only');cancel()
            for _ in range(16):
                try:kind,value=output.get_nowait()
                except queue.Empty:break
                emit(kind,**({'message':value} if kind=='error' else {'text':value}))
                if kind=='error':
                    failed=True
                    if code is None:cancel()
            if cancel_at is not None and code is None and time.monotonic()-cancel_at>=cancel_timeout:
                emit('error',message='Official CLI exit is unconfirmed after Ctrl+C; host containment is required');return 1
            time.sleep(.02)
    except Exception as error:
        if process is not None and process.poll() is None:
            try:cancel();process.wait(timeout=cancel_timeout)
            except Exception:pass
        try:emit('error',message=str(error)[:500])
        except (OSError,ValueError):pass
        return 1
    finally:
        finished.set()
        if process is not None and process.poll() is not None:
            for stream in (process.stdout,process.stderr):
                try:stream.close()
                except (OSError,ValueError):pass
        if console is not None:console.close()
