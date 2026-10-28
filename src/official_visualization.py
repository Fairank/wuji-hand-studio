"""Embed the original CLI viewer, without fabricating feedback or device state."""
import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode

from cli_coordination import root_folder,gate,reserve,release,reservation,atomic,identity
from official_cli import cli_path
from official_device_discovery import use_native as native_network


def viewer_url(value,theme=None):
    if not isinstance(value,str) or len(value)>4096:raise ValueError('Invalid official viewer URL')
    parsed=urlsplit(value)
    if parsed.scheme!='http' or parsed.hostname not in {'localhost','127.0.0.1'} or parsed.username or parsed.password or parsed.fragment or not parsed.port or parsed.path not in {'','/'}:
        raise ValueError('Official viewer must use a loopback HTTP endpoint')
    pairs=parse_qsl(parsed.query,keep_blank_values=True);streams=[v for k,v in pairs if k=='url']
    if len(streams)!=1:raise ValueError('Official data endpoint missing')
    stream=urlsplit(streams[0])
    if stream.scheme!='rerun+http' or stream.hostname not in {'localhost','127.0.0.1'} or not stream.port or stream.username or stream.password or stream.path!='/proxy' or stream.query or stream.fragment:
        raise ValueError('Invalid official viewer data endpoint')
    if theme is not None:
        if theme not in {'light','dark'}:raise ValueError('Unsupported viewer theme')
        pairs=[(k,v) for k,v in pairs if k!='theme']+[('theme',theme)]
    return urlunsplit((parsed.scheme,parsed.netloc,parsed.path,urlencode(pairs),''))


class OfficialVisualization:
    def __init__(self,folder=None,idle_check=None,scan_check=None):
        self.folder=Path(folder or root_folder());self.idle_check=idle_check or (lambda:None)
        self.scan_check=scan_check
        self.lock=threading.RLock();self.process=None;self.thread=None;self.token=None;self.config=None
        self.native_job=None;self.native_stop=threading.Event()
        self.transport=None
        self.scan_cancel=threading.Event();self.scan_done=threading.Event();self.scan_done.set()
        self.scan_pauses=0
        self.state=dict(supported=sys.platform=='win32' or sys.platform.startswith('linux'),busy=False,active=False,phase='idle',device=None,devices=[],scanning=False,scanned_at=None,viewer_url=None,cli_version=None,error=None,logs=[],started_at=None,stream_freshness='not_reported',zero_on_start=False)

    def snapshot(self,theme=None):
        with self.lock:row=copy.deepcopy(self.state)
        lease=reservation(self.folder)
        blocked=bool(lease) and lease.get('token')!=self.token
        scanned=row.get('scanned_at')
        row['devices_fresh']=type(scanned) in (int,float) and 0<=time.time()-scanned<=30 and not row.get('error')
        row['can_start']=row['supported'] and not row['busy'] and not row['active'] and not lease and row['devices_fresh']
        row['reserved']=bool(lease) and lease.get('kind')=='viz'
        # A session identifier is an exact ownership selector, not a CSRF or
        # permission token. Every handover rechecks its process/marker identity.
        row['session_id']=self.token
        row['blocked_reason']='另一个官方 CLI 会话尚未结束 / Another official CLI session is active' if blocked else None
        if row.get('viewer_url'):row['viewer_url']=viewer_url(row['viewer_url'],theme)
        return row

    def _set(self,**values):
        with self.lock:self.state.update(**values)

    def _log(self,message):
        with self.lock:self.state['logs']=(self.state['logs']+[str(message)[:1200]])[-60:]

    def _browser_url(self,value):
        from viewer_transport import ViewerTransport
        with self.lock:
            if self.transport is not None:
                if self.transport.source==value and self.transport.url:return self.transport.url
                if not self.transport.close():raise ValueError('Previous Viewer relay cleanup unconfirmed')
            self.transport=ViewerTransport(value)
            self.transport.open()
            self.state['loopback_forwarding']=self.transport.forwarded
            return self.transport.url

    def _close_transport(self):
        with self.lock:
            if self.transport is None:return True
            try:closed=self.transport.close()
            except Exception as error:
                self._log('Viewer relay cleanup: '+str(error)[:400]);closed=False
            if closed:self.transport=None;self.state['loopback_forwarding']=False
            return closed

    def scan(self):
        token=self._begin_scan()
        try:
            threading.Thread(target=self._collect_scan,args=(token,True),name='Official CLI device discovery',daemon=True).start()
        except Exception as error:
            self._set(devices=[],scanned_at=None,busy=False,scanning=False,error=str(error)[:700])
            with gate(self.folder):release(token,self.folder)
            self.scan_done.set()
            raise
        return self.snapshot()

    def check_scan(self):
        with self.lock:
            if self.scan_pauses:raise ValueError('设备更新中，查找已暂停 / Device update in progress; discovery is paused')
        (self.scan_check or self.idle_check)()

    @contextmanager
    def discovery_priority(self,timeout=50):
        """Yield only our finite discovery read to an explicit maintenance task."""
        with gate(self.folder),self.lock:
            self.scan_pauses+=1
            self.scan_cancel.set()
        try:
            if not self.scan_done.wait(timeout):
                raise ValueError('设备查找尚未结束，请稍后重试 / Device discovery has not finished; retry shortly')
            yield
        finally:
            with self.lock:self.scan_pauses-=1

    def _begin_scan(self,inline=False):
        with gate(self.folder):
            with self.lock:
                if self.scan_pauses and not inline:raise ValueError('设备更新中，查找已暂停 / Device update in progress; discovery is paused')
            token=reserve('scan',self.folder)
            try:(self.scan_check or self.idle_check)()
            except Exception:release(token,self.folder);raise
            with self.lock:
                if self.state['busy'] or self.state['active']:release(token,self.folder);raise ValueError('Official CLI session is busy')
                self.state.update(busy=True,scanning=True,error=None,devices=[],scanned_at=None)
                self.scan_cancel.clear();self.scan_done.clear()
        return token

    def _collect_scan(self,token,cancellable=False):
        clean=True
        try:
            from bridge_config import load_config
            from calibration_cli import cli_json
            config=load_config()
            value=cli_json(config,'devices',cancelled=self.scan_cancel.is_set) if cancellable else cli_json(config,'devices')
            self._set(backend='windows_native' if native_network(config) else 'linux_controller')
            devices=value.get('devices')
            if not isinstance(devices,list):raise ValueError('Invalid official device list')
            devices=[copy.deepcopy(x) for x in devices if isinstance(x,dict)]
            self._set(devices=devices,scanned_at=time.time())
            return copy.deepcopy(devices)
        except Exception as error:
            from official_device_discovery import DiscoveryDeferred,DiscoveryCleanupUnconfirmed
            clean=not isinstance(error,DiscoveryCleanupUnconfirmed)
            self._set(devices=[],scanned_at=None,error=None if isinstance(error,DiscoveryDeferred) else str(error)[:700])
            return None
        finally:
            with gate(self.folder):
                # Finish the state transition before another session can start.
                if clean:
                    self._set(busy=False,scanning=False)
                    release(token,self.folder)
                    self.scan_done.set()

    def discover_inline(self):
        """Explicit synchronous scan for calibration, with identical ownership."""
        token=self._begin_scan(inline=True)
        devices=self._collect_scan(token)
        if devices is None:raise ValueError(self.snapshot().get('error') or 'Official discovery failed')
        return devices

    def _command(self,config,*extra):
        from calibration_cli import controller_args
        if config['mode']=='wsl':
            from managed_runtime import WslController,PYTHON
            directory=WslController(config,'hand2_left').agent_directory;python=PYTHON
        elif config['mode']=='local':directory=config.get('agent_directory') or str(Path(__file__).parent);python=config.get('python') or 'python3'
        else:raise ValueError('原版可视化需要内置或本机 Linux；远程 SSH 需先配置独立隧道 / Use the built-in or local Linux controller for the original viewer')
        return controller_args(config,[python,'-u',directory+'/official_viz_runner.py',*extra])

    def start(self,serial,theme='light'):
        if not isinstance(serial,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',serial):raise ValueError('Select an official device serial number')
        if theme not in {'light','dark'}:raise ValueError('Unsupported viewer theme')
        from bridge_config import load_config
        config=load_config()
        if config['mode'] not in {'wsl','local'}:raise ValueError('Use the built-in or local Linux controller for the original viewer')
        with gate(self.folder):
            token=reserve('viz',self.folder)
            try:
                self.idle_check()
                with self.lock:
                    if self.process or self.state['busy'] or self.state['active'] or self.thread and self.thread.is_alive():raise ValueError('Official visualization already active')
                    scanned=self.state.get('scanned_at')
                    if (type(scanned) not in (int,float) or not 0<=time.time()-scanned<=30 or self.state.get('error')):
                        raise ValueError('请重新查找设备后再打开画面 / Find devices again before starting the Viewer')
                    found=next((x for x in self.state['devices'] if (x.get('sn') or x.get('serial'))==serial),None)
                    if not found:raise ValueError('请先扫描并选择实际设备 / Scan and select a discovered device first')
                    from device_inventory import normalize_devices
                    normalized=normalize_devices(self.state['devices'],source='official_cli',observed_at=scanned)
                    selected=next((row for row in normalized['devices'] if row['sn']==serial and row['selectable']),None)
                    if selected is None:raise ValueError('设备类型未知或身份冲突，请重新发现 / Device type is unknown or conflicting; rescan')
                    kind=' '.join(str(found.get(k,'')) for k in ('model','type','device_type','device_name')).lower()
                    if serial.startswith('WH1') or ('hand' in kind and '2' not in kind and not serial.startswith('WH2')):raise ValueError('官方 viz 当前支持 Hand 2 和 Glove / Official viz supports Hand 2 and Glove')
                    self.token=token;self.config=config
                    self.native_stop.clear()
                    self.state.update(busy=True,active=False,phase='starting',viewer_url=None,error=None,logs=[],device=found,reported_device=None,started_at=time.time())
                    if native_network(config):
                        marker=reservation(self.folder)
                        marker.update(backend='windows_native',job_name='Local\\HandWorkbench-OfficialViz-'+token,serial=serial)
                        atomic(self.folder/'session.json',marker)
                    self.thread=threading.Thread(target=self._run,args=(config,serial,theme),name='Official CLI visualization',daemon=True)
                    self.thread.start()
            except Exception as error:
                if self.token==token:
                    self._set(busy=False,active=False,phase='error',viewer_url=None,error=str(error)[:700])
                    self.token=None;self.thread=None;self.config=None
                release(token,self.folder);raise
        return self.snapshot(theme)

    def _run(self,config,serial,theme):
        if native_network(config):return self._run_native(serial,theme)
        p=None;cleanup_confirmed=False
        try:
            from doctor import local_run
            from calibration_cli import controller_args
            code,out,err=local_run(controller_args(config,[cli_path(config),'--version']),timeout=20)
            version=re.search(r'\b(20\d{2}\.\d{1,2}\.\d{1,2})\b',out)
            if code or not version:raise ValueError('Official CLI version check failed')
            self._set(cli_version=version[1])
            command=self._command(config,'--cli',cli_path(config),'--sn',serial,'--session-dir','/tmp/hand-workbench-official-viz')
            p=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',bufsize=1,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            with self.lock:self.process=p;stopping=self.state['phase']=='stopping'
            if stopping:p.stdin.write('stop\n');p.stdin.flush()
            def stderr():
                for line in p.stderr:self._log(line.rstrip())
            reader=threading.Thread(target=stderr,daemon=True);reader.start()
            ready=threading.Event()
            def timeout():
                if not ready.wait(60) and p.poll() is None:
                    self._set(error='官方可视化在60秒内未返回启动信息 / Official viewer startup timed out',phase='error')
                    try:p.stdin.write('stop\n');p.stdin.flush()
                    except (OSError,ValueError):pass
            threading.Thread(target=timeout,daemon=True).start()
            for line in p.stdout:
                if len(line)>32768:raise ValueError('Official viewer startup record too large')
                try:event=json.loads(line)
                except ValueError:self._log(line.rstrip());continue
                if not isinstance(event,dict):continue
                if event.get('workbench_event'):
                    if event['workbench_event']=='stderr':self._log(event.get('message',''))
                    if event['workbench_event']=='exited' and event.get('sn')==serial and event.get('cleanup_confirmed') is True:cleanup_confirmed=True
                    if event['workbench_event']=='error':self._set(error=str(event.get('message','Official CLI runner failed'))[:700])
                    continue
                if event.get('status')=='running':
                    reported=event.get('device') or {}
                    if reported.get('sn')!=serial:raise ValueError('Official viewer returned a different device serial')
                    url=viewer_url((event.get('viewer') or {}).get('url'),theme)
                    for endpoint in (url,urlsplit(url).query and dict(parse_qsl(urlsplit(url).query))['url']):
                        port=urlsplit(endpoint).port
                        with socket.create_connection(('127.0.0.1',port),timeout=5):pass
                    url=self._browser_url(url)
                    with self.lock:
                        if self.state['phase']!='stopping':self.state.update(active=True,busy=False,phase='running',device={**self.state['device'],**reported},viewer_url=url)
                    ready.set()
            code=p.wait(timeout=10);ready.set();reader.join(timeout=2)
            with self.lock:
                requested=self.state['phase']=='stopping'
                self.state.update(active=False,busy=False,phase='idle' if requested else 'exited',viewer_url=None)
                if code and not requested:self.state.update(phase='error',error='官方可视化进程退出 / Official visualization exited ('+str(code)+')')
        except Exception as error:
            self._set(active=False,busy=False,phase='error',viewer_url=None,error=str(error)[:700])
        finally:
            transport_cleanup=self._close_transport()
            if p and p.poll() is None:
                try:p.stdin.write('stop\n');p.stdin.flush();p.wait(timeout=12)
                except (OSError,ValueError,subprocess.TimeoutExpired):
                    # Do not release a device reservation merely because the
                    # Windows launcher disappeared. Explicit runner recovery is required.
                    self._set(error='CLI 会话退出尚未确认，请重试停止 / CLI cleanup not confirmed; retry Stop',phase='error')
                    return
            with self.lock:self.process=None
            if (p is None or cleanup_confirmed) and transport_cleanup:
                with gate(self.folder):release(self.token,self.folder);self.token=None
            else:self._set(phase='error',busy=False,error='CLI 会话退出尚未确认，请点击停止以核对 / CLI cleanup not confirmed; use Stop to recover')

    def _run_native(self,serial,theme):
        """Observation-only native CLI, confined to a Windows kill-on-close job.

        Stop terminates this Viewer process tree, not a hand or motor. The
        original CLI startup report is not evidence of data freshness.
        """
        job=None;p=None;reader=None;timer_done=threading.Event();cleanup=False
        try:
            from native_cli import prepare
            from windows_process_job import OwnedProcess
            from native_environment import official_cli_environment
            component=prepare(strict=True)
            self._set(cli_version=component['version'],backend='windows_native')
            with self.lock:
                if self.native_stop.is_set():return
                marker=reservation(self.folder)
                if marker.get('token')!=self.token:raise ValueError('Official Viewer ownership changed')
                job=OwnedProcess([component['binary'],'viz','--sn',serial,'--no-zero','--no-open','--jsonl'],
                    job_name=marker['job_name'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    text=True,encoding='utf-8',errors='replace',bufsize=1,
                    env=official_cli_environment())
                p=job.process;self.native_job=job;self.process=p
            def stderr():
                for line in p.stderr:self._log(line.rstrip())
            reader=threading.Thread(target=stderr,daemon=True);reader.start()
            def timeout():
                if not timer_done.wait(60):
                    self._set(error='官方可视化启动超时 / Official Viewer startup timed out',phase='error')
                    self.native_stop.set();job.terminate_and_wait(5)
            threading.Thread(target=timeout,daemon=True).start()
            for line in p.stdout:
                if len(line)>32768:raise ValueError('Official Viewer startup record too large')
                try:event=json.loads(line)
                except ValueError:self._log(line.rstrip());continue
                if not isinstance(event,dict) or event.get('status')!='running':continue
                reported=event.get('device') or {}
                if reported.get('sn')!=serial:raise ValueError('Official Viewer returned a different device serial')
                url=viewer_url((event.get('viewer') or {}).get('url'),theme)
                for endpoint in (url,dict(parse_qsl(urlsplit(url).query))['url']):
                    with socket.create_connection(('127.0.0.1',urlsplit(endpoint).port),timeout=5):pass
                url=self._browser_url(url)
                with self.lock:
                    if not self.native_stop.is_set():
                        self.state.update(active=True,busy=False,phase='running',device={**self.state['device'],**reported},reported_device=copy.deepcopy(reported),viewer_url=url)
                timer_done.set()
            code=p.wait(timeout=10);reader.join(timeout=2)
            with self.lock:
                requested=self.state['phase']=='stopping'
                self.state.update(active=False,busy=False,phase='idle' if requested else 'exited',viewer_url=None)
                if code and not requested:self.state.update(phase='error',error='官方可视化进程退出 / Official visualization exited ('+str(code)+')')
        except Exception as error:
            self._set(active=False,busy=False,phase='error',viewer_url=None,error=str(error)[:700])
        finally:
            timer_done.set()
            try:cleanup=job is None or job.close(timeout=7)
            except Exception as error:
                cleanup=False;self._log('Native CLI cleanup: '+str(error)[:500])
            cleanup=self._close_transport() and cleanup
            with self.lock:
                self.process=None
                if cleanup:self.native_job=None
            if cleanup and p is not None:
                if reader is not None:reader.join(timeout=1)
                for stream in (p.stdout,p.stderr):
                    if stream is not None and (stream is not p.stderr or reader is None or not reader.is_alive()):
                        try:stream.close()
                        except (OSError,AttributeError):pass
            if cleanup:
                with gate(self.folder):release(self.token,self.folder);self.token=None
            else:self._set(phase='error',busy=False,active=False,viewer_url=None,error='原生 CLI 退出未确认，请重试停止 / Native CLI cleanup unconfirmed; retry Stop')

    def connection_record(self,serial,session_id,*,require_glove=True):
        """Verify an actual local native Viewer, independently of scan TTL.

        Only the CLI's exact startup report establishes the current task's
        identity. This does not claim sensor freshness or SDK preview state.
        Callers performing a transition must also hold the shared CLI gate.
        """
        if (not isinstance(serial,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',serial)
                or not isinstance(session_id,str) or not re.fullmatch(r'[0-9a-f]{32}',session_id)):
            raise ValueError('请选择本工作区已接入的手套 / Select the glove connected in this workspace')
        with self.lock:
            marker=reservation(self.folder);reported=self.state.get('reported_device') or {}
            if (self.token!=session_id or marker.get('token')!=session_id or marker.get('kind')!='viz'
                    or marker.get('backend')!='windows_native' or marker.get('serial')!=serial
                    or marker.get('job_name')!='Local\\HandWorkbench-OfficialViz-'+session_id
                    or marker.get('pid')!=os.getpid() or marker.get('start') is None
                    or identity(marker.get('pid'))!=marker.get('start')
                    or self.state.get('backend')!='windows_native' or self.state.get('active') is not True
                    or self.state.get('phase')!='running' or self.state.get('busy') is not False
                    or not isinstance(reported,dict) or reported.get('sn')!=serial
                    or self.process is None or self.process.poll() is not None or self.native_job is None):
                raise ValueError('设备画面会话已变化或尚未接入，请先接入所选设备 / The Viewer session changed or is not connected; connect the selected device first')
            from calibration_cli import is_glove_device
            if require_glove and not is_glove_device(reported):raise ValueError('标定需要本次接入的手套 / Calibration requires the connected glove')
            side=reported.get('handedness',reported.get('side'))
            side=side.lower().removeprefix('handedness.') if isinstance(side,str) else None
            return dict(serial=serial,side=side if side in ('left','right') else None,
                source='native_viewer',viewer_token=session_id,cli_version=self.state.get('cli_version'),
                checked_at=time.time(),owner_pid=marker['pid'],owner_start=marker['start'])

    def stop_owned(self,serial,session_id,*,require_glove=True):
        """Stop only the explicitly selected local Viewer, then verify release."""
        result=self._stop(expected_token=session_id,expected_serial=serial,require_glove=require_glove)
        with self.lock:
            if (self.token is not None or self.process is not None or self.native_job is not None
                    or self.transport is not None or self.thread and self.thread.is_alive()
                    or self.state.get('active') or self.state.get('busy') or self.state.get('phase')!='idle'):
                raise ValueError('画面会话释放尚未确认，未开始标定 / Viewer release is unconfirmed; calibration has not started')
        return result

    def stop(self):
        native=reservation(self.folder).get('backend')=='windows_native'
        try:return self._stop()
        except Exception as error:
            if native:
                with self.lock:
                    if self.state['phase']=='stopping':
                        self.state.update(phase='error',busy=False,active=False,viewer_url=None,
                            error='原生 CLI 退出未确认，请重试停止 / Native CLI cleanup unconfirmed: '+str(error)[:400])
            raise

    def _stop(self,*,expected_token=None,expected_serial=None,require_glove=True):
        if expected_token is not None:
            with gate(self.folder):self.connection_record(expected_serial,expected_token,require_glove=require_glove)
        stale=reservation(self.folder)
        if expected_token is not None and stale.get('token')!=expected_token:
            raise ValueError('Viewer ownership changed before handover')
        expected_token=stale.get('token')
        if stale and stale.get('kind')!='viz':raise ValueError('另一个 CLI 操作正在进行 / Another CLI operation is in progress')
        if stale and stale.get('token')!=self.token:
            if identity(stale.get('pid'))==stale.get('start'):raise ValueError('请回到拥有该可视化的工作区停止 / Stop the visualization in its owning workspace')
        native=stale.get('backend')=='windows_native'
        with self.lock:
            p=self.process;thread=self.thread;self.state.update(phase='stopping',busy=True)
            job=self.native_job
            if native:self.native_stop.set()
            if not native and p and p.poll() is None:
                try:p.stdin.write('stop\n');p.stdin.flush()
                except (OSError,ValueError):pass
        if native and job is not None:job.terminate_and_wait(timeout=7)
        if thread and thread.is_alive():thread.join(timeout=15)
        if thread and thread.is_alive():raise ValueError('官方可视化仍在退出 / Official visualization is still closing')
        with gate(self.folder):
            stale=reservation(self.folder)
            if stale and stale.get('token')!=expected_token:
                # A completed old stop must never terminate a subsequent
                # session that acquired the now-free handover marker.
                return self.snapshot()
            if stale:
                if stale.get('kind')!='viz':raise ValueError('另一个 CLI 操作正在进行 / Another CLI operation is in progress')
                if stale.get('backend')=='windows_native':
                    # Never act on a live other workspace or an arbitrary job.
                    if identity(stale.get('pid'))==stale.get('start'):
                        if stale.get('token')!=self.token:raise ValueError('Stop in the owning workspace')
                        if self.native_job and not self.native_job.close(timeout=7):raise ValueError('Native CLI cleanup remains unconfirmed')
                    from windows_process_job import recover_viz_job
                    expected='Local\\HandWorkbench-OfficialViz-'+str(stale.get('token',''))
                    if stale.get('job_name')!=expected or not recover_viz_job(expected,timeout=7):raise ValueError('Native CLI cleanup remains unconfirmed')
                else:
                    from bridge_config import load_config
                    result=subprocess.run(self._command(load_config(),'--stop','/tmp/hand-workbench-official-viz'),capture_output=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    if result.returncode:raise ValueError('无法确认旧 CLI 会话退出 / Previous CLI session cleanup not confirmed')
                    try:confirmed=json.loads(result.stdout)
                    except (ValueError,TypeError):raise ValueError('Invalid CLI cleanup confirmation') from None
                    if confirmed.get('cleanup_confirmed') is not True:raise ValueError('CLI cleanup remains unconfirmed')
                if not self._close_transport():raise ValueError('Viewer relay cleanup remains unconfirmed')
                release(stale.get('token'),self.folder)
        with self.lock:
            if self.token not in (None,expected_token):return self.snapshot()
            if native:self.native_job=None;self.process=None;self.token=None
            self.state.update(phase='idle',busy=False,active=False,viewer_url=None,error=None)
        return self.snapshot()
