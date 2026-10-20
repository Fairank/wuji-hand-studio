"""Private Linux graphical session for the unmodified official Studio binary.

No device SDK calls or motion commands. Installation extracts the verified .deb
to an immutable version directory; the vendor's package scripts are not run.
"""
import argparse
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import queue
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

ROOT=Path('/opt/hand-workbench-studio')
HOME=Path('/home/workbench/.local/share/hand-workbench-studio')
TOOLS=('Xtigervnc','tigervncpasswd','websockify','openbox','dbus-run-session','xauth','xwininfo','unshare','runuser')


def version(value):
    if not isinstance(value,str) or not re.fullmatch(r'20\d{2}\.\d{1,2}\.\d{1,2}',value):
        raise ValueError('Invalid official Studio version')
    return value


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def emit(kind,**values):
    print('STUDIO_JSON:'+json.dumps(dict(type=kind,**values)),flush=True)


def owned_directory(path):
    path=Path(path)
    if path.is_symlink():raise ValueError('Studio directory is redirected')
    path.mkdir(parents=True,exist_ok=True)
    identity=pwd.getpwnam('workbench');os.chown(path,identity.pw_uid,identity.pw_gid);path.chmod(0o700)
    return path


def inventory():
    versions=[]
    for path in sorted((ROOT/'versions').glob('*/receipt.json')):
        try:
            row=json.loads(path.read_text());version(row['version'])
            if path.parent.name==row['version'] and (path.parent/'usr/bin/wuji-studio').is_file():versions.append(row)
        except (ValueError,KeyError,OSError):pass
    return dict(versions=versions,dependencies={name:shutil.which(name) is not None for name in TOOLS},
                client_ready=(ROOT/'client/core/rfb.js').is_file(),data_directory=str(HOME/'profile'))


def install(package,selected,expected):
    selected=version(selected)
    if not re.fullmatch('[a-f0-9]{64}',expected) or digest(package)!=expected:
        raise ValueError('Official Studio package integrity check failed')
    metadata=subprocess.check_output(['dpkg-deb','-f',str(package),'Package','Version','Architecture'],text=True)
    if metadata.splitlines()!=['Package: wuji-studio','Version: '+selected,'Architecture: amd64']:
        raise ValueError('Unexpected Studio package metadata')
    target=ROOT/'versions'/selected
    if target.exists():
        row=json.loads((target/'receipt.json').read_text())
        if row['sha256']!=expected or digest(target/'usr/bin/wuji-studio')!=row['binary_sha256']:
            raise ValueError('Installed Studio differs from the verified package')
    else:
        stage=target.with_name(selected+'.pending-'+secrets.token_hex(4));stage.mkdir(parents=True)
        try:
            subprocess.run(['dpkg-deb','-x',str(package),str(stage)],check=True)
            binary=stage/'usr/bin/wuji-studio'
            if not binary.is_file():raise ValueError('Official ELF executable missing')
            with binary.open('rb') as stream:
                if stream.read(4)!=b'\x7fELF':raise ValueError('Official ELF executable missing')
            row=dict(version=selected,sha256=expected,binary_sha256=digest(binary),installed=time.time(),vendor_binary_unmodified=True)
            (stage/'receipt.json').write_text(json.dumps(row,indent=2))
            stage.rename(target)
        finally:
            if stage.exists() and stage.parent==ROOT/'versions' and not stage.is_symlink():shutil.rmtree(stage)
    client=ROOT/'client'
    if not (client/'core/rfb.js').is_file():
        client.mkdir(parents=True,exist_ok=True)
        for name in ('core','vendor'):
            source=Path('/usr/share/novnc')/name
            if source.is_dir():shutil.copytree(source,client/name,dirs_exist_ok=True)
        shutil.copyfile('/usr/share/doc/novnc/copyright',client/'COPYRIGHT.txt')
    for path in (HOME,HOME/'profile',HOME/'runtime'):owned_directory(path)
    return dict(installed=row,**inventory())


def free_port():
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def process_start(pid):
    try:return Path('/proc/'+str(pid)+'/stat').read_text().split(') ',1)[1].split()[19]
    except (OSError,IndexError):return None


def probe():
    path=ROOT/'session.json'
    try:
        row=json.loads(path.read_text())
        boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if row.get('boot_id') and row['boot_id']!=boot:return dict(active=False)
        row['active']=(bool(row.get('process_start')) and process_start(row['pid'])==row['process_start'] or any(
            bool(child.get('process_start')) and process_start(child['pid'])==child['process_start'] for child in row.get('children',[])))
        return row
    except (OSError,ValueError,KeyError):return dict(active=False)


def stop_orphan():
    row=probe()
    if not row.get('active'):return dict(active=False,device_disable_confirmed=False)
    pid=row['pid']
    if row.get('process_start') and process_start(pid)==row['process_start']:
        args=Path('/proc/'+str(pid)+'/cmdline').read_bytes().split(b'\0')
        if b'serve' not in args or not any(a.endswith(b'/studio_agent.py') for a in args):
            raise ValueError('Existing process is not an owned Studio session')
        os.kill(pid,signal.SIGTERM)
    else:
        for child in row.get('children',[]):
            if child.get('process_start') and process_start(child['pid'])==child['process_start']:
                os.killpg(child['pid'],signal.SIGTERM)
    deadline=time.monotonic()+25
    while probe().get('active') and time.monotonic()<deadline:time.sleep(.1)
    if probe().get('active'):raise TimeoutError('Studio session exit is not confirmed')
    return dict(active=False,device_disable_confirmed=False)


def serve(selected,port,offline,width,height):
    selected=version(selected)
    if not 1024<=port<=65535 or not 960<=width<=2560 or not 600<=height<=1600:raise ValueError('Invalid graphical session size or port')
    ROOT.mkdir(exist_ok=True)
    lease=(ROOT/'session.lock').open('a')
    try:fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise ValueError('Official Studio already has a graphical session') from None
    for name in TOOLS:
        if not shutil.which(name):raise ValueError('Missing Studio dependency: '+name)
    target=ROOT/'versions'/selected;receipt=json.loads((target/'receipt.json').read_text())
    binary=target/'usr/bin/wuji-studio'
    if digest(binary)!=receipt['binary_sha256']:raise ValueError('Studio binary changed; reinstall the component')
    profile=owned_directory(HOME/'profile');runtime=owned_directory(HOME/'runtime'/secrets.token_hex(8))
    identity=pwd.getpwnam('workbench')
    # WSLg owns its X11 directory. A new mount namespace keeps this graphical
    # session's sockets separate without modifying the user's existing display.
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.unshare(0x00020000)!=0:raise OSError(ctypes.get_errno(),'Private Studio mount namespace unavailable')
    subprocess.run(['mount','--make-rprivate','/'],check=True,capture_output=True)
    private_x11=runtime/'x11';private_x11.mkdir();private_x11.chmod(0o1777)
    subprocess.run(['mount','--bind',str(private_x11),'/tmp/.X11-unix'],check=True,capture_output=True)
    display=next((n for n in range(110,150) if not Path('/tmp/.X'+str(n)+'-lock').exists() and not Path('/tmp/.X11-unix/X'+str(n)).exists()),None)
    if display is None:raise ValueError('No private Studio display is available')
    display=':'+str(display);password=secrets.token_urlsafe(6)
    auth=runtime/'vnc.auth'
    result=subprocess.run(['tigervncpasswd','-f'],input=(password+'\n').encode(),capture_output=True,check=True)
    auth.write_bytes(result.stdout);auth.chmod(0o600);os.chown(auth,identity.pw_uid,identity.pw_gid)
    xauth=runtime/'Xauthority';xauth.touch(mode=0o600);os.chown(xauth,identity.pw_uid,identity.pw_gid)
    subprocess.run(['xauth','-f',str(xauth)],input='add '+display+' MIT-MAGIC-COOKIE-1 '+secrets.token_hex(16)+'\n',text=True,check=True,capture_output=True)
    os.chown(xauth,identity.pw_uid,identity.pw_gid);xauth.chmod(0o600)
    config=runtime/'openbox.xml'
    config.write_text('<openbox_config xmlns="http://openbox.org/3.4/rc"><applications><application type="normal"><decor>no</decor><maximized>yes</maximized><focus>yes</focus></application></applications></openbox_config>')
    config.chmod(0o644)
    environment={**os.environ,'HOME':str(profile),'DISPLAY':display,'XAUTHORITY':str(xauth),'GDK_BACKEND':'x11',
        'XDG_RUNTIME_DIR':str(runtime),'XDG_CONFIG_HOME':str(profile/'.config'),'XDG_DATA_HOME':str(profile/'.local/share'),
        'XDG_CACHE_HOME':str(profile/'.cache'),'WEBKIT_DISABLE_DMABUF_RENDERER':'1','LIBGL_ALWAYS_SOFTWARE':'1',
        'WUJI_TELEMETRY_DISABLE':'1','LANG':'C.UTF-8'}
    for key in ('WAYLAND_DISPLAY','DBUS_SESSION_BUS_ADDRESS','SESSION_MANAGER'):environment.pop(key,None)
    processes=[];commands=queue.Queue();log=(runtime/'session.log').open('ab')
    session_state=dict(active=True,pid=os.getpid(),process_start=process_start(os.getpid()),children=[],boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    def save_session(value):
        stage=ROOT/'session.pending.json';stage.write_text(json.dumps(value));stage.replace(ROOT/'session.json')
    save_session(session_state)
    for signum in (signal.SIGTERM,signal.SIGHUP):signal.signal(signum,lambda *_:commands.put(dict(name='stop')))
    def launch(args,isolated=False):
        command=['runuser','--preserve-environment','-u','workbench','--',*args]
        if isolated:command=['unshare','--net','--',*command]
        child=subprocess.Popen(command,env=environment,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        processes.append(child);session_state['children'].append(dict(pid=child.pid,process_start=process_start(child.pid),role=args[0]));save_session(session_state);return child
    def read():
        for line in sys.stdin:
            try:commands.put(json.loads(line))
            except ValueError:commands.put(dict(name='invalid'))
        commands.put(dict(name='stop'))
    threading.Thread(target=read,daemon=True).start()
    def stopping():
        try:return commands.get_nowait().get('name')=='stop'
        except queue.Empty:return False
    try:
        rfb=free_port()
        server=launch(['Xtigervnc',display,'-geometry',str(width)+'x'+str(height),'-depth','24','-localhost','yes',
            '-SecurityTypes','VncAuth','-PasswordFile',str(auth),'-AlwaysShared','-rfbport',str(rfb),'-nolisten','tcp','-auth',str(xauth)])
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            if stopping():return
            if server.poll() is not None:raise RuntimeError('Studio private display exited; inspect component log')
            if Path('/tmp/.X11-unix/X'+display[1:]).exists():break
            time.sleep(.1)
        else:raise TimeoutError('Studio private display did not become ready')
        launch(['openbox','--config-file',str(config),'--sm-disable'])
        proxy=launch(['websockify','127.0.0.1:'+str(port),'127.0.0.1:'+str(rfb)])
        app=launch(['dbus-run-session','--',str(binary)],isolated=offline)
        deadline=time.monotonic()+60;title=''
        while time.monotonic()<deadline:
            if stopping():return
            if app.poll() is not None:raise RuntimeError('Official Studio exited during startup; inspect component log')
            tree=subprocess.run(['xwininfo','-root','-tree'],env=environment,text=True,capture_output=True,timeout=5).stdout
            if re.search(r'"[^"]*[Ww]uji[^"]*"',tree):title='Wuji Studio';break
            time.sleep(.3)
        else:raise TimeoutError('Official Studio window did not appear')
        isolated=os.readlink('/proc/'+str(app.pid)+'/ns/net')!=os.readlink('/proc/self/ns/net')
        # The runuser parent stays in the selected namespace; the full vendor
        # process inherits it. Offline QA must never fall back to normal access.
        if offline and not isolated:raise RuntimeError('Offline Studio network isolation was not established')
        state=dict(**session_state,studio_pid=app.pid,
            version=selected,offline=offline,network_isolated=isolated,port=port,display=display,
            title=title,log=str(runtime/'session.log'),binary_sha256=receipt['binary_sha256'])
        save_session(state)
        emit('ready',**state,password=password)
        last=0.
        while all(p.poll() is None for p in (server,proxy,app)):
            try:command=commands.get(timeout=.2)
            except queue.Empty:command={}
            if command.get('name')=='stop':break
            if command.get('name')=='restore':
                windows=subprocess.run(['wmctrl','-l'],env=environment,capture_output=True,text=True,timeout=5).stdout
                for line in windows.splitlines():
                    if re.search(r'\b[Ww]uji',line):
                        identifier=line.split()[0]
                        if re.fullmatch(r'0x[0-9a-fA-F]+',identifier):
                            subprocess.run(['wmctrl','-ia',identifier],env=environment,capture_output=True,timeout=5)
                            subprocess.run(['wmctrl','-ir',identifier,'-b','add,maximized_vert,maximized_horz'],env=environment,capture_output=True,timeout=5)
                        break
            if time.monotonic()-last>=2:emit('heartbeat',active=True);last=time.monotonic()
    finally:
        # Only children created by this session are signalled. Device state is
        # not inferred from GUI exit; the parent reports that limit explicitly.
        for child in reversed(processes):
            if child.poll() is None:
                try:os.killpg(child.pid,signal.SIGTERM)
                except ProcessLookupError:pass
        for child in reversed(processes):
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                child.wait(timeout=3)
        log.close();auth.unlink(missing_ok=True)
        save_session(dict(active=False,pid=0,process_start=None,finished=time.time()))
        emit('closed',active=False,device_disable_confirmed=False)
        lease.close()


def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='operation',required=True)
    sub.add_parser('inventory');sub.add_parser('probe');sub.add_parser('stop')
    installer=sub.add_parser('install');installer.add_argument('package');installer.add_argument('version');installer.add_argument('sha256')
    runner=sub.add_parser('serve');runner.add_argument('version');runner.add_argument('--port',type=int,required=True)
    runner.add_argument('--offline',action='store_true');runner.add_argument('--width',type=int,default=1280);runner.add_argument('--height',type=int,default=800)
    args=parser.parse_args()
    if os.geteuid()!=0:raise ValueError('Use only the owned controller setup interface')
    if args.operation=='inventory':print(json.dumps(inventory()))
    elif args.operation=='probe':print(json.dumps(probe()))
    elif args.operation=='stop':print(json.dumps(stop_orphan()))
    elif args.operation=='install':print(json.dumps(install(args.package,args.version,args.sha256)))
    else:serve(args.version,args.port,args.offline,args.width,args.height)


if __name__=='__main__':main()
