"""Explicit SSH configuration, strict known-host verification, no bundled credentials."""
import json, os, re, shlex, sys
from pathlib import Path
from runtime_paths import DATA,RESOURCE

_source=RESOURCE.parents[1]/'controller/source' if getattr(sys,'frozen',False) else RESOURCE
DEFAULT=dict(mode='local' if sys.platform.startswith('linux') else 'ssh',host='',port=22,username='',key_filename='',known_hosts='',agent_directory=str(_source) if sys.platform.startswith('linux') else '',python='python3',cli='wuji')

def validate(values):
    if not isinstance(values,dict) or set(values)!=set(DEFAULT):raise ValueError('Unexpected connection fields')
    out={}
    for k in DEFAULT:
        v=values[k]
        if k=='port':
            if type(v) is not int or not 1<=v<=65535:raise ValueError('Invalid SSH port')
        elif not isinstance(v,str) or len(v)>1024 or any(ord(c)<32 for c in v):raise ValueError('Invalid connection field: '+k)
        out[k]=v.strip() if isinstance(v,str) else v
    if out['host'] and not re.fullmatch(r'[A-Za-z0-9_.:\-]+',out['host']):raise ValueError('Invalid host')
    if out['username'] and not re.fullmatch(r'[A-Za-z0-9_.\-]+',out['username']):raise ValueError('Invalid username')
    if out['mode'] not in ('local','ssh','wsl','macvm'):raise ValueError('Invalid controller mode')
    if out['mode']=='local' and not sys.platform.startswith('linux'):raise ValueError('Local SDK control requires Linux')
    if out['mode']=='wsl' and sys.platform!='win32':raise ValueError('Built-in WSL controller requires Windows')
    if out['mode']=='macvm' and sys.platform!='darwin':raise ValueError('Built-in Mac controller requires macOS')
    for k in ('agent_directory','python','cli'):
        if out[k] and (not re.fullmatch(r'[A-Za-z0-9_./\- ]+',out[k]) or '..' in out[k].split('/')):raise ValueError('Invalid controller path')
    if out['agent_directory'] and not out['agent_directory'].startswith('/'):raise ValueError('Controller directory must be absolute')
    return out

def load_config():
    p=DATA/'connection.json'
    if not p.exists():return dict(DEFAULT)
    values=json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(values,dict):raise ValueError('Invalid connection configuration')
    # v0.1.0 had SSH only. Upgrading it on Linux must not silently switch host.
    return validate({**DEFAULT,'mode':'ssh',**values})

def save_config(values):
    out=validate(values);DATA.mkdir(parents=True,exist_ok=True)
    p=DATA/'connection.pending';p.write_text(json.dumps(out,indent=2),encoding='utf-8');p.replace(DATA/'connection.json')
    if os.name!='nt':(DATA/'connection.json').chmod(0o600)

def ssh_client(c):
    import paramiko
    from controller_credentials import read as saved_password
    if not all(c[k] for k in ('host','username')):
        raise RuntimeError('Configure the Linux controller in Connection / 请在连接页配置Linux控制端')
    client=paramiko.SSHClient();client.load_system_host_keys()
    hosts=Path(c['known_hosts']).expanduser() if c['known_hosts'] else Path.home()/'.ssh/known_hosts'
    if hosts.is_file():client.load_host_keys(str(hosts))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        password=saved_password(c) if not c['key_filename'] else None
        client.connect(c['host'],port=c['port'],username=c['username'],
            key_filename=str(Path(c['key_filename']).expanduser()) if c['key_filename'] else None,
            password=password,timeout=8,banner_timeout=8,auth_timeout=8,
            allow_agent=password is None,look_for_keys=password is None)
        client.agent_directory=c['agent_directory'].rstrip('/')
        from device_profiles import selected_profile
        from controller_launch import parameter_environment
        try:
            sftp=client.open_sftp()
            try:sftp.stat(client.agent_directory+'/agent_bootstrap.py');bootstrap=True
            except IOError:bootstrap=False
            finally:sftp.close()
        except OSError:
            bootstrap=False
        if os.environ.get('WUJI_FLEET_ID') and not bootstrap:
            raise ValueError('多手 SSH 控制端需要新版独立启动程序 / Update SSH controller for multi-hand use')
        client.parameters_in_launch=bootstrap
        if bootstrap:
            client.agent_command='env '+shlex.quote(parameter_environment())+' WUJI_HAND_PROFILE='+shlex.quote(selected_profile()['id'])+' '+shlex.quote(c['python'])+' -u '+shlex.quote(client.agent_directory+'/agent_bootstrap.py')
        else:
            client.agent_command='env WUJI_HAND_PROFILE='+shlex.quote(selected_profile()['id'])+' '+shlex.quote(c['python'])+' -u '+shlex.quote(client.agent_directory+'/console_agent.py')
        client.get_transport().set_keepalive(10)
        return client
    except Exception:
        client.close();raise

def bridge_client():
    c=load_config()
    if c['mode']=='macvm':
        from macos_runtime import MacController
        from device_profiles import selected_profile
        return MacController(c,selected_profile()['id'])
    if c['mode']=='wsl':
        from managed_runtime import WslController
        from device_profiles import selected_profile
        return WslController(c,selected_profile()['id'])
    if not all(c[k] for k in ('agent_directory','python')):raise ValueError('请先展开连接页的“控制端与诊断设置”，填写控制程序目录和 Python 路径 / Configure controller settings first')
    if c['mode']=='local':
        from local_controller import LocalController
        from device_profiles import selected_profile
        return LocalController(c,selected_profile()['id'])
    return ssh_client(c)
