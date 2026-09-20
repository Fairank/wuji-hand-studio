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
    if out['mode'] not in ('local','ssh'):raise ValueError('Invalid controller mode')
    if out['mode']=='local' and not sys.platform.startswith('linux'):raise ValueError('Local SDK control requires Linux')
    for k in ('agent_directory','python','cli'):
        if out[k] and (not re.fullmatch(r'[A-Za-z0-9_./\- ]+',out[k]) or '..' in out[k].split('/')):raise ValueError('Invalid controller path')
    if out['agent_directory'] and not out['agent_directory'].startswith('/'):raise ValueError('Controller directory must be absolute')
    return out

def load_config():
    p=DATA/'connection.json'
    return validate({**DEFAULT,**json.loads(p.read_text(encoding='utf-8'))}) if p.exists() else dict(DEFAULT)

def save_config(values):
    out=validate(values);DATA.mkdir(parents=True,exist_ok=True)
    p=DATA/'connection.pending';p.write_text(json.dumps(out,indent=2),encoding='utf-8');p.replace(DATA/'connection.json')
    if os.name!='nt':(DATA/'connection.json').chmod(0o600)

def ssh_client(c):
    import paramiko
    if not all(c[k] for k in ('host','username')):
        raise RuntimeError('Configure the Linux controller in Connection / 请在连接页配置Linux控制端')
    client=paramiko.SSHClient();client.load_system_host_keys()
    hosts=Path(c['known_hosts']).expanduser() if c['known_hosts'] else Path.home()/'.ssh/known_hosts'
    if hosts.is_file():client.load_host_keys(str(hosts))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(c['host'],port=c['port'],username=c['username'],
            key_filename=str(Path(c['key_filename']).expanduser()) if c['key_filename'] else None,
            timeout=8,banner_timeout=8,auth_timeout=8,allow_agent=True,look_for_keys=True)
        client.agent_directory=c['agent_directory'].rstrip('/')
        from device_profiles import selected_profile
        client.agent_command='env WUJI_HAND_PROFILE='+shlex.quote(selected_profile()['id'])+' '+shlex.quote(c['python'])+' -u '+shlex.quote(client.agent_directory+'/console_agent.py')
        client.get_transport().set_keepalive(10)
        return client
    except Exception:
        client.close();raise

def bridge_client():
    c=load_config()
    if not all(c[k] for k in ('agent_directory','python')):raise ValueError('Configure the SDK controller directory and Python path')
    if c['mode']=='local':
        from local_controller import LocalController
        from device_profiles import selected_profile
        return LocalController(c,selected_profile()['id'])
    return ssh_client(c)
