"""Recover only a recorded, identity-verified Linux visualization process."""
import json
import os
from pathlib import Path
import signal
import sys
import time


def _process(pid,start,uid,expected):
    if type(pid) is not int or pid<=1 or not isinstance(start,str):return False
    proc=Path('/proc')/str(pid)
    try:
        fields=(proc/'stat').read_text().rsplit(')',1)[1].split()
        if fields[0]=='Z':return False
        if fields[19]!=start or proc.stat().st_uid!=uid:return False
        argv=(proc/'cmdline').read_bytes().rstrip(b'\0').decode('utf-8').split('\0')
        return expected(argv)
    except (OSError,IndexError,UnicodeError):return False


def _read(directory):
    directory=Path(directory)
    if not directory.is_absolute() or directory.is_symlink():raise ValueError('Invalid visualization session directory')
    if not directory.exists():return directory,None
    if directory.stat().st_uid!=os.getuid():raise ValueError('Visualization session belongs to another user')
    path=directory/'session.json'
    if not path.exists():return directory,None
    if path.is_symlink() or path.stat().st_size>8192:raise ValueError('Invalid visualization session record')
    row=json.loads(path.read_text())
    if (not isinstance(row,dict) or row.get('kind')!='official_viz_runner' or row.get('uid')!=os.getuid()
        or row.get('session_dir')!=str(directory) or not isinstance(row.get('token'),str) or len(row['token'])!=32
        or not isinstance(row.get('runner'),str) or Path(row['runner']).name!='official_viz_runner.py'
        or not Path(row['runner']).is_absolute() or not isinstance(row.get('cli'),str) or not Path(row['cli']).is_absolute()):
        raise ValueError('Unrecognized visualization session record')
    return directory,row


def _runner(row):
    def expected(argv):
        try:
            i=argv.index(row['runner'])
            return (argv[:i] in ([sys.executable,'-u'],[sys.executable])
                and argv[i+1:]==['--cli',row['cli'],'--sn',row['sn'],'--session-dir',row['session_dir']])
        except (ValueError,KeyError):return False
    return _process(row.get('pid'),row.get('starttime'),row['uid'],expected)


def _child(row):
    return _process(row.get('child_pid'),row.get('child_starttime'),row['uid'],
        lambda argv:argv==[row['cli'],'viz','--sn',row['sn'],'--no-zero','--no-open','--jsonl'])


def inspect_session(directory):
    directory,row=_read(directory)
    if not row:
        from official_viz_runner import _session_gate
        try:
            with _session_gate(directory):
                _,latest=_read(directory)
                if latest:return dict(cleanup_confirmed=False,runner_alive=_runner(latest),child_alive=_child(latest))
                return dict(cleanup_confirmed=True,runner_alive=False,child_alive=False)
        except ValueError:return dict(cleanup_confirmed=False,runner_alive=True,child_alive=False)
    runner=_runner(row);child=_child(row);confirmed=False
    if not runner and not child:
        from official_viz_runner import _session_gate
        try:
            with _session_gate(directory):
                _,latest=_read(directory)
                confirmed=not latest or latest.get('token')==row['token'] and not _runner(latest) and not _child(latest)
        except ValueError:pass
    return dict(cleanup_confirmed=confirmed,runner_alive=runner,child_alive=child,sn=row.get('sn'),token=row['token'])


def _signal(row,child,number):
    """Pin process identity with a pidfd before checking metadata and signalling."""
    pid=row.get('child_pid') if child else row.get('pid')
    if type(pid) is not int or pid<=1:return
    if not hasattr(os,'pidfd_open') or not hasattr(signal,'pidfd_send_signal'):
        raise ValueError('Exact Linux process signalling requires pidfd support')
    try:fd=os.pidfd_open(pid)
    except ProcessLookupError:return
    try:
        if (_child(row) if child else _runner(row)):
            try:signal.pidfd_send_signal(fd,number)
            except ProcessLookupError:pass
    finally:os.close(fd)


def stop_session(directory):
    directory,row=_read(directory)
    if not row:
        from official_viz_runner import _session_gate
        with _session_gate(directory):
            _,latest=_read(directory)
            if latest:raise ValueError('Visualization session appeared during cleanup; retry Stop')
            return dict(cleanup_confirmed=True,runner_alive=False,child_alive=False)
    if _runner(row):_signal(row,False,signal.SIGTERM)
    elif _child(row):
        # An orphan can exist only after an abnormal old runner. Verify its
        # start-time, UID and exact CLI argv before each individual signal.
        for number,wait in ((signal.SIGINT,5),(signal.SIGTERM,2),(signal.SIGKILL,1)):
            if not _child(row):break
            _signal(row,True,number)
            deadline=time.monotonic()+wait
            while time.monotonic()<deadline and _child(row):time.sleep(.05)
    deadline=time.monotonic()+11
    while time.monotonic()<deadline and (_runner(row) or _child(row)):time.sleep(.05)
    # Acquiring the runner lock also covers the startup interval before the
    # metadata was published, and confirms all profile/device leases released.
    from official_viz_runner import _session_gate
    with _session_gate(directory):
        if _runner(row) or _child(row):raise ValueError('Visualization process exit remains unconfirmed')
        path=directory/'session.json'
        if path.exists():
            latest=json.loads(path.read_text())
            if latest.get('token')!=row['token']:raise ValueError('Visualization session changed during cleanup')
            path.unlink()
    return dict(cleanup_confirmed=True,runner_alive=False,child_alive=False,sn=row.get('sn'))
