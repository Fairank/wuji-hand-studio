"""Cross-workspace handover for official CLI sessions; never controls motors."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import threading
import time

from user_dirs import user_data_dir
_local=threading.local()
_mutex=threading.RLock()


def root_folder():
    return Path(os.environ.get('WUJI_OFFICIAL_CLI_DATA', str(user_data_dir('WujiStudio')/'official-cli')))


def identity(pid):
    if os.name == 'nt':
        from studio_component import process_identity
        return process_identity(pid)
    try:
        return Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


@contextmanager
def gate(folder=None):
    root=Path(folder or root_folder());root.mkdir(parents=True, exist_ok=True)
    key=str(root.resolve())
    if key in getattr(_local,'held',set()):
        yield
        return
    with _mutex:
        with _file_gate(root):
            held=getattr(_local,'held',set());held.add(key);_local.held=held
            try:yield
            finally:held.remove(key)


@contextmanager
def _file_gate(root):
    with (root/'handover.lock').open('a+b') as f:
        if not f.seek(0, 2):f.write(b'0');f.flush()
        deadline=time.monotonic()+8
        while True:
            try:
                if os.name=='nt':
                    import msvcrt
                    f.seek(0);msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f, fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic()>deadline:raise ValueError('CLI 会话正在切换，请稍后重试 / CLI session handover in progress') from None
                time.sleep(.03)
        try:yield
        finally:
            if os.name=='nt':
                f.seek(0);msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:fcntl.flock(f, fcntl.LOCK_UN)


def reservation(folder=None):
    p=Path(folder or root_folder())/'session.json'
    try:
        row=json.loads(p.read_text(encoding='utf-8'))
        if not isinstance(row,dict):raise ValueError()
        # A dead visualization owner can leave a live Linux child. Only an
        # explicit runner probe/stop may clear this marker.
        if row.get('kind') not in {'viz','calibration','firmware','sdk_update'} and identity(row.get('pid'))!=row.get('start'):
            return {}
        return row
    except FileNotFoundError:return {}
    except (OSError,ValueError,TypeError):return dict(kind='unknown')


def reserve(kind, folder=None, *, metadata=None):
    root=Path(folder or root_folder());root.mkdir(parents=True,exist_ok=True)
    if reservation(root):raise ValueError('请先结束官方可视化或组件切换 / Finish the official CLI session first')
    row=dict(kind=kind,pid=os.getpid(),start=identity(os.getpid()),token=secrets.token_hex(16),created=time.time())
    if metadata is not None:
        if (not isinstance(metadata,dict) or set(metadata)-{'serial','stage'}
                or any(not isinstance(value,str) or not value or len(value)>100 or any(ord(c)<32 for c in value) for value in metadata.values())):
            raise ValueError('Invalid reservation metadata')
        row.update(metadata)
    atomic(root/'session.json',row)
    return row['token']


def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    stage=path.with_name(path.name+'.'+secrets.token_hex(6)+'.pending')
    try:
        with stage.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(stage,path)
    finally:stage.unlink(missing_ok=True)


def release(token,folder=None):
    root=Path(folder or root_folder())
    with gate(root):
        for attempt in range(10):
            if reservation(root).get('token')!=token:return
            try:
                (root/'session.json').unlink(missing_ok=True)
                return
            except PermissionError:
                # Concurrent read-only snapshots may briefly deny deletion on
                # Windows. Recheck the exact owner before every bounded retry.
                if attempt==9:raise
                time.sleep(.01)


def enforce(command,folder=None):
    if not isinstance(command,dict):return
    name=command.get('name','')
    blocked=(name in {'connect','parameters_sync','doctor_run','doctor_version','doctor_support','control_tools_check','runtime_install','runtime_select','bridge_config_save','device_profile_select','solver_install','hardware_start','hardware_trial','hardware_probe','program_start','program_resume'}
        or name.startswith('calibration_') and name not in {'calibration_cancel','calibration_terminal_input'}
        or name.startswith('glove_') and name not in {'glove_disconnect','glove_stop','glove_keepalive'}
        or name.startswith('group_') and name not in {'group_abort','group_beat','group_stop'})
    if blocked and reservation(folder):raise ValueError('请先结束官方可视化或 CLI 切换，再使用设备控制 / Close the official visualization or CLI handover first')
