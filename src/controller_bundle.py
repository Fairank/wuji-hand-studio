"""Install immutable, versioned controller source in the owned Linux runtime."""
import hashlib
import json
import os
from pathlib import Path
import threading
import time

_lock=threading.Lock()

# Run only in the owned WSL interpreter. All inputs are data on stdin; this
# verifier imports stdlib only and never imports a controller or the SDK.
VERIFY_BUNDLE_SCRIPT=r'''
import hashlib,json,sys
from pathlib import Path,PurePosixPath

def verify_tree(root,scope,target,expected):
    root=Path(root);scope=Path(scope);target=Path(target)
    if root.resolve()!=scope or not root.is_dir():raise ValueError('Controller root changed during verification')
    def scoped(path):
        resolved=path.resolve()
        if not resolved.is_relative_to(scope):raise ValueError('Controller path is a link outside its directory')
        return resolved
    scoped(target);marker=target/'bundle.json';installed_marker=scoped(marker)
    try:marker.lstat()
    except FileNotFoundError:
        if root.resolve()!=scope:raise ValueError('Controller root changed during verification')
        return False
    if not installed_marker.is_file() or json.loads(installed_marker.read_text(encoding='utf8'))!=expected:
        raise ValueError('Controller bundle marker does not match packaged source')
    for rel,digest in expected.items():
        p=PurePosixPath(rel)
        if not isinstance(rel,str) or not rel or p.is_absolute() or '..' in p.parts or p.as_posix()!=rel or '\\' in rel or '\0' in rel:
            raise ValueError('Invalid controller bundle member')
        candidate=target/rel;installed=scoped(candidate)
        if not installed.is_file() or hashlib.sha256(installed.read_bytes()).hexdigest()!=digest:
            raise ValueError('Controller bundle file does not match packaged source: '+rel)
        if scoped(candidate)!=installed:raise ValueError('Controller bundle path changed during verification')
    if scoped(marker)!=installed_marker or root.resolve()!=scope:raise ValueError('Controller root changed during verification')
    return True

if __name__=='__main__':
    try:
        value=json.load(sys.stdin)
        verified=verify_tree('/opt/hand-workbench',value['scope'],value['target'],value['expected'])
        print(json.dumps({'version':1,'status':'verified' if verified else 'missing','target':value['target']}))
    except Exception as error:
        print(type(error).__name__+': '+str(error),file=sys.stderr)
        raise SystemExit(2)
'''


def source_files(root):
    root=Path(root)
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.is_symlink():continue
        rel=path.relative_to(root)
        if any(part in ('web','__pycache__','sessions') for part in rel.parts):continue
        if path.name.startswith('test_'):continue
        yield rel,path


def ensure_version(root, remote_files):
    """remote_files exposes path() within /opt/hand-workbench. Never overwrite a running version."""
    if not Path(root).is_dir():raise ValueError('Controller source directory does not exist')
    rows=[];h=hashlib.sha256()
    for rel,path in source_files(root):
        data=path.read_bytes();digest=hashlib.sha256(data).hexdigest()
        rows.append((rel,data,digest))
        h.update(rel.as_posix().encode()+b'\0'+digest.encode()+b'\n')
    if not rows:raise ValueError('Controller source directory is empty')
    version=h.hexdigest()[:16]
    target=f'/opt/hand-workbench/code/{version}'
    with _lock:
        expected={rel.as_posix():digest for rel,_,digest in rows}
        verify=getattr(remote_files,'verify_existing_bundle',None)
        if callable(verify):
            verified=verify(target,expected)
            if verified is True:return target
            if verified is not False and verified is not None:raise ValueError('Invalid controller bundle verification result')
        marker=remote_files.path(target+'/bundle.json')
        marker.parent.mkdir(parents=True,exist_ok=True)
        if marker.exists():
            if json.loads(marker.read_text(encoding='utf-8'))!=expected:
                raise ValueError('Controller bundle marker does not match packaged source')
            for rel,_,digest in rows:
                installed=remote_files.path(target+'/'+rel.as_posix())
                if not installed.is_file() or hashlib.sha256(installed.read_bytes()).hexdigest()!=digest:
                    raise ValueError('Controller bundle file does not match packaged source: '+rel.as_posix())
            return target
        for rel,data,digest in rows:
            dest=remote_files.path(target+'/'+rel.as_posix())
            dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists():
                if hashlib.sha256(dest.read_bytes()).hexdigest()!=digest:
                    raise ValueError('Incomplete controller bundle has mismatched file: '+rel.as_posix())
            else:
                with dest.open('xb') as out:out.write(data)
        for rel,_,digest in rows:
            if hashlib.sha256(remote_files.path(target+'/'+rel.as_posix()).read_bytes()).hexdigest()!=digest:
                raise ValueError('Controller bundle verification failed')
        with marker.open('x',encoding='utf-8') as out:json.dump(expected,out,sort_keys=True)
        return target
