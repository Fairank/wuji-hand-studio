"""Vendor the existing xterm terminal renderer; no CLI or device is started."""
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
PACKAGES = {'xterm': ('5.5.0', 'https://registry.npmjs.org/@xterm%2fxterm/5.5.0',
    'sha512-hqJHYaQb5OptNunnyAnkHyM8aCjZ1MEIDTQu1iIbbTD/xops91NB5yq1ZK/dC2JDbVWtF23zUtl9JE2NqwT87A=='),
    'addon-fit': ('0.10.0', 'https://registry.npmjs.org/@xterm%2faddon-fit/0.10.0', 'sha512-UFYkDm4HUahf2lnEyHvio51TNGiLK66mqP2JoATy7hRZeXaGMRDr00JiSF7m63vR5WKATF605yEggJKsw0JpMQ==')}


def verify():
    manifest = json.loads((ROOT/'src/official_terminal_manifest.json').read_text(encoding='utf-8'))
    if manifest['versions'] != {name: value[0] for name, value in PACKAGES.items()}:
        raise ValueError('Unexpected terminal component versions')
    for relative, digest in manifest['files'].items():
        file = ROOT/'src/web/vendor/terminal'/relative
        if hashlib.sha256(file.read_bytes()).hexdigest() != digest:
            raise ValueError('Terminal renderer integrity check failed: '+relative)
    return manifest


def prepare():
    target = ROOT/'src/web/vendor/terminal'
    target.mkdir(parents=True, exist_ok=True)
    manifest = dict(versions={name: v[0] for name,v in PACKAGES.items()}, files={}, packages={})
    for name, (version, metadata_url, expected) in PACKAGES.items():
        with urlopen(metadata_url, timeout=25) as response:
            metadata = json.loads(response.read(1024*1024))
        if metadata['version'] != version:
            raise ValueError('Terminal package version mismatch')
        dist = metadata['dist']
        integrity = dist['integrity']
        if expected is not None and integrity != expected:
            raise ValueError('Terminal package integrity differs from pin')
        if not dist['tarball'].startswith('https://registry.npmjs.org/@xterm/') or not integrity.startswith('sha512-'):
            raise ValueError('Unexpected terminal package origin')
        with urlopen(dist['tarball'], timeout=30) as response:
            data = response.read(8*1024*1024+1)
        if len(data)>8*1024*1024 or base64.b64encode(hashlib.sha512(data).digest()).decode() != integrity[7:]:
            raise ValueError('Terminal package download failed integrity verification')
        files = {'package/lib/'+name+'.js':name+'.js', 'package/LICENSE':name+'-LICENSE'}
        if name=='xterm':files['package/css/xterm.css']='xterm.css'
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            for member, destination in files.items():
                entry = archive.getmember(member)
                if not entry.isfile() or entry.size>4*1024*1024:
                    raise ValueError('Invalid terminal package member')
                content = archive.extractfile(entry).read()
                (target/destination).write_bytes(content)
                manifest['files'][destination] = hashlib.sha256(content).hexdigest()
        manifest['packages'][name] = dict(version=version, metadata_url=metadata_url,
            tarball=dist['tarball'], integrity=integrity, license=metadata['license'])
    (ROOT/'src/official_terminal_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return verify()


if __name__=='__main__':
    result=verify() if '--check' in sys.argv else prepare()
    print(json.dumps(dict(ok=True,versions=result['versions'],files=len(result['files']))))
