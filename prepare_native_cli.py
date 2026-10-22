"""Bundle the verified official CLI with app updates, without opening devices."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))
from native_cli import NativeCLIStore,PINNED_VERSION,ARCHIVE_NAME,ARCHIVE_SHA256,ARCHIVE_SIZE

LICENSE_NAME='LICENSE-wuji-cli.txt'
LICENSE_COMMIT='a54280f4a6d4cf43b37f551c35e9db04b6cf476d'
LICENSE_URL='https://raw.githubusercontent.com/wuji-technology/wuji-cli/'+LICENSE_COMMIT+'/LICENSE'
LICENSE_SHA256='463dfb867696523ae4f880fa6ee4b8155daa5a317a6d3162b5612d9ec4e00281'


def verify_license(path):
    """Retain exact upstream MIT text from the fixed CLI release commit."""
    if hashlib.sha256(path.read_bytes()).hexdigest()!=LICENSE_SHA256:
        raise ValueError('Bundled Windows CLI license differs from the fixed upstream release')


def verify():
    folder=ROOT/'runtime_payload/official-cli-native'
    path=folder/ARCHIVE_NAME
    with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if path.stat().st_size!=ARCHIVE_SIZE or digest!=ARCHIVE_SHA256:raise ValueError('Bundled Windows CLI does not match the pin')
    release=json.loads((folder/'release.json').read_text(encoding='utf-8'))
    NativeCLIStore()._asset(PINNED_VERSION,release)
    verify_license(ROOT/'src/official_data'/LICENSE_NAME)
    verify_license(folder/LICENSE_NAME)
    return dict(version=PINNED_VERSION,archive_sha256=digest,
                license_sha256=LICENSE_SHA256,license_source=LICENSE_URL)


def prepare(source=None):
    license_source=ROOT/'src/official_data'/LICENSE_NAME
    verify_license(license_source)
    store=NativeCLIStore(source)
    component=store._cached(store.root/PINNED_VERSION,PINNED_VERSION) if source else store.prepare()
    folder=ROOT/'runtime_payload/official-cli-native';folder.mkdir(parents=True,exist_ok=True)
    for name in (ARCHIVE_NAME,'release.json'):
        shutil.copyfile(Path(component['directory'])/name,folder/name)
    shutil.copyfile(license_source,folder/LICENSE_NAME)
    return verify()


if __name__=='__main__':
    source=sys.argv[sys.argv.index('--source-store')+1] if '--source-store' in sys.argv else None
    print(json.dumps(verify() if '--check' in sys.argv else prepare(source)))
