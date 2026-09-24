"""Prepare an isolated, hash-pinned Linux optimizer; no SDK/device calls."""
import hashlib,json,os,platform,subprocess,sys
from pathlib import Path
from solver_session import environment

ROOT=Path(__file__).resolve().parent
LOCK=ROOT/'official_data/solver/requirements-linux-x86_64-py312.txt'

def status():
    target=environment();marker=target/'ready.json'
    supported=sys.platform.startswith('linux') and platform.machine() in ('x86_64','AMD64') and sys.version_info[:2]==(3,12)
    result=dict(ready=False,supported=supported,engine='official_open',python=str(target/'bin/python'))
    if marker.is_file():
        record=json.loads(marker.read_text())
        result['ready']=supported and record.get('lock_sha256')==hashlib.sha256(LOCK.read_bytes()).hexdigest() and (target/'bin/python').is_file()
    return result

def install():
    import fcntl
    if not status()['supported']:raise ValueError('This verified solver package requires Linux x86_64 Python 3.12')
    target=environment();target.parent.mkdir(parents=True,exist_ok=True)
    with (target.parent/'official-solver-0212.lock').open('w') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if status()['ready']:return status()
        subprocess.run([sys.executable,'-m','venv',str(target)],check=True,stdout=sys.stderr,timeout=90)
        subprocess.run([str(target/'bin/python'),'-m','pip','install','--disable-pip-version-check','--only-binary=:all:',
                        '--require-hashes','-r',str(LOCK)],check=True,stdout=sys.stderr,timeout=900)
        subprocess.run([str(target/'bin/python'),'-c','import pinocchio,nlopt,numpy,scipy,yaml'],check=True,stdout=sys.stderr,timeout=40)
        temp=target/'ready.pending';temp.write_text(json.dumps(dict(lock_sha256=hashlib.sha256(LOCK.read_bytes()).hexdigest())))
        temp.replace(target/'ready.json')
        return status()

if __name__=='__main__':
    try:print(json.dumps(dict(ok=True,**(install() if sys.argv[1:] == ['install'] else status()))))
    except Exception as error:print(json.dumps(dict(ok=False,ready=False,error=str(error)[:600])));sys.exit(1)
