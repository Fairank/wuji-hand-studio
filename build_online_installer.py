"""Build a small, hash-pinned Inno entry. Never download, execute, or install."""
import hashlib
from pathlib import Path
import re
import subprocess

REPOSITORY = 'Fairank/wuji-hand-studio'
VERSION = re.compile(r'(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})\Z')


def build(root, version, iscc, zh_language):
    root = Path(root).resolve(strict=True)
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError('A canonical three-part version is required')
    name = f'HandWorkbench-{version}-windows-x64-offline-setup.exe'
    payload = root/'dist'/name
    if payload.is_symlink() or payload.is_junction() or not payload.is_file():
        raise ValueError('A plain complete offline installer is required')
    size = payload.stat().st_size
    if not 2 < size <= 1024**3:
        raise ValueError('Offline installer size is invalid')
    with payload.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError('Offline installer must be a Windows executable')
        stream.seek(0)
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    defines = dict(AppVersion=version, PayloadName=name,
        PayloadUrl=f'https://github.com/{REPOSITORY}/releases/download/v{version}/{name}',
        PayloadSha256=digest, PayloadBytes=size, OutputDir=root/'dist',
        OutputName=f'HandWorkbench-{version}-windows-x64-setup',
        IconFile=root/'src/WujiStudio.ico', ZhIsl=Path(zh_language).resolve(strict=True))
    # No environment/config/CLI option may redirect production downloads or
    # enable the compile-time transport-only QA variant.
    subprocess.run([str(Path(iscc).resolve(strict=True)),
        *[f'/D{key}={value}' for key,value in defines.items()],
        str(root/'installer/online.iss')],check=True)
    entry = root/'dist'/(str(defines['OutputName'])+'.exe')
    if not entry.is_file() or entry.stat().st_size > 5*1024**2:
        raise ValueError('Online entry is missing or unexpectedly large')
    with entry.open('rb') as stream:
        entry_digest = hashlib.file_digest(stream,'sha256').hexdigest()
    entry.with_name(entry.name+'.sha256').write_text(entry_digest+'  '+entry.name+'\n',encoding='ascii')
    return dict(installer=str(entry),sha256=entry_digest,bytes=entry.stat().st_size,
        offline_installer=str(payload),offline_sha256=digest,offline_bytes=size,
        offline_url=defines['PayloadUrl'])
