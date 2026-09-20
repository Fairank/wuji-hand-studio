"""One owned WSL2 distribution. No SSH, passwords, global WSL settings or autostart motors."""
import hashlib,json,os,shutil,subprocess,sys,threading,time
from pathlib import Path,PurePosixPath
from user_dirs import user_data_dir
from runtime_paths import RESOURCE

NAME='HandWorkbenchControl'
AGENT='/opt/hand-workbench'
PYTHON='/opt/hand-workbench-venv/bin/python'
CLI='/usr/local/bin/wuji'
ROOT=user_data_dir('WujiStudio')/'runtime'
_lock=threading.Lock()
_state=dict(busy=False,stage='idle',error=None)

def _decode(raw):
    if b'\0' in raw:return raw.decode('utf-16-le',errors='replace').lstrip('\ufeff').strip()
    for encoding in ('utf-8','mbcs'):
        try:return raw.decode(encoding).strip()
        except (UnicodeError,LookupError):pass
    return raw.decode('utf-8',errors='replace').strip()

def registered():
    if sys.platform!='win32':return None
    import winreg
    try:key=winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Lxss')
    except FileNotFoundError:return None
    with key:
        for i in range(winreg.QueryInfoKey(key)[0]):
            with winreg.OpenKey(key,winreg.EnumKey(key,i)) as sub:
                if winreg.QueryValueEx(sub,'DistributionName')[0]==NAME:
                    path=Path(winreg.QueryValueEx(sub,'BasePath')[0].removeprefix('\\\\?\\')).resolve()
                    if path!=(ROOT/NAME).resolve():raise RuntimeError('A different distribution owns this name; it will not be changed')
                    return dict(name=NAME,path=str(path),version=winreg.QueryValueEx(sub,'Version')[0])
    return None

def image_manifest():
    path=RESOURCE/'runtime_manifest.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None

def status():
    result=dict(_state,supported=sys.platform=='win32',installed=False,ready=False,distribution=NAME,uses_ssh=False,starts_motors=False)
    if sys.platform!='win32':return result
    try:
        entry=registered();result['installed']=entry is not None
        marker=ROOT/'runtime-info.json'
        if entry and marker.is_file():
            saved=json.loads(marker.read_text(encoding='utf-8'));manifest=image_manifest()
            result.update(ready=entry['version']==2 and bool(manifest) and saved.get('runtime_version')==manifest['runtime_version'],runtime_version=saved.get('runtime_version'),sdk_version=saved.get('sdk_version'))
    except (OSError,ValueError,RuntimeError) as error:result['error']=str(error)
    return result

def args(command,user='workbench'):
    if sys.platform!='win32':raise RuntimeError('Managed runtime requires Windows')
    if not isinstance(command,list) or not command or not all(isinstance(x,str) and '\0' not in x for x in command):raise ValueError('Invalid runtime command')
    return ['wsl.exe','--distribution',NAME,'--user',user,'--cd',AGENT,'--exec',*command]

def check_ready():
    state=status()
    if not state['ready']:raise ValueError('请在连接页安装内置控制环境 / Install the built-in controller on the Connection page')

def _image():
    manifest=image_manifest()
    if not manifest:raise ValueError('Runtime image is not included in this build')
    name=manifest['file']
    if Path(name).name!=name:raise ValueError('Invalid runtime image name')
    roots=[Path(sys.executable).parent/'runtime',RESOURCE.parent/'runtime_payload']
    found=next((p/name for p in roots if (p/name).is_file()),None)
    if not found:raise ValueError('Use the Windows installer that includes the control runtime / 请使用包含控制组件的 Windows 安装包')
    h=hashlib.sha256()
    with found.open('rb') as stream:
        while chunk:=stream.read(1024*1024):h.update(chunk)
    if h.hexdigest()!=manifest['sha256']:raise ValueError('Control runtime image failed integrity verification')
    return found,manifest

def start_install():
    if not _lock.acquire(blocking=False):raise ValueError('Control environment setup is already running')
    _state.update(busy=True,stage='checking',error=None)
    def work():
        try:
            image,manifest=_image()
            probe=subprocess.run(['wsl.exe','--version'],capture_output=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
            if probe.returncode:raise RuntimeError('请先安装 WSL 2 系统组件，可能需要重启 / Install WSL 2; a restart may be required')
            existing=registered();ROOT.mkdir(parents=True,exist_ok=True)
            pending=ROOT/'pending-install.json'
            if existing and not status()['ready']:
                previous=json.loads(pending.read_text(encoding='utf-8')) if pending.is_file() else {}
                marker=ROOT/'runtime-info.json'
                saved=json.loads(marker.read_text(encoding='utf-8')) if marker.is_file() else {}
                if saved.get('runtime_version')=='1.0.0' and manifest['runtime_version']=='1.0.1' and saved.get('sdk_version')==manifest['sdk_version']:
                    _state['stage']='upgrading'
                    with Files() as files:
                        files.path(AGENT+'/runtime_update.py').write_bytes((RESOURCE/'runtime_update.py').read_bytes())
                    converted=subprocess.run(args(['/usr/bin/wslpath','-a',str(image)]),capture_output=True,check=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
                    upgraded=subprocess.run(args(['/usr/bin/python3',AGENT+'/runtime_update.py',_decode(converted.stdout),manifest['sha256']],user='root'),capture_output=True,timeout=90,creationflags=subprocess.CREATE_NO_WINDOW)
                    if upgraded.returncode:raise RuntimeError('Controller update failed: '+_decode(upgraded.stderr)[-400:])
                elif previous.get('sha256')!=manifest['sha256']:raise RuntimeError('Existing runtime version is not supported for automatic upgrade')
            if not existing:
                _state['stage']='importing';target=ROOT/NAME;target.mkdir(exist_ok=True)
                pending.write_text(json.dumps(dict(sha256=manifest['sha256'])),encoding='utf-8')
                result=subprocess.run(['wsl.exe','--import',NAME,str(target),str(image),'--version','2'],capture_output=True,timeout=180,creationflags=subprocess.CREATE_NO_WINDOW)
                if result.returncode:raise RuntimeError('WSL import failed: '+_decode(result.stderr or result.stdout)[-600:])
            _state['stage']='verifying'
            result=subprocess.run(args([PYTHON,'-c','from wuji_sdk import SdkManager;import console_agent']),capture_output=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:raise RuntimeError('Control runtime import check failed: '+_decode(result.stderr)[-500:])
            (ROOT/'runtime-info.json').write_text(json.dumps(dict(runtime_version=manifest['runtime_version'],sdk_version=manifest['sdk_version'],installed=time.time())),encoding='utf-8')
            pending.unlink(missing_ok=True)
            select();_state['stage']='ready'
        except Exception as error:_state.update(stage='failed',error=str(error))
        finally:_state['busy']=False;_lock.release()
    threading.Thread(target=work,name='Workbench runtime setup',daemon=True).start();return status()

def select():
    check_ready()
    from bridge_config import DEFAULT,save_config
    from runtime_paths import DATA
    from parameter_file import parse_parameters
    parameters=DATA/'motion_parameters.py'
    if parameters.is_file():
        source=parameters.read_text(encoding='utf-8-sig');parse_parameters(source)
        with Files() as files:
            target=files.path(AGENT+'/motion_parameters.py');staged=target.with_suffix('.py.pending')
            staged.write_text(source,encoding='utf-8');staged.replace(target)
    save_config(dict(DEFAULT,mode='wsl',agent_directory=AGENT,python=PYTHON,cli=CLI))

class Files:
    def __init__(self):self.root=Path('\\\\wsl.localhost')/NAME/'opt/hand-workbench'
    def path(self,value):
        p=PurePosixPath(value)
        if not p.is_absolute() or '..' in p.parts or not p.is_relative_to(AGENT) or '\\' in str(p):raise ValueError('Path escapes the control environment')
        result=self.root.joinpath(*p.relative_to(AGENT).parts)
        # Do not permit an in-runtime symlink to cross the owned controller tree.
        if not result.resolve().is_relative_to(self.root.resolve()):raise ValueError('Controller path is a link outside its directory')
        return result
    def open(self,path,mode):return self.path(path).open(mode)
    def posix_rename(self,a,b):self.path(a).replace(self.path(b))
    def listdir(self,path):return [p.name for p in self.path(path).iterdir()]
    def remove(self,path):self.path(path).unlink()
    def __enter__(self):return self
    def __exit__(self,*_):pass

class WslController:
    def __init__(self,config,profile_id):
        from device_profiles import profile
        profile(profile_id);check_ready();self.process=None;self.agent_directory=AGENT
        self.agent_command=args(['/usr/bin/env','WUJI_HAND_PROFILE='+profile_id,PYTHON,'-u',AGENT+'/console_agent.py'])
    def open_sftp(self):return Files()
    def exec_command(self,command,timeout=None):
        from local_controller import ReadStream
        if command!=self.agent_command or self.process is not None:raise ValueError('Only the owned controller process may be started')
        self.process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',bufsize=1,creationflags=subprocess.CREATE_NO_WINDOW)
        return self.process.stdin,ReadStream(self.process.stdout,self.process),ReadStream(self.process.stderr,self.process)
    def close(self):
        from local_controller import LocalController
        LocalController.close(self)
