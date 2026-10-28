"""Check and prepare this application's published installer; never execute it.

No I/O occurs at import or construction. The caller owns idle checks, same-GUID
installation, app exit and restart. ``runner`` is an injectable urllib-style
opener ``runner(Request, timeout=seconds)`` returning a streaming response.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from urllib.parse import urlsplit
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


REPOSITORY = 'Fairank/wuji-hand-studio'
REPO_URL = 'https://github.com/' + REPOSITORY
API_URL = 'https://api.github.com/repos/' + REPOSITORY
LATEST_URL = API_URL + '/releases/latest'
MAX_METADATA = 2 * 1024 * 1024
MAX_CHECKSUM = 64 * 1024
MAX_INSTALLER = 1024 * 1024 * 1024
_VERSION = re.compile(r'(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})')
_SHA256 = re.compile(r'[a-fA-F0-9]{64}')


class WorkbenchReleaseError(ValueError):
    """Release metadata, transport or local staging failed verification."""


def _version(value, *, tag=False):
    if not isinstance(value, str):
        raise WorkbenchReleaseError('Expected a stable X.Y.Z application version')
    match = _VERSION.fullmatch(value[1:] if tag and value.startswith('v') else value)
    if not match or tag and not value.startswith('v'):
        raise WorkbenchReleaseError('Expected a stable vX.Y.Z release tag' if tag else 'Expected a stable X.Y.Z application version')
    return tuple(int(item) for item in match.groups())


def _redirect(url, original, *, asset):
    """Metadata cannot redirect; asset CDN redirects originate at a bound URL."""
    if not isinstance(url, str) or len(url) > 8192:
        raise WorkbenchReleaseError('Invalid release response URL')
    try:
        parsed = urlsplit(url)
        safe = parsed.scheme == 'https' and parsed.username is None and parsed.password is None and parsed.port in (None, 443) and not parsed.fragment
    except ValueError as error:
        raise WorkbenchReleaseError('Invalid release response URL') from error
    if not safe:
        raise WorkbenchReleaseError('Release transport requires trusted HTTPS URLs')
    if url == original:
        return
    if not asset or parsed.hostname not in {'release-assets.githubusercontent.com', 'objects.githubusercontent.com'} or not parsed.path.startswith('/') or parsed.path == '/':
        raise WorkbenchReleaseError('Release request redirected outside trusted GitHub asset storage')


class _RedirectHandler(HTTPRedirectHandler):
    def __init__(self, original, asset):
        self.original, self.asset = original, asset

    def redirect_request(self, request, response, code, message, headers, newurl):
        _redirect(newurl, self.original, asset=self.asset)
        return super().redirect_request(request, response, code, message, headers, newurl)


def _plain(path, *, directory=None):
    """Reject symlinks, Windows junctions/reparse points and unexpected types."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
        raise WorkbenchReleaseError('Update paths must not be filesystem redirects')
    if directory is True and not stat.S_ISDIR(info.st_mode) or directory is False and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise WorkbenchReleaseError('Unexpected update path type')
    return True


def _directory(path, *, create=False):
    for item in [*reversed(path.parents), path]:
        if not _plain(item, directory=True):
            if not create:
                raise WorkbenchReleaseError('Update directory disappeared')
            try:
                item.mkdir()
            except FileExistsError:
                pass
            if not _plain(item, directory=True):
                raise WorkbenchReleaseError('Update directory could not be created')


class WorkbenchRelease:
    """A bounded download-only store at data_dir/updates/versions/<version>.

    check() returns public release metadata and an availability flag. prepare()
    accepts that result, its raw ``release`` object, or None to check latest.
    It revalidates every field and atomically commits a version folder only
    after size/hash verification. Existing current/previous pointers are never
    read, changed or used as cleanup targets.
    """
    def __init__(self, data_dir, current_version, *, runner=None, timeout=180,
                 socket_timeout=15, max_size=MAX_INSTALLER):
        _version(current_version)
        for value in (timeout, socket_timeout):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 600:
                raise ValueError('Timeouts must be finite, positive and at most 600 seconds')
        if type(max_size) is not int or not 0 < max_size <= MAX_INSTALLER:
            raise ValueError('Invalid installer size limit')
        self.data_dir = Path(data_dir).expanduser().absolute()
        self.versions = self.data_dir / 'updates' / 'versions'
        self.current_version, self.runner = current_version, runner
        self.timeout, self.socket_timeout, self.max_size = timeout, socket_timeout, max_size

    @staticmethod
    def _cancel(cancel_event, deadline):
        if cancel_event is not None and cancel_event.is_set():
            raise WorkbenchReleaseError('Application update preparation cancelled')
        if time.monotonic() >= deadline:
            raise WorkbenchReleaseError('Application update preparation timed out')

    def _read(self, url, *, limit, deadline, cancel_event, expected=None, output=None, asset=False, progress=None):
        self._cancel(cancel_event, deadline)
        if not asset and url != LATEST_URL:
            raise WorkbenchReleaseError('Untrusted release metadata endpoint')
        _redirect(url, url, asset=asset)
        request = Request(url, headers={'User-Agent': 'HandWorkbench-ApplicationUpdate', 'Accept': 'application/octet-stream' if asset else 'application/vnd.github+json', 'Accept-Encoding': 'identity'})
        opener = self.runner or build_opener(_RedirectHandler(url, asset)).open
        remaining = min(self.socket_timeout, max(.001, deadline - time.monotonic()))
        chunks, total, digest = [], 0, hashlib.sha256()
        with opener(request, timeout=remaining) as response:
            if response.status != 200:
                raise HTTPError(url, response.status, 'GitHub release request failed', response.headers, None)
            _redirect(response.geturl(), url, asset=asset)
            length = response.headers.get('Content-Length')
            if length is not None:
                if not isinstance(length, str) or not re.fullmatch(r'[0-9]{1,12}', length):
                    raise WorkbenchReleaseError('Invalid release Content-Length')
                length = int(length)
                if length > limit or expected is not None and length != expected:
                    raise WorkbenchReleaseError('Release response size differs from metadata')
            while True:
                self._cancel(cancel_event, deadline)
                block = response.read(min(64 * 1024, limit - total + 1))
                self._cancel(cancel_event, deadline)
                if not block:
                    break
                if not isinstance(block, bytes):
                    raise WorkbenchReleaseError('Invalid release response bytes')
                total += len(block)
                if total > limit:
                    raise WorkbenchReleaseError('Release response exceeds its size limit')
                digest.update(block)
                if output is None:
                    chunks.append(block)
                else:
                    output.write(block)
                    if progress:progress(dict(stage='downloading',received_bytes=total,total_bytes=expected))
            if length is not None and total != length or expected is not None and total != expected:
                raise WorkbenchReleaseError('Release download is truncated or has extra bytes')
        return (digest.hexdigest(), total) if output is not None else b''.join(chunks)

    def _asset(self, item, name, tag, limit):
        if not isinstance(item, dict) or item.get('name') != name:
            raise WorkbenchReleaseError('Release asset does not match its exact installer name')
        url = REPO_URL + '/releases/download/' + tag + '/' + name
        if item.get('browser_download_url') != url:
            raise WorkbenchReleaseError('Asset URL is not this repository and release')
        if type(item.get('size')) is not int or not 0 < item['size'] <= limit:
            raise WorkbenchReleaseError('Invalid release asset size')
        if item.get('digest') is not None and (not isinstance(item['digest'], str) or not re.fullmatch(r'sha256:[a-fA-F0-9]{64}', item['digest'])):
            raise WorkbenchReleaseError('Malformed GitHub asset SHA-256 digest')
        return dict(item)

    def _metadata(self, release):
        if not isinstance(release, dict):
            raise WorkbenchReleaseError('Release metadata is not an object')
        version = _version(release.get('tag_name'), tag=True)
        tag = release['tag_name']
        if release.get('draft') is not False or release.get('prerelease') is not False or release.get('html_url') != REPO_URL + '/releases/tag/' + tag:
            raise WorkbenchReleaseError('Expected a published stable release from this repository')
        assets = release.get('assets')
        if not isinstance(assets, list) or len(assets) > 200:
            raise WorkbenchReleaseError('Invalid release asset list')
        number = tag[1:]
        name = 'HandWorkbench-' + number + '-windows-x64-setup.exe'
        found = [item for item in assets if isinstance(item, dict) and item.get('name') == name]
        if len(found) != 1:
            raise WorkbenchReleaseError('Release must have exactly one matching Windows x64 installer')
        installer = self._asset(found[0], name, tag, self.max_size)
        checksum = None
        if installer.get('digest') is None:
            matches = [item for item in assets if isinstance(item, dict) and item.get('name') == name + '.sha256']
            if len(matches) != 1:
                raise WorkbenchReleaseError('Installer needs a GitHub SHA-256 digest or one matching .sha256 asset')
            checksum = self._asset(matches[0], name + '.sha256', tag, MAX_CHECKSUM)
        return version, number, installer, checksum

    def check(self, *, cancel_event=None):
        deadline = time.monotonic() + self.timeout
        try:
            release = json.loads(self._read(LATEST_URL, limit=MAX_METADATA, deadline=deadline, cancel_event=cancel_event))
        except HTTPError as error:
            if error.code == 404:
                return {'current_version': self.current_version, 'latest_version': None,
                        'available': False, 'reason': 'unpublished', 'release': None, 'asset': None}
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkbenchReleaseError('GitHub release metadata is not valid JSON') from error
        version, number, installer, checksum = self._metadata(release)
        return {'current_version': self.current_version, 'latest_version': number,
                'available': version > _version(self.current_version), 'release': release,
                'asset': installer, 'verification': 'github_asset_digest' if checksum is None else 'release_sha256_asset'}

    def _expected(self, installer, checksum, deadline, cancel_event):
        if checksum is None:
            return installer['digest'][7:].lower(), 'github_asset_digest'
        raw = self._read(checksum['browser_download_url'], limit=MAX_CHECKSUM, expected=checksum['size'], deadline=deadline, cancel_event=cancel_event, asset=True)
        if checksum.get('digest') and hashlib.sha256(raw).hexdigest() != checksum['digest'][7:].lower():
            raise WorkbenchReleaseError('Checksum asset failed its GitHub digest')
        try:
            lines = [line.strip() for line in raw.decode('utf-8').splitlines() if line.strip()]
        except UnicodeDecodeError as error:
            raise WorkbenchReleaseError('Installer checksum is not UTF-8') from error
        if len(lines) != 1:
            raise WorkbenchReleaseError('Checksum must identify the installer exactly once')
        line = lines[0]
        match = re.fullmatch(r'([a-fA-F0-9]{64})[ \t]+\*?(.+)', line)
        if _SHA256.fullmatch(line):
            return line.lower(), 'release_sha256_asset'
        if match and match[2] == installer['name']:
            return match[1].lower(), 'release_sha256_asset'
        raise WorkbenchReleaseError('Checksum does not identify this exact installer')

    @contextmanager
    def _stage(self, name):
        _directory(self.versions, create=True)
        stage = Path(tempfile.mkdtemp(prefix='.prepare-', dir=self.versions))
        try:
            yield stage
        finally:
            # Only our two exact staging files are cleanup targets; never recurse
            # through a changed directory or touch current/previous pointers.
            _directory(self.versions)
            if _plain(stage, directory=True):
                for filename in (name, 'manifest.json'):
                    (stage / filename).unlink(missing_ok=True)
                try:
                    stage.rmdir()
                except FileNotFoundError:
                    pass

    @staticmethod
    def _create(path):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
        return os.fdopen(os.open(path, flags, 0o600), 'wb')

    def _existing(self, destination, installer, digest, deadline, cancel_event):
        self._cancel(cancel_event, deadline)
        _directory(destination)
        file, manifest_file = destination / installer['name'], destination / 'manifest.json'
        if not _plain(file, directory=False) or not _plain(manifest_file, directory=False) or manifest_file.stat().st_size > MAX_CHECKSUM:
            raise WorkbenchReleaseError('Prepared installer record is incomplete')
        try:
            manifest = json.loads(manifest_file.read_bytes())
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkbenchReleaseError('Prepared installer manifest is invalid') from error
        if not isinstance(manifest, dict) or manifest.get('schema_version') != 1 or manifest.get('repository') != REPOSITORY or manifest.get('version') != destination.name or manifest.get('tag') != 'v' + destination.name or manifest.get('release_url') != REPO_URL + '/releases/tag/v' + destination.name or manifest.get('asset_url') != installer['browser_download_url'] or manifest.get('installer') != installer['name'] or manifest.get('sha256') != digest or manifest.get('size') != installer['size'] or file.stat().st_size != installer['size']:
            raise WorkbenchReleaseError('Existing prepared installer has different provenance')
        stamp = manifest.get('prepared_at')
        try:
            if not isinstance(stamp, str) or len(stamp) > 80 or datetime.fromisoformat(stamp).tzinfo is None:
                raise ValueError('Invalid timestamp')
        except ValueError as error:
            raise WorkbenchReleaseError('Prepared installer timestamp is invalid') from error
        digest_state = hashlib.sha256()
        with file.open('rb') as stream:
            while True:
                self._cancel(cancel_event, deadline)
                chunk = stream.read(64 * 1024)
                if not chunk:
                    break
                digest_state.update(chunk)
        self._cancel(cancel_event, deadline)
        if digest_state.hexdigest() != digest:
            raise WorkbenchReleaseError('Existing prepared installer failed SHA-256 verification')
        return dict(manifest, prepared=True, installer=str(file), installer_path=str(file), manifest_path=str(manifest_file))

    def prepare(self, release=None, *, cancel_event=None, progress=None):
        deadline = time.monotonic() + self.timeout
        self._cancel(cancel_event, deadline)
        if release is None:
            # Keep this operation's deadline across metadata and download too.
            try:
                release = json.loads(self._read(LATEST_URL, limit=MAX_METADATA, deadline=deadline, cancel_event=cancel_event))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise WorkbenchReleaseError('GitHub release metadata is not valid JSON') from error
        elif isinstance(release, dict) and 'release' in release:
            release = release['release']
        version, number, installer, checksum = self._metadata(release)
        if version <= _version(self.current_version):
            raise WorkbenchReleaseError('Installer version must be newer than the current application')
        digest, source = self._expected(installer, checksum, deadline, cancel_event)
        _directory(self.versions, create=True)
        destination = self.versions / number
        if _plain(destination, directory=True):
            if progress:progress(dict(stage='verifying',received_bytes=installer['size'],total_bytes=installer['size']))
            return self._existing(destination, installer, digest, deadline, cancel_event)
        if progress:progress(dict(stage='downloading',received_bytes=0,total_bytes=installer['size']))
        with self._stage(installer['name']) as stage:
            with self._create(stage / installer['name']) as stream:
                actual, size = self._read(installer['browser_download_url'], limit=self.max_size, expected=installer['size'], deadline=deadline, cancel_event=cancel_event, output=stream, asset=True, progress=progress)
                stream.flush()
                os.fsync(stream.fileno())
            if progress:progress(dict(stage='verifying',received_bytes=size,total_bytes=installer['size']))
            if actual != digest:
                raise WorkbenchReleaseError('Installer failed SHA-256 verification')
            manifest = {'schema_version': 1, 'repository': REPOSITORY, 'version': number,
                        'tag': release['tag_name'], 'release_url': release['html_url'],
                        'installer': installer['name'], 'asset_url': installer['browser_download_url'],
                        'sha256': digest, 'size': size, 'verification': source,
                        'prepared_at': datetime.now(timezone.utc).isoformat()}
            with self._create(stage / 'manifest.json') as stream:
                stream.write((json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
                stream.flush()
                os.fsync(stream.fileno())
            self._cancel(cancel_event, deadline)
            _directory(self.versions)
            _directory(stage)
            if _plain(destination, directory=True):
                raise WorkbenchReleaseError('Installer version was prepared by another operation; retry')
            stage.rename(destination)
            return self._existing(destination, installer, digest, deadline, cancel_event)
