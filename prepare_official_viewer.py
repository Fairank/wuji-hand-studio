"""Prepare pinned, licensed Rerun web assets; never starts a device or Viewer."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from urllib.parse import urlsplit
import urllib.request

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / 'src' / 'official_viewer_manifest.json'
DESTINATION = ROOT / 'runtime_payload' / 'official-viewer'


def verify(folder=DESTINATION):
    spec = json.loads(MANIFEST.read_text(encoding='utf-8'))
    for name, row in spec['files'].items():
        file = folder / name
        if file.is_symlink() or not file.is_file():
            raise ValueError('Original Viewer resources missing. Run prepare_official_viewer.py first.')
        body = file.read_bytes()
        if len(body) != row['bytes'] or hashlib.sha256(body).hexdigest() != row['sha256']:
            raise ValueError('Original Viewer asset checksum mismatch: ' + name)
    return spec


def prepare():
    spec = json.loads(MANIFEST.read_text(encoding='utf-8'))
    DESTINATION.mkdir(parents=True, exist_ok=True)
    for name, row in spec['files'].items():
        if '/' in name or '\\' in name or name in {'.', '..'}:
            raise ValueError('Invalid Viewer asset name')
        target = DESTINATION / name
        if target.is_file() and not target.is_symlink():
            body = target.read_bytes()
            if len(body) == row['bytes'] and hashlib.sha256(body).hexdigest() == row['sha256']:
                continue
        source = urlsplit(row['url'])
        if source.scheme != 'https' or source.hostname not in {'app.rerun.io', 'raw.githubusercontent.com'}:
            raise ValueError('Unexpected official Viewer origin')
        request = urllib.request.Request(row['url'], headers={'User-Agent': 'HandWorkbench-build'})
        with tempfile.TemporaryDirectory(prefix='.viewer-', dir=DESTINATION) as temporary:
            staged = Path(temporary) / name
            size = 0
            with urllib.request.urlopen(request, timeout=60) as response, staged.open('wb') as output:
                final = urlsplit(response.url)
                if final.scheme != source.scheme or final.hostname != source.hostname:
                    raise ValueError('Unexpected Viewer asset redirect')
                while block := response.read(1024 * 1024):
                    size += len(block)
                    if size > row['bytes']:
                        raise ValueError('Viewer asset exceeds pinned size')
                    output.write(block)
            if size != row['bytes'] or hashlib.sha256(staged.read_bytes()).hexdigest() != row['sha256']:
                raise ValueError('Original Viewer download checksum mismatch')
            if target.is_symlink():
                raise ValueError('Refusing a redirected Viewer destination')
            staged.replace(target)
    return verify()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Verify existing files without networking')
    args = parser.parse_args()
    result = verify() if args.check else prepare()
    print(json.dumps(dict(ok=True, viewer=result['version'], files=len(result['files']),
                          device_actions=False)))
