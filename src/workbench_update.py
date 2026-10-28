"""App-level updates. Checks/downloads never close a device or the application."""
import copy
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from cli_coordination import atomic,gate,reservation
from workbench_release import WorkbenchRelease,REPOSITORY,_plain,_directory

APP_ID='44DF38B2-39A0-491F-B154-E587231C83CE'


class WorkbenchUpdate:
    def __init__(self,data_dir,edition,*,idle_check=None,desktop=None,store=None):
        self.data=Path(data_dir);self.edition=edition;self.idle_check=idle_check or (lambda:None)
        self.desktop=desktop or (lambda:None)
        self.store=store or WorkbenchRelease(self.data,edition['version'])
        self.lock=threading.RLock();self.closed=threading.Event();self.thread=None;self.latest=None;self.started=False
        self.result_file=None;self.apply_token=None;self.helper=None
        self._completion_reads=0;self._completion_checked_at=0;self._completion_retired=False
        self.state=dict(current_version=edition['version'],phase='idle',busy=False,latest_version=None,
            available=False,prepared=None,error=None,error_stage=None,cancelled=False,checked_at=None,source=REPOSITORY,progress=None)
        self._restore_completed()

    def _restore_completed(self):
        """Only an exact completed local update to this running version is success."""
        if self._completion_retired:return
        self._completion_reads+=1;self._completion_checked_at=time.monotonic()
        try:
            root=self.data/'updates';_directory(root)
            pointer=root/'last-application-update.json'
            if not _plain(pointer,directory=False) or pointer.stat().st_size>65536:return
            plan=json.loads(pointer.read_text('utf-8'))
            if not isinstance(plan,dict) or plan.get('repository')!=REPOSITORY or plan.get('app_id')!=APP_ID:return
            token=plan.get('token');version=plan.get('version');digest=plan.get('sha256')
            if not isinstance(token,str) or not re.fullmatch('[0-9a-f]{32}',token):return
            if version!=self.edition['version'] or not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):return
            result=root/('apply-'+token+'.result.json')
            if not _plain(result,directory=False) or result.stat().st_size>65536:return
            report=json.loads(result.read_text('utf-8-sig'))
            if not isinstance(report,dict) or any(report.get(k)!=plan[k] for k in ('token','version','sha256')) or report.get('phase')!='installed':return
            if report.get('launch_started') is not True or report.get('version_verified') is not True:return
            self.state['completed_version']=version
            if self.state['phase']=='idle':self.state['phase']='completed'
        except (OSError,ValueError,TypeError):return

    def _retire_completion(self):
        # Keep the signed-off local receipts on disk as history, but do not
        # associate the old installed version with a new or failed operation.
        self._completion_retired=True
        self.state.pop('completed_version',None)

    def _idle(self):
        if reservation():raise ValueError('请结束官方 CLI 会话后更新 / End the official CLI session before updating')
        self.idle_check()

    def _helper_settled(self):
        if self.helper is None:return self.result_file is None
        try:return type(self.helper.poll()) is int
        except (OSError,ValueError):return False

    def snapshot(self):
        with self.lock:
            # The helper records success after launching the new process. Allow
            # its exact result to arrive after this process's constructor.
            if not self._completion_retired and not self.state.get('completed_version') and self._completion_reads<15 and time.monotonic()-self._completion_checked_at>=2:
                self._restore_completed()
            result=copy.deepcopy(self.state)
            if self.result_file:
                try:
                    if not _plain(self.result_file,directory=False) or self.result_file.stat().st_size>=65536:raise ValueError('No valid updater result')
                    report=json.loads(self.result_file.read_text(encoding='utf-8-sig'))
                    if not isinstance(report,dict) or report.get('token')!=self.apply_token or report.get('phase')!='error':
                        raise ValueError('No matching updater error result')
                    error=str(report.get('error') or 'Update helper failed')[:700]
                    self._retire_completion()
                    self.state.update(phase='error',busy=False,error=error,error_stage='install',cancelled='cancel' in error.lower());result=copy.deepcopy(self.state)
                except (OSError,ValueError,TypeError):
                    if self.helper and self._helper_settled() and self.state['phase']=='applying':
                        self._retire_completion()
                        self.state.update(phase='error',busy=False,error='更新助手未完成，请重试 / Update helper did not complete; retry',error_stage='install',cancelled=False);result=copy.deepcopy(self.state)
        result['supported']=sys.platform=='win32' and bool(getattr(sys,'frozen',False)) and self.desktop() is not None
        result['can_apply']=False
        if result['supported'] and result.get('prepared') and not result['busy'] and self._helper_settled():
            try:self._idle();result['can_apply']=True
            except (ValueError,OSError):pass
        return result

    def startup(self):
        if os.environ.get('WUJI_FLEET_ID') or os.environ.get('WUJI_APP_AUTO_CHECK')=='0' or not self.snapshot()['supported']:return
        with self.lock:
            if self.started:return
            self.started=True
        self.start('check')

    def start(self,operation):
        if operation not in ('check','prepare'):raise ValueError('Unknown application update operation')
        # Observe the exact helper result before a deliberate retry. A previous
        # failure must not keep overwriting the next check's ready state.
        if self.result_file:self.snapshot()
        with self.lock:
            if self.state['busy']:raise ValueError('软件更新正在处理 / Application update is busy')
            if operation=='prepare' and (not self.latest or self.latest.get('available') is not True):raise ValueError('先检查可用的新版本 / Check for an available release first')
            if self.result_file:
                if self.state['phase']!='error' or not self._helper_settled():
                    raise ValueError('更新助手正在结束，请稍候重试 / Update helper is still finishing; retry shortly')
                # Keep the old plan and result files as history. Only retire
                # this completed association, under the same service lock.
                self.result_file=None;self.apply_token=None;self.helper=None
            if operation=='prepare':self._retire_completion()
            self.closed.clear();self.state.update(busy=True,error=None,error_stage=None,cancelled=False,progress=None,phase='checking' if operation=='check' else 'downloading')
            self.thread=threading.Thread(target=self._work,args=(operation,),name='Workbench software update',daemon=True);self.thread.start()
        return self.snapshot()

    def _work(self,operation):
        try:
            if operation=='check':
                latest=self.store.check(cancel_event=self.closed)
                with self.lock:
                    if self.closed.is_set():raise ValueError('Application update cancelled')
                    self.latest=latest
                    if latest.get('available') is True:self._retire_completion()
                    prepared=self.state.get('prepared')
                    prepared_current=self._prepared_matches(prepared,latest)
                    # Leave the verified files on disk for an explicit prepare
                    # to reuse or replace; neither polling nor checking downloads.
                    # Sidecar-only releases must be reverified by prepare because
                    # a metadata response cannot prove their installer digest.
                    if not prepared_current:self.state['prepared']=None
                    self.state.update(latest_version=latest.get('latest_version'),available=latest.get('available') is True,
                        checked_at=time.time(),phase='ready' if prepared_current else 'unpublished' if latest.get('reason')=='unpublished' else 'available' if latest.get('available') else 'up_to_date')
            else:
                prepared=self.store.prepare(self.latest,cancel_event=self.closed,progress=self._progress)
                with self.lock:
                    if self.closed.is_set():raise ValueError('Application update cancelled')
                    self.state.update(prepared=prepared,phase='ready')
        except Exception as error:
            with self.lock:
                self._retire_completion()
                self.state.update(phase='error',error=str(error)[:700],error_stage='check' if operation=='check' else 'download',cancelled=self.closed.is_set() or 'cancel' in str(error).lower())
        finally:
            with self.lock:self.state['busy']=False

    def _progress(self,value):
        with self.lock:
            if self.state['phase']=='downloading' and not self.closed.is_set():
                self.state['progress']=dict(value)

    @staticmethod
    def _prepared_matches(prepared,latest):
        # A tag can be republished. A version match alone cannot authorize the
        # old packet after a check reports a different installer identity.
        if not isinstance(prepared,dict) or latest.get('available') is not True:return False
        asset=latest.get('asset')
        if not isinstance(asset,dict):return False
        digest=asset.get('digest')
        if not isinstance(digest,str) or not re.fullmatch(r'sha256:[0-9a-fA-F]{64}',digest):return False
        if not isinstance(asset.get('browser_download_url'),str) or not asset['browser_download_url']:return False
        version=latest.get('latest_version')
        return (prepared.get('version')==version and prepared.get('repository')==REPOSITORY
            and asset.get('name')=='HandWorkbench-'+str(version)+'-windows-x64-setup.exe'
            and prepared.get('asset_url')==asset.get('browser_download_url')
            and type(asset.get('size')) is int and asset['size']>0
            and type(prepared.get('size')) is int and prepared['size']==asset['size']
            and prepared.get('sha256')==digest[7:].lower())

    def _stage_installer(self,prepared):
        """Reuse the existing finite desktop prepare_update interface's layout."""
        version=prepared['version'];digest=prepared['sha256'];name='HandWorkbench-'+version+'-windows-x64-setup.exe'
        from desktop_tools import version_tuple
        version_tuple(version)
        source=Path(prepared['installer']);expected=self.data/'updates/versions'/version/name
        if source!=expected or prepared.get('repository')!=REPOSITORY:raise ValueError('Unexpected prepared installer location or source')
        _directory(source.parent)
        if not _plain(source,directory=False):raise ValueError('Prepared installer disappeared')
        with source.open('rb') as stream:
            if stream.read(2)!=b'MZ':raise ValueError('Expected a Windows installer')
            stream.seek(0)
            if hashlib.file_digest(stream,'sha256').hexdigest()!=digest:raise ValueError('Prepared installer changed after download')
        destination=self.data/'updates'/name
        _directory(destination.parent)
        _plain(destination,directory=False)
        fd,temp=tempfile.mkstemp(prefix=name+'.',suffix='.partial',dir=destination.parent)
        stage=Path(temp)
        try:
            with os.fdopen(fd,'wb') as output,source.open('rb') as incoming:
                shutil.copyfileobj(incoming,output);output.flush();os.fsync(output.fileno())
            with stage.open('rb') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()!=digest:raise ValueError('Installer changed while staging')
            _plain(destination,directory=False);os.replace(stage,destination)
        finally:stage.unlink(missing_ok=True)
        return destination

    def apply(self):
        from runtime_paths import RESOURCE
        if not self.snapshot()['supported']:raise ValueError('请在 Windows 已安装版本中使用软件更新 / Use software update in the installed Windows application')
        with self.lock,gate():
            if self.state['busy'] or not self.state.get('prepared'):raise ValueError('Download and verify the new installer first')
            if not self._helper_settled():raise ValueError('更新助手正在结束，请稍候重试 / Update helper is still finishing; retry shortly')
            self._idle()
            self._retire_completion()
            prepared=copy.deepcopy(self.state['prepared']);installer=self._stage_installer(prepared)
            host=self.desktop();info=host.info()
            if info.get('native') is not True or info.get('edition')!=self.edition['name'] or info.get('pid')!=os.getpid() or Path(info.get('data_dir',''))!=self.data:
                raise ValueError('Application update host identity changed')
            token=uuid.uuid4().hex;updates=self.data/'updates'
            plan_file=updates/('apply-'+token+'.json');result_file=updates/('apply-'+token+'.result.json')
            plan=dict(schema=1,app_id=APP_ID,repository=REPOSITORY,token=token,data_dir=str(self.data),
                version=prepared['version'],sha256=prepared['sha256'],installer=str(installer),
                executable=str(Path(sys.executable).absolute()),origin=host.origin,expected_app_pid=os.getpid(),
                old_version=self.edition['version'],result_file=str(result_file))
            atomic(plan_file,plan)
            atomic(updates/'last-application-update.json',dict(app_id=APP_ID,repository=REPOSITORY,
                token=token,version=prepared['version'],sha256=prepared['sha256']))
            shell=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
            script=RESOURCE/'workbench_update_apply.ps1'
            if not script.is_file():raise ValueError('Application updater is missing from this installation')
            env=dict(os.environ)
            for key in tuple(env):
                if key.upper()=='PSMODULEPATH':env.pop(key)
            self.helper=subprocess.Popen([str(shell),'-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                '-File',str(script),'-PlanFile',str(plan_file)],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),env=env)
            self.result_file=result_file;self.apply_token=token
            self.state.update(phase='applying',busy=True,error=None,error_stage=None,cancelled=False)
        return self.snapshot()

    def cancel_and_wait(self,timeout=16):
        self.closed.set()
        if self.thread and self.thread is not threading.current_thread():self.thread.join(timeout)
        return not self.thread or not self.thread.is_alive()
