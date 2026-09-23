"""Install immutable, versioned controller source in the owned Linux runtime."""
import hashlib
import json
import os
from pathlib import Path
import threading
import time

_lock=threading.Lock()


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
    rows=[];h=hashlib.sha256()
    for rel,path in source_files(root):
        data=path.read_bytes();digest=hashlib.sha256(data).hexdigest()
        rows.append((rel,data,digest))
        h.update(rel.as_posix().encode()+b'\0'+digest.encode()+b'\n')
    version=h.hexdigest()[:16]
    target=f'/opt/hand-workbench/code/{version}'
    with _lock:
        marker=remote_files.path(target+'/bundle.json')
        marker.parent.mkdir(parents=True,exist_ok=True)
        expected={rel.as_posix():digest for rel,_,digest in rows}
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
