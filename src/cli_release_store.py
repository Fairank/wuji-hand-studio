"""Prepare official CLI releases and source provenance; never run or activate them.

The caller owns compatibility checks and any later live-version switch. Network
and git execution are injectable. Importing this module performs no I/O.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import hashlib
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


REPOSITORY = 'wuji-technology/wuji-cli'
REPO_URL = 'https://github.com/' + REPOSITORY
API_URL = 'https://api.github.com/repos/' + REPOSITORY
GIT_URL = REPO_URL + '.git'
MAX_METADATA = 2 * 1024 * 1024
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_EXTRACTED = 2 * 1024 * 1024 * 1024
MAX_MEMBERS = 20000
_DIGEST = re.compile(r'[a-fA-F0-9]{64}')


class ReleaseStoreError(ValueError):
    """The requested release could not be prepared with verified provenance."""


def _version(value):
    if not isinstance(value, str) or len(value) > 80:
        raise ReleaseStoreError('Expected an explicit CLI version')
    value = value.removeprefix('v')
    if not re.fullmatch(r'[0-9]+(?:\.[0-9A-Za-z]+){1,5}(?:[-+][0-9A-Za-z.-]+)?', value) or '..' in value:
        raise ReleaseStoreError('Invalid CLI version')
    return value


def _redirect_url(url, original):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment:
        raise ReleaseStoreError('Release download redirected to an unsupported URL')
    if url == original:
        return
    if original == REPO_URL + '/releases/latest' and url.startswith(REPO_URL + '/releases/tag/v') and not parsed.query:
        tag = unquote(parsed.path.rsplit('/', 1)[-1])
        if url == REPO_URL + '/releases/tag/' + quote('v' + _version(tag), safe=''):
            return
    if parsed.hostname not in {'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}:
        raise ReleaseStoreError('Release download redirected outside GitHub asset storage')


class _AssetRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        _redirect_url(newurl, request.full_url)
        return super().redirect_request(request, response, code, message, headers, newurl)


class _AssetLinks(HTMLParser):
    _void = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__()
        self.links = []
        self.rows = []
        self._stack = []
        self._rows = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        frame = dict(tag=tag, row=None, capture=None)
        if tag == 'li' and 'Box-row' in (attrs.get('class') or '').split():
            row = dict(links=[], copies=[], text=[], nested=bool(self._rows))
            for parent in self._rows:
                parent['nested'] = True
            self._rows.append(row)
            frame['row'] = row
        row = self._rows[-1] if self._rows else None
        if tag == 'a':
            href = attrs.get('href')
            if href is not None:
                self.links.append(href)
                if row is not None:
                    row['links'].append(href)
        if row is not None and tag == 'clipboard-copy':
            row['copies'].append((attrs.get('aria-label'), attrs.get('value')))
        if row is not None and tag == 'span' and 'Truncate-text' in (attrs.get('class') or '').split():
            frame['capture'] = (row, [])
        if tag not in self._void:
            self._stack.append(frame)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._void:
            self.handle_endtag(tag)

    def handle_data(self, data):
        for frame in self._stack:
            if frame['capture'] is not None:
                frame['capture'][1].append(data)

    def handle_endtag(self, tag):
        index = next((index for index in range(len(self._stack) - 1, -1, -1) if self._stack[index]['tag'] == tag), None)
        if index is None:
            return
        frames = self._stack[index:]
        del self._stack[index:]
        for frame in reversed(frames):
            if frame['capture'] is not None:
                row, text = frame['capture']
                row['text'].append(''.join(text).strip())
            if frame['row'] is not None:
                row = frame['row']
                if len(frames) != 1:
                    row['nested'] = True  # An unclosed child makes this row ambiguous.
                self._rows.remove(row)
                self.rows.append(row)

    def digest_for(self, url, name):
        """Bind a digest only to one closed GitHub release asset Box-row.

        GitHub currently repeats it in span.Truncate-text and clipboard-copy.
        A copy control also names its exact asset in its accessible label.
        """
        rows = [row for row in self.rows if url in {urljoin(REPO_URL + '/', href) for href in row['links']}]
        if not rows:
            return None
        if len(rows) != 1 or rows[0]['nested']:
            raise ReleaseStoreError('Official HTML asset row is ambiguous')
        row = rows[0]
        if len({urljoin(REPO_URL + '/', href) for href in row['links']}) != 1:
            raise ReleaseStoreError('Official HTML row contains more than one asset link')
        digests = []
        for label, value in row['copies']:
            if (isinstance(value, str) and value.startswith('sha256:')) or (isinstance(label, str) and label.startswith('Copy to clipboard digest for ')):
                if label != 'Copy to clipboard digest for ' + name or not isinstance(value, str) or not re.fullmatch(r'sha256:[a-fA-F0-9]{64}', value):
                    raise ReleaseStoreError('Official HTML digest does not identify its asset')
                digests.append(value.lower())
        for text in row['text']:
            if text.startswith('sha256:'):
                if not re.fullmatch(r'sha256:[a-fA-F0-9]{64}', text):
                    raise ReleaseStoreError('Official HTML asset digest is malformed')
                digests.append(text.lower())
        if len(set(digests)) > 1:
            raise ReleaseStoreError('Official HTML asset digests disagree')
        return digests[0] if digests else None


class _ReleaseTarInfo(tarfile.TarInfo):
    @classmethod
    def frombuf(cls, buffer, encoding, errors):
        member = super().frombuf(buffer, encoding, errors)
        # Bound extension records before tarfile reads their data into memory.
        if not member.isreg() and member.size > 64 * 1024:
            raise ReleaseStoreError('Archive extension or directory metadata is too large')
        return member


@contextmanager
def _open_archive(path):
    try:
        with tarfile.open(path, mode='r:gz', tarinfo=_ReleaseTarInfo) as archive:
            yield archive
    except tarfile.TarError as error:
        raise ReleaseStoreError('Official CLI archive is invalid or has unsafe headers') from error


def _is_redirect(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def _file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class CLIReleaseStore:
    """An owned, independent version store with no active-version pointer.

    http_open has the urllib opener signature (Request, timeout=seconds).
    runner has subprocess.run's signature. Neither injection is called at init.
    Explicit release metadata must have GitHub's tag_name/html_url/assets shape;
    a SHA comes from asset.digest, an asset-scoped official HTML digest, or an
    actual listed checksum asset.
    """

    def __init__(self, root, *, http_open=None, runner=None, timeout=60, socket_timeout=None, git_timeout=120, lock_timeout=15, cancel_event=None):
        socket_timeout = timeout if socket_timeout is None else socket_timeout
        for number in (timeout, socket_timeout, git_timeout, lock_timeout):
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not 0 < number <= 600:
                raise ValueError('Timeouts must be positive and no greater than 600 seconds')
        self.root = Path(root).expanduser().absolute()
        self.http_open = http_open
        self.runner = runner or subprocess.run
        self.timeout = timeout
        self.socket_timeout = socket_timeout
        self.git_timeout = git_timeout
        self.lock_timeout = lock_timeout
        self.cancel_event = cancel_event

    def _check_cancel(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise ReleaseStoreError('Official CLI preparation cancelled')

    @contextmanager
    def _locked(self):
        if _is_redirect(self.root):
            raise ReleaseStoreError('Version-store root must not be a filesystem redirect')
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / '.prepare.lock'
        if _is_redirect(path):
            raise ReleaseStoreError('Version-store lock must not be a filesystem redirect')
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        with os.fdopen(os.open(path, flags, 0o600), 'r+b', buffering=0) as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ReleaseStoreError('Invalid version-store lock')
            if os.name == 'nt':
                import msvcrt
                def acquire():
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                def release():
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                def acquire():
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                def release():
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            deadline = time.monotonic() + self.lock_timeout
            while True:
                try:
                    acquire()
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Another CLI release preparation holds the store lock') from error
                    time.sleep(min(.05, max(0, deadline - time.monotonic())))
            try:
                if not os.fstat(stream.fileno()).st_size:
                    stream.write(b'\0')
                yield
            finally:
                release()

    def _read_http(self, url, *, limit, destination=None, expected_size=None, with_url=False):
        self._check_cancel()
        request = Request(url, headers={'User-Agent': 'HandWorkbench-CLIReleaseStore', 'Accept': 'application/vnd.github+json' if url.startswith(API_URL + '/') else 'application/octet-stream'})
        opener = self.http_open or build_opener(_AssetRedirect()).open
        deadline = time.monotonic() + self.timeout
        chunks = []
        total = 0
        digest = hashlib.sha256()
        with opener(request, timeout=self.socket_timeout) as response:
            if response.status != 200:
                raise HTTPError(url, response.status, 'Official release HTTP request failed', response.headers, None)
            final_url = response.geturl()
            _redirect_url(final_url, url)
            length = response.headers.get('Content-Length')
            if length is not None:
                try:
                    length = int(length)
                except (ValueError, TypeError) as error:
                    raise ReleaseStoreError('Invalid release Content-Length') from error
                if length < 0 or length > limit or (expected_size is not None and length != expected_size):
                    raise ReleaseStoreError('Release response size does not match its metadata')
            while True:
                self._check_cancel()
                if time.monotonic() >= deadline:
                    raise TimeoutError('Official release download timed out')
                block = response.read(min(64 * 1024, limit - total + 1))
                if not block:
                    break
                total += len(block)
                if total > limit:
                    raise ReleaseStoreError('Official release response exceeds the size limit')
                digest.update(block)
                if destination is not None:
                    destination.write(block)
                else:
                    chunks.append(block)
            if (length is not None and total != length) or (expected_size is not None and total != expected_size):
                raise ReleaseStoreError('Official release download was truncated')
        result = digest.hexdigest() if destination is not None else b''.join(chunks)
        return (result, final_url) if with_url else result

    def fetch_release(self, version=None):
        """Discover latest or a selected official release, without credentials.

        Rate limiting and timeouts fall back to GitHub's official release HTML.
        Discovery alone does not establish a binary checksum or availability.
        """
        version = _version(version) if version is not None else None
        url = API_URL + ('/releases/latest' if version is None else '/releases/tags/' + quote('v' + version, safe=''))
        try:
            release = json.loads(self._read_http(url, limit=MAX_METADATA))
        except HTTPError as error:
            if error.code not in (403, 429):
                raise
            return self._html_release(version)
        except TimeoutError:
            return self._html_release(version)
        except URLError as error:
            if not isinstance(error.reason, TimeoutError):
                raise
            return self._html_release(version)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReleaseStoreError('Official release metadata is not valid JSON') from error
        if version is None:
            if not isinstance(release, dict):
                raise ReleaseStoreError('Official release metadata is not an object')
            version = _version(release.get('tag_name'))
        self._asset(version, release)
        release['discovery_source'] = 'github_api'
        return release

    def _html_release(self, version):
        page = REPO_URL + ('/releases/latest' if version is None else '/releases/tag/' + quote('v' + version, safe=''))
        _, final_url = self._read_http(page, limit=MAX_METADATA, with_url=True)
        if version is None:
            prefix = REPO_URL + '/releases/tag/v'
            if not final_url.startswith(prefix):
                raise ReleaseStoreError('Official latest release did not resolve to an explicit tag')
            version = _version(unquote(final_url[len(prefix):]))
        tag = 'v' + version
        expected_page = REPO_URL + '/releases/tag/' + quote(tag, safe='')
        if final_url != expected_page:
            raise ReleaseStoreError('Official release page does not identify the selected version')
        expanded = REPO_URL + '/releases/expanded_assets/' + quote(tag, safe='')
        try:
            parser = _AssetLinks()
            parser.feed(self._read_http(expanded, limit=MAX_METADATA).decode('utf-8'))
            parser.close()
        except UnicodeDecodeError as error:
            raise ReleaseStoreError('Official release HTML is not UTF-8') from error
        assets = []
        seen = set()
        prefix = REPO_URL + '/releases/download/' + quote(tag, safe='') + '/'
        for href in parser.links:
            url = urljoin(REPO_URL + '/', href)
            if not url.startswith(prefix) or url in seen:
                continue
            seen.add(url)
            name = unquote(url[len(prefix):])
            digest = parser.digest_for(url, name)
            asset = dict(name=name, browser_download_url=url, size=None, digest=digest)
            if digest is not None:
                asset['digest_source'] = 'github_release_html_asset_digest'
            self._asset_url(asset, tag)
            assets.append(asset)
        release = dict(tag_name=tag, html_url=expected_page, draft=False, assets=assets, discovery_source='github_release_html', assets_page=expanded)
        self._asset(version, release)
        return release

    def _asset(self, version, release):
        tag = 'v' + version
        if not isinstance(release, dict) or release.get('tag_name') != tag or release.get('html_url') != REPO_URL + '/releases/tag/' + quote(tag, safe='') or release.get('draft') is not False:
            raise ReleaseStoreError('Metadata is not the selected published official release')
        assets = release.get('assets')
        if not isinstance(assets, list) or len(assets) > 200:
            raise ReleaseStoreError('Invalid official release assets')
        name = 'wuji_' + version + '_x86_64-unknown-linux-gnu.tar.gz'
        matches = [asset for asset in assets if isinstance(asset, dict) and asset.get('name') == name]
        if len(matches) != 1:
            raise ReleaseStoreError('Release must list exactly one matching Linux x86_64 archive')
        self._asset_url(matches[0], tag)
        return dict(size=None, **matches[0]) if 'size' not in matches[0] else matches[0]

    @staticmethod
    def _asset_url(asset, tag):
        name = asset.get('name')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.+-]{1,160}', name) or '..' in name:
            raise ReleaseStoreError('Invalid official asset name')
        expected = REPO_URL + '/releases/download/' + quote(tag, safe='') + '/' + quote(name, safe='')
        if asset.get('browser_download_url') != expected:
            raise ReleaseStoreError('Release asset URL is not the selected official repository and tag')
        size = asset.get('size')
        if size is not None and (type(size) is not int or not 0 < size <= MAX_ARCHIVE):
            raise ReleaseStoreError('Invalid official asset size')
        return expected

    def _expected_sha(self, release, asset, tag):
        digest = asset.get('digest')
        if digest is not None:
            if not isinstance(digest, str) or not re.fullmatch(r'sha256:[a-fA-F0-9]{64}', digest):
                raise ReleaseStoreError('Release asset has no valid SHA-256 digest')
            source = 'github_release_html_asset_digest' if asset.get('digest_source') == 'github_release_html_asset_digest' else 'github_asset_digest'
            return digest[7:].lower(), source
        names = (asset['name'] + '.sha256', 'SHA256SUMS', 'sha256sums.txt')
        checksums = [item for item in release['assets'] if isinstance(item, dict) and item.get('name') in names]
        if len(checksums) != 1:
            raise ReleaseStoreError('Official metadata must provide a SHA-256 digest or one unambiguous checksum asset')
        checksum = checksums[0]
        url = self._asset_url(checksum, tag)
        try:
            text = self._read_http(url, limit=MAX_METADATA, expected_size=checksum.get('size')).decode('utf-8')
        except UnicodeDecodeError as error:
            raise ReleaseStoreError('Official checksums are not UTF-8 text') from error
        found = []
        for line in text.splitlines():
            match = re.fullmatch(r'([a-fA-F0-9]{64})[ \t]+\*?([^\r\n]+)', line)
            if match and match[2] == asset['name']:
                found.append(match[1].lower())
            elif checksum['name'] == asset['name'] + '.sha256' and _DIGEST.fullmatch(line.strip()):
                found.append(line.strip().lower())
        if len(found) != 1:
            raise ReleaseStoreError('Checksum file must identify the selected archive exactly once')
        return found[0], url

    @staticmethod
    def _member_path(name):
        if not isinstance(name, str) or '\\' in name or name.startswith('/'):
            raise ReleaseStoreError('Unsafe archive path')
        while name.startswith('./'):
            name = name[2:]
        name = name.rstrip('/')
        parts = name.split('/')
        for part in parts:
            stem = part.split('.')[0].upper()
            if not part or part in {'.', '..'} or part.endswith(('.', ' ')) or any(ord(char) < 32 or char in ':<>"|?*' for char in part) or stem in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(COM|LPT)[0-9]', stem):
                raise ReleaseStoreError('Unsafe archive path')
        return PurePosixPath(*parts)

    def _extract(self, archive, destination):
        self._check_cancel()
        destination.mkdir()
        entries = {}
        files = []
        expanded = 0
        deadline = time.monotonic() + self.timeout
        with _open_archive(archive) as source:
            for count, member in enumerate(source, 1):
                self._check_cancel()
                if time.monotonic() >= deadline:
                    raise TimeoutError('Official release extraction timed out')
                if count > MAX_MEMBERS:
                    raise ReleaseStoreError('Archive contains too many entries')
                if not (member.isdir() or member.isreg()) or member.sparse is not None:
                    raise ReleaseStoreError('Archive links, sparse files and special entries are not accepted')
                if member.isdir() and member.size:
                    raise ReleaseStoreError('Archive directories must not contain file data')
                if member.isdir() and member.name in {'.', './'}:
                    continue
                relative = self._member_path(member.name)
                key = str(relative).casefold()
                if key in entries or any(entries.get(str(parent).casefold()) == 'file' for parent in relative.parents) or (member.isreg() and any(item.startswith(key + '/') for item in entries)):
                    raise ReleaseStoreError('Archive contains colliding paths')
                entries[key] = 'dir' if member.isdir() else 'file'
                target = destination.joinpath(*relative.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                expanded += member.size
                if member.size < 0 or expanded > MAX_EXTRACTED:
                    raise ReleaseStoreError('Expanded archive exceeds the size limit')
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                copied = 0
                with source.extractfile(member) as content, target.open('xb') as stream:
                    while True:
                        self._check_cancel()
                        block = content.read(64 * 1024)
                        if not block:
                            break
                        if time.monotonic() >= deadline:
                            raise TimeoutError('Official release extraction timed out')
                        copied += len(block)
                        if copied > member.size:
                            raise ReleaseStoreError('Archive member exceeds its declared size')
                        stream.write(block)
                        digest.update(block)
                if copied != member.size:
                    raise ReleaseStoreError('Truncated archive member')
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
                files.append(dict(path=relative.as_posix(), size=copied, sha256=digest.hexdigest()))
        binaries = [entry['path'] for entry in files if PurePosixPath(entry['path']).name == 'wuji']
        if len(binaries) != 1:
            raise ReleaseStoreError('Archive must contain exactly one wuji executable candidate')
        return sorted(files, key=lambda entry: entry['path']), binaries[0]

    def _git(self, arguments, home):
        self._check_cancel()
        # Public-repository access does not consult the user's git config,
        # credential helper, askpass, netrc home or token environment variables.
        allowed = {'PATH', 'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TMP', 'TEMP', 'TMPDIR', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}
        env = {key: value for key, value in os.environ.items() if key in allowed}
        env.update(HOME=str(home), USERPROFILE=str(home), GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0', GIT_ASKPASS='', SSH_ASKPASS='')
        command = ['git', '-c', 'credential.helper=', '-c', 'core.hooksPath=' + str(home), '-c', 'protocol.file.allow=never', '-c', 'protocol.ext.allow=never'] + arguments
        result = self.runner(command, capture_output=True, text=True, stdin=subprocess.DEVNULL, shell=False, timeout=self.git_timeout, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self._check_cancel()
        if result.returncode:
            raise ReleaseStoreError('Official git source preparation failed')
        return result.stdout.strip()

    def _source(self, metadata, tag):
        home = metadata / 'git-home'
        home.mkdir()
        source = metadata / 'source'
        self._git(['clone', '--no-checkout', '--no-tags', '--depth=1', '--', GIT_URL, str(source)], home)
        self._git(['-C', str(source), 'fetch', '--no-tags', '--depth=1', 'origin', 'refs/tags/' + tag + ':refs/tags/' + tag], home)
        return self._source_commit(source, home, tag)

    def _source_commit(self, source, home, tag):
        if self._git(['-C', str(source), 'remote', 'get-url', 'origin'], home) != GIT_URL:
            raise ReleaseStoreError('Source repository is not the official CLI repository')
        commit = self._git(['-C', str(source), 'rev-parse', '--verify', 'refs/tags/' + tag + '^{commit}'], home)
        if not re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})', commit):
            raise ReleaseStoreError('Official release tag did not resolve to a commit')
        return commit

    @staticmethod
    def _write_json(path, value):
        with path.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())

    def _cached(self, directory, version, asset, digest):
        if _is_redirect(directory) or not directory.is_dir():
            raise ReleaseStoreError('Existing version directory is invalid')
        manifest_path = directory / 'manifest.json'
        if _is_redirect(manifest_path) or manifest_path.stat().st_size > MAX_METADATA:
            raise ReleaseStoreError('Existing version manifest is invalid')
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (ValueError, UnicodeDecodeError) as error:
            raise ReleaseStoreError('Existing version manifest is invalid') from error
        expected_paths = dict(archive=asset['name'], payload='payload', source='metadata/source')
        if not isinstance(manifest, dict) or not isinstance(manifest.get('paths'), dict) or manifest.get('schema') != 1 or manifest.get('version') != version or manifest.get('repository') != REPOSITORY or manifest.get('tag') != 'v' + version or manifest.get('archive_sha256') != digest or manifest.get('status') != 'prepared' or any(manifest['paths'].get(key) != value for key, value in expected_paths.items()):
            raise ReleaseStoreError('Existing version identity does not match the selected release')
        for path in directory.rglob('*'):
            if _is_redirect(path):
                raise ReleaseStoreError('Existing version contains a filesystem redirect')
        archive = directory / asset['name']
        if (asset['size'] is not None and archive.stat().st_size != asset['size']) or _file_hash(archive) != digest:
            raise ReleaseStoreError('Existing release archive failed SHA-256 verification')
        entries = manifest.get('files')
        if not isinstance(entries, list) or not 0 < len(entries) <= MAX_MEMBERS:
            raise ReleaseStoreError('Existing payload manifest is invalid')
        expected_files = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ReleaseStoreError('Existing payload manifest is invalid')
            relative = self._member_path(entry.get('path'))
            file = directory / 'payload' / Path(*relative.parts)
            if not file.is_file() or file.stat().st_size != entry.get('size') or _file_hash(file) != entry.get('sha256'):
                raise ReleaseStoreError('Existing CLI payload failed integrity verification')
            expected_files.add(relative.as_posix())
        actual_files = {file.relative_to(directory / 'payload').as_posix() for file in (directory / 'payload').rglob('*') if file.is_file()}
        binary = manifest.get('paths', {}).get('binary')
        if actual_files != expected_files or not isinstance(binary, str) or not binary.startswith('payload/') or binary[8:] not in expected_files or PurePosixPath(binary).name != 'wuji':
            raise ReleaseStoreError('Existing CLI payload has unexpected files or binary path')
        commit = self._source_commit(directory / 'metadata/source', directory / 'metadata/git-home', 'v' + version)
        if commit != manifest.get('commit'):
            raise ReleaseStoreError('Existing source tag no longer matches its manifest')
        return dict(manifest, directory=str(directory))

    def prepare(self, version, *, release=None):
        """Atomically prepare one release; do not run it or change a live pointer."""
        self._check_cancel()
        version = _version(version)
        tag = 'v' + version
        release = self.fetch_release(version) if release is None else release
        asset = self._asset(version, release)
        # Bound and detach caller-owned JSON before taking any filesystem action.
        encoded = json.dumps(release).encode('utf-8')
        if len(encoded) > MAX_METADATA:
            raise ReleaseStoreError('Release metadata exceeds the size limit')
        release = json.loads(encoded)
        asset = self._asset(version, release)
        with self._locked():
            digest, checksum_source = self._expected_sha(release, asset, tag)
            final = self.root / version
            if final.exists() or _is_redirect(final):
                return self._cached(final, version, asset, digest)
            stage = Path(tempfile.mkdtemp(prefix='.prepare-' + version + '-', dir=self.root))
            try:
                partial = stage / (asset['name'] + '.partial')
                with partial.open('xb') as stream:
                    actual_digest = self._read_http(asset['browser_download_url'], limit=MAX_ARCHIVE, destination=stream, expected_size=asset['size'])
                    stream.flush()
                    os.fsync(stream.fileno())
                if actual_digest != digest:
                    raise ReleaseStoreError('Official CLI archive failed SHA-256 verification')
                archive = stage / asset['name']
                os.replace(partial, archive)
                files, binary = self._extract(archive, stage / 'payload')
                metadata = stage / 'metadata'
                metadata.mkdir()
                self._write_json(metadata / 'release.json', release)
                commit = self._source(metadata, tag)
                manifest = dict(schema=1, repository=REPOSITORY, version=version, tag=tag, commit=commit,
                                release_url=release['html_url'], asset_url=asset['browser_download_url'],
                                archive_sha256=digest, checksum_source=checksum_source,
                                paths=dict(archive=asset['name'], payload='payload', binary='payload/' + binary, source='metadata/source'),
                                files=files, status='prepared', compatibility_checked=False, cli_executed=False,
                                prepared_at=datetime.now(timezone.utc).isoformat())
                self._write_json(stage / 'manifest.json', manifest)
                self._check_cancel()
                os.replace(stage, final)
                return dict(manifest, directory=str(final))
            finally:
                if stage.exists():
                    # Only delete this invocation's verified, direct staging child.
                    if stage.parent != self.root or not stage.name.startswith('.prepare-') or _is_redirect(stage) or stage.resolve().parent != self.root.resolve():
                        raise ReleaseStoreError('Unsafe staging cleanup path')
                    def remove_readonly(function, path, error):
                        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
                        function(path)
                    shutil.rmtree(stage, onexc=remove_readonly)
