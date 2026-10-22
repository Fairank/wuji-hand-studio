"""Pinned Windows CLI preparation, separate from the Linux CLI selection.

Importing performs no filesystem, network or process work. ``prepare`` may
download the public release and runs only version/help checks, never discovery
or a device command. ``resolve`` verifies local files without running anything.
"""
from datetime import datetime, timezone
from collections import OrderedDict
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import zipfile

from cli_release_store import CLIReleaseStore, MAX_METADATA, REPOSITORY, REPO_URL, ReleaseStoreError


PINNED_VERSION = '2026.10.12'
ARCHIVE_NAME = 'wuji_2026.10.12_x86_64-pc-windows-msvc.zip'
ASSET_URL = REPO_URL + '/releases/download/v' + PINNED_VERSION + '/' + ARCHIVE_NAME
# Official GitHub release asset digest, also verified against the downloaded ZIP.
ARCHIVE_SHA256 = '1c9a902386432c9afda160c28a438f4710e74f3c0329f2b9d52216456747bfe0'
ARCHIVE_SIZE = 29993749
BINARY_SHA256 = '5a2325310040085bb3cb93d3711738eb2fbf456f33426f498aa0b6b6f3826b01'
MAX_EXTRACTED = 512 * 1024 * 1024
MAX_MEMBERS = 256
MAX_RATIO = 200
_verified=OrderedDict()
_verified_lock=threading.RLock()
_MAX_VERIFIED=16


def _redirect(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def _hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _version(value):
    if value not in (PINNED_VERSION, 'v' + PINNED_VERSION):
        raise ReleaseStoreError('Only the pinned Windows CLI release is supported')
    return PINNED_VERSION


def _default_folder():
    from runtime_paths import DATA
    return DATA / 'official-cli-native'


class NativeCLIStore(CLIReleaseStore):
    """Independent ``official-cli-native/versions`` store; no active pointer.

    Injection signatures match CLIReleaseStore. ``archive`` is an optional
    explicitly supplied local copy; its bytes still have to match the pin.
    Returned manifests add absolute ``directory`` and ``binary`` paths, while
    the persistent manifest contains only relative payload paths.
    """

    def __init__(self, folder=None, *, http_open=None, runner=None, timeout=300,
                 socket_timeout=20, lock_timeout=15, cancel_event=None):
        self.folder = Path(folder if folder is not None else _default_folder()).expanduser().absolute()
        super().__init__(self.folder / 'versions', http_open=http_open, runner=runner,
                         timeout=timeout, socket_timeout=socket_timeout,
                         lock_timeout=lock_timeout, cancel_event=cancel_event)

    def _safe_root(self):
        for path in (self.root, *self.root.parents):
            if _redirect(path):
                raise ReleaseStoreError('Native CLI store must not traverse a filesystem redirect')

    def _asset(self, version, release):
        _version(version)
        tag = 'v' + version
        if (not isinstance(release, dict) or release.get('tag_name') != tag or
                release.get('html_url') != REPO_URL + '/releases/tag/' + tag or
                release.get('draft') is not False):
            raise ReleaseStoreError('Metadata is not the pinned published official release')
        assets = release.get('assets')
        if not isinstance(assets, list) or len(assets) > 200:
            raise ReleaseStoreError('Invalid official release assets')
        matches = [asset for asset in assets if isinstance(asset, dict) and asset.get('name') == ARCHIVE_NAME]
        if len(matches) != 1:
            raise ReleaseStoreError('Release must list exactly one matching Windows x86_64 archive')
        asset = dict(size=None, **matches[0]) if 'size' not in matches[0] else dict(matches[0])
        self._asset_url(asset, tag)
        if asset['browser_download_url'] != ASSET_URL or asset['size'] not in (None, ARCHIVE_SIZE):
            raise ReleaseStoreError('Windows CLI asset differs from the pinned release')
        return asset

    def _extract(self, archive, destination):
        self._check_cancel()
        destination.mkdir()
        entries, files, expanded = {}, [], 0
        deadline = time.monotonic() + self.timeout
        try:
            with zipfile.ZipFile(archive) as source:
                members = source.infolist()
                if not 0 < len(members) <= MAX_MEMBERS:
                    raise ReleaseStoreError('ZIP contains too many entries or is empty')
                for member in members:
                    self._check_cancel()
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Native CLI extraction timed out')
                    if member.orig_filename != member.filename:
                        raise ReleaseStoreError('ZIP contains a normalized or truncated filename')
                    relative = self._member_path(member.filename)
                    mode = member.external_attr >> 16
                    kind = stat.S_IFMT(mode)
                    is_dir = member.is_dir()
                    # Reject Unix symlinks/special files and Windows reparse data.
                    if (member.flag_bits & 1 or member.external_attr & 0x400 or
                            kind not in (0, stat.S_IFDIR if is_dir else stat.S_IFREG) or
                            is_dir and member.file_size):
                        raise ReleaseStoreError('ZIP encrypted entries, links and special files are not accepted')
                    key = relative.as_posix().casefold()
                    if (key in entries or any(entries.get(parent.as_posix().casefold()) == 'file' for parent in relative.parents) or
                            not is_dir and any(item.startswith(key + '/') for item in entries)):
                        raise ReleaseStoreError('ZIP contains colliding paths')
                    entries[key] = 'dir' if is_dir else 'file'
                    target = destination.joinpath(*relative.parts)
                    if is_dir:
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    expanded += member.file_size
                    if (member.file_size < 0 or expanded > MAX_EXTRACTED or
                            member.file_size > max(1, member.compress_size) * MAX_RATIO):
                        raise ReleaseStoreError('Expanded ZIP exceeds size or compression-ratio limit')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest, copied = hashlib.sha256(), 0
                    with source.open(member) as content, target.open('xb') as output:
                        while True:
                            self._check_cancel()
                            block = content.read(64 * 1024)
                            if not block:
                                break
                            if time.monotonic() >= deadline:
                                raise TimeoutError('Native CLI extraction timed out')
                            copied += len(block)
                            if copied > member.file_size:
                                raise ReleaseStoreError('ZIP member exceeds its declared size')
                            output.write(block)
                            digest.update(block)
                    if copied != member.file_size:
                        raise ReleaseStoreError('ZIP member is truncated')
                    files.append(dict(path=relative.as_posix(), size=copied, sha256=digest.hexdigest()))
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as error:
            raise ReleaseStoreError('Official Windows CLI ZIP is invalid') from error
        binaries = [entry['path'] for entry in files if PurePosixPath(entry['path']).name.casefold() == 'wuji.exe']
        if len(binaries) != 1:
            raise ReleaseStoreError('ZIP must contain exactly one wuji.exe candidate')
        return sorted(files, key=lambda entry: entry['path']), binaries[0]

    def _validate_software(self, binary, stage):
        home = stage / 'software-check-home'
        home.mkdir()
        keep = {'PATH', 'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TMP', 'TEMP', 'TMPDIR'}
        env = {key: value for key, value in os.environ.items() if key in keep}
        env.update(HOME=str(home), USERPROFILE=str(home), LOCALAPPDATA=str(home), APPDATA=str(home),
                   XDG_CONFIG_HOME=str(home), XDG_DATA_HOME=str(home))
        evidence = {}
        for label, arguments in (('version', ['--version']), ('help', ['--help']), ('viz', ['viz', '--help'])):
            self._check_cancel()
            result = self.runner([str(binary), *arguments], capture_output=True, text=True,
                                 encoding='utf-8', errors='replace', timeout=min(30, self.timeout),
                                 stdin=subprocess.DEVNULL, shell=False, cwd=str(stage), env=env,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise ReleaseStoreError('Native CLI software check failed: ' + label)
            output = result.stdout or ''
            if not isinstance(output, str) or len(output) > 128 * 1024:
                raise ReleaseStoreError('Native CLI software check returned invalid output')
            evidence[label] = output
        if (not re.search(r'\b' + re.escape(PINNED_VERSION) + r'\b', evidence['version']) or
                '--jsonl' not in evidence['help'] or '--no-zero' not in evidence['viz'] or
                '--no-open' not in evidence['viz']):
            raise ReleaseStoreError('Native CLI version or required interfaces do not match')
        shutil.rmtree(home)
        return evidence

    def _identity_tree(self,directory):
        """Cheap identity of every path; redirects never inherit prior trust.

        This is a metadata-poll cache only. A strict process-launch check still
        hashes content even if an adversary can restore file timestamps.
        """
        self._safe_root()
        if not directory.is_dir() or _redirect(directory):
            raise ReleaseStoreError('Native CLI version is not prepared')
        pending=[directory];rows=[]
        while pending:
            path=pending.pop()
            if _redirect(path):raise ReleaseStoreError('Native CLI version contains a filesystem redirect')
            info=path.lstat()
            if stat.S_ISDIR(info.st_mode):pending.extend(path.iterdir())
            elif not stat.S_ISREG(info.st_mode):raise ReleaseStoreError('Invalid native CLI cache path type')
            rows.append((path.relative_to(directory).as_posix(),info.st_mode,info.st_size,
                info.st_mtime_ns,info.st_ctime_ns,info.st_ino,info.st_dev,
                getattr(info,'st_file_attributes',0),getattr(info,'st_reparse_tag',0)))
        return tuple(sorted(rows))

    def _cached(self,directory,version,*,strict=False):
        if type(strict) is not bool:raise ValueError('Expected a boolean strict verification option')
        key=(os.path.normcase(str(directory.absolute())),version,REPOSITORY,REPO_URL,PINNED_VERSION,ARCHIVE_NAME,
             ASSET_URL,ARCHIVE_SHA256,ARCHIVE_SIZE,BINARY_SHA256)
        with _verified_lock:
            try:before=self._identity_tree(directory)
            except BaseException:
                _verified.pop(key,None);raise
            previous=_verified.get(key)
            if not strict and previous is not None and previous[0]==before:
                _verified.move_to_end(key)
                return copy.deepcopy(previous[1])
            _verified.pop(key,None)
            result=self._cached_full(directory,version)
            after=self._identity_tree(directory)
            if before!=after:raise ReleaseStoreError('Native CLI changed during integrity verification')
            _verified[key]=(after,copy.deepcopy(result))
            while len(_verified)>_MAX_VERIFIED:_verified.popitem(last=False)
            return result

    def _cached_full(self, directory, version):
        if _redirect(directory) or not directory.is_dir():
            raise ReleaseStoreError('Native CLI version is not prepared')
        manifest_path = directory / 'manifest.json'
        try:
            if _redirect(manifest_path) or manifest_path.stat().st_size > MAX_METADATA:
                raise ReleaseStoreError('Invalid native CLI manifest')
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeDecodeError) as error:
            raise ReleaseStoreError('Native CLI manifest is missing or invalid') from error
        expected = dict(schema=1, platform='windows-x86_64', repository=REPOSITORY, version=version,
                        tag='v' + version, asset_url=ASSET_URL, archive_sha256=ARCHIVE_SHA256,
                        archive_size=ARCHIVE_SIZE, binary_sha256=BINARY_SHA256,
                        validated=True, status='prepared')
        if not isinstance(manifest, dict) or any(manifest.get(key) != value for key, value in expected.items()):
            raise ReleaseStoreError('Native CLI identity differs from the pinned release')
        for path in directory.rglob('*'):
            if _redirect(path):
                raise ReleaseStoreError('Native CLI version contains a filesystem redirect')
        archive = directory / ARCHIVE_NAME
        if not archive.is_file() or archive.stat().st_size != ARCHIVE_SIZE or _hash(archive) != ARCHIVE_SHA256:
            raise ReleaseStoreError('Native CLI archive failed integrity verification')
        files, paths = manifest.get('files'), manifest.get('paths')
        if (not isinstance(files, list) or not 0 < len(files) <= MAX_MEMBERS or
                not isinstance(paths, dict) or paths.get('archive') != ARCHIVE_NAME or paths.get('payload') != 'payload'):
            raise ReleaseStoreError('Native CLI payload manifest is invalid')
        expected_files = set()
        for entry in files:
            if not isinstance(entry, dict):
                raise ReleaseStoreError('Invalid native CLI payload entry')
            relative = self._member_path(entry.get('path'))
            file = directory / 'payload' / Path(*relative.parts)
            if (relative.as_posix() in expected_files or not file.is_file() or
                    file.stat().st_size != entry.get('size') or _hash(file) != entry.get('sha256')):
                raise ReleaseStoreError('Native CLI payload failed integrity verification')
            expected_files.add(relative.as_posix())
        actual_files = {path.relative_to(directory / 'payload').as_posix() for path in (directory / 'payload').rglob('*') if path.is_file()}
        binaries = [path for path in expected_files if PurePosixPath(path).name.casefold() == 'wuji.exe']
        if actual_files != expected_files or len(binaries) != 1 or paths.get('binary') != 'payload/' + binaries[0]:
            raise ReleaseStoreError('Native CLI payload contains unexpected files or executable paths')
        binary = directory / paths['binary']
        if manifest.get('binary_sha256') != _hash(binary):
            raise ReleaseStoreError('Native CLI executable failed integrity verification')
        return dict(manifest, directory=str(directory), binary=str(binary))

    def resolve(self, version=PINNED_VERSION, *, strict=False):
        """Return a verified local executable; never download or execute it."""
        version = _version(version)
        self._safe_root()
        return self._cached(self.root / version, version,strict=strict)['binary']

    def prepare(self, version=PINNED_VERSION, *, release=None, archive=None, strict=False):
        """Explicitly prepare and help-check the pinned official Windows CLI."""
        if type(strict) is not bool:raise ValueError('Expected a boolean strict verification option')
        version = _version(version)
        self._safe_root()
        self._check_cancel()
        # Cached versions resolve without network or subprocess work.
        with self._locked():
            final = self.root / version
            if final.exists() or _redirect(final):
                return self._cached(final, version,strict=strict)
            release = self.fetch_release(version) if release is None else release
            encoded = json.dumps(release).encode('utf-8')
            if len(encoded) > MAX_METADATA:
                raise ReleaseStoreError('Release metadata exceeds the size limit')
            release = json.loads(encoded)
            asset = self._asset(version, release)
            digest, source = self._expected_sha(release, asset, 'v' + version)
            if digest != ARCHIVE_SHA256:
                raise ReleaseStoreError('Official asset digest differs from the pinned Windows CLI')
            stage = Path(tempfile.mkdtemp(prefix='.prepare-' + version + '-', dir=self.root))
            try:
                candidate = stage / ARCHIVE_NAME
                if archive is None:
                    with candidate.open('xb') as output:
                        actual = self._read_http(ASSET_URL, limit=ARCHIVE_SIZE, destination=output, expected_size=ARCHIVE_SIZE)
                        output.flush()
                        os.fsync(output.fileno())
                else:
                    local = Path(archive).expanduser().absolute()
                    if _redirect(local) or not local.is_file() or local.stat().st_size != ARCHIVE_SIZE:
                        raise ReleaseStoreError('Local native CLI archive is invalid')
                    digestor = hashlib.sha256()
                    with local.open('rb') as incoming, candidate.open('xb') as output:
                        while block := incoming.read(64 * 1024):
                            self._check_cancel()
                            output.write(block)
                            digestor.update(block)
                        output.flush()
                        os.fsync(output.fileno())
                    actual = digestor.hexdigest()
                if actual != ARCHIVE_SHA256 or candidate.stat().st_size != ARCHIVE_SIZE:
                    raise ReleaseStoreError('Native CLI archive failed SHA-256 verification')
                files, binary = self._extract(candidate, stage / 'payload')
                if _hash(stage / 'payload' / binary) != BINARY_SHA256:
                    raise ReleaseStoreError('Native CLI executable differs from the verified release')
                evidence = self._validate_software(stage / 'payload' / binary, stage)
                self._write_json(stage / 'release.json', release)
                manifest = dict(schema=1, platform='windows-x86_64', repository=REPOSITORY, version=version,
                                tag='v' + version, release_url=release['html_url'], asset_url=ASSET_URL,
                                archive_sha256=ARCHIVE_SHA256, archive_size=ARCHIVE_SIZE, checksum_source=source,
                                binary_sha256=_hash(stage / 'payload' / binary), files=files,
                                paths=dict(archive=ARCHIVE_NAME, payload='payload', binary='payload/' + binary),
                                status='prepared', validated=True, compatibility_checked=True,
                                software_checks=evidence, device_commands_executed=False,
                                prepared_at=datetime.now(timezone.utc).isoformat())
                self._write_json(stage / 'manifest.json', manifest)
                self._check_cancel()
                os.replace(stage, final)
                return self._cached(final, version,strict=strict)
            finally:
                if stage.exists():
                    if (stage.parent != self.root or not stage.name.startswith('.prepare-') or
                            _redirect(stage) or stage.resolve().parent != self.root.resolve()):
                        raise ReleaseStoreError('Unsafe native CLI staging cleanup path')
                    shutil.rmtree(stage)


def resolve(version=PINNED_VERSION, *, folder=None,strict=False):
    return NativeCLIStore(folder).resolve(version,strict=strict)


def prepare(version=PINNED_VERSION, *, folder=None, release=None, archive=None,strict=False):
    if version in (PINNED_VERSION,'v'+PINNED_VERSION) and release is None and archive is None:
        from runtime_paths import RESOURCE
        bundled=RESOURCE/'official-cli-native'
        if (bundled/ARCHIVE_NAME).is_file() and (bundled/'release.json').is_file():
            metadata=bundled/'release.json'
            if metadata.stat().st_size>MAX_METADATA:raise ReleaseStoreError('Bundled CLI release metadata exceeds its limit')
            release=json.loads(metadata.read_text(encoding='utf-8'));archive=bundled/ARCHIVE_NAME
    return NativeCLIStore(folder).prepare(version, release=release, archive=archive,strict=strict)


def json_command(*arguments):
    """Same Windows executable and SDK HOME; no shell or web-supplied argv."""
    from windows_process_job import OwnedProcess
    # Every executable launch, including local profile reads, rehashes content.
    # Stat-based reuse is only for metadata polling that does not run a binary.
    component=prepare(strict=True)
    with OwnedProcess([component['binary'],'--json',*arguments],stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',
            env={**os.environ,'WUJI_NO_UPDATE_CHECK':'1','WUJI_TELEMETRY_DISABLE':'1'}) as owned:
        try:out,err=owned.process.communicate(timeout=20)
        except subprocess.TimeoutExpired as error:raise ValueError('Official CLI metadata request timed out') from error
        if owned.process.returncode:raise ValueError((err or out or 'Official CLI failed').strip()[:400])
    if len(out)>2*1024*1024:raise ValueError('Official CLI metadata exceeds its limit')
    try:value=json.loads(out)
    except (ValueError,TypeError) as error:raise ValueError('Official CLI did not return JSON') from error
    if not isinstance(value,dict):raise ValueError('Unexpected official CLI response')
    return value
