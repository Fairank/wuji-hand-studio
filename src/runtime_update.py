"""Apply only public controller files from a hash-verified owned runtime image."""
import hashlib,json,os,pathlib,subprocess,sys,tarfile

PREFIXES=('opt/hand-workbench/','opt/hand-workbench-venv/lib/python3.12/site-packages/zenoh/','opt/hand-workbench-venv/lib/python3.12/site-packages/eclipse_zenoh-1.9.0.dist-info/')
def files_from_image(image,digest):
    h=hashlib.sha256()
    with open(image,'rb') as stream:
        for b in iter(lambda:stream.read(1024*1024),b''):h.update(b)
    if h.hexdigest()!=digest:raise ValueError('Runtime image integrity check failed')
    selected={}
    with tarfile.open(image) as archive:
        for m in archive:
            p=pathlib.PurePosixPath(m.name)
            if p.is_absolute() or '..' in p.parts:raise ValueError('Unsafe archive path')
            name=str(p)
            if not name.startswith(PREFIXES) or not m.isfile():continue
            if name=='opt/hand-workbench/motion_parameters.py' or '/sessions/' in name:continue
            if name in selected:raise ValueError('Duplicate controller member')
            if m.size>64*1024*1024:raise ValueError('Controller member is too large')
            selected[name]=archive.extractfile(m).read()
    if 'opt/hand-workbench/device_discovery.py' not in selected:raise ValueError('Controller discovery component missing')
    return selected

def apply(image,digest,root=pathlib.Path('/')):
    selected=files_from_image(image,digest);root=root.resolve()
    if root==pathlib.Path('/'):
        if os.environ.get('WSL_DISTRO_NAME')!='HandWorkbenchControl':raise ValueError('Unexpected control environment')
        for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
            try:args=p.read_bytes().split(b'\0')
            except OSError:continue
            if any(a.endswith(b'/console_agent.py') for a in args):raise ValueError('Finish the active device session before upgrading')
    controller=root/'opt/hand-workbench'
    backup=controller/'upgrade-backup-1.0.1'
    if not backup.resolve().is_relative_to(controller):raise ValueError('Backup resolves outside controller')
    # Validate every destination before writing any controller component.
    for name in selected:
        allowed=controller if name.startswith('opt/hand-workbench/') else root/'opt/hand-workbench-venv'
        if not (root/name).resolve().is_relative_to(allowed):raise ValueError('Target resolves outside controller')
        if not (backup/name).resolve().is_relative_to(backup):raise ValueError('Backup target escapes controller')
    backup.mkdir(parents=True,exist_ok=True)
    changed=[]
    try:
        for name,data in selected.items():
            dest=root/name
            if dest.is_file() and dest.read_bytes()==data:continue
            old=dest.read_bytes() if dest.is_file() else None
            saved=backup/name
            if old is not None and not saved.exists():saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(old)
            dest.parent.mkdir(parents=True,exist_ok=True)
            staged=dest.with_name(dest.name+'.upgrade-pending');staged.write_bytes(data);staged.replace(dest)
            changed.append((dest,old))
            if name.startswith('opt/hand-workbench/') and root==pathlib.Path('/'):os.chown(dest,1000,1000)
        if root==pathlib.Path('/'):
            subprocess.run(['/opt/hand-workbench-venv/bin/python','-c',"import sys;sys.path.insert(0,'/opt/hand-workbench');import zenoh,console_agent,device_discovery;print('controller_updated')"],check=True,timeout=25)
    except Exception:
        for dest,old in reversed(changed):
            if old is None:dest.unlink(missing_ok=True)
            else:dest.write_bytes(old)
        raise
    return dict(ok=True,updated_files=len(changed),parameters_preserved=True,motion_started=False)

if __name__=='__main__':print(json.dumps(apply(sys.argv[1],sys.argv[2])))
