"""Versioned official Studio component. Owns software sessions, never motors."""
import copy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlencode,urlsplit
from urllib.request import Request,urlopen

from runtime_paths import RESOURCE
from user_dirs import user_data_dir


def root_folder():
    return Path(os.environ.get('WUJI_STUDIO_COMPONENT_DATA',str(user_data_dir('WujiStudio')/'official-studio')))


def validate_version(value):
    if not isinstance(value,str) or not re.fullmatch(r'20\d{2}\.\d{1,2}\.\d{1,2}',value):raise ValueError('Invalid official Studio version')
    return value


def lease_state(folder=None):
    path=Path(folder or root_folder())/'lease.json'
    try:return dict(json.loads(path.read_text(encoding='utf-8')),reserved=True)
    except FileNotFoundError:return dict(reserved=False)
    except (OSError,ValueError,TypeError):return dict(reserved=True,phase='needs_check')


def process_identity(pid):
    if sys.platform!='win32':return str(pid) if pid==os.getpid() else None
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.restype=wintypes.HANDLE;kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
    kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return None
    try:
        code=wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value!=259:return None
        values=[wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle,*map(ctypes.byref,values)):return None
        return str((values[0].dwHighDateTime<<32)|values[0].dwLowDateTime)
    finally:kernel.CloseHandle(handle)


def enforce_session_owner(command,folder=None):
    """All workspace processes honour one shared Studio handover marker."""
    if not isinstance(command,dict):return
    name=command.get('name','')
    blocked=(name in {'connect','parameters_sync','doctor_run','doctor_version','doctor_support','runtime_install','runtime_select',
        'bridge_config_save','bridge_password_save','bridge_password_forget','device_profile_select','solver_install','hardware_start',
        'hardware_trial','hardware_probe','program_start','program_resume','demo_start'}
        or name.startswith('calibration_') and name!='calibration_cancel'
        or name.startswith('glove_') and name not in {'glove_disconnect','glove_stop','glove_keepalive'}
        or name.startswith('group_') and name not in {'group_abort','group_beat'})
    if lease_state(folder).get('reserved') and blocked:
        raise ValueError('官方 Studio 正在管理设备会话，请先关闭 Studio / Close official Studio before using workbench device controls')


def require_idle(state,doctor=None):
    if ((state.get('official_cli') or {}).get('update') or {}).get('recovery_required') is True:
        raise ValueError('CLI 更新占用退出尚未确认，请先重新检查 / CLI update reservation cleanup is unconfirmed; check it again first')
    from connection_switch import unsettled
    if unsettled(state):raise ValueError('Device switch or SDK cleanup is unconfirmed')
    from device_termination import unconfirmed
    if (state.get('connection')!='disconnected' or state.get('hardware',{}).get('active') is not False
        or state.get('glove',{}).get('busy') or state.get('calibration',{}).get('running')
        or state.get('recording',{}).get('active') or state.get('parameter_sync',{}).get('busy')
        or state.get('program',{}).get('active') or state.get('group',{}).get('active') or (doctor or {}).get('running') or unconfirmed(state)):
        raise ValueError('请先结束所有工作区中的设备、标定和诊断会话 / Finish device, calibration and diagnostic sessions in every workspace')


def release_asset(document):
    selected=validate_version(str(document.get('tag_name','')).removeprefix('v'))
    name='wuji-studio_'+selected+'_amd64.deb'
    item=next((a for a in document.get('assets',[]) if a.get('name')==name),None)
    if not item or type(item.get('size')) is not int or not 0<item['size']<=200*1024**2:raise ValueError('Official Linux package missing or too large')
    expected='https://github.com/wuji-technology/wuji-studio/releases/download/v'+selected+'/'+name
    checksum=str(item.get('digest',''))
    if item.get('browser_download_url')!=expected or not re.fullmatch('sha256:[a-f0-9]{64}',checksum):
        raise ValueError('Official package URL or SHA-256 is missing')
    return dict(version=selected,tag='v'+selected,asset=name,url=expected,sha256=checksum[7:],bytes=item['size'])


class StudioComponent:
    def __init__(self,folder=None,idle_check=None):
        self.folder=Path(folder or root_folder());self.idle_check=idle_check or (lambda:None)
        self.lock=threading.RLock();self.worker=None;self.process=None;self.secret=None;self.token=None;self.cancel=threading.Event()
        self.state=dict(supported=sys.platform=='win32',busy=False,active=False,phase='unchecked',error=None,
            inventory=None,current=None,previous=None,catalog=[],upstream_maintenance='archived',device_state='not_observed')
        try:
            saved=json.loads((self.folder/'installation.json').read_text(encoding='utf-8'))
            self.state.update(inventory=saved['inventory'],current=saved['current'],previous=saved.get('previous'),phase='installed',last_verified=saved.get('verified'))
        except (OSError,ValueError,KeyError,TypeError):pass

    def snapshot(self):
        with self.lock:
            result=copy.deepcopy(self.state);lease=lease_state(self.folder)
            result['reserved']=lease['reserved'];result['reservation_phase']=lease.get('phase')
            # No viewer credential, arbitrary executable or raw log is public.
            return result

    def _update(self,**values):
        with self.lock:self.state.update(**values)

    def _reserve(self,phase):
        if self.token:raise ValueError('Studio 清理尚未确认，请先核对原会话 / Studio cleanup is unconfirmed; review the original session')
        if self.folder.is_symlink() or self.folder.is_junction():raise ValueError('Studio component directory is redirected')
        self.folder.mkdir(parents=True,exist_ok=True)
        self.token=secrets.token_hex(16)
        row=dict(owner_pid=os.getpid(),owner_start=process_identity(os.getpid()),token=self.token,phase=phase,started=time.time())
        try:
            with (self.folder/'lease.json').open('x',encoding='utf-8') as stream:json.dump(row,stream)
        except FileExistsError:self.token=None;raise ValueError('Studio 会话尚未结束；请检查组件状态 / Check the existing Studio session') from None

    def _release(self):
        # snapshot reads the same file under this lock. Windows read handles
        # can deny unlink; another process may also briefly hold a read handle.
        with self.lock:
            if not self.token:return True
            token=self.token
            for attempt in range(10):
                row=lease_state(self.folder)
                if row.get('reserved') is False:
                    self.token=None;self.state['cleanup_required']=False;return True
                if row.get('token')==token:
                    try:(self.folder/'lease.json').unlink(missing_ok=True)
                    except PermissionError:pass
                    except OSError:break
                    else:
                        self.token=None;self.state['cleanup_required']=False;return True
                elif row.get('token'):
                    # A replacement owns this path. Never retry its deletion.
                    break
                if attempt<9:time.sleep(.01)
            self.state.update(busy=False,phase='cleanup_required',cleanup_required=True,
                error='Studio 会话记录清理未确认，请稍后核对原会话 / Studio session cleanup is unconfirmed; review the original session')
            return False

    def _agent(self):
        from managed_runtime import check_ready,Files
        check_ready();data=(RESOURCE/'studio_agent.py').read_bytes();key=hashlib.sha256(data).hexdigest()[:20]
        remote='/opt/hand-workbench/components/studio/'+key+'/studio_agent.py'
        with Files() as files:
            path=files.path(remote);path.parent.mkdir(parents=True,exist_ok=True)
            if path.exists():
                if path.read_bytes()!=data:raise ValueError('Studio component source does not match this workbench')
            else:
                with path.open('xb') as stream:stream.write(data)
        return remote

    def _command(self,*args):
        from managed_runtime import args as runtime_args
        return runtime_args(['/usr/bin/python3','-u',self._agent(),*args],user='root')

    def _linux(self,*args,timeout=90):
        result=subprocess.run(self._command(*args),capture_output=True,text=True,encoding='utf-8',errors='replace',
            timeout=timeout,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise ValueError('Studio 组件操作失败 / Component operation failed: '+result.stderr[-800:])
        return json.loads(result.stdout)

    def _persist(self):
        with self.lock:row={k:self.state.get(k) for k in ('inventory','current','previous')}
        row['verified']=time.time();self.folder.mkdir(parents=True,exist_ok=True)
        stage=self.folder/'installation.pending.json';stage.write_text(json.dumps(row,indent=2),encoding='utf-8');stage.replace(self.folder/'installation.json')

    def _startup_failed(self,error,token=None,*,phase='error'):
        """Roll back an unlaunched local worker, never a Linux GUI session."""
        with self.lock:
            if token is not None and self.token!=token:return
            self.worker=None;self.state.update(busy=False,phase=phase,error=str(error)[:1000])
            if token is not None:
                try:released=self._release()
                except Exception:released=False
                if not released:
                    self.state.update(phase='cleanup_required',cleanup_required=True,
                        error='Studio 会话记录清理未确认，请稍后核对原会话 / Studio session cleanup is unconfirmed; review the original session')
            # A metadata worker does not own an earlier retained reservation.
            # Its startup failure must neither release nor hide that owner.
            if self.token or self.state.get('cleanup_required'):
                self.state.update(phase='cleanup_required',cleanup_required=True)

    def _background(self,phase,work,reserve=False):
        if sys.platform!='win32':raise ValueError('此嵌入组件用于 Windows；Ubuntu 版直接使用官方 Studio / Windows component only')
        with self.lock:
            if self.state['busy'] or self.state['active']:raise ValueError('请先结束当前 Studio 操作 / Finish the current Studio operation first')
            if reserve:self._reserve(phase)
            token=self.token if reserve else None
            self.state.update(busy=True,phase=phase,error=None)
        def run():
            try:
                if reserve:self.idle_check()
                work()
            except Exception as error:self._update(phase='error',error=str(error)[:1000])
            finally:
                try:
                    if reserve:self._release()
                except Exception:
                    self._update(phase='cleanup_required',cleanup_required=True,
                        error='Studio 会话记录清理未确认，请稍后核对原会话 / Studio session cleanup is unconfirmed; review the original session')
                finally:self._update(busy=False)
        try:self.worker=threading.Thread(target=run,name='Official Studio component',daemon=True);self.worker.start()
        except Exception as error:
            self._startup_failed(error,token)
            raise
        return self.snapshot()

    def probe(self):
        def work():
            info=self._linux('inventory');session=self._linux('probe')
            row=lease_state(self.folder)
            # Recover a marker only after the owning process is gone AND the
            # actual Linux GUI session has independently been found inactive.
            if row.get('token') and not session.get('active') and process_identity(row.get('owner_pid',0))!=row.get('owner_start'):
                if lease_state(self.folder).get('token')==row['token']:(self.folder/'lease.json').unlink(missing_ok=True)
            if session.get('active') and not lease_state(self.folder).get('reserved'):self._reserve('external_session')
            available={r['version'] for r in info['versions']};current=self.state['current']
            if current not in available:current=max(available,key=lambda s:tuple(map(int,s.split('.')))) if available else None
            self._update(inventory=info,current=current,active=bool(session.get('active')),phase='external_session' if session.get('active') else 'installed' if current else 'not_installed')
            self._persist()
        return self._background('checking',work)

    def check_updates(self):
        def work():
            cache=self.folder/'releases.json';warning=None;checked=None
            try:
                request=Request('https://api.github.com/repos/wuji-technology/wuji-studio/releases?per_page=30',headers={'User-Agent':'Hand-Workbench-Studio-Component'})
                with urlopen(request,timeout=30) as response:documents=json.load(response)
                catalog=[]
                for row in documents:
                    if row.get('draft') or row.get('prerelease'):continue
                    try:catalog.append(release_asset(row))
                    except ValueError:continue
                if not catalog:raise ValueError('No verified official Linux Studio release was found')
                checked=time.time();source='github';self.folder.mkdir(parents=True,exist_ok=True)
                stage=cache.with_suffix('.pending.json');stage.write_text(json.dumps(dict(documents=documents,checked=checked)),encoding='utf-8');stage.replace(cache)
            except Exception as error:
                # Network failure must not make a verified installed component
                # unusable, or label a bundled version as a fresh online check.
                warning='未能联网确认版本，显示已核验记录 / Could not check online; showing verified records: '+str(error)[:220]
                catalog=[];source='bundled'
                try:
                    saved=json.loads(cache.read_text(encoding='utf-8'))
                    for row in saved['documents']:
                        if row.get('draft') or row.get('prerelease'):continue
                        try:catalog.append(release_asset(row))
                        except ValueError:continue
                    if catalog:source='cache';checked=saved.get('checked')
                except (OSError,ValueError,KeyError,TypeError):pass
                if not catalog:
                    pinned=json.loads((RESOURCE/'studio_manifest.json').read_text(encoding='utf-8'))
                    catalog=[{k:pinned[k] for k in ('version','tag','asset','url','sha256','bytes')}]
            self._update(catalog=catalog,catalog_source=source,update_warning=warning,checked=checked,
                update_attempted=time.time(),phase='installed' if self.state['current'] else 'not_installed')
        return self._background('checking_updates',work)

    def _package(self,item):
        folder=self.folder/'packages';folder.mkdir(parents=True,exist_ok=True);path=folder/item['asset']
        def verified():
            if not path.is_file() or path.stat().st_size!=item['bytes']:return False
            with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()==item['sha256']
        if verified():return path
        partial=path.with_suffix('.download');h=hashlib.sha256();total=0
        request=Request(item['url'],headers={'User-Agent':'Hand-Workbench-Studio-Component'})
        with urlopen(request,timeout=90) as response,partial.open('wb') as stream:
            destination=urlsplit(response.geturl())
            if destination.scheme!='https' or destination.hostname not in {'github.com','release-assets.githubusercontent.com'}:raise ValueError('Unexpected official download destination')
            while data:=response.read(1024*1024):
                total+=len(data)
                if total>item['bytes']:raise ValueError('Official package exceeds release size')
                h.update(data);stream.write(data);self._update(download_bytes=total,download_total=item['bytes'])
        if total!=item['bytes'] or h.hexdigest()!=item['sha256']:raise ValueError('Official Studio download failed SHA-256 verification')
        partial.replace(path);return path

    def install(self,selected=None):
        if selected is not None:validate_version(selected)
        pinned=json.loads((RESOURCE/'studio_manifest.json').read_text(encoding='utf-8'))
        item={k:pinned[k] for k in ('version','tag','asset','url','sha256','bytes')}
        if selected and selected!=item['version']:
            validate_version(selected);item=next((r for r in self.state['catalog'] if r['version']==selected),None)
            if item is None:raise ValueError('先检查官方版本列表 / Check official versions first')
        def work():
            if self._linux('probe').get('active'):raise ValueError('Close the current Studio session before installation')
            self._update(phase='downloading');package=self._package(item)
            self._update(phase='dependencies')
            from managed_runtime import args as runtime_args
            with (self.folder/'setup.log').open('ab') as log:
                commands=[['apt-get','update'],['apt-get','install','-y','--no-install-recommends',*pinned['dependencies']]]
                for command in commands:
                    result=subprocess.run(runtime_args(['/usr/bin/env','DEBIAN_FRONTEND=noninteractive',*command],user='root'),stdout=log,stderr=subprocess.STDOUT,timeout=900,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    if result.returncode:raise ValueError('Studio 图形依赖安装失败，记录在组件 setup.log / Graphical dependency installation failed')
            self._update(phase='installing')
            converted=self._stage_package(package,item['asset'])
            info=self._linux('install',converted,item['version'],item['sha256'])
            old=self.state['current'];self._update(inventory=info,current=item['version'],previous=old if old!=item['version'] else self.state['previous'],phase='installed')
            self._persist()
        return self._background('preparing',work,reserve=True)

    def _stage_package(self,package,name):
        # The managed distro need not mount Windows disks. Transfer through the
        # existing owned filesystem channel; the Linux installer rehashes it.
        from managed_runtime import Files
        remote='/opt/hand-workbench/components/studio/packages/'+name
        with Files() as files:
            target=files.path(remote);target.parent.mkdir(parents=True,exist_ok=True)
            stage=target.with_suffix('.pending.deb')
            try:
                with Path(package).open('rb') as source,stage.open('wb') as destination:shutil.copyfileobj(source,destination,1024*1024)
                stage.replace(target)
            finally:stage.unlink(missing_ok=True)
        return remote

    def select(self,selected):
        selected=validate_version(selected)
        def work():
            if self._linux('probe').get('active'):raise ValueError('Close Studio before switching versions')
            info=self._linux('inventory')
            if selected not in {r['version'] for r in info['versions']}:raise ValueError('Install the selected official version first')
            old=self.state['current'];self._update(inventory=info,current=selected,previous=old if old!=selected else self.state['previous'],phase='installed');self._persist()
        return self._background('switching',work,reserve=True)

    def viewer_url(self):
        with self.lock:
            if not self.secret or not self.state['active']:raise ValueError('Studio 视图尚未就绪 / Studio view is not ready')
            return '/official_studio_viewer.html#'+urlencode(self.secret)

    def open(self,offline=False):
        if type(offline) is not bool:raise ValueError('Expected offline mode boolean')
        with self.lock:
            if self.state['active']:return dict(studio=self.snapshot(),viewer_url=self.viewer_url())
            if self.state['busy']:raise ValueError('Studio component is busy')
            selected=validate_version(self.state.get('current'))
            self._reserve('starting')
            token=self.token
            self.state.update(busy=True,phase='starting',error=None,offline=offline)
            self.cancel.clear()
        # Do not invert the Controller -> Studio snapshot lock order while
        # checking other workspaces. The shared reservation blocks new starts.
        try:self.idle_check()
        except Exception as error:
            self._startup_failed(error,token,phase='installed');raise
        def run():
            external=False
            try:
                if self._linux('probe').get('active'):
                    external=True;self._update(active=True,phase='external_session')
                    raise ValueError('另一个官方 Studio 会话仍在运行 / Another Studio session is still running')
                if self.cancel.is_set():self._update(phase='installed');return
                with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                command=self._command('serve',selected,'--port',str(port),*(['--offline'] if offline else []))
                self.folder.mkdir(parents=True,exist_ok=True)
                with (self.folder/'session.log').open('ab') as log:
                    process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    with self.lock:
                        self.process=process
                        if self.cancel.is_set():process.stdin.write('{"name":"stop"}\n');process.stdin.flush()
                    for line in process.stdout:
                        if not line.startswith('STUDIO_JSON:'):continue
                        event=json.loads(line[len('STUDIO_JSON:'):]);kind=event.get('type')
                        if kind=='ready':
                            if offline and not event.get('network_isolated'):raise ValueError('Offline Studio isolation failed')
                            self.secret=dict(port=event['port'],password=event['password'],session=secrets.token_hex(12))
                            self._update(active=True,busy=False,phase='running',offline=offline,network_isolated=event['network_isolated'],
                                binary_sha256=event['binary_sha256'],vendor_binary_unmodified=True,started=time.time())
                        elif kind=='heartbeat':self._update(heartbeat=time.time())
                        elif kind=='closed':self._update(active=False,phase='installed',device_disable_confirmed=False)
                    code=process.wait(timeout=10)
                    if code:raise ValueError('官方 Studio 会话退出，详见组件日志 / Studio session exited; see component log')
            except Exception as error:self._update(phase='error',error=str(error)[:1000])
            finally:
                # stdin closure asks only this component agent to clean up its
                # own children; no broad process-name termination is used.
                process=self.process
                if process and process.poll() is None:
                    try:process.stdin.close();process.wait(timeout=25)
                    except (OSError,subprocess.TimeoutExpired):self._update(phase='needs_check',error='Studio 组件退出尚未确认 / Studio component exit is not confirmed');return
                if process and process.returncode:
                    try:external=bool(self._linux('probe').get('active'))
                    except Exception:external=True
                self.process=None;self.secret=None;self._update(active=external,busy=False)
                if not external:self._release()
        try:self.worker=threading.Thread(target=run,name='Official Ubuntu Studio',daemon=True);self.worker.start()
        except Exception as error:
            self._startup_failed(error,token)
            raise
        return dict(studio=self.snapshot())

    def _stop_owner(self,token):
        row=lease_state(self.folder)
        if token is not None and row.get('token')!=token:
            with self.lock:
                if self.token in (None,token):
                    self.state.update(busy=False,phase='cleanup_required',cleanup_required=True,
                        error='Studio 会话所属已改变，原会话退出仍待核对 / Studio session owner changed; original cleanup remains unconfirmed')
            raise ValueError('Studio session owner changed; stop in its owning workspace')
        return row

    def stop(self):
        with self.lock:
            token=self.token
            self._stop_owner(token)
            if self.state['busy'] and self.state['phase'] not in {'starting'}:raise ValueError('请等待组件操作完成 / Wait for the component operation')
            process=self.process
            self.cancel.set()
            if process and process.poll() is None:
                try:process.stdin.write('{"name":"stop"}\n');process.stdin.flush()
                except (OSError,ValueError):pass
                self.state['phase']='closing'
        if self.worker:self.worker.join(timeout=25)
        if self.worker and self.worker.is_alive():raise ValueError('Studio 正在退出，请稍后重试 / Studio is closing; try again shortly')
        if self.state['active'] or self.token or lease_state(self.folder).get('reserved'):
            row=self._stop_owner(token)
            if row.get('owner_pid')!=os.getpid() and row.get('owner_start') and process_identity(row.get('owner_pid',0))==row['owner_start']:
                raise ValueError('请在打开 Studio 的工作台中关闭会话 / Close Studio in its owning workbench')
            if not row.get('token'):raise ValueError('Studio 会话记录需要核对 / Studio session record needs verification')
            if token is None:token=row['token']
            self._stop_owner(token)
            self._linux('stop',timeout=35)
            self._stop_owner(token)
            if self._linux('probe').get('active'):raise ValueError('Studio 会话仍在运行 / Studio is still running')
            self._stop_owner(token)
            (self.folder/'lease.json').unlink(missing_ok=True)
            self._update(active=False,busy=False,phase='installed',cleanup_required=False)
            self.token=None;self.process=None;self.secret=None
        if self.state['active'] or self.token:raise ValueError('Studio 退出尚未确认，请查看组件状态 / Studio exit is not confirmed')
        return dict(studio=self.snapshot(),component_closed=True,device_disable_confirmed=False)

    def asset(self,relative):
        parts=Path(relative).parts
        if not relative or '\\' in relative or any(p in ('..','.') for p in parts) or Path(relative).is_absolute():raise ValueError('Invalid Studio client asset')
        if Path(relative).suffix not in {'.js','.css','.txt','.svg','.png'}:raise ValueError('Unsupported Studio client asset')
        root=Path('\\\\wsl.localhost/HandWorkbenchControl/opt/hand-workbench-studio/client')
        path=root.joinpath(*parts)
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_size>5*1024**2:raise FileNotFoundError('Studio client asset unavailable')
        return path

    def handle(self,command):
        name=command.get('name')
        if name=='official_studio_probe':return dict(studio=self.probe())
        if name=='official_studio_check_updates':return dict(studio=self.check_updates())
        if name=='official_studio_install':return dict(studio=self.install(command.get('version')))
        if name=='official_studio_select':return dict(studio=self.select(command.get('version')))
        if name=='official_studio_open':return self.open(command.get('offline',False))
        if name=='official_studio_view':
            with self.lock:
                if self.process and self.process.poll() is None:self.process.stdin.write('{"name":"restore"}\n');self.process.stdin.flush()
            return dict(viewer_url=self.viewer_url())
        if name=='official_studio_close':return self.stop()
        raise ValueError('Unknown official Studio operation')
