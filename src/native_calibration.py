"""Official Windows calibration, with exact ownership and original output.

PyWinpty runs the official human workflow. xterm renders its unmodified ANSI.
Human PTY output is never interpreted as SDK progress or success. Guided mode
reads original JSONL through raw pipes, outside the terminal renderer.
"""
from collections import deque
import copy
import io
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


def _attach_pipes():
    """Windowed frozen Python has None streams even when it inherits pipes."""
    if sys.stdin is not None and sys.stdout is not None:return
    import ctypes
    from ctypes import wintypes as w
    import msvcrt
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.GetStdHandle.argtypes=[w.DWORD];k.GetStdHandle.restype=w.HANDLE
    k.GetFileType.argtypes=[w.HANDLE];k.GetFileType.restype=w.DWORD
    for attribute,number,flag in [('stdin',-10,os.O_RDONLY),('stdout',-11,os.O_WRONLY)]:
        if getattr(sys,attribute) is not None:continue
        handle=k.GetStdHandle(number & 0xffffffff)
        if not handle or k.GetFileType(handle)!=3:raise OSError('Internal terminal pipe is unavailable')
        fd=msvcrt.open_osfhandle(int(handle),flag|os.O_BINARY)
        stream=io.TextIOWrapper(io.FileIO(fd,'r' if attribute=='stdin' else 'w'),encoding='utf-8',errors='strict',write_through=True)
        setattr(sys,attribute,stream)


def worker_main(arguments):
    """Internal entry; no device command before job, user and side validation."""
    _attach_pipes()
    try:
        if len(arguments) not in (4,7):raise ValueError('Invalid internal calibration request')
        token,serial,expected_side,timeout=arguments[:4]
        guided=len(arguments)==7
        user=arguments[5] if guided else None
        replace=guided and arguments[6]=='replace'
        if guided and (arguments[4]!='guided' or arguments[6] not in ('replace','keep')):
            raise ValueError('Invalid internal guided request')
        if (not re.fullmatch('[0-9a-f]{32}',token) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,79}',serial)
                or expected_side not in ('left','right','auto') or not timeout.isdecimal() or not 60<=int(timeout)<=1800):
            raise ValueError('Invalid internal calibration identity')
        from windows_process_job import current_in_named_job
        from native_cli import json_command,prepare
        job_name='Local\\HandWorkbench-OfficialCalibration-'+token
        if not current_in_named_job(job_name):raise ValueError('Calibration worker is not contained in its private job')
        with gate():
            marker=reservation()
            if (marker.get('kind')!='calibration' or marker.get('token')!=token or marker.get('job_name')!=job_name
                    or identity(marker.get('pid'))!=marker.get('start') or marker.get('serial')!=serial
                    or marker.get('side')!=(None if expected_side=='auto' else expected_side)):
                raise ValueError('Calibration ownership changed before capture')
            if guided:
                from calibration_guided import validate_profile
                if marker.get('interface')!='guided' or marker.get('user')!=user or marker.get('replace') is not replace:
                    raise ValueError('Guided calibration ownership changed')
                validate_profile(json_command('user','show'),user,expected_side,replace)
            elif marker.get('interface')=='guided':raise ValueError('Guided worker cannot start an interactive terminal')
            # The official skill specifies the exact top-level typed value.
            # Missing, numeric or unknown values do not establish handedness.
            hand=json_command('get','hand_side','--sn',serial)
            side=hand.get('value') if isinstance(hand,dict) else None
            if side not in ('left','right') or expected_side!='auto' and side!=expected_side:
                raise ValueError('Selected glove hand side could not be verified / 手套左右手核对失败')
            device=hand.get('device')
            if device is not None and not isinstance(device,dict):raise ValueError('Invalid official device identity')
            for returned in (hand.get('sn'),hand.get('serial'),(device or {}).get('sn')):
                if returned is not None and returned!=serial:raise ValueError('Official CLI returned a different device identity')
            component=prepare(strict=True)
            if component['version']!=marker.get('cli_version'):raise ValueError('Official CLI changed before capture')
        if guided:from native_jsonl_worker import run
        else:from native_terminal_worker import run
        print(json.dumps(dict(type='ready',source='official_jsonl_native' if guided else 'official_tty',serial=serial,side=side)),flush=True)
        # Human mode owns its original user/overwrite prompts, pose art and result.
        # A single exact SN selector is used; side is independently verified above.
        return run([component['binary'],*(['--jsonl'] if guided else []),'calib','hand-model','--sn',serial,'--timeout-s',timeout])
    except Exception as error:
        print(json.dumps(dict(type='error',message=str(error)[:700])),flush=True)
        return 1


def terminal_software_check(token, *, guided=False):
    """Frozen-pipe/PTY smoke test: only the pinned CLI's --help, no SDK task."""
    _attach_pipes()
    from windows_process_job import current_in_named_job
    if not isinstance(token,str) or not re.fullmatch('[0-9a-f]{32}',token):return 1
    if not current_in_named_job('Local\\HandWorkbench-OfficialCalibration-'+token):return 1
    from native_cli import prepare
    if guided:from native_jsonl_worker import run
    else:from native_terminal_worker import run
    component=prepare(strict=True)
    return run([component['binary'],'--help'])


class NativeCalibration:
    def __init__(self,owner):
        self.owner=owner;self.folder=root_folder();self.token=None;self.job=None
        self.frames=deque();self.sequence=0;self.size=0;self.cancel_requested=False
        self.finished=threading.Event();self.cancel_timer=False;self.forced_stop=False
        self.launching=False

    def start_human(self,serial,expected_side=None,timeout=900,*,cancel_check=None):
        """The original Human TTY owns user selection and overwrite prompts."""
        return self._start(serial,expected_side,timeout,cancel_check=cancel_check)

    def start_guided(self,serial,side,user,replace=False,timeout=900,*,cancel_check=None):
        from calibration_guided import validate_profile
        from native_cli import json_command
        validate_profile(json_command('user','show'),user,side,replace)
        if cancel_check is not None:cancel_check()
        return self._start(serial,side,timeout,user=user,replace=replace,cancel_check=cancel_check)

    def _start(self,serial,expected_side,timeout,user=None,replace=False,cancel_check=None):
        if (not isinstance(serial,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,79}',serial)
                or expected_side not in (None,'left','right') or type(timeout) is not int or not 60<=timeout<=1800):
            raise ValueError('Invalid selected glove or human terminal options')
        from native_cli import prepare
        from windows_process_job import OwnedProcess
        owner=self.owner
        with gate(self.folder):
            self.recover()
            owner.metadata_check()
            component=prepare(strict=True)
            if cancel_check is not None:cancel_check()
            self.token=reserve('calibration',self.folder)
            with owner.lock:self.launching=True
            try:
                marker=reservation(self.folder)
                marker.update(backend='windows_native',cli_version=component['version'],
                    job_name='Local\\HandWorkbench-OfficialCalibration-'+self.token,serial=serial,side=expected_side)
                if user is not None:marker.update(interface='guided',user=user,replace=replace)
                atomic(self.folder/'session.json',marker)
                command=[sys.executable]
                if not getattr(sys,'frozen',False):command.append(str(Path(__file__).with_name('desktop.py')))
                command.extend(['--native-calibration-worker',self.token,serial,expected_side or 'auto',str(timeout)])
                if user is not None:command.extend(['guided',user,'replace' if replace else 'keep'])
                if cancel_check is not None:cancel_check()
                if self.cancel_requested:raise ValueError('Calibration startup was cancelled')
                options={}
                if user is not None and sys.platform=='win32':
                    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
                    options['startupinfo']=startup
                self.job=OwnedProcess(command,job_name=marker['job_name'],stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='strict',bufsize=1,
                    env={**os.environ,'WUJI_NO_UPDATE_CHECK':'1','WUJI_TELEMETRY_DISABLE':'1'},**options)
            except BaseException:
                with owner.lock:self.launching=False
                release(self.token,self.folder);self.token=None;raise
            with owner.lock:
                owner.process=self.job.process
                owner.run=dict(running=True,status='cancelling' if self.cancel_requested else 'starting',id=uuid.uuid4().hex,user=user,serial=serial,side=None,
                    expected_side=expected_side,
                    started=time.time(),event=None,result=None,error=None,exit_code=None,step=None,progress=None,
                    source='official_jsonl_native' if user else 'official_tty',terminal=user is None,
                    interface='guided' if user else 'terminal',
                    phase='preflight',event_count=0,captured_poses=[],pose_index=None,cli_version=component['version'],
                    cli_binary_sha256=component.get('binary_sha256'),cli_archive_sha256=component.get('archive_sha256'))
                owner.thread=threading.Thread(target=self._collect,name='Official calibration terminal',daemon=True)
                self.launching=False
            owner.thread.start()
            if self.cancel_requested:
                try:self._send(dict(type='cancel'))
                except (OSError,ValueError):pass
        return owner.snapshot(refresh=False)

    def recover(self):
        marker=reservation(self.folder)
        if not marker or marker.get('kind')!='calibration':return
        if identity(marker.get('pid'))==marker.get('start'):
            raise ValueError('请回到拥有标定的工作区停止 / Stop calibration in its owning workspace')
        expected='Local\\HandWorkbench-OfficialCalibration-'+str(marker.get('token',''))
        from windows_process_job import recover_viz_job
        if marker.get('backend')!='windows_native' or marker.get('job_name')!=expected or not recover_viz_job(expected,timeout=7):
            raise ValueError('Calibration process cleanup remains unconfirmed')
        release(marker['token'],self.folder)

    def output(self,run_id,after):
        with self.owner.lock:
            if run_id!=self.owner.run.get('id'):raise ValueError('Terminal session changed; refresh its status')
            if type(after) is not int or after<0:raise ValueError('Invalid terminal cursor')
            chunks=[];size=0
            for seq,text in self.frames:
                if seq<=after:continue
                chunks.append(dict(sequence=seq,text=text));size+=len(text)
                if size>=128*1024:break
            return dict(id=run_id,chunks=chunks,sequence=chunks[-1]['sequence'] if chunks else after,
                gap=bool(self.frames and after<self.frames[0][0]-1),running=self.owner.run['running'])

    def input(self,command):
        with self.owner.lock:
            if command.get('run_id')!=self.owner.run.get('id') or not self.owner.run['running']:
                raise ValueError('This terminal session is no longer active')
            if self.cancel_requested:raise ValueError('Official terminal is closing')
            if not self.owner.run.get('terminal'):raise ValueError('Guided calibration accepts no terminal keystrokes')
            marker=reservation(self.folder)
            if not self.token or marker.get('token')!=self.token:raise ValueError('Terminal session ownership changed')
            kind=command.get('type')
            if kind=='write':
                value=command.get('text')
                if not isinstance(value,str) or not 0<len(value)<=4096 or '\x00' in value:raise ValueError('Invalid terminal input')
                payload=dict(type=kind,text=value)
            elif kind=='resize':
                rows,cols=command.get('rows'),command.get('cols')
                if type(rows) is not int or type(cols) is not int or not 12<=rows<=100 or not 81<=cols<=240:
                    raise ValueError('Terminal size must be 81–240 columns and 12–100 rows')
                payload=dict(type=kind,rows=rows,cols=cols)
            else:raise ValueError('Unsupported terminal input')
            self._send(payload)
        return dict(accepted=True)

    def _send(self,payload):
        process=self.owner.process
        if process is None or process.poll() is not None:raise ValueError('Official terminal has exited')
        process.stdin.write(json.dumps(payload)+'\n');process.stdin.flush()

    def cancel(self):
        owner=self.owner
        # Never wait on the global gate or a Job while holding the run lock:
        # startup holds that gate and needs this lock to publish its state.
        with owner.lock:
            launching=self.launching;running=owner.run['running']
            run_id=owner.run.get('id')
            if launching or running:self.cancel_requested=True
            if running:
                owner.run['status']='cancelling'
                try:self._send(dict(type='cancel'))
                except (OSError,ValueError):pass
            schedule=running and not self.cancel_timer
            if schedule:self.cancel_timer=True
            thread=owner.thread;token=self.token;job=self.job
        if launching:return owner.snapshot(refresh=False)
        if not running:
            if token and job is not None:
                if not job.close(timeout=7):raise ValueError('Calibration process cleanup remains unconfirmed')
                with gate(self.folder):
                    with owner.lock:
                        if (self.token!=token or self.job is not job or owner.run.get('id')!=run_id
                                or owner.run.get('running') is not False):
                            raise ValueError('Calibration cleanup owner changed')
                    marker=reservation(self.folder)
                    if marker and (marker.get('kind')!='calibration' or marker.get('token')!=token):
                        raise ValueError('Calibration cleanup owner changed')
                    release(token,self.folder)
                    if reservation(self.folder):raise ValueError('Calibration process cleanup remains unconfirmed')
                    with owner.lock:
                        if (self.token!=token or self.job is not job or owner.run.get('id')!=run_id
                                or owner.run.get('running') is not False):
                            raise ValueError('Calibration cleanup owner changed')
                        self.token=None
                        owner.run['cleanup_confirmed']=True
            return owner.snapshot(refresh=False)
        if schedule:
            def deadline():
                if not self.finished.wait(12):
                    self.forced_stop=True
                    job.terminate_and_wait(timeout=7)
            threading.Thread(target=deadline,name='Calibration cancel deadline',daemon=True).start()
        if thread is not None and not thread.is_alive():
            if job.close(timeout=7):
                with gate(self.folder):
                    release(token,self.folder)
                    if self.token==token:self.token=None
                with owner.lock:
                    if owner.run.get('id')==run_id:
                        owner.run.update(running=False,status='stopped',cleanup_confirmed=True)
        return owner.snapshot(refresh=False)

    def _collect(self):
        owner=self.owner;process=owner.process;child_exit=None;transport_error=None
        guided=owner.run.get('source')=='official_jsonl_native';ready=False;exit_seen=False
        from calibration_guided import OfficialEvents
        decoder=OfficialEvents(owner.run['serial'],owner.run.get('expected_side'),owner.run.get('user')) if guided else None
        def accept(text,final=False):
            from calibration_cli import record_progress
            for event in decoder.feed(text,final):
                if not ready:raise ValueError('Official guided data arrived before verified device readiness')
                owner.run['event']=event;owner.run['event_count']+=1
                kind=event['event']
                if kind=='progress':
                    record_progress(owner.run,event)
                    if not self.cancel_requested:owner.run['status']='solving' if owner.run.get('phase')=='solving' else 'collecting'
                elif kind=='result':owner.run['result']=event
                elif kind=='error':owner.run['error']=str(event.get('error') or 'Official calibration failed')[:800]
        def drain():
            try:
                for _ in process.stderr:pass
            except (OSError,ValueError):pass
        reader=threading.Thread(target=drain,daemon=True);reader.start()
        try:
            for line in process.stdout:
                if len(line)>128*1024:raise ValueError('Terminal transport record exceeds its limit')
                event=json.loads(line)
                if not isinstance(event,dict):raise ValueError('Invalid terminal transport event')
                kind=event.get('type')
                with owner.lock:
                    if exit_seen:raise ValueError('Terminal transport emitted data after its exit status')
                    if kind=='ready':
                        if ready:raise ValueError('Duplicate terminal readiness event')
                        side=event.get('side')
                        if (side not in ('left','right') or event.get('serial')!=owner.run['serial']
                                or owner.run.get('expected_side') not in (None,side)):
                            raise ValueError('Official terminal ready identity could not be verified')
                        owner.run['side']=side
                        ready=True
                        if not self.cancel_requested:owner.run.update(phase='capture_starting' if guided else 'tty_starting')
                    elif kind in ('output','diagnostic'):
                        text=event.get('text')
                        if not isinstance(text,str) or len(text)>65536:raise ValueError('Invalid terminal output')
                        if guided and kind=='output':accept(text)
                        elif not guided and not self.cancel_requested:owner.run.update(status='terminal',phase='official_tty')
                        self.sequence+=1;self.frames.append((self.sequence,text));self.size+=len(text)
                        while self.size>2*1024*1024 and len(self.frames)>1:self.size-=len(self.frames.popleft()[1])
                    elif kind=='exit':
                        exit_seen=True
                        child_exit=event.get('exitstatus')
                        if child_exit is not None and type(child_exit) is not int:raise ValueError('Invalid terminal exit status')
                    elif kind=='error':transport_error=str(event.get('message') or 'Terminal transport failed')[:700]
            if guided:
                with owner.lock:accept('',final=True)
            worker_code=process.wait(timeout=10)
            if worker_code or child_exit is None:
                transport_error=transport_error or 'Official terminal exit could not be verified'
        except Exception as error:transport_error=str(error)[:700]
        finally:
            try:clean=self.job.close(timeout=7)
            except Exception:clean=False
            reader.join(timeout=1)
            with owner.lock:
                status='error' if transport_error or not clean or self.forced_stop else 'cancelled' if child_exit==9 else 'stopped' if self.cancel_requested else 'session_ended' if child_exit==0 else 'error'
                if guided and status=='session_ended':
                    status='completed' if ready and decoder.terminal=='result' and decoder.result is not None else 'error'
                owner.run.update(running=not clean,status=status,exit_code=child_exit,ended=time.time(),
                    error=transport_error or owner.run.get('error') or (None if clean and status!='error' else 'Official CLI did not confirm a complete calibration result; inspect details'),
                    cleanup_confirmed=clean)
                owner.process=None if clean else process;owner.cache=None
                report=copy.deepcopy(owner.run)
                transcript=''.join(text for _,text in self.frames)
            if clean:
                with gate(self.folder):release(self.token,self.folder);self.token=None
                for stream in (process.stdin,process.stdout,process.stderr):
                    try:stream.close()
                    except (OSError,ValueError):pass
            self.finished.set()
            if owner.reports:
                try:
                    owner.reports.mkdir(parents=True,exist_ok=True)
                    (owner.reports/(report['id']+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                    (owner.reports/(report['id']+'-terminal.ansi')).write_text(transcript,encoding='utf-8')
                except OSError:pass
