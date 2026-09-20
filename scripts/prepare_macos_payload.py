"""Build-time download of pinned public Linux/Lima components, never user VM state."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'runtime_payload/macos'
LIMA = 'https://github.com/lima-vm/lima/releases/download/v2.2.0/lima-2.2.0-Darwin-arm64.tar.gz'
LIMA_SHA = 'bbdef91774885a0d05f7b048c4eb89ae2bcf3a0c252ae7ca7934e63df76d93c3'
IMAGE = 'https://cloud-images.ubuntu.com/releases/noble/release-20260705/ubuntu-24.04-server-cloudimg-arm64.img'
IMAGE_SHA = '7df0201546f75b8bcc1044594c806c35749421ad3c9bc1be2a3ab806cfae39cc'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def download(url, dest, sha):
    if dest.is_file() and digest(dest) == sha:
        return
    pending = dest.with_suffix(dest.suffix + '.pending')
    with urllib.request.urlopen(url, timeout=90) as src, pending.open('wb') as target:
        shutil.copyfileobj(src, target, 1024 * 1024)
    if digest(pending) != sha:
        raise ValueError('Public dependency checksum mismatch: ' + dest.name)
    pending.replace(dest)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    download(LIMA, OUT / 'lima.tar.gz', LIMA_SHA)
    download(IMAGE, OUT / 'ubuntu.qcow2', IMAGE_SHA)
    download('https://github.com/wuji-technology/wuji-cli/releases/download/v2026.8.31/wuji_2026.8.31_aarch64-unknown-linux-gnu.tar.gz',
             OUT / 'wuji-cli.tar.gz', 'c8ad80fe7fb39bc51d79206a13aa9da239648316c2a27681f38208c631fc1efa')
    lima = OUT / 'lima'
    lima.mkdir(exist_ok=True)
    with tarfile.open(OUT / 'lima.tar.gz') as archive:
        archive.extractall(lima, filter='data')
    # A raw image avoids asking the installed app to find Homebrew/qemu-img.
    subprocess.run(['qemu-img', 'convert', '-f', 'qcow2', '-O', 'raw', str(OUT / 'ubuntu.qcow2'), str(OUT / 'ubuntu.raw')], check=True)
    (OUT / 'lima.tar.gz').unlink()
    (OUT / 'ubuntu.qcow2').unlink()
    # Preserve the helper's public Apple Virtualization entitlement when ad-hoc signing.
    entitlements = ROOT / 'scripts/macos-vz.entitlements'
    for path in (lima / 'bin').iterdir():
        if path.is_file() and not path.is_symlink():
            subprocess.run(['codesign', '--force', '--sign', '-', '--entitlements', str(entitlements), str(path)], check=True)
    files = {p.relative_to(OUT).as_posix(): digest(p) for p in OUT.rglob('*') if p.is_file() and not p.is_symlink() and p.name != 'manifest.json'}
    m = dict(schema=1, payload_id='mac-linux-1.0.0', arch='aarch64', sdk_version='2026.8.31',
             lima_version='2.2.0', image='ubuntu.raw', files=files, first_setup_requires_internet=True,
             sources=[dict(url=LIMA, sha256=LIMA_SHA), dict(url=IMAGE, sha256=IMAGE_SHA)])
    (OUT / 'manifest.json').write_text(json.dumps(m, indent=2), encoding='utf-8')
    print(json.dumps(dict(payload=m['payload_id'], files=len(files), image_bytes=(OUT / 'ubuntu.raw').stat().st_size)))


if __name__ == '__main__':
    main()
