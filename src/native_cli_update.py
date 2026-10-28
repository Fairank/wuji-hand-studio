"""Managed stable Windows CLI releases using the existing official release store.

Only app-owned immutable versions are written. No self-update, global Skills,
PATH modification, SDK user migration, device command or active-version switch.
The OfficialCLI coordinator alone owns the idle reservation and pointer switch.
"""
from datetime import date,datetime,timezone
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import shutil
import tempfile
import zipfile

from cli_release_store import CLIReleaseStore,MAX_ARCHIVE,MAX_METADATA,REPOSITORY,REPO_URL,ReleaseStoreError
from native_cli import NativeCLIStore,PINNED_VERSION,MAX_MEMBERS,_default_folder,_redirect,_hash


def version(value):
    if not isinstance(value,str):raise ReleaseStoreError('Invalid stable CLI version')
    value=value.removeprefix('v')
    if not re.fullmatch(r'20\d{2}\.\d{1,2}\.\d{1,2}',value):raise ReleaseStoreError('Invalid stable CLI version')
    try:date(*map(int,value.split('.')))
    except ValueError as error:raise ReleaseStoreError('Invalid stable CLI date') from error
    return value


def read_json(path):
    if _redirect(path) or path.stat().st_size>MAX_METADATA:raise ReleaseStoreError('Invalid native CLI metadata')
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict):raise ReleaseStoreError('Invalid native CLI metadata')
    return value


def selection(folder=None):
    root=Path(folder if folder is not None else _default_folder())
    for path in (root,*root.parents):
        if _redirect(path):raise ReleaseStoreError('Native CLI selection path is redirected')
    try:row=read_json(root/'selection.json')
    except FileNotFoundError:return {}
    for key in ('current','previous'):
        item=row.get(key)
        if item is not None and (not isinstance(item,dict) or version(item.get('version'))!=item.get('version')
                or any(not isinstance(item.get(k),str) or not re.fullmatch('[a-f0-9]{64}',item[k]) for k in ('archive_sha256','binary_sha256'))):
            raise ReleaseStoreError('Invalid selected native CLI identity')
    return row


def version_info(value,*,folder=None,strict=False):
    value=version(value)
    store=NativeCLIStore(folder) if value==PINNED_VERSION else ManagedNativeCLIStore(folder)
    store._safe_root()
    return store._cached(store.root/value,value,strict=strict)


def pointer(info):
    return {key:info[key] for key in ('version','archive_sha256','binary_sha256')}


class ManagedNativeCLIStore(NativeCLIStore):
    def __init__(self,folder=None,*,progress=None,**options):
        super().__init__(folder,**options);self.progress=progress

    def _asset(self,value,release):
        value=version(value);tag='v'+value
        # HTML without an explicit stability signal cannot authorize installing.
        if (not isinstance(release,dict) or release.get('tag_name')!=tag
                or release.get('html_url')!=REPO_URL+'/releases/tag/'+tag
                or release.get('draft') is not False or release.get('prerelease') is not False):
            raise ReleaseStoreError('A published stable official CLI release is required')
        assets=release.get('assets');name='wuji_'+value+'_x86_64-pc-windows-msvc.zip'
        if not isinstance(assets,list) or len(assets)>200:raise ReleaseStoreError('Invalid official release assets')
        matches=[a for a in assets if isinstance(a,dict) and a.get('name')==name]
        if len(matches)!=1:raise ReleaseStoreError('Release must list one Windows x86_64 archive')
        asset=dict(size=None,**matches[0]) if 'size' not in matches[0] else dict(matches[0])
        self._asset_url(asset,tag)
        return asset

    def _cached_full(self,directory,value):
        manifest=read_json(directory/'manifest.json');release=read_json(directory/'release.json')
        asset=self._asset(value,release);digest=asset.get('digest')
        if not isinstance(digest,str) or not re.fullmatch(r'sha256:[a-f0-9]{64}',digest):raise ReleaseStoreError('Native CLI stored release digest is invalid')
        expected=dict(schema=2,platform='windows-x86_64',repository=REPOSITORY,version=value,
            tag='v'+value,asset_url=asset['browser_download_url'],archive_sha256=digest[7:],status='prepared',validated=True)
        if any(manifest.get(k)!=v for k,v in expected.items()):raise ReleaseStoreError('Native CLI identity differs from its stable release')
        archive=directory/asset['name']
        if (not archive.is_file() or _hash(archive)!=digest[7:] or archive.stat().st_size!=manifest.get('archive_size')
                or asset.get('size') not in (None,archive.stat().st_size)):
            raise ReleaseStoreError('Native CLI archive failed integrity verification')
        files=manifest.get('files');paths=manifest.get('paths')
        if not isinstance(files,list) or not 0<len(files)<=MAX_MEMBERS or not isinstance(paths,dict):raise ReleaseStoreError('Native CLI payload manifest is invalid')
        expected_files={}
        for entry in files:
            if not isinstance(entry,dict):raise ReleaseStoreError('Native CLI payload manifest is invalid')
            relative=self._member_path(entry.get('path'));name=relative.as_posix();file=directory/'payload'/Path(*relative.parts)
            if (name in expected_files or not file.is_file() or file.stat().st_size!=entry.get('size') or _hash(file)!=entry.get('sha256')):
                raise ReleaseStoreError('Native CLI payload failed integrity verification')
            expected_files[name]=entry
        # Bind every payload file to the verified archive, including future DLLs.
        with zipfile.ZipFile(archive) as source:
            actual={}
            for entry in source.infolist():
                if entry.is_dir():continue
                name=self._member_path(entry.filename).as_posix()
                if name in actual or name not in expected_files or entry.file_size!=expected_files[name]['size']:raise ReleaseStoreError('Native CLI archive inventory differs')
                with source.open(entry) as stream:actual[name]=hashlib.file_digest(stream,'sha256').hexdigest()
            if actual!={k:v['sha256'] for k,v in expected_files.items()}:raise ReleaseStoreError('Native CLI archive and payload differ')
        found={p.relative_to(directory/'payload').as_posix() for p in (directory/'payload').rglob('*') if p.is_file()}
        binaries=[name for name in found if PurePosixPath(name).name.casefold()=='wuji.exe']
        if (found!=set(expected_files) or len(binaries)!=1 or paths!=dict(archive=asset['name'],payload='payload',binary='payload/'+binaries[0])
                or manifest.get('binary_sha256')!=expected_files[binaries[0]]['sha256']):
            raise ReleaseStoreError('Native CLI executable inventory is invalid')
        return dict(manifest,directory=str(directory),binary=str(directory/paths['binary']))

    def prepare(self,value,*,release=None,archive=None,strict=False):
        value=version(value);self._safe_root();self._check_cancel()
        with self._locked():
            final=self.root/value
            if final.exists() or _redirect(final):return self._cached(final,value,strict=strict)
            release=self.fetch_release(value) if release is None else json.loads(json.dumps(release))
            if len(json.dumps(release).encode())>MAX_METADATA:raise ReleaseStoreError('Official release metadata exceeds its limit')
            asset=self._asset(value,release);digest,source=self._expected_sha(release,asset,'v'+value)
            stage=Path(tempfile.mkdtemp(prefix='.prepare-'+value+'-',dir=self.root))
            try:
                candidate=stage/asset['name']
                if archive is None:
                    with candidate.open('xb') as output:
                        actual=self._read_http(asset['browser_download_url'],limit=MAX_ARCHIVE,destination=output,expected_size=asset.get('size'))
                        output.flush();os.fsync(output.fileno())
                else:
                    local=Path(archive).absolute()
                    if _redirect(local) or not local.is_file() or not 0<local.stat().st_size<=MAX_ARCHIVE:raise ReleaseStoreError('Invalid local official archive')
                    with local.open('rb') as stream,candidate.open('xb') as output:shutil.copyfileobj(stream,output)
                    actual=_hash(candidate)
                if actual!=digest or asset.get('size') not in (None,candidate.stat().st_size):raise ReleaseStoreError('Native CLI archive failed SHA-256 verification')
                if self.progress:self.progress(dict(stage='verifying',received_bytes=candidate.stat().st_size,total_bytes=candidate.stat().st_size))
                files,binary=self._extract(candidate,stage/'payload')
                evidence=self._validate_software(stage/'payload'/binary,stage,value,calibration=True)
                for entry in release['assets']:
                    if isinstance(entry,dict) and entry.get('name')==asset['name']:entry['digest']='sha256:'+digest
                self._write_json(stage/'release.json',release)
                manifest=dict(schema=2,platform='windows-x86_64',repository=REPOSITORY,version=value,tag='v'+value,
                    release_url=release['html_url'],asset_url=asset['browser_download_url'],archive_sha256=digest,
                    archive_size=candidate.stat().st_size,checksum_source=source,binary_sha256=_hash(stage/'payload'/binary),files=files,
                    paths=dict(archive=asset['name'],payload='payload',binary='payload/'+binary),status='prepared',validated=True,
                    compatibility_checked=True,software_checks=evidence,device_commands_executed=False,prepared_at=datetime.now(timezone.utc).isoformat())
                self._write_json(stage/'manifest.json',manifest);self._check_cancel()
                os.replace(stage,final)
                return self._cached(final,value,strict=True)
            finally:
                if stage.exists():
                    if stage.parent!=self.root or not stage.name.startswith('.prepare-') or _redirect(stage) or stage.resolve().parent!=self.root.resolve():raise ReleaseStoreError('Unsafe native CLI staging cleanup path')
                    shutil.rmtree(stage)
