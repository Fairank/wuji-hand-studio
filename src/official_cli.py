"""Verified official CLI releases, independently versioned from the SDK image."""
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

from cli_coordination import root_folder,gate,reserve,release,atomic,reservation
from cli_release_store import CLIReleaseStore


def selected(folder=None):
    try:
        value=json.loads((Path(folder or root_folder())/'selection.json').read_text(encoding='utf-8'))
        if not isinstance(value,dict):raise ValueError()
        return value
    except (OSError,ValueError,TypeError):return {}


def version_order(value):
    if not isinstance(value,str) or not re.fullmatch(r'20\d{2}\.\d{1,2}\.\d{1,2}',value):raise ValueError('Unsupported CLI release version')
    return tuple(map(int,value.split('.')))


def cli_path(config,folder=None):
    """Never replace an explicitly configured remote/custom CLI."""
    original=config.get('cli') or 'wuji'
    if config.get('mode') not in {'wsl','local'} or original not in {'wuji','/usr/local/bin/wuji'}:return original
    current=selected(folder).get('current') or {}
    version=current.get('version','');path=current.get('binary','')
    if not current.get('validated') or not re.fullmatch(r'20\d{2}\.\d{1,2}\.\d{1,2}',version):return original
    if config['mode']=='wsl':
        expected='/opt/hand-workbench/components/cli/versions/'+version+'/wuji'
        return path if current.get('mode')=='wsl' and path==expected else original
    root=Path(folder or root_folder()).resolve()/'versions'/version
    try:
        if current.get('mode')=='local' and Path(path).resolve().is_relative_to(root) and Path(path).is_file():return path
    except OSError:pass
    return original


def runtime_command(config,command):
    from calibration_cli import controller_args
    return controller_args(config,command)


class OfficialCLI:
    def __init__(self,folder=None,idle_check=None,store=None,native_store=None):
        self.folder=Path(folder or root_folder());self.closed=threading.Event()
        self.store=store or CLIReleaseStore(self.folder/'versions',timeout=300,socket_timeout=20,cancel_event=self.closed)
        from native_cli_update import ManagedNativeCLIStore
        self.native_store=native_store or ManagedNativeCLIStore(timeout=300,socket_timeout=20,cancel_event=self.closed,progress=self._native_progress)
        self.idle_check=idle_check or (lambda:None);self.lock=threading.RLock();self.thread=None
        self.state=dict(phase='unchecked',checking=False,preparing=False,available=False,latest_version=None,checked_at=None,error=None,auto_update=True)
        self.started=False
        self.initial_check_started=False
        self.monitor=None;self.monitor_stop=threading.Event()

    def _native_progress(self,progress):
        self._set(phase=progress['stage'],progress=progress)

    def set_auto_update(self,enabled):
        from native_cli_maintenance import set_preference
        return set_preference(self,enabled)

    def snapshot(self):
        from managed_runtime import image_manifest
        saved=selected(self.folder);manifest=image_manifest() or {}
        from bridge_config import load_config
        from official_device_discovery import use_native
        native=use_native(load_config())
        with self.lock:
            update=copy.deepcopy(self.state)
        current=saved.get('current') or dict(version=manifest.get('cli_version'),source='bundled',validated=False)
        if native:
            from native_cli_maintenance import snapshot,preference
            current,previous=snapshot(self)
            update.update(auto_update=preference(self),update_method='managed_native')
            return dict(update=update,current=current,previous=previous,versions=[v for v in (current,previous) if v],busy=bool(self.thread and self.thread.is_alive()) or update['checking'] or update['preparing'])
        versions=[v for v in (current,saved.get('previous')) if v]
        return dict(update=update,current=current,previous=saved.get('previous'),versions=versions,busy=bool(self.thread and self.thread.is_alive()) or update['checking'] or update['preparing'])

    def _set(self,**values):
        with self.lock:self.state.update(**values)

    def startup(self):
        if os.environ.get('WUJI_FLEET_ID') or os.environ.get('WUJI_CLI_AUTO_CHECK')=='0':return
        with self.lock:
            if self.started:return
            self.started=True
        try:
            if not self.initial_check_started:
                if not (self.thread and self.thread.is_alive()):self.start(check_only=True,automatic=True)
                with self.lock:self.initial_check_started=True
            self.monitor_stop.clear()
            self.monitor=threading.Thread(target=self._monitor,name='Official CLI update schedule',daemon=True);self.monitor.start()
        except Exception:
            with self.lock:self.started=False;self.monitor=None
            raise

    def _monitor(self):
        while not self.monitor_stop.wait(60):
            from native_cli_maintenance import preference
            if not preference(self):continue
            from bridge_config import load_config
            from official_device_discovery import use_native
            if not use_native(load_config()):continue
            with self.lock:
                due=self.state.get('auto_update_pending') or time.time()-(self.state.get('checked_at') or 0)>=900
                busy=bool(self.thread and self.thread.is_alive())
            if due and not busy:
                try:self.start(check_only=False,automatic=True)
                except ValueError:pass

    def start(self,check_only=False,automatic=False):
        with self.lock:
            if self.thread and self.thread.is_alive():raise ValueError('CLI 更新检查正在进行 / CLI update already in progress')
            self.closed.clear();self.state.update(phase='checking',checking=True,preparing=False,error=None)
            try:
                self.thread=threading.Thread(target=self._work,args=(check_only,automatic),name='Official CLI update',daemon=True);self.thread.start()
            except Exception as error:
                self.thread=None
                self.state.update(phase='error',checking=False,preparing=False,error=str(error)[:700])
                raise
        return self.snapshot()

    def _work(self,check_only,automatic=False):
        try:
            if self.closed.is_set():return
            from bridge_config import load_config
            from official_device_discovery import use_native
            if use_native(load_config()):
                from native_cli_maintenance import run
                run(self,check_only,automatic)
                return
            release_doc=self.store.fetch_release();version=release_doc['tag_name'].removeprefix('v')
            saved=selected(self.folder);current=saved.get('current') or {}
            available=(not current.get('version') or version_order(version)>version_order(current['version'])
                or current.get('source')=='bundled' and version_order(version)==version_order(current['version']))
            self._set(latest_version=version,available=available,checked_at=time.time(),checking=False)
            if check_only or not available or automatic and saved.get('hold_version')==version:self._set(phase='checked');return
            from bridge_config import load_config
            config=load_config()
            if config['mode'] not in {'wsl','local'}:
                self._set(phase='manual',error='自动安装适用于 Windows 内置 Linux 或本机 Linux；远程控制端保持原配置 / Automatic install requires the built-in or local Linux controller');return
            if config['mode']=='wsl':
                from managed_runtime import check_ready
                check_ready()
            self._set(phase='preparing',preparing=True)
            prepared=self.store.prepare(version,release=release_doc)
            if self.closed.is_set():return
            candidate=self._deploy(prepared,config)
            atomic(self.folder/'prepared.json',candidate)
            while not self.closed.is_set():
                try:
                    self._activate(candidate)
                    self._set(phase='ready',available=False);break
                except ValueError as error:
                    self._set(phase='waiting_idle',preparing=False,error=str(error))
                    if self.closed.wait(10):break
        except Exception as error:
            if not self.closed.is_set():logging.getLogger(__name__).exception('Official CLI release maintenance failed')
            self._set(phase='cancelled' if self.closed.is_set() else 'error',error=None if self.closed.is_set() else str(error)[:700])
        finally:
            if self.closed.is_set():self._set(phase='cancelled',error=None)
            self._set(checking=False,preparing=False)

    def cancel_and_wait(self,timeout=22):
        self.monitor_stop.set()
        self.closed.set()
        with self.lock:worker=self.thread
        if worker and worker is not threading.current_thread():worker.join(timeout=timeout)
        return not worker or not worker.is_alive()

    def _deploy(self,prepared,config):
        source=Path(prepared['directory'])/prepared['paths']['binary']
        with source.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        version=prepared['version']
        if not re.fullmatch(r'20\d{2}\.\d{1,2}\.\d{1,2}',version):raise ValueError('Unsupported CLI release version')
        if config['mode']=='wsl':
            from managed_runtime import Files,args,PYTHON
            target='/opt/hand-workbench/components/cli/versions/'+version+'/wuji'
            with Files() as files:
                destination=files.path(target);destination.parent.mkdir(parents=True,exist_ok=True)
                if destination.exists():
                    with destination.open('rb') as f:
                        if hashlib.file_digest(f,'sha256').hexdigest()!=digest:raise ValueError('Existing managed CLI differs from verified release')
                else:
                    stage=destination.with_suffix('.pending')
                    try:
                        with source.open('rb') as f,stage.open('xb') as out:shutil.copyfileobj(f,out)
                        stage.replace(destination)
                    finally:stage.unlink(missing_ok=True)
            code="import hashlib,os,sys; p=sys.argv[1]; assert hashlib.sha256(open(p,'rb').read()).hexdigest()==sys.argv[2]; os.chmod(p,0o555)"
            checked=subprocess.run(args([PYTHON,'-c',code,target,digest],user='root'),capture_output=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if checked.returncode:raise ValueError('Linux CLI transfer verification failed')
        else:
            target=str(source.resolve());os.chmod(source,0o555)
        from doctor import local_run
        from calibration_cli import controller_args
        evidence={}
        for label,arguments in [('version',['--version']),('help',['--help']),('viz',['viz','--help']),('calibration',['calib','hand-model','--help'])]:
            code,out,err=local_run(controller_args(config,[target,*arguments]),timeout=30)
            if code:raise ValueError('New CLI software compatibility check failed: '+label)
            evidence[label]=out
        found=re.search(r'\b(20\d{2}\.\d{1,2}\.\d{1,2})\b',evidence['version'])
        if not found or found[1]!=version or '--no-zero' not in evidence['viz'] or '--no-open' not in evidence['viz'] or '--jsonl' not in evidence['help']:
            raise ValueError('New CLI version or required interfaces do not match')
        return dict(version=version,commit=prepared['commit'],archive_sha256=prepared['archive_sha256'],binary_sha256=digest,binary=target,mode=config['mode'],validated=True,validated_at=time.time(),validation='software-help-only',source=prepared['release_url'])

    def _activate(self,candidate):
        with gate(self.folder):
            token=reserve('switch',self.folder)
            try:
                self.idle_check()
                previous=selected(self.folder)
                baseline=previous.get('current')
                if baseline is None:
                    from managed_runtime import image_manifest
                    manifest=image_manifest() or {}
                    if manifest.get('cli_version'):
                        baseline=dict(version=manifest['cli_version'],source='bundled',mode='wsl',binary='/usr/local/bin/wuji',validated=False)
                atomic(self.folder/'selection.json',dict(current=candidate,previous=baseline,updated_at=time.time()))
            finally:release(token,self.folder)
        self._set(error=None)

    def rollback(self):
        from bridge_config import load_config
        from official_device_discovery import use_native
        if use_native(load_config()):
            from native_cli_maintenance import rollback
            return rollback(self)
        with self.lock:
            if self.thread and self.thread.is_alive():raise ValueError('Wait for the CLI update to finish')
        with gate(self.folder):
            saved=selected(self.folder);previous=saved.get('previous')
            if not previous:raise ValueError('尚无可回退的独立 CLI 版本 / No prior managed CLI version')
            token=reserve('switch',self.folder)
            try:
                self.idle_check()
                atomic(self.folder/'selection.json',dict(current=previous,previous=saved.get('current'),hold_version=(saved.get('current') or {}).get('version'),updated_at=time.time()))
            finally:release(token,self.folder)
        return self.snapshot()
