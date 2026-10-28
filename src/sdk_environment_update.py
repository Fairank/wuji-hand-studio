"""Update only the app-owned Linux SDK venv, with staged verification/rollback.

The desktop calls update_owned while holding its all-workspace reservation. This
module never opens a device, changes SDK users, or updates arbitrary SSH/local
Python installations. The guest apply function additionally refuses live agents.
"""
import base64
from contextlib import contextmanager
import csv
import hashlib
import io
import json
import os
from pathlib import Path,PurePosixPath
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
import urllib.request
import zipfile

ENV='opt/hand-workbench-venv'
AGENT='/opt/hand-workbench'
PYTHON='/opt/hand-workbench-venv/bin/python'
MAX_UNPACKED=512*1024*1024
RECEIPTS='opt/hand-workbench/components/sdk-updates'


class SDKUpdateError(ValueError):
    def __init__(self,message,*,rollback_confirmed,switched=False):
        super().__init__(message)
        self.rollback_confirmed=bool(rollback_confirmed)
        self.switched=bool(switched)


def _token(value=None):
    value=value or secrets.token_hex(16)
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',value):raise ValueError('Invalid SDK operation token')
    return value


def _atomic_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    pending=path.with_name(path.name+'.'+secrets.token_hex(8)+'.pending')
    try:
        with pending.open('x',encoding='utf-8') as stream:
            json.dump(value,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
        pending.replace(path)
        if sys.platform=='linux':
            descriptor=os.open(path.parent,os.O_DIRECTORY)
            try:os.fsync(descriptor)
            finally:os.close(descriptor)
    finally:pending.unlink(missing_ok=True)


def _process_start(pid):
    try:return (Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split()[19]
    except FileNotFoundError:return None


def _identity(proof):
    return {key:proof.get(key) for key in ('available','version','module_sha256','native_sha256','api_compatible')}


def _operation_ended(operation,root):
    if root!=Path('/'):return True
    start=_process_start(operation['pid'])
    return bool(operation.get('process_start')) and start!=operation['process_start']


@contextmanager
def _guest_lock(root):
    """The same nonblocking OS lock protects guest apply and recovery.

    Linux owns production; the Windows byte lock exercises identical exclusion
    against synthetic roots without turning a test-root flag into a bypass.
    """
    path=root/'opt/hand-workbench-sdk-update.lock'
    if path.is_symlink():raise ValueError('SDK maintenance lock is redirected')
    guard=path.open('a+b')
    try:
        try:
            if sys.platform=='linux':
                import fcntl
                fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
            elif root!=Path('/') and os.name=='nt':
                import msvcrt
                guard.seek(0,2)
                if not guard.tell():guard.write(b'0');guard.flush()
                guard.seek(0);msvcrt.locking(guard.fileno(),msvcrt.LK_NBLCK,1)
            else:raise ValueError('SDK guest maintenance requires Linux')
        except OSError:
            raise ValueError('SDK 更新仍可能在运行，请稍后重试 / SDK updater may still be running; retry later') from None
        yield
    finally:guard.close()


def _request_path(root,token):
    return root/RECEIPTS/(token+'.request.json')


def _original_identity(proof):
    """A zero-write cancellation still needs actual installed software hashes."""
    from sdk_release import stable_version
    if (not isinstance(proof,dict) or proof.get('proof_version')!=1 or proof.get('available') is not True
            or type(proof.get('api_compatible')) is not bool or proof.get('platform')!='linux'
            or proof.get('implementation')!='cpython' or not proof.get('executable') or not proof.get('import_path')
            or not isinstance(proof.get('module_sha256'),str) or not re.fullmatch('[0-9a-f]{64}',proof['module_sha256'])
            or not isinstance(proof.get('native_sha256'),dict) or not proof['native_sha256']
            or any(not isinstance(name,str) or not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest)
                   for name,digest in proof['native_sha256'].items())):
        raise ValueError('Original SDK identity is not verifiable')
    stable_version(proof.get('version'))
    return _identity(proof)


def _cancel_before_start(release,mode,token,root,run):
    """Called only under _guest_lock: tombstone this exact late launch forever."""
    from sdk_release import validate_release
    request_path=_request_path(root,token)
    if (not request_path.is_file() or request_path.is_symlink() or request_path.stat().st_size>2*1024*1024
            or any(parent.is_symlink() for parent in request_path.parents)):
        raise ValueError('SDK 更新请求尚未确认，请保留维护锁 / SDK update request is unconfirmed; keep the maintenance lock')
    request=json.loads(request_path.read_text(encoding='utf-8'))
    if (not isinstance(request,dict) or request.get('schema')!=1 or request.get('operation_token')!=token
            or request.get('mode')!=mode or not isinstance(request.get('release'),dict)
            or validate_release(request['release'],require_fresh=False)!=release):
        raise ValueError('SDK recovery request does not match this operation')
    original=request.get('original_proof');expected_identity=_original_identity(original)
    current=root/ENV
    if not current.is_dir() or current.is_symlink() or any(parent.is_symlink() for parent in current.parents):
        raise ValueError('Original SDK environment is not verifiable')
    actual=_probe(current/'bin/python',Path(__file__).resolve().parent,run)
    if _original_identity(actual)!=expected_identity:
        raise ValueError('Original SDK identity changed; keep the maintenance lock')
    operation=dict(schema=1,operation_token=token,catalog_sha256=release['catalog_sha256'],version=release['version'],
                   pid=os.getpid(),process_start=_process_start(os.getpid()) if root==Path('/') else 'synthetic',
                   phase='cancelled_before_start',original_proof=original,rollback_confirmed=True,
                   recovered=True,updated_at=time.time())
    _atomic_json(root/RECEIPTS/(token+'.json'),operation)
    return dict(ok=True,recovered=True,cancelled_before_start=True,sdk_proof=actual,
                rollback_confirmed=True,operation_token=token)


def _run_apply_command(command,*,timeout,env,creationflags):
    """Distinguish a proven Popen failure from an uncertain dispatched helper."""
    try:
        process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                 text=True,env=env,creationflags=creationflags)
    except OSError as error:
        raise SDKUpdateError('SDK 更新进程未启动，原环境未改变 / SDK updater did not start; original environment was not changed',
                             rollback_confirmed=True,switched=False) from error
    # No context manager: its wait-on-exit would make a timeout block forever.
    # A dispatched helper may still own its guest lock; never infer rollback or
    # kill/retry it after a transport failure. Recovery observes that same lock.
    out,err=process.communicate(timeout=timeout)
    if process.returncode:raise subprocess.CalledProcessError(process.returncode,command,output=out,stderr=err)
    return subprocess.CompletedProcess(command,process.returncode,out,err)


def dependency_contract(release):
    # The supported SDK is self-contained. Existing numpy/zenoh dependencies
    # are copied from the working runtime and imported again in the new venv.
    # A future new base dependency requires a reviewed component manifest.
    for requirement in release.get('requires_dist',[]):
        if not isinstance(requirement,str) or not re.search(r';\s*extra\s*==\s*[\"\'][A-Za-z0-9_-]+[\"\']\s*$',requirement):
            raise ValueError('新版 SDK 依赖需要软件适配，请先更新工作台 / Update the workbench to support the new SDK dependencies')


def download_wheel(release,proof,folder,fetch=None):
    from sdk_release import select_wheel
    dependency_contract(release)
    wheel=select_wheel(release,proof);folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    destination=folder/wheel['filename']
    def verified(path):
        return path.is_file() and not path.is_symlink() and path.stat().st_size==wheel['size'] and hashlib.sha256(path.read_bytes()).hexdigest()==wheel['sha256']
    if verified(destination):return destination,wheel
    if fetch is None:
        def fetch(url):
            request=urllib.request.Request(url,headers={'User-Agent':'WujiHandWorkbench/SDK-updater'})
            response=urllib.request.urlopen(request,timeout=60)
            if response.url!=url:response.close();raise ValueError('SDK download redirected unexpectedly')
            return response
    pending=destination.with_name(destination.name+'.'+secrets.token_hex(8)+'.partial')
    try:
        with fetch(wheel['url']) as source,pending.open('xb') as output:
            count=0
            while chunk:=source.read(1024*1024):
                count+=len(chunk)
                if count>wheel['size']:raise ValueError('SDK download exceeds its declared size')
                output.write(chunk)
        if not verified(pending):raise ValueError('SDK 安装包校验失败 / SDK wheel integrity check failed')
        pending.replace(destination)
    finally:pending.unlink(missing_ok=True)
    return destination,wheel


def wheel_members(path,wheel,version):
    if Path(path).stat().st_size!=wheel['size'] or hashlib.sha256(Path(path).read_bytes()).hexdigest()!=wheel['sha256']:
        raise ValueError('SDK wheel integrity check failed')
    prefix='wuji_sdk-'+version+'.dist-info';selected={};total=0
    with zipfile.ZipFile(path) as archive:
        members=archive.infolist()
        if len(members)>2000:raise ValueError('SDK wheel has too many entries')
        for entry in members:
            name=PurePosixPath(entry.filename);mode=(entry.external_attr>>16)&0xffff
            if (name.is_absolute() or '..' in name.parts or '\\' in entry.filename
                    or not name.parts or name.parts[0] not in ('wuji_sdk','wuji_sdk.libs',prefix)
                    or entry.filename in selected or entry.flag_bits&1
                    or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0,stat.S_IFREG,stat.S_IFDIR))):
                raise ValueError('Unsafe SDK wheel member')
            if entry.is_dir():continue
            total+=entry.file_size
            if entry.file_size>128*1024*1024 or total>MAX_UNPACKED:raise ValueError('SDK wheel expands beyond limit')
            selected[entry.filename]=archive.read(entry)
        metadata=selected.get(prefix+'/METADATA',b'').decode('utf-8')
        if not re.search(r'^Name: wuji-sdk\s*$',metadata,re.M) or not re.search(r'^Version: '+re.escape(version)+r'\s*$',metadata,re.M):
            raise ValueError('SDK wheel package/version does not match official catalog')
        record=selected.get(prefix+'/RECORD',b'').decode('utf-8')
        seen=set()
        for name,digest,size in csv.reader(io.StringIO(record)):
            if name in seen or name not in selected:raise ValueError('SDK RECORD has an invalid path')
            seen.add(name)
            if name==prefix+'/RECORD' and not digest and not size:continue
            content=selected[name]
            expected='sha256='+base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip('=')
            if digest!=expected or size!=str(len(content)):raise ValueError('SDK RECORD integrity check failed')
        if seen!=set(selected) or 'wuji_sdk/__init__.py' not in selected:raise ValueError('SDK RECORD is incomplete')
    return selected


def assert_idle(root,mode):
    if root!=Path('/'):return  # Synthetic test root; never a production override.
    if sys.platform!='linux' or mode not in ('wsl','macvm'):
        raise ValueError('Only the owned Linux controller may update its SDK')
    if mode=='wsl' and os.environ.get('WSL_DISTRO_NAME')!='HandWorkbenchControl':
        raise ValueError('Unexpected WSL controller')
    if mode=='macvm' and os.environ.get('WUJI_MANAGED_RUNTIME')!='macvm':
        raise ValueError('Unexpected Mac controller')
    for path in Path('/proc').glob('[0-9]*/cmdline'):
        if path.parent.name==str(os.getpid()):continue
        try:args=path.read_bytes().split(b'\0')
        except FileNotFoundError:continue
        except PermissionError:raise ValueError('Cannot confirm controller processes are idle') from None
        if any(a.endswith((b'/console_agent.py',b'/agent_bootstrap.py',b'/calibration_runner.py')) or a.rsplit(b'/',1)[-1]==b'wuji' for a in args if a):
            raise ValueError('请先结束所有设备、标定和遥操作会话 / Finish every device, calibration and teleoperation session first')


def _probe(python,module_folder,run=subprocess.run):
    code='import sys,json;sys.path.insert(0,'+repr(str(module_folder))+');from sdk_runtime import probe_runtime;print(json.dumps(probe_runtime()))'
    result=run([str(python),'-c',code],capture_output=True,text=True,timeout=45,
               env={**os.environ,'WUJI_TELEMETRY_DISABLE':'1'},check=True)
    if len(result.stdout)>65536:raise ValueError('SDK probe output exceeds limit')
    return json.loads(result.stdout)


def probe_selected(config,*,run=None,background=False):
    """Read actual configured worker Python; imports only, no agent or manager.

    A supplied run replaces subprocess.run for owned/local modes, or
    doctor.remote_run(config, argv, timeout=45) for SSH mode.
    """
    from runtime_paths import RESOURCE
    mode=config.get('mode');python=config.get('python');directory=config.get('agent_directory')
    if mode not in ('wsl','macvm','local','ssh') or not isinstance(python,str) or not python or not isinstance(directory,str):
        raise ValueError('Invalid SDK controller configuration')
    source=(RESOURCE/'sdk_runtime.py').read_text(encoding='utf-8')
    code='import sys,json;sys.path.insert(0,'+repr(directory)+');namespace={};exec('+repr(source)+',namespace);print(json.dumps(namespace["probe_runtime"]()))'
    argv=[python,'-c',code]
    if mode=='ssh':
        from doctor import remote_run
        status,out,_=(run or remote_run)(config,argv,timeout=45)
        if status:raise ValueError('外部控制端 SDK 检查失败 / External controller SDK probe failed')
    else:
        environment=None;working_directory=None
        if mode in ('wsl','macvm'):
            if config.get('agent_directory')!=AGENT or python!=PYTHON:raise ValueError('Invalid owned SDK controller path')
            if mode=='wsl':
                import managed_runtime as runtime
                runtime.check_ready()
                if background:
                    running=(run or subprocess.run)(['wsl.exe','--list','--running','--quiet'],capture_output=True,
                        timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=True)
                    names=runtime._decode(running.stdout) if isinstance(running.stdout,bytes) else running.stdout
                    if runtime.NAME not in names.splitlines():raise ValueError('Controller is stopped; automatic SDK probe is deferred')
            else:
                import macos_runtime as runtime
                if background:
                    records=[json.loads(line) for line in runtime.run('list','--json',runtime.NAME,timeout=5).splitlines() if line.strip()]
                    if not any(row.get('name')==runtime.NAME and row.get('status')=='Running' for row in records):
                        raise ValueError('Controller is stopped; automatic SDK probe is deferred')
                else:runtime.ensure_running()
                environment=runtime.env()
            argv=runtime.args(argv)
        elif sys.platform!='linux':raise ValueError('Local SDK controller requires Linux')
        else:
            if not directory or not directory.startswith('/'):raise ValueError('Controller directory must be absolute')
            working_directory=directory
            environment={**os.environ,'WUJI_TELEMETRY_DISABLE':'1'}
        result=(run or subprocess.run)(argv,capture_output=True,text=True,timeout=45,env=environment,
                                       creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=True,
                                       **({'cwd':working_directory} if working_directory else {}))
        out=result.stdout
    if not isinstance(out,str) or len(out)>65536:raise ValueError('Invalid SDK probe output')
    value=json.loads(out)
    if not isinstance(value,dict) or value.get('proof_version')!=1:raise ValueError('Invalid SDK controller proof')
    return value


def apply(wheel_path,release,mode,root=Path('/'),*,run=None,operation_token=None):
    """Guest-only atomic replacement. User home, CLI and calibration stores untouched."""
    from sdk_release import validate_release,select_wheel,requirement
    from sdk_runtime import verify_proof
    root=Path(root).resolve();run=run or subprocess.run;assert_idle(root,mode);operation_token=_token(operation_token)
    release=validate_release(release,require_fresh=False);dependency_contract(release)
    current=root/ENV
    if current.is_symlink() or not current.is_dir() or any(p.is_symlink() for p in current.parents):
        raise ValueError('SDK environment path is redirected or missing')
    # Recovery uses exactly this lock, including before a helper has a receipt.
    with _guest_lock(root):
        receipt_path=root/RECEIPTS/(operation_token+'.json')
        if receipt_path.is_symlink():raise ValueError('SDK operation receipt is redirected')
        if receipt_path.exists():
            # A recovery tombstone is authoritative before any probe, wheel
            # extraction, staging or rename. The same token is never replayed.
            if receipt_path.is_symlink() or not receipt_path.is_file() or receipt_path.stat().st_size>65536:
                raise ValueError('SDK operation receipt is not verifiable')
            prior=json.loads(receipt_path.read_text(encoding='utf-8'))
            cancelled=(prior.get('phase')=='cancelled_before_start' and prior.get('operation_token')==operation_token
                       and prior.get('catalog_sha256')==release['catalog_sha256'] and prior.get('version')==release['version'])
            raise SDKUpdateError('SDK operation was cancelled before start' if cancelled else
                                 'SDK operation token was already used; inspect its receipt before starting another update',
                                 rollback_confirmed=cancelled,switched=not cancelled)
        proof=_probe(current/'bin/python',Path(__file__).resolve().parent,run)
        wheel=select_wheel(release,proof);members=wheel_members(wheel_path,wheel,release['version'])
        expected={k:release[k] for k in ('version','catalog_sha256','checked_at')}
        operation=dict(schema=1,operation_token=operation_token,catalog_sha256=release['catalog_sha256'],version=release['version'],
                       pid=os.getpid(),process_start=_process_start(os.getpid()) if root==Path('/') else 'synthetic',
                       phase='staging',original_proof=proof,updated_at=time.time())
        _atomic_json(receipt_path,operation)
        def phase(value,**fields):
            operation.update(phase=value,updated_at=time.time(),**fields);_atomic_json(receipt_path,operation)
        if proof.get('version')==release['version'] and proof.get('available') is True and proof.get('api_compatible') is True:
            verify_proof(proof,expected)
            phase('completed',sdk_proof=proof)
            return dict(ok=True,changed=False,sdk_proof=proof,sdk_version=release['version'],profiles_preserved=True,motion_started=False,operation_token=operation_token)
        sites=list(current.glob('lib/python*/site-packages'))
        if len(sites)!=1 or sites[0].is_symlink() or not sites[0].resolve().is_relative_to(current):
            raise ValueError('SDK environment site-packages is ambiguous')
        required=sum(p.stat().st_size for p in current.rglob('*') if p.is_file() and not p.is_symlink())+sum(map(len,members.values()))+64*1024*1024
        if shutil.disk_usage(current.parent).free<required:raise ValueError('SDK update needs more free disk space')
        token=operation_token;staged=current.with_name(current.name+'.sdk-pending-'+token);backup=current.with_name(current.name+'.sdk-backup-'+token)
        swapped=False;displaced=False;touched=False
        try:
            shutil.copytree(current,staged,symlinks=True)
            target=staged/sites[0].relative_to(current)
            for path in target.iterdir():
                if path.name in ('wuji_sdk','wuji_sdk.libs') or re.fullmatch(r'wuji_sdk-[A-Za-z0-9_.+-]+\.dist-info',path.name):
                    if path.is_symlink():raise ValueError('Existing SDK package is redirected')
                    if path.is_dir():shutil.rmtree(path)
                    else:path.unlink()
            for name,data in members.items():
                destination=target/name
                if not destination.resolve().is_relative_to(target):raise ValueError('SDK wheel escapes staging environment')
                destination.parent.mkdir(parents=True,exist_ok=True)
                with destination.open('xb') as output:output.write(data)
                destination.chmod(0o644)
            staged_proof=verify_proof(_probe(staged/'bin/python',Path(__file__).resolve().parent,run),expected)
            receipt=dict(schema=1,version=release['version'],previous_version=proof.get('version'),
                         wheel=wheel,catalog_sha256=release['catalog_sha256'],installed_at=time.time())
            (staged/'hand-workbench-sdk.json').write_text(json.dumps(receipt,sort_keys=True),encoding='utf-8')
            assert_idle(root,mode)
            phase('switching',staged_proof=staged_proof)
            current.rename(backup);displaced=True;touched=True
            staged.rename(current);swapped=True
            final_proof=verify_proof(_probe(current/'bin/python',Path(__file__).resolve().parent,run),expected)
            phase('completed',sdk_proof=final_proof)
            return dict(ok=True,changed=True,sdk_version=release['version'],sdk_proof=final_proof,
                        sdk_backup=str(backup),profiles_preserved=True,motion_started=False,operation_token=operation_token)
        except Exception as error:
            confirmed=True
            if displaced:
                try:
                    if swapped:current.rename(staged)
                    backup.rename(current);displaced=False
                    restored=_probe(current/'bin/python',Path(__file__).resolve().parent,run)
                    if _identity(restored)!=_identity(proof):raise ValueError('Original SDK restoration proof changed')
                except Exception:confirmed=False
            try:phase('rolled_back' if confirmed else 'unknown',rollback_confirmed=confirmed)
            except Exception:confirmed=False
            raise SDKUpdateError('SDK 更新未完成，'+('已恢复原控制环境 / Original SDK environment restored' if confirmed
                else '环境恢复未确认，请在设置中重新检查组件 / SDK restoration is unconfirmed; check components in Settings'),
                rollback_confirmed=confirmed,switched=touched) from error
        finally:
            if staged.exists() and staged.parent==current.parent and not staged.is_symlink():shutil.rmtree(staged)


def _update_owned(config,release,*,idle,run=None,folder=None,operation_token=None,on_dispatch=None):
    """Injectable synchronous service. Caller must retain its global reservation."""
    from sdk_release import validate_release
    from runtime_paths import RESOURCE,DATA
    if config.get('mode') not in ('wsl','macvm'):
        raise ValueError('请选择内置控制环境更新；外部控制端需在对应 Python 环境安装官方 SDK / Select the built-in controller; external environments must be updated separately')
    if config.get('agent_directory')!=AGENT or config.get('python')!=PYTHON:
        raise ValueError('SDK updater only accepts the app-owned controller')
    if not callable(idle):raise ValueError('SDK update requires an all-workspace idle reservation')
    release=validate_release(release);dependency_contract(release);idle();injected_run=run is not None;run=run or subprocess.run;operation_token=_token(operation_token)
    if config['mode']=='wsl':
        import managed_runtime as runtime
        if sys.platform!='win32':raise ValueError('The owned WSL controller requires Windows')
        runtime.check_ready()
    else:
        import macos_runtime as runtime
        if sys.platform!='darwin':raise ValueError('The owned Mac controller requires macOS')
        runtime.ensure_running()
    def execute(argv,*,privileged=False,timeout=60,apply_launch=False):
        if config['mode']=='macvm' and privileged:argv=['sudo','-n','--','/usr/bin/env','WUJI_MANAGED_RUNTIME=macvm',*argv]
        command=runtime.args(argv,**({'user':'root'} if config['mode']=='wsl' and privileged else {}))
        environment=runtime.env() if config['mode']=='macvm' else None
        flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
        result=(_run_apply_command(command,timeout=timeout,env=environment,creationflags=flags)
                if apply_launch and not injected_run else
                run(command,capture_output=True,text=True,timeout=timeout,env=environment,creationflags=flags,check=True))
        if len(result.stdout)>65536:raise ValueError('SDK updater output exceeds limit')
        value=json.loads(result.stdout)
        if value.get('ok') is False:
            raise SDKUpdateError(value.get('error','SDK update failed'),rollback_confirmed=value.get('rollback_confirmed') is True,
                                 switched=value.get('switched') is True)
        return value
    # Stage a private immutable helper version using the existing confined file
    # transport. The target interpreter performs its own software-only probe.
    names=('sdk_environment_update.py','sdk_release.py','sdk_runtime.py')
    contents={name:(RESOURCE/name).read_bytes() for name in names}
    digest=hashlib.sha256(b''.join(name.encode()+b'\0'+data for name,data in contents.items())).hexdigest()
    remote=AGENT+'/components/sdk-updater/'+digest
    with runtime.Files() as files:
        for name,data in contents.items():
            destination=files.path(remote+'/'+name);destination.parent.mkdir(parents=True,exist_ok=True)
            if destination.exists() and destination.read_bytes()!=data:raise ValueError('SDK updater staging checksum mismatch')
            if not destination.exists():destination.write_bytes(data)
        probe=execute([PYTHON,'-c','import sys,json;sys.path.insert(0,'+repr(remote)+');from sdk_runtime import probe_runtime;print(json.dumps(probe_runtime()))'])
        path,wheel=download_wheel(release,probe,folder or DATA/'components'/'sdk-wheels')
        target=files.path(remote+'/'+path.name)
        pending=target.with_name(target.name+'.partial')
        try:
            shutil.copyfile(path,pending)
            if hashlib.sha256(pending.read_bytes()).hexdigest()!=wheel['sha256']:raise ValueError('Transferred SDK checksum mismatch')
            pending.replace(target)
        finally:pending.unlink(missing_ok=True)
        # Per-operation immutable files survive a root crash after on_dispatch.
        # They bind recovery to this release and the actual original SDK proof,
        # not a shared release.json overwritten by a later check/update.
        payload=files.path(remote+'/release-'+operation_token+'.json')
        if payload.exists() and payload.read_text(encoding='utf-8')!=json.dumps(release):
            raise ValueError('SDK operation release request changed')
        if not payload.exists():payload.write_text(json.dumps(release),encoding='utf-8')
        request=dict(schema=1,operation_token=operation_token,mode=config['mode'],release=release,original_proof=probe)
        request_path=files.path('/'+RECEIPTS+'/'+operation_token+'.request.json')
        if request_path.exists():
            if request_path.is_symlink() or json.loads(request_path.read_text(encoding='utf-8'))!=request:
                raise ValueError('SDK operation request changed')
        else:_atomic_json(request_path,request)
    idle()
    if on_dispatch is not None:
        if not callable(on_dispatch):raise ValueError('Invalid SDK dispatch callback')
        on_dispatch()
    try:
        return execute(['/usr/bin/python3',remote+'/sdk_environment_update.py',remote+'/'+path.name,
                        remote+'/release-'+operation_token+'.json',config['mode'],operation_token],privileged=True,timeout=300,apply_launch=True)
    except SDKUpdateError:raise
    except Exception as error:
        raise SDKUpdateError('SDK 更新结果未确认，请在设置中重新检查组件 / SDK update result is unconfirmed; check components in Settings',
                             rollback_confirmed=False,switched=True) from error


def update_owned(config,release,*,idle,run=None,folder=None,operation_token=None,on_dispatch=None):
    try:return _update_owned(config,release,idle=idle,run=run,folder=folder,operation_token=operation_token,on_dispatch=on_dispatch)
    except SDKUpdateError:raise
    except Exception as error:
        message=str(error) if isinstance(error,ValueError) else 'SDK 更新准备未完成，原环境未改变 / SDK update preparation failed; original environment was not changed'
        raise SDKUpdateError(message,
                             rollback_confirmed=True,switched=False) from error


def recover_guest(release,mode,operation_token,root=Path('/'),*,run=None):
    """Resolve or revoke an exact operation under the same guest apply lock."""
    from sdk_release import validate_release
    root=Path(root).resolve();token=_token(operation_token)
    assert_idle(root,mode);release=validate_release(release,require_fresh=False)
    with _guest_lock(root):
        return _recover_guest_locked(release,mode,token,root,run=run)


def _recover_guest_locked(release,mode,operation_token,root,*,run=None):
    """Caller owns _guest_lock for all receipt checks and directory changes."""
    from sdk_release import validate_release
    from sdk_runtime import verify_proof
    root=Path(root).resolve();run=run or subprocess.run;token=_token(operation_token)
    assert_idle(root,mode);release=validate_release(release,require_fresh=False)
    path=root/RECEIPTS/(token+'.json')
    if path.is_symlink():raise ValueError('SDK recovery receipt is redirected')
    if not path.exists():
        return _cancel_before_start(release,mode,token,root,run)
    if not path.is_file() or path.is_symlink() or path.stat().st_size>65536:raise ValueError('SDK 更新记录尚未确认，请稍后重试 / SDK update receipt is not confirmed; retry later')
    operation=json.loads(path.read_text(encoding='utf-8'))
    if (operation.get('operation_token')!=token or operation.get('catalog_sha256')!=release['catalog_sha256']
            or operation.get('version')!=release['version'] or type(operation.get('pid')) is not int
            or not isinstance(operation.get('original_proof'),dict)):
        raise ValueError('SDK recovery receipt does not match this operation')
    if not _operation_ended(operation,root):
        raise ValueError('SDK 更新仍可能在运行，请稍后重试 / SDK updater may still be running; retry later')
    current=root/ENV;staged=current.with_name(current.name+'.sdk-pending-'+token);backup=current.with_name(current.name+'.sdk-backup-'+token)
    for item in (current,staged,backup):
        if item.is_symlink() or any(p.is_symlink() for p in item.parents):raise ValueError('SDK recovery path is redirected')
    expected={k:release[k] for k in ('version','catalog_sha256','checked_at')}
    def inspect(location):return _probe(location/'bin/python',Path(__file__).resolve().parent,run)
    original=operation['original_proof'];phase=operation.get('phase')
    current_proof=None
    if current.is_dir():
        try:current_proof=inspect(current)
        except Exception:pass
    if phase in ('completed','switching','unknown') and current_proof:
        try:
            verify_proof(current_proof,expected)
            comparison=operation.get('sdk_proof') or operation.get('staged_proof')
            if comparison and _identity(current_proof)==_identity(comparison):
                operation.update(phase='completed',sdk_proof=current_proof,recovered=True,updated_at=time.time());_atomic_json(path,operation)
                return dict(ok=True,recovered=True,sdk_proof=current_proof,rollback_confirmed=True,operation_token=token)
        except ValueError:pass
    if current_proof and _identity(current_proof)==_identity(original):
        cancelled=phase=='cancelled_before_start'
        operation.update(phase=phase if cancelled else 'rolled_back',rollback_confirmed=True,recovered=True,updated_at=time.time());_atomic_json(path,operation)
        return dict(ok=True,recovered=True,sdk_proof=current_proof,rollback_confirmed=True,operation_token=token,
                    **(dict(cancelled_before_start=True) if cancelled else {}))
    # A stopped process may have died between the two directory renames. Only
    # the exact recorded original environment is eligible for restoration.
    if phase not in ('switching','unknown') or not backup.is_dir() or _identity(inspect(backup))!=_identity(original):
        raise ValueError('SDK 恢复未确认，请保留当前会话锁并检查组件 / SDK recovery is unconfirmed; keep the maintenance lock')
    failed=current.with_name(current.name+'.sdk-failed-'+token)
    if failed.exists():raise ValueError('SDK recovery already has a quarantined environment')
    if current.exists():current.rename(failed)
    backup.rename(current)
    restored=inspect(current)
    if _identity(restored)!=_identity(original):raise ValueError('Restored SDK proof is unconfirmed')
    operation.update(phase='rolled_back',rollback_confirmed=True,recovered=True,updated_at=time.time());_atomic_json(path,operation)
    return dict(ok=True,recovered=True,sdk_proof=restored,rollback_confirmed=True,operation_token=token)


def recover_owned(config,expectedrelease,*,operation_token,run=None):
    """Read/restore the matching owned operation; never retries its update."""
    from runtime_paths import RESOURCE
    from sdk_release import validate_release
    release=validate_release(expectedrelease,require_fresh=False);token=_token(operation_token)
    if config.get('mode') not in ('wsl','macvm') or config.get('agent_directory')!=AGENT or config.get('python')!=PYTHON:
        raise ValueError('SDK recovery only supports the owned controller')
    if config['mode']=='wsl':
        import managed_runtime as runtime
        if sys.platform!='win32':raise ValueError('WSL SDK recovery requires Windows')
        runtime.check_ready()
    else:
        import macos_runtime as runtime
        if sys.platform!='darwin':raise ValueError('Mac SDK recovery requires macOS')
        runtime.ensure_running()
    contents={name:(RESOURCE/name).read_bytes() for name in ('sdk_environment_update.py','sdk_release.py','sdk_runtime.py')}
    digest=hashlib.sha256(b''.join(name.encode()+b'\0'+data for name,data in contents.items())).hexdigest()
    remote=AGENT+'/components/sdk-updater/'+digest
    with runtime.Files() as files:
        for name,data in contents.items():
            target=files.path(remote+'/'+name);target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists() and target.read_bytes()!=data:raise ValueError('SDK recovery helper integrity mismatch')
            if not target.exists():target.write_bytes(data)
        target=files.path(remote+'/recover-'+token+'.json');target.write_text(json.dumps(release),encoding='utf-8')
    argv=['/usr/bin/python3',remote+'/sdk_environment_update.py','--recover',remote+'/recover-'+token+'.json',config['mode'],token]
    if config['mode']=='macvm':argv=['sudo','-n','--','/usr/bin/env','WUJI_MANAGED_RUNTIME=macvm',*argv]
    command=runtime.args(argv,**({'user':'root'} if config['mode']=='wsl' else {}))
    result=(run or subprocess.run)(command,capture_output=True,text=True,timeout=120,check=True,
        env=runtime.env() if config['mode']=='macvm' else None,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if len(result.stdout)>65536:raise ValueError('SDK recovery result is incomplete')
    value=json.loads(result.stdout)
    if value.get('ok') is not True or value.get('operation_token')!=token or value.get('rollback_confirmed') is not True:
        raise ValueError(value.get('error','SDK recovery remains unconfirmed'))
    return value


if __name__=='__main__':
    try:
        release=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
        result=(recover_guest(release,sys.argv[3],sys.argv[4]) if sys.argv[1]=='--recover' else
                apply(sys.argv[1],release,sys.argv[3],operation_token=sys.argv[4]))
    except SDKUpdateError as error:result=dict(ok=False,error=str(error),rollback_confirmed=error.rollback_confirmed,switched=error.switched)
    except Exception:result=dict(ok=False,error='SDK update preflight failed; original environment was not changed',rollback_confirmed=True,switched=False)
    print(json.dumps(result))
