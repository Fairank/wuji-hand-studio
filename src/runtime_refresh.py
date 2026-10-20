"""Upgrade only the owned WSL controller's SDK and CLI from a verified image.

SDK environments are staged and swapped, with the prior environment retained.
No user profiles, parameters, firmware or devices are modified.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import time
import venv

SDK_VERSION='2026.10.12'
ENV='opt/hand-workbench-venv'
CLI='usr/local/bin/wuji'


def image_members(image,digest):
    with open(image,'rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest()!=digest:
            raise ValueError('Runtime image integrity check failed')
    selected={};total=0
    with tarfile.open(image) as archive:
        for member in archive:
            p=PurePosixPath(member.name)
            if p.is_absolute() or '..' in p.parts:
                raise ValueError('Unsafe archive path')
            name=str(p)
            if name!=CLI and not name.startswith(ENV+'/'):
                continue
            if member.isdir():continue
            if member.issym() and name in {ENV+'/bin/python',ENV+'/bin/python3',ENV+'/bin/python3.12',ENV+'/lib64'}:
                continue  # Created by the local system Python, never extracted.
            if not member.isfile():raise ValueError('Unexpected link in SDK environment')
            if name in selected:raise ValueError('Duplicate SDK member')
            total+=member.size
            if member.size>128*1024**2 or total>700*1024**2 or len(selected)>20000:
                raise ValueError('SDK payload exceeds supported size')
            selected[name]=member
    expected=ENV+'/lib/python3.12/site-packages/wuji_sdk-'+SDK_VERSION+'.dist-info/METADATA'
    if CLI not in selected or expected not in selected:
        raise ValueError('Expected SDK and CLI payload missing')
    return selected


def assert_idle(root):
    if root!=Path('/'):return
    if os.environ.get('WSL_DISTRO_NAME')!='HandWorkbenchControl':
        raise ValueError('Unexpected control environment')
    for path in Path('/proc').glob('[0-9]*/cmdline'):
        try:args=path.read_bytes().split(b'\0')
        except OSError:continue
        if any(a.endswith((b'/console_agent.py',b'/agent_bootstrap.py',b'/calibration_runner.py')) for a in args):
            raise ValueError('Finish active device and calibration sessions before upgrading')


def apply(image,digest,root=Path('/')):
    root=Path(root).resolve();assert_idle(root)
    selected=image_members(image,digest)
    token=str(time.time_ns())
    current=root/ENV;staged=current.with_name(current.name+'.pending-'+token)
    previous=current.with_name(current.name+'.backup-'+token)
    cli=root/CLI;cli_pending=cli.with_name('wuji.pending-'+token)
    cli_previous=cli.with_name('wuji.backup-'+token)
    for path in (current,staged,previous,cli,cli_pending,cli_previous):
        if path.is_symlink() or not path.parent.resolve().is_relative_to(root):
            raise ValueError('Runtime destination is redirected')
    if (current.exists() and not current.is_dir()) or (cli.exists() and not cli.is_file()):raise ValueError('Invalid controller component path')
    current.parent.mkdir(parents=True,exist_ok=True);cli.parent.mkdir(parents=True,exist_ok=True)
    env_swapped=cli_swapped=False
    try:
        venv.EnvBuilder(with_pip=False,symlinks=True).create(staged)
        with tarfile.open(image) as archive:
            for name,member in selected.items():
                destination=cli_pending if name==CLI else staged/PurePosixPath(name).relative_to(ENV)
                if not destination.resolve().is_relative_to(staged) and destination!=cli_pending:
                    raise ValueError('SDK target escapes staging directory')
                destination.parent.mkdir(parents=True,exist_ok=True)
                with archive.extractfile(member) as source,destination.open('xb' if name==CLI else 'wb') as output:
                    shutil.copyfileobj(source,output)
                destination.chmod(member.mode & 0o755)
        command=[str(staged/'bin/python'),'-c',
                 "import importlib.metadata,wuji_sdk,numpy,zenoh;assert importlib.metadata.version('wuji-sdk')=='"+SDK_VERSION+"'"]
        environment={**os.environ,'WUJI_TELEMETRY_DISABLE':'1'}
        subprocess.run(command,check=True,timeout=30,env=environment,capture_output=True)
        result=subprocess.run([str(cli_pending),'--version'],check=True,timeout=15,capture_output=True,text=True,env=environment)
        if SDK_VERSION not in result.stdout:raise ValueError('CLI version mismatch')
        assert_idle(root)
        if current.exists():current.rename(previous)
        try:staged.rename(current);env_swapped=True
        except BaseException:
            if previous.exists():previous.rename(current)
            raise
        if cli.exists():cli.rename(cli_previous)
        try:cli_pending.rename(cli);cli_swapped=True
        except BaseException:
            if cli_previous.exists():cli_previous.rename(cli)
            raise
        subprocess.run([str(current/'bin/python'),'-c',command[-1]],check=True,timeout=30,env=environment,capture_output=True)
        return dict(ok=True,sdk_version=SDK_VERSION,cli_version=SDK_VERSION,
                    sdk_backup=str(previous) if previous.exists() else None,cli_backup=str(cli_previous) if cli_previous.exists() else None,parameters_preserved=True,motion_started=False)
    except BaseException:
        if cli_swapped:
            cli.rename(cli_pending)
            if cli_previous.exists():cli_previous.rename(cli)
        if env_swapped:
            current.rename(staged)
            if previous.exists():previous.rename(current)
        raise
    finally:
        if staged.exists() and staged.parent==current.parent and not staged.is_symlink():shutil.rmtree(staged)
        cli_pending.unlink(missing_ok=True)


if __name__=='__main__':print(json.dumps(apply(sys.argv[1],sys.argv[2])))
