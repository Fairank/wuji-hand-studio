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
from native_environment import official_cli_environment


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
        restoring=len(arguments)==7 and arguments[4]=='restore'
        guided=len(arguments)==7 and not restoring
        user=arguments[5] if len(arguments)==7 else None
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
            if restoring:
                from calibration_backups import CalibrationBackups
                backup_id=arguments[6]
                if marker.get('interface')!='restore' or marker.get('user')!=user or marker.get('backup_id')!=backup_id:
                    raise ValueError('Profile restore ownership changed')
                backup,archive=CalibrationBackups().verify_current_owner(backup_id,marker.get('backup_sha256'))
                if backup['profile']!=user or backup['serial']!=serial or backup['side']!=expected_side:
                    raise ValueError('Profile restore backup identity differs')
            elif guided:
                from calibration_guided import validate_profile
                from calibration_identity import read_current_sdk_user,require_sdk_user
                if marker.get('interface')!='guided' or marker.get('user')!=user or marker.get('replace') is not replace:
                    raise ValueError('Guided calibration ownership changed')
                validate_profile(json_command('user','show'),user,expected_side,replace)
                require_sdk_user(read_current_sdk_user(user),marker.get('sdk_user'),user)
            elif marker.get('interface') in ('guided','restore'):raise ValueError('Owned worker mode cannot become an unrelated interactive terminal')
            # The official skill specifies the exact top-level typed value.
            # Missing, numeric or unknown values do not establish handedness.
            hand={} if restoring else json_command('get','hand_side','--sn',serial)
            side=expected_side if restoring else hand.get('value') if isinstance(hand,dict) else None
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
        print(json.dumps(dict(type='ready',source='official_user_import_tty' if restoring else 'official_jsonl_native' if guided else 'official_tty',serial=serial,side=side)),flush=True)
        # Human mode owns its original user/overwrite prompts, pose art and result.
        # A single exact SN selector is used; side is independently verified above.
        if restoring:return run([component['binary'],'user','import',str(archive)])
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
        self.failed_start_run=None;self.spawn_attempted=False

    def start_human(self,serial,expected_side=None,timeout=900,*,cancel_check=None):
        """The original Human TTY owns user selection and overwrite prompts."""
        return self._start(serial,expected_side,timeout,cancel_check=cancel_check)

    def start_guided(self,serial,side,user,replace=False,timeout=900,*,cancel_check=None):
        from calibration_guided import validate_profile
        from calibration_identity import read_current_sdk_user
        from native_cli import json_command
        if cancel_check is not None:
            original_cancel_check=cancel_check
            def cancel_check():
                try:original_cancel_check()
                except BaseException:
                    self.cancel_requested=True
                    raise
        profile=validate_profile(json_command('user','show'),user,side,replace)
        sdk_user=read_current_sdk_user(user)
        run_id=uuid.uuid4().hex;backup=None
        if replace and profile[side+'_hand']['calibrated'] is True:
            from calibration_backups import CalibrationBackups
            from native_cli import prepare
            backup=CalibrationBackups().create(user,side,serial,sdk_user,run_id,
                cli_version=prepare(strict=True)['version'],cancel_check=cancel_check)
        try:
            if cancel_check is not None:cancel_check()
            return self._start(serial,side,timeout,user=user,replace=replace,sdk_user=sdk_user,cancel_check=cancel_check,run_id=run_id,backup=backup)
        except BaseException as error:
            if backup and self.owner.run.get('id')!=run_id:
                self._start_failure(error,run_id,serial,side,user,sdk_user,backup,False,False)
            raise

    def start_restore(self,backup_id,expected_sha256,timeout=900):
        from calibration_backups import CalibrationBackups
        value,_=CalibrationBackups().verify_current_owner(backup_id,expected_sha256)
        return self._start(value['serial'],value['side'],timeout,user=value['profile'],sdk_user=value['sdk_user'],
            backup=CalibrationBackups.summary(value),restoring=True)

    def _start(self,serial,expected_side,timeout,user=None,replace=False,sdk_user=None,cancel_check=None,run_id=None,backup=None,restoring=False):
        run_id=run_id or uuid.uuid4().hex
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
            spawn_attempted=False
            try:
                marker=reservation(self.folder)
                marker.update(backend='windows_native',cli_version=component['version'],
                    job_name='Local\\HandWorkbench-OfficialCalibration-'+self.token,serial=serial,side=expected_side,run_id=run_id,launch_stage='reserved')
                if restoring:
                    marker.update(interface='restore',user=user,sdk_user=sdk_user,backup_id=backup['id'],backup_sha256=backup['sha256'])
                elif user is not None:
                    from calibration_identity import sdk_user_identity
                    sdk_user=sdk_user_identity(sdk_user,user)
                    marker.update(interface='guided',user=user,replace=replace,sdk_user=sdk_user)
                atomic(self.folder/'session.json',marker)
                command=[sys.executable]
                if not getattr(sys,'frozen',False):command.append(str(Path(__file__).with_name('desktop.py')))
                command.extend(['--native-calibration-worker',self.token,serial,expected_side or 'auto',str(timeout)])
                if restoring:command.extend(['restore',user,backup['id']])
                elif user is not None:command.extend(['guided',user,'replace' if replace else 'keep'])
                if cancel_check is not None:cancel_check()
                if self.cancel_requested:raise ValueError('Calibration startup was cancelled')
                options={}
                if user is not None and sys.platform=='win32':
                    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
                    options['startupinfo']=startup
                environment=official_cli_environment()
                marker['launch_stage']='starting';atomic(self.folder/'session.json',marker);spawn_attempted=True
                self.job=OwnedProcess(command,job_name=marker['job_name'],stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='strict',bufsize=1,
                    env=environment,**options)
            except BaseException as error:
                self._start_failure(error,run_id,serial,expected_side,user,sdk_user,backup,spawn_attempted,restoring)
                raise
            with owner.lock:
                owner.process=self.job.process
                owner.run=dict(running=True,status='cancelling' if self.cancel_requested else 'starting',id=run_id,user=user,serial=serial,side=None,
                    expected_side=expected_side,
                    started=time.time(),event=None,result=None,error=None,exit_code=None,step=None,progress=None,
                    source='official_user_import_tty' if restoring else 'official_jsonl_native' if user else 'official_tty',terminal=restoring or user is None,
                    interface='restore' if restoring else 'guided' if user else 'terminal',sdk_user=copy.deepcopy(sdk_user),backup=copy.deepcopy(backup),
                    phase='preflight',event_count=0,captured_poses=[],pose_index=None,cli_version=component['version'],
                    cli_binary_sha256=component.get('binary_sha256'),cli_archive_sha256=component.get('archive_sha256'))
                self.launching=False
            try:
                owner.thread=threading.Thread(target=self._collect,name='Official calibration terminal',daemon=True)
                owner.thread.start()
            except BaseException as error:
                self._start_failure(error,run_id,serial,expected_side,user,sdk_user,backup,True,restoring)
                raise
            if self.cancel_requested:
                try:self._send(dict(type='cancel'))
                except (OSError,ValueError):pass
        return owner.snapshot(refresh=False)

    def _start_failure(self,error,run_id,serial,side,user,sdk_user,backup,spawn_attempted,restoring):
        """Never abandon a possibly running private Job or a retained lease."""
        owner=self.owner;self.failed_start_run=run_id;self.spawn_attempted=spawn_attempted
        clean=not spawn_attempted;released=self.token is None;cleanup_error=None
        if self.job is not None:
            try:clean=self.job.close(timeout=7) is True
            except Exception:clean=False
        if clean and self.token is not None:
            try:
                marker=reservation(self.folder)
                if marker and (marker.get('kind')!='calibration' or marker.get('token')!=self.token or marker.get('run_id') not in (None,run_id)):
                    raise ValueError('Calibration cleanup owner changed')
                release(self.token,self.folder)
                if reservation(self.folder):raise ValueError('Calibration reservation remains unconfirmed')
                self.token=None;released=True
            except Exception as failure:cleanup_error=str(failure)[:300]
        if not clean or not released:
            cleanup_error='启动未完成，进程或占用待结束核对 / Startup did not complete; process or reservation cleanup is unconfirmed'+(': '+cleanup_error if cleanup_error else '')
        with owner.lock:
            self.launching=False
            prior=owner.run if owner.run.get('id')==run_id else {}
            cancelled=clean and released and self.cancel_requested
            owner.run=dict(prior,id=run_id,user=user,serial=serial,side=None,expected_side=side,
                source='official_user_import_tty' if restoring else 'official_jsonl_native' if user else 'official_tty',
                terminal=restoring or user is None,interface='restore' if restoring else 'guided' if user else 'terminal',
                sdk_user=copy.deepcopy(sdk_user),backup=copy.deepcopy(backup),status='cancelled' if cancelled else 'error',running=not clean,
                error=None if cancelled else str(error)[:700],startup_error=str(error)[:700],cancel_reason=str(error)[:700] if cancelled else None,
                cleanup_confirmed=clean and released,cleanup_error=cleanup_error,
                result=None,event=None,exit_code=None,event_count=0,step=None,progress=None,ended=time.time())
            owner.process=None if clean else getattr(self.job,'process',None)
            record=copy.deepcopy(owner.run)
        if clean:self.finished.set()
        if owner.reports:
            try:
                owner.reports.mkdir(parents=True,exist_ok=True)
                (owner.reports/(run_id+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
            except OSError:pass

    def _cleanup_failed_start(self):
        """Explicit retry only; recover this private Job if construction was uncertain."""
        owner=self.owner
        with owner.lock:token=self.token;run_id=owner.run.get('id');job=self.job
        if not token or self.failed_start_run!=run_id:raise ValueError('Calibration startup cleanup owner changed')
        with gate(self.folder):
            marker=reservation(self.folder);expected='Local\\HandWorkbench-OfficialCalibration-'+token
            if (marker and (marker.get('kind')!='calibration' or marker.get('token')!=token
                    or marker.get('run_id') not in (None,run_id))):
                raise ValueError('Calibration startup cleanup owner changed')
            if self.spawn_attempted and job is None and (not marker or marker.get('job_name')!=expected):
                raise ValueError('Calibration startup Job ownership is unverified')
        if job is not None:
            if job.close(timeout=7) is not True:raise ValueError('Calibration startup process cleanup remains unconfirmed')
        elif self.spawn_attempted:
            from windows_process_job import recover_viz_job
            if recover_viz_job(expected,timeout=7) is not True:
                raise ValueError('Calibration startup process cleanup remains unconfirmed')
        with gate(self.folder):
            with owner.lock:
                if self.token!=token or owner.run.get('id')!=run_id or self.job is not job:
                    raise ValueError('Calibration startup cleanup owner changed')
            marker=reservation(self.folder)
            if marker and (marker.get('kind')!='calibration' or marker.get('token')!=token):
                raise ValueError('Calibration startup cleanup owner changed')
            release(token,self.folder)
            if reservation(self.folder):raise ValueError('Calibration startup reservation remains unconfirmed')
            with owner.lock:
                self.token=None;owner.process=None;owner.run.update(running=False,cleanup_confirmed=True,cleanup_error=None)
            self.finished.set()
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
        if not self.launching and self.token and self.failed_start_run==owner.run.get('id'):
            return self._cleanup_failed_start()
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
                        if 'cleanup_error' in owner.run:owner.run['cleanup_error']=None
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
        token=self.token;job=self.job;run_id=owner.run.get('id');serial=owner.run.get('serial')
        guided=owner.run.get('source')=='official_jsonl_native';ready=False;exit_seen=False
        restoring=owner.run.get('source')=='official_user_import_tty' and owner.run.get('interface')=='restore'
        pending_transport=None
        from calibration_guided import OfficialEvents
        decoder=OfficialEvents(owner.run['serial'],owner.run.get('expected_side'),owner.run.get('user'),owner.run.get('sdk_user')) if guided else None
        def append_output(text):
            # Keep the original bounded output even when its structured result
            # is rejected. Recording it does not establish calibration success.
            self.sequence+=1;self.frames.append((self.sequence,text));self.size+=len(text)
            while self.size>2*1024*1024 and len(self.frames)>1:self.size-=len(self.frames.popleft()[1])
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
        reader=None;reader_started=False
        try:
            reader=threading.Thread(target=drain,daemon=True);reader.start();reader_started=True
            for line in process.stdout:
                pending_transport=line
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
                        append_output(text)
                        pending_transport=None
                        if guided and kind=='output':accept(text)
                        elif not guided and not self.cancel_requested:owner.run.update(status='terminal',phase='official_tty')
                    elif kind=='exit':
                        exit_seen=True
                        child_exit=event.get('exitstatus')
                        if child_exit is not None and type(child_exit) is not int:raise ValueError('Invalid terminal exit status')
                    elif kind=='error':transport_error=str(event.get('message') or 'Terminal transport failed')[:700]
                    pending_transport=None
            if guided:
                with owner.lock:accept('',final=True)
            worker_code=process.wait(timeout=10)
            if worker_code or child_exit is None:
                transport_error=transport_error or 'Official terminal exit could not be verified'
        except Exception as error:
            transport_error=str(error)[:700]
            with owner.lock:
                if pending_transport is not None:
                    append_output('\n[Rejected terminal transport record; bounded prefix]\n'+pending_transport[:65536])
                append_output('\n[Terminal transport error] '+transport_error+'\n')
        finally:
            try:clean=job.close(timeout=7)
            except Exception:clean=False
            if reader_started:
                try:reader.join(timeout=1)
                except (RuntimeError,OSError):pass
            model_values={}
            with owner.lock:
                status='error' if transport_error or not clean or self.forced_stop else 'cancelled' if child_exit==9 else 'stopped' if self.cancel_requested else 'session_ended' if child_exit==0 else 'error'
                if guided and status=='session_ended':
                    status='completed' if ready and decoder.terminal=='result' and decoder.result is not None else 'error'
                if guided and status=='completed':
                    try:
                        from calibration_portability import file_digest
                        digest,size=file_digest(decoder.result['result']['calibrated_urdf'])
                        model_values=dict(model_sha256=digest,model_bytes=size)
                    except (OSError,ValueError,TypeError) as error:
                        status='error'
                        transport_error='官方流程已结束，但保存的手型无法核对 / Saved hand model could not be verified after calibration: '+str(error)[:350]
                        append_output('\n[Saved model verification failed] '+transport_error+'\n')
            if restoring and clean and ready and status=='session_ended':
                try:
                    from calibration_backups import CalibrationBackups
                    backup=owner.run.get('backup') or {}
                    verified=CalibrationBackups().verify_restored(backup.get('id'),backup.get('sha256'))
                    model_values.update(restore_verified=verified,restore_error=None if verified else '官方流程已结束，SDK档案内容尚未与备份完整匹配 / Official import ended; SDK contents do not yet exactly match the backup')
                except Exception as error:
                    model_values.update(restore_verified=False,restore_error=str(error)[:600])
                status='restore_verified' if model_values.get('restore_verified') else 'restore_unconfirmed'
            released=False;cleanup_error=None
            if clean:
                try:
                    with gate(self.folder):
                        with owner.lock:
                            if self.token!=token or self.job is not job or owner.run.get('id')!=run_id:
                                raise ValueError('Calibration cleanup owner changed')
                        marker=reservation(self.folder)
                        if marker and (marker.get('kind')!='calibration' or marker.get('token')!=token or marker.get('serial')!=serial):
                            raise ValueError('Calibration cleanup owner changed')
                        release(token,self.folder)
                        if reservation(self.folder):raise ValueError('Calibration reservation remains unconfirmed')
                        self.token=None;released=True
                except Exception as error:
                    cleanup_error='标定进程已结束，但设备占用尚未释放；请点击结束重试 / Calibration ended but its reservation was not released; end the session to retry: '+str(error)[:250]
                    with owner.lock:append_output('\n[Calibration cleanup unconfirmed] '+cleanup_error+'\n')
                for stream in (process.stdin,process.stdout,process.stderr):
                    try:stream.close()
                    except (OSError,ValueError):pass
            with owner.lock:
                owner.run.update(running=not clean,status='error' if cleanup_error else status,exit_code=child_exit,ended=time.time(),
                    error=transport_error or owner.run.get('error') or cleanup_error or (None if clean and status!='error' else 'Official CLI did not confirm a complete calibration result; inspect details'),
                    cleanup_confirmed=clean and released,**model_values)
                if restoring and (not clean or not released):owner.run['restore_verified']=False
                if cleanup_error:owner.run['cleanup_error']=cleanup_error
                owner.process=None if clean else process;owner.cache=None
                report=copy.deepcopy(owner.run)
                transcript=''.join(text for _,text in self.frames)
            self.finished.set()
            if owner.reports:
                try:
                    owner.reports.mkdir(parents=True,exist_ok=True)
                    (owner.reports/(report['id']+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                    (owner.reports/(report['id']+'-terminal.ansi')).write_text(transcript,encoding='utf-8')
                except OSError:pass
