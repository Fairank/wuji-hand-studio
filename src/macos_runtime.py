"""One app-owned Lima/VZ Linux guest; no user SSH configuration or motor startup."""
import hashlib
import json
import os
import platform
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import threading
import time
from runtime_paths import DATA, RESOURCE

NAME = 'hand-workbench'
# macOS Unix-domain socket paths are limited to 104 bytes; Application Support
# can already exhaust that budget before Lima appends its socket names.
ROOT = Path.home() / '.hand-workbench-runtime'
AGENT = '/opt/hand-workbench'
PYTHON = '/opt/hand-workbench-venv/bin/python'
CLI = '/usr/local/bin/wuji'
_lock = threading.Lock()
_boot_lock = threading.Lock()
_state = dict(busy=False, stage='idle', error=None)


def payload():
    roots = [RESOURCE / 'mac-runtime', RESOURCE.parent / 'runtime_payload/macos']
    if getattr(sys, 'frozen', False):
        roots.insert(0, Path(sys.executable).parent.parent / 'Resources/mac-runtime')
    for root in roots:
        if (root / 'manifest.json').is_file():
            return root
    raise ValueError('请使用包含 Linux 的 Mac 安装包 / Use the Mac package with bundled Linux')


def manifest():
    return json.loads((payload() / 'manifest.json').read_text(encoding='utf-8'))


def supported():
    return sys.platform == 'darwin' and platform.machine().lower() in ('arm64', 'aarch64')


def env():
    # Lima uses a private internal key; never imports ~/.ssh keys or an SSH agent.
    return {**os.environ, 'LIMA_HOME': str(ROOT / 'lima'), 'LIMA_SHELL': '/bin/bash',
            'LIMA_WORKDIR': AGENT, 'SSH_AUTH_SOCK': ''}


def command(*parts):
    if not supported():
        raise RuntimeError('此内置 Linux 版本需要 Apple Silicon Mac / Apple Silicon Mac required')
    return [str(payload() / 'lima/bin/limactl'), *parts]


def run(*parts, timeout=30):
    p = subprocess.run(command(*parts), env=env(), capture_output=True,
                       text=True, encoding='utf-8', errors='replace', timeout=timeout)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout)[-900:])
    return p.stdout


def status():
    result = dict(_state, supported=supported(), installed=False, ready=False,
                  distribution=NAME, engine='Apple Virtualization · Lima', mode='macvm',
                  uses_ssh=False, internal_transport='Lima-managed SSH over vsock',
                  starts_motors=False, usb_passthrough=False)
    try:
        m = manifest()
        result['bundled'] = True
        marker = ROOT / 'runtime-info.json'
        result['installed'] = (ROOT / 'lima' / NAME / 'lima.yaml').is_file()
        if marker.is_file() and result['installed']:
            saved = json.loads(marker.read_text(encoding='utf-8'))
            result.update(ready=saved.get('payload_id') == m['payload_id'],
                          runtime_version=m['payload_id'], sdk_version=m['sdk_version'])
    except (OSError, ValueError, KeyError) as e:
        result.update(bundled=False, error=str(e))
    return result


def verify_payload():
    root, m = payload(), manifest()
    if m['arch'] != 'aarch64':
        raise ValueError('Wrong Linux image architecture')
    for name, digest in m['files'].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError('Invalid runtime payload path')
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(block)
        actual = h.hexdigest()
        if actual != digest:
            raise ValueError('Runtime integrity check failed: ' + name)
    return m


def configuration(root, m):
    """JSON is also valid YAML. No home mount, agent forwarding or Docker daemon."""
    return dict(vmType='vz', arch='aarch64', cpus=2, memory='2GiB', disk='12GiB',
                images=[dict(location=str(root / m['image']), arch='aarch64',
                             digest='sha256:' + m['files'][m['image']])],
                mounts=[dict(location=str(ROOT / 'controller'), mountPoint=AGENT, writable=True)],
                mountType='virtiofs', networks=[dict(vzNAT=True)],
                containerd=dict(system=False, user=False),
                ssh=dict(loadDotSSHPubKeys=False, forwardAgent=False, forwardX11=False),
                provision=[dict(mode='system', script=(RESOURCE / 'macos_provision.sh').read_text(encoding='utf-8'))])


def _stage_controller():
    target = ROOT / 'controller'
    target.mkdir(parents=True, exist_ok=True)
    source = RESOURCE / 'controller-source'
    if not source.is_dir():
        source = RESOURCE
    for path in source.iterdir():
        if path.is_file() and path.suffix in ('.py', '.json') and not path.name.startswith('test_'):
            if path.name == 'motion_parameters.py' and (target / path.name).exists():
                continue
            shutil.copy2(path, target / path.name)
    for name in ('assets', 'official_data'):
        shutil.copytree(RESOURCE / name, target / name, dirs_exist_ok=True)
    shutil.copy2(payload() / 'wuji-cli.tar.gz', target / 'wuji-cli.tar.gz')


def check_ready():
    if not status()['ready']:
        raise ValueError('请先安装内置控制环境 / Set up the built-in controller first')


def ensure_running():
    check_ready()
    with _boot_lock:
        records = [json.loads(line) for line in run('list', '--json', NAME).splitlines() if line.strip()]
        if not any(row.get('name') == NAME and row.get('status') == 'Running' for row in records):
            run('start', '--tty=false', NAME, timeout=240)


def args(command_args, user='workbench'):
    if not isinstance(command_args, list) or not command_args or not all(isinstance(v, str) and '\0' not in v for v in command_args):
        raise ValueError('Invalid controller command')
    # limactl shell accepts an argv and performs shell quoting itself.
    return command('shell', '--workdir=' + AGENT, NAME, '--', *command_args)


def select():
    check_ready()
    from bridge_config import DEFAULT, save_config
    from parameter_file import parse_parameters
    parameters = DATA / 'motion_parameters.py'
    if parameters.is_file():
        source = parameters.read_text(encoding='utf-8-sig')
        parse_parameters(source)
        target = Files().path(AGENT + '/motion_parameters.py')
        staged = target.with_suffix('.pending')
        staged.write_text(source, encoding='utf-8')
        staged.replace(target)
    save_config(dict(DEFAULT, mode='macvm', agent_directory=AGENT, python=PYTHON, cli=CLI))


def start_install():
    if not supported():
        raise ValueError('Apple Silicon Mac required for this runtime')
    if not _lock.acquire(blocking=False):
        raise ValueError('Controller setup is already running')
    _state.update(busy=True, stage='checking', error=None)
    def work():
        try:
            m = verify_payload()
            ROOT.mkdir(parents=True, exist_ok=True)
            ROOT.chmod(0o700)
            owner = ROOT / 'owner.json'
            existing = ROOT / 'lima' / NAME / 'lima.yaml'
            if existing.is_file() and (not owner.is_file() or json.loads(owner.read_text()).get('product') != 'hand-workbench'):
                raise ValueError('An unowned VM occupies the controller directory; it was not changed')
            owner.write_text(json.dumps(dict(product='hand-workbench')), encoding='utf-8')
            _stage_controller()
            config = ROOT / 'controller.yaml'
            config.write_text(json.dumps(configuration(payload(), m), indent=2), encoding='utf-8')
            _state['stage'] = 'importing'
            run('start', '--tty=false', NAME if existing.exists() else '--name=' + NAME,
                *([] if existing.exists() else [str(config)]), timeout=900)
            _state['stage'] = 'verifying'
            p = subprocess.run(args([PYTHON, '-c', 'from wuji_sdk import SdkManager; import console_agent']),
                               env=env(), capture_output=True, text=True, timeout=45)
            if p.returncode:
                raise RuntimeError('Official SDK check failed: ' + p.stderr[-500:])
            pending = ROOT / 'runtime-info.pending'
            pending.write_text(json.dumps(dict(payload_id=m['payload_id'], installed=time.time())), encoding='utf-8')
            pending.replace(ROOT / 'runtime-info.json')
            select()
            _state['stage'] = 'ready'
        except Exception as error:
            _state.update(stage='failed', error=str(error))
        finally:
            _state['busy'] = False
            _lock.release()
    threading.Thread(target=work, name='Mac controller setup', daemon=True).start()
    return status()


class Files:
    def __init__(self):
        self.root = ROOT / 'controller'
    def path(self, value):
        p = PurePosixPath(value)
        if not p.is_absolute() or '..' in p.parts or '\\' in str(p) or not p.is_relative_to(AGENT):
            raise ValueError('Path escapes the owned controller')
        result = self.root.joinpath(*p.relative_to(AGENT).parts)
        if not result.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('Controller symlink escapes its directory')
        return result
    def open(self, path, mode): return self.path(path).open(mode)
    def posix_rename(self, a, b): self.path(a).replace(self.path(b))
    def listdir(self, path): return [p.name for p in self.path(path).iterdir()]
    def remove(self, path): self.path(path).unlink()
    def __enter__(self): return self
    def __exit__(self, *_): pass


class MacController:
    def __init__(self, config, profile_id):
        from device_profiles import profile
        profile(profile_id)
        ensure_running()
        self.agent_directory, self.process = AGENT, None
        self.agent_command = args(['/usr/bin/env', 'WUJI_HAND_PROFILE=' + profile_id,
                                   'WUJI_MANAGED_RUNTIME=macvm', PYTHON, '-u', AGENT + '/console_agent.py'])
    def open_sftp(self): return Files()
    def exec_command(self, command_args, timeout=None):
        from local_controller import ReadStream
        if command_args != self.agent_command or self.process is not None:
            raise ValueError('Only the owned controller may be started')
        self.process = subprocess.Popen(command_args, env=env(), stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding='utf-8', errors='replace', bufsize=1)
        return self.process.stdin, ReadStream(self.process.stdout, self.process), ReadStream(self.process.stderr, self.process)
    def close(self):
        from local_controller import LocalController
        LocalController.close(self)
