"""Embed the original CLI viewer, without fabricating feedback or device state."""
import copy
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

from cli_coordination import root_folder,gate,reserve,release,reservation
from official_cli import cli_path


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
    def __init__(self,folder=None,idle_check=None):
        self.folder=Path(folder or root_folder());self.idle_check=idle_check or (lambda:None)
        self.lock=threading.RLock();self.process=None;self.thread=None;self.token=None;self.config=None
        self.state=dict(supported=sys.platform=='win32' or sys.platform.startswith('linux'),busy=False,active=False,phase='idle',device=None,devices=[],scanning=False,scanned_at=None,viewer_url=None,cli_version=None,error=None,logs=[],started_at=None,stream_freshness='not_reported',zero_on_start=False)

    def snapshot(self,theme=None):
        with self.lock:row=copy.deepcopy(self.state)
        lease=reservation(self.folder)
        blocked=bool(lease) and lease.get('token')!=self.token
        row['can_start']=row['supported'] and not row['busy'] and not row['active'] and not lease
        row['reserved']=bool(lease) and lease.get('kind')=='viz'
        row['blocked_reason']='另一个官方 CLI 会话尚未结束 / Another official CLI session is active' if blocked else None
        if row.get('viewer_url'):row['viewer_url']=viewer_url(row['viewer_url'],theme)
        return row

    def _set(self,**values):
        with self.lock:self.state.update(**values)

    def _log(self,message):
        with self.lock:self.state['logs']=(self.state['logs']+[str(message)[:1200]])[-60:]

    def scan(self):
        with gate(self.folder):
            token=reserve('scan',self.folder)
            try:self.idle_check()
            except Exception:release(token,self.folder);raise
            with self.lock:
                if self.state['busy'] or self.state['active']:release(token,self.folder);raise ValueError('Official CLI session is busy')
                self.state.update(busy=True,scanning=True,error=None)
        def work():
            try:
                from bridge_config import load_config
                from calibration_cli import cli_json
                config=load_config();value=cli_json(config,'devices')
                devices=value.get('devices')
                if not isinstance(devices,list):raise ValueError('Invalid official device list')
                devices=[x for x in devices if isinstance(x,dict)]
                self._set(devices=devices,scanned_at=time.time())
            except Exception as error:self._set(error=str(error)[:700])
            finally:
                with gate(self.folder):release(token,self.folder)
                self._set(busy=False,scanning=False)
        threading.Thread(target=work,name='Official CLI device discovery',daemon=True).start()
        return self.snapshot()

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
                    found=next((x for x in self.state['devices'] if (x.get('sn') or x.get('serial'))==serial),None)
                    if not found:raise ValueError('请先扫描并选择实际设备 / Scan and select a discovered device first')
                    kind=' '.join(str(found.get(k,'')) for k in ('model','type','device_type','device_name')).lower()
                    if serial.startswith('WH1') or ('hand' in kind and '2' not in kind and not serial.startswith('WH2')):raise ValueError('官方 viz 当前支持 Hand 2 和 Glove / Official viz supports Hand 2 and Glove')
                    self.token=token;self.config=config
                    self.state.update(busy=True,active=False,phase='starting',viewer_url=None,error=None,logs=[],device=found,started_at=time.time())
                    self.thread=threading.Thread(target=self._run,args=(config,serial,theme),name='Official CLI visualization',daemon=True)
                    self.thread.start()
            except Exception:release(token,self.folder);raise
        return self.snapshot(theme)

    def _run(self,config,serial,theme):
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
            if p and p.poll() is None:
                try:p.stdin.write('stop\n');p.stdin.flush();p.wait(timeout=12)
                except (OSError,ValueError,subprocess.TimeoutExpired):
                    # Do not release a device reservation merely because the
                    # Windows launcher disappeared. Explicit runner recovery is required.
                    self._set(error='CLI 会话退出尚未确认，请重试停止 / CLI cleanup not confirmed; retry Stop',phase='error')
                    return
            with self.lock:self.process=None
            if p is None or cleanup_confirmed:
                with gate(self.folder):release(self.token,self.folder);self.token=None
            else:self._set(phase='error',busy=False,error='CLI 会话退出尚未确认，请点击停止以核对 / CLI cleanup not confirmed; use Stop to recover')

    def stop(self):
        stale=reservation(self.folder)
        expected_token=stale.get('token')
        if stale and stale.get('kind')!='viz':raise ValueError('另一个 CLI 操作正在进行 / Another CLI operation is in progress')
        if stale and stale.get('token')!=self.token:
            from cli_coordination import identity
            if identity(stale.get('pid'))==stale.get('start'):raise ValueError('请回到拥有该可视化的工作区停止 / Stop the visualization in its owning workspace')
        with self.lock:
            p=self.process;thread=self.thread;self.state.update(phase='stopping',busy=True)
            if p and p.poll() is None:
                try:p.stdin.write('stop\n');p.stdin.flush()
                except (OSError,ValueError):pass
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
                from bridge_config import load_config
                result=subprocess.run(self._command(load_config(),'--stop','/tmp/hand-workbench-official-viz'),capture_output=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if result.returncode:raise ValueError('无法确认旧 CLI 会话退出 / Previous CLI session cleanup not confirmed')
                try:confirmed=json.loads(result.stdout)
                except (ValueError,TypeError):raise ValueError('Invalid CLI cleanup confirmation') from None
                if confirmed.get('cleanup_confirmed') is not True:raise ValueError('CLI cleanup remains unconfirmed')
                release(stale.get('token'),self.folder)
        with self.lock:
            if self.token not in (None,expected_token):return self.snapshot()
            self.state.update(phase='idle',busy=False,active=False,viewer_url=None)
        return self.snapshot()
