"""Synthetic release/archive/git tests; never access GitHub, CLI or devices."""
import copy
import hashlib
from html import escape
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import cli_release_store as store_module
from cli_release_store import API_URL, GIT_URL, REPO_URL, CLIReleaseStore, ReleaseStoreError


VERSION = '2026.10.12'
TAG = 'v' + VERSION
COMMIT = 'a' * 40


def archive_bytes(entries=None):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w:gz') as archive:
        for name, data, kind in entries or [('wuji', b'synthetic CLI; not executable', tarfile.REGTYPE)]:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.mode = 0o755
            if kind == tarfile.REGTYPE:
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            else:
                member.linkname = data.decode() if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ''
                archive.addfile(member)
    return result.getvalue()


def release_metadata(data, version=VERSION):
    name = 'wuji_' + version + '_x86_64-unknown-linux-gnu.tar.gz'
    url = REPO_URL + '/releases/download/v' + version + '/' + name
    return dict(tag_name='v' + version, html_url=REPO_URL + '/releases/tag/v' + version, draft=False,
                assets=[dict(name=name, browser_download_url=url, size=len(data), digest='sha256:' + hashlib.sha256(data).hexdigest())])


def html_asset_row(name, digest, *, href=None, text=True, clipboard=True, label=None):
    # Minimal synthetic structure matching the public expanded_assets page
    # observed 2026-10-21: one Box-row, one download link, displayed digest and
    # a copy control naming that exact file. No GitHub page content is copied.
    href = href or REPO_URL + '/releases/download/' + TAG + '/' + name
    label = 'Copy to clipboard digest for ' + name if label is None else label
    row = '<li data-view-component="true" class="Box-row d-flex"><div><a href="' + escape(href, quote=True) + '"><span class="text-bold">' + escape(name) + '</span></a></div><div>'
    if digest is not None:
        if text:
            row += '<span class="Truncate text-mono"><span class="Truncate-text">' + escape(digest) + '</span></span>'
        if clipboard:
            row += '<span><clipboard-copy aria-label="' + escape(label, quote=True) + '" value="' + escape(digest, quote=True) + '"></clipboard-copy></span>'
    return row + '</div></li>'


class Response(io.BytesIO):
    def __init__(self, data, url, *, status=200, length=True):
        super().__init__(data)
        self.url = url
        self.status = status
        self.headers = {'Content-Length': str(len(data))} if length else {}

    def geturl(self):
        return self.url


class FakeNetwork:
    def __init__(self):
        self.responses = {}
        self.calls = []

    def __call__(self, request, *, timeout):
        self.calls.append((request.full_url, dict(request.header_items()), timeout))
        value = self.responses[request.full_url]
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            return value(request.full_url)
        return Response(value, request.full_url)


class FakeGit:
    def __init__(self):
        self.calls = []
        self.origin = GIT_URL
        self.commit = COMMIT
        self.error = None
        self.returncode = 0
        self.readonly = False

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.error:
            raise self.error
        if 'clone' in command:
            (Path(command[-1])/'.git').mkdir(parents=True)
            if self.readonly:
                file = Path(command[-1])/'.git/synthetic-readonly-pack'
                file.write_bytes(b'synthetic git object')
                file.chmod(0o444)
        output = self.origin if 'get-url' in command else self.commit if 'rev-parse' in command else ''
        return SimpleNamespace(returncode=self.returncode, stdout=output, stderr='')


class CLIReleaseStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)/'versions'
        self.network = FakeNetwork()
        self.git = FakeGit()
        self.store = CLIReleaseStore(self.root, http_open=self.network, runner=self.git, timeout=2, git_timeout=3, lock_timeout=.1)
        self.data = archive_bytes()
        self.release = release_metadata(self.data)
        self.url = self.release['assets'][0]['browser_download_url']
        self.network.responses[self.url] = self.data

    def prepare(self, release=None):
        return self.store.prepare(VERSION, release=self.release if release is None else release)

    def assert_no_partial(self):
        self.assertFalse((self.root/VERSION).exists())
        self.assertEqual(list(self.root.glob('.prepare-*')), [])

    def html_fallback(self, api_error, *, selected=False, html=None):
        api = API_URL + ('/releases/tags/' + TAG if selected else '/releases/latest')
        page = REPO_URL + ('/releases/tag/' + TAG if selected else '/releases/latest')
        expanded = REPO_URL + '/releases/expanded_assets/' + TAG
        self.network.responses[api] = api_error
        self.network.responses[page] = lambda url: Response(b'<html>official release</html>', self.release['html_url'])
        href = self.url.removeprefix('https://github.com')
        self.network.responses[expanded] = (html if html is not None else '<a href="' + href + '">CLI</a>').encode()

    def test_prepares_verified_archive_and_git_provenance_without_activation(self):
        result = self.prepare()
        directory = self.root/VERSION
        self.assertEqual(result['status'], 'prepared')
        self.assertFalse(result['compatibility_checked'])
        self.assertFalse(result['cli_executed'])
        self.assertEqual(result['commit'], COMMIT)
        self.assertEqual(result['archive_sha256'], hashlib.sha256(self.data).hexdigest())
        self.assertEqual((directory/result['paths']['binary']).read_bytes(), b'synthetic CLI; not executable')
        self.assertTrue((directory/'metadata/source/.git').is_dir())
        self.assertEqual(json.loads((directory/'metadata/release.json').read_text())['tag_name'], TAG)
        self.assertFalse((self.root/'current').exists())
        self.assertEqual(list(self.root.glob('.prepare-*')), [])
        commands = [command for command, _ in self.git.calls]
        self.assertEqual(len(commands), 4)
        self.assertIn('--no-checkout', commands[0])
        self.assertIn('refs/tags/' + TAG + ':refs/tags/' + TAG, commands[1])
        self.assertEqual(commands[-1][-1], 'refs/tags/' + TAG + '^{commit}')
        for command, options in self.git.calls:
            self.assertEqual(command[0], 'git')
            self.assertFalse(options['shell'])
            self.assertEqual(options['timeout'], 3)
            self.assertEqual(options['stdin'], subprocess.DEVNULL)
            self.assertEqual(options['env']['GIT_TERMINAL_PROMPT'], '0')
            self.assertNotIn('GITHUB_TOKEN', options['env'])

    def test_fetch_selected_release_uses_fixed_anonymous_api(self):
        url = API_URL + '/releases/tags/' + TAG
        self.network.responses[url] = json.dumps(self.release).encode()
        result = self.store.fetch_release('v' + VERSION)
        self.assertEqual(result['tag_name'], TAG)
        self.assertEqual(result['discovery_source'], 'github_api')
        called, headers, timeout = self.network.calls[0]
        self.assertEqual(called, url)
        self.assertFalse(any(key.lower() == 'authorization' for key in headers))
        self.assertEqual(timeout, 2)
        self.assertEqual(self.git.calls, [])
        self.assertFalse(self.root.exists())

    def test_socket_timeout_defaults_to_total_and_rejects_invalid_values(self):
        self.assertEqual(self.store.socket_timeout, self.store.timeout)
        self.assertEqual(CLIReleaseStore(self.root, timeout=300, socket_timeout=20).socket_timeout, 20)
        for value in (0, -1, 601, True, False, float('nan'), float('inf'), '20'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                CLIReleaseStore(self.root, socket_timeout=value)
        self.assertFalse(self.root.exists())

    def test_socket_timeout_does_not_shorten_total_download_deadline(self):
        clock = [0.]
        reads = []

        class ProgressResponse(Response):
            def read(response, size):
                reads.append(size)
                clock[0] += .4
                return super().read(min(size, 2))

        self.network.responses[self.url] = lambda url: ProgressResponse(b'abcdefgh', url)
        store = CLIReleaseStore(self.root, http_open=self.network, timeout=3, socket_timeout=1)
        with patch.object(store_module.time, 'monotonic', side_effect=lambda: clock[0]):
            self.assertEqual(store._read_http(self.url, limit=16), b'abcdefgh')
        self.assertGreater(clock[0], store.socket_timeout)
        self.assertLess(clock[0], store.timeout)
        self.assertEqual(self.network.calls[0][2], 1)
        self.assertEqual(len(reads), 5)

    def test_total_download_deadline_still_applies_with_longer_socket_timeout(self):
        reads = []

        class SmallResponse(Response):
            def read(response, size):
                reads.append(size)
                return super().read(min(size, 2))

        self.network.responses[self.url] = lambda url: SmallResponse(b'abcdefgh', url)
        store = CLIReleaseStore(self.root, http_open=self.network, timeout=1, socket_timeout=20)
        with patch.object(store_module.time, 'monotonic', side_effect=[0., 0., .4, .8, 1.2]):
            with self.assertRaisesRegex(TimeoutError, 'download timed out'):
                store._read_http(self.url, limit=16)
        self.assertEqual(self.network.calls[0][2], 20)
        self.assertEqual(len(reads), 3)

    def test_preexisting_cancellation_performs_no_http_git_or_store_creation(self):
        cancel = threading.Event()
        cancel.set()
        store = CLIReleaseStore(self.root, http_open=self.network, runner=self.git, cancel_event=cancel)
        for operation in (store.fetch_release, lambda: store.prepare(VERSION, release=self.release), lambda: store._git(['--version'], self.root)):
            with self.subTest(operation=operation), self.assertRaisesRegex(ReleaseStoreError, 'cancelled'):
                operation()
        self.assertEqual(self.network.calls, [])
        self.assertEqual(self.git.calls, [])
        self.assertFalse(self.root.exists())

    def test_midstream_cancellation_cleans_partial_and_preserves_old_files(self):
        cancel = threading.Event()
        reads = []
        response = None
        old = self.root/'2026.9.22'/'keep'
        old.parent.mkdir(parents=True)
        old.write_bytes(b'previous verified version')

        class CancelResponse(Response):
            def read(current, size):
                reads.append(size)
                block = super().read(min(size, 7))
                cancel.set()
                return block

        def open_response(url):
            nonlocal response
            response = CancelResponse(self.data, url)
            return response

        self.network.responses[self.url] = open_response
        store = CLIReleaseStore(self.root, http_open=self.network, runner=self.git, cancel_event=cancel)
        with self.assertRaisesRegex(ReleaseStoreError, 'cancelled'):
            store.prepare(VERSION, release=self.release)
        self.assert_no_partial()
        self.assertEqual(old.read_bytes(), b'previous verified version')
        self.assertEqual(len(reads), 1)
        self.assertTrue(response.closed)
        self.assertEqual(self.git.calls, [])

    def test_cancellation_between_archive_members_cleans_stage_and_preserves_old_files(self):
        cancel = threading.Event()
        data = archive_bytes([('package/', b'', tarfile.DIRTYPE), ('package/wuji', b'never run', tarfile.REGTYPE)])
        release = release_metadata(data)
        self.network.responses[self.url] = data
        old = self.root/'2026.9.22'/'keep'
        old.parent.mkdir(parents=True)
        old.write_bytes(b'previous version')
        store = CLIReleaseStore(self.root, http_open=self.network, runner=self.git, cancel_event=cancel)
        original = store._member_path

        def cancel_after_directory(name):
            relative = original(name)
            if name == 'package':
                cancel.set()
            return relative

        # tarfile normalizes directory names before the per-member safety check.
        with patch.object(store, '_member_path', side_effect=cancel_after_directory):
            with self.assertRaisesRegex(ReleaseStoreError, 'cancelled'):
                store.prepare(VERSION, release=release)
        self.assert_no_partial()
        self.assertEqual(old.read_bytes(), b'previous version')
        self.assertEqual(self.git.calls, [])

    def test_cancellation_while_git_returns_cleans_stage_and_stops_later_commands(self):
        cancel = threading.Event()
        old = self.root/'2026.9.22'/'keep'
        old.parent.mkdir(parents=True)
        old.write_bytes(b'previous version')

        def cancel_after_git(command, **options):
            result = self.git(command, **options)
            cancel.set()
            return result

        store = CLIReleaseStore(self.root, http_open=self.network, runner=cancel_after_git, cancel_event=cancel)
        with self.assertRaisesRegex(ReleaseStoreError, 'cancelled'):
            store.prepare(VERSION, release=self.release)
        self.assert_no_partial()
        self.assertEqual(old.read_bytes(), b'previous version')
        self.assertEqual(len(self.git.calls), 1)
        self.assertIn('clone', self.git.calls[0][0])

    def test_latest_api_discovers_actual_tag(self):
        self.network.responses[API_URL + '/releases/latest'] = json.dumps(self.release).encode()
        self.assertEqual(self.store.fetch_release()['tag_name'], TAG)

    def test_rate_limit_and_timeout_fallback_parse_real_links(self):
        for error in (HTTPError(API_URL, 403, 'limit', {}, None), HTTPError(API_URL, 429, 'limit', {}, None), TimeoutError('slow'), URLError(TimeoutError('slow'))):
            with self.subTest(error=repr(error)):
                self.html_fallback(error)
                result = self.store.fetch_release()
                self.assertEqual(result['tag_name'], TAG)
                self.assertEqual(result['assets'][0]['browser_download_url'], self.url)
                self.assertIsNone(result['assets'][0]['size'])
                self.assertIsNone(result['assets'][0]['digest'])
                self.assertEqual(result['discovery_source'], 'github_release_html')
        self.assertFalse(self.root.exists())

    def test_selected_html_fallback_keeps_selected_tag(self):
        self.html_fallback(TimeoutError('slow'), selected=True)
        self.assertEqual(self.store.fetch_release(VERSION)['tag_name'], TAG)

    def test_fallback_without_checksum_is_discovery_only(self):
        self.html_fallback(TimeoutError('slow'))
        result = self.store.fetch_release()
        with self.assertRaisesRegex(ReleaseStoreError, 'SHA-256'):
            self.prepare(result)
        self.assertEqual(self.git.calls, [])

    def test_html_unknown_size_prepares_only_with_actual_checksum_link(self):
        checksum_url = self.url + '.sha256'
        html = '<a href="' + self.url + '">CLI</a><a href="' + checksum_url + '">sum</a>'
        self.html_fallback(TimeoutError('slow'), html=html)
        self.network.responses[checksum_url] = (hashlib.sha256(self.data).hexdigest() + '  ' + self.release['assets'][0]['name'] + '\n').encode()
        release = self.store.fetch_release()
        self.assertEqual(self.prepare(release)['checksum_source'], checksum_url)

    def test_html_asset_digest_prepares_exact_linux_archive_after_api_limit(self):
        name = self.release['assets'][0]['name']
        other = name.replace('x86_64', 'aarch64')
        digest = self.release['assets'][0]['digest']
        html = '<ul>' + html_asset_row(other, 'sha256:' + '1' * 64) + html_asset_row(name, digest) + '</ul>'
        self.html_fallback(HTTPError(API_URL, 403, 'limit', {}, None), html=html)
        release = self.store.fetch_release()
        selected = next(asset for asset in release['assets'] if asset['name'] == name)
        self.assertEqual(selected['digest'], digest)
        self.assertEqual(selected['digest_source'], 'github_release_html_asset_digest')
        result = self.prepare(release)
        self.assertEqual(result['archive_sha256'], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(result['checksum_source'], 'github_release_html_asset_digest')
        self.assertFalse(result['cli_executed'])
        downloads = [url for url, _, _ in self.network.calls if '/releases/download/' in url]
        self.assertEqual(downloads, [self.url])

    def test_html_asset_digest_allows_display_or_named_copy_control(self):
        name = self.release['assets'][0]['name']
        digest = self.release['assets'][0]['digest']
        for options in (dict(clipboard=False), dict(text=False), dict(href=self.url.removeprefix('https://github.com'))):
            with self.subTest(options=options):
                self.html_fallback(TimeoutError('slow'), html=html_asset_row(name, digest.upper().replace('SHA256:', 'sha256:'), **options))
                result = self.store.fetch_release()
                self.assertEqual(result['assets'][0]['digest'], digest)

    def test_html_never_borrows_digest_from_another_row_or_free_text(self):
        name = self.release['assets'][0]['name']
        digest = self.release['assets'][0]['digest']
        target = html_asset_row(name, None)
        other = html_asset_row(name.replace('x86_64', 'aarch64'), digest)
        arbitrary = target.replace('</div></li>', '<span class="Truncate-text">' + digest[7:] + '</span></div></li>')
        unstructured = target.replace('</div></li>', '<div>' + digest + '</div></div></li>')
        for html in (other + target, '<span class="Truncate-text">' + digest + '</span>' + target, arbitrary, unstructured):
            with self.subTest(html=html):
                self.html_fallback(TimeoutError('slow'), html=html)
                result = self.store.fetch_release()
                selected = next(asset for asset in result['assets'] if asset['name'] == name)
                self.assertIsNone(selected['digest'])
                with self.assertRaisesRegex(ReleaseStoreError, 'SHA-256'):
                    self.prepare(result)
                self.assert_no_partial()
        self.assertEqual(self.git.calls, [])

    def test_html_copy_digest_must_name_exact_asset_even_with_matching_display(self):
        name = self.release['assets'][0]['name']
        digest = self.release['assets'][0]['digest']
        for label in ('Copy to clipboard digest for other.tar.gz', 'Copy to clipboard', ''):
            with self.subTest(label=label):
                self.html_fallback(TimeoutError('slow'), html=html_asset_row(name, digest, label=label))
                with self.assertRaisesRegex(ReleaseStoreError, 'identify its asset'):
                    self.store.fetch_release()
        self.assertFalse(self.root.exists())

    def test_html_disagreeing_display_and_copy_digests_are_rejected(self):
        name = self.release['assets'][0]['name']
        digest = self.release['assets'][0]['digest']
        html = html_asset_row(name, digest).replace('value="' + digest + '"', 'value="sha256:' + '0' * 64 + '"')
        self.html_fallback(TimeoutError('slow'), html=html)
        with self.assertRaisesRegex(ReleaseStoreError, 'disagree'):
            self.store.fetch_release()

    def test_html_malformed_digest_is_not_treated_as_a_valid_checksum(self):
        name = self.release['assets'][0]['name']
        for options in (dict(clipboard=False), dict(text=False)):
            with self.subTest(options=options):
                self.html_fallback(TimeoutError('slow'), html=html_asset_row(name, 'sha256:' + '0' * 63, **options))
                with self.assertRaises(ReleaseStoreError):
                    self.store.fetch_release()

    def test_html_duplicate_multi_asset_nested_and_unclosed_rows_are_not_associated(self):
        name = self.release['assets'][0]['name']
        digest = self.release['assets'][0]['digest']
        row = html_asset_row(name, digest)
        other = html_asset_row(name.replace('x86_64', 'aarch64'), digest)
        extra_link = '<a href="' + self.url.replace('x86_64', 'aarch64') + '">another asset</a>'
        for html in (row + row, row.replace('</li>', extra_link + '</li>'), row.replace('</li>', other + '</li>'), row.replace('</div></li>', '</li>')):
            with self.subTest(html=html):
                self.html_fallback(TimeoutError('slow'), html=html)
                with self.assertRaises(ReleaseStoreError):
                    self.store.fetch_release()
        # A row never closed supplies no digest; discovery alone is permitted.
        self.html_fallback(TimeoutError('slow'), html=row.removesuffix('</li>'))
        result = self.store.fetch_release()
        self.assertIsNone(result['assets'][0]['digest'])

    def test_html_missing_fresh_digest_never_uses_existing_cached_sha(self):
        previous = self.prepare()
        self.network.calls.clear()
        self.git.calls.clear()
        self.html_fallback(HTTPError(API_URL, 429, 'limit', {}, None), html=html_asset_row(self.release['assets'][0]['name'], None))
        fresh = self.store.fetch_release()
        with self.assertRaisesRegex(ReleaseStoreError, 'SHA-256'):
            self.prepare(fresh)
        self.assertEqual(self.git.calls, [])
        self.assertFalse(any('/releases/download/' in url for url, _, _ in self.network.calls))
        self.assertTrue((Path(previous['directory'])/'manifest.json').is_file())

    def test_fallback_rejects_unrelated_redirect_and_missing_actual_archive_link(self):
        self.html_fallback(TimeoutError('slow'))
        self.network.responses[REPO_URL + '/releases/latest'] = lambda url: Response(b'', 'https://github.com/other/repo/releases/tag/' + TAG)
        with self.assertRaises(ReleaseStoreError):
            self.store.fetch_release()
        self.html_fallback(TimeoutError('slow'), html='<a href="https://example.com/' + self.release['assets'][0]['name'] + '">bad</a>')
        with self.assertRaises(ReleaseStoreError):
            self.store.fetch_release()

    def test_other_api_errors_do_not_claim_a_latest_version(self):
        self.network.responses[API_URL + '/releases/latest'] = HTTPError(API_URL, 404, 'not found', {}, None)
        with self.assertRaises(HTTPError):
            self.store.fetch_release()
        self.assertEqual(len(self.network.calls), 1)

    def test_digest_mismatch_has_no_committed_cache_and_retry_succeeds(self):
        bad = copy.deepcopy(self.release)
        bad['assets'][0]['digest'] = 'sha256:' + '0' * 64
        with self.assertRaisesRegex(ReleaseStoreError, 'SHA-256'):
            self.prepare(bad)
        self.assert_no_partial()
        self.assertEqual(self.git.calls, [])
        self.assertEqual(self.prepare()['status'], 'prepared')

    def test_checksum_must_identify_selected_archive_once(self):
        release = copy.deepcopy(self.release)
        release['assets'][0]['digest'] = None
        checksum_url = REPO_URL + '/releases/download/' + TAG + '/SHA256SUMS'
        checksum = dict(name='SHA256SUMS', browser_download_url=checksum_url, size=None)
        release['assets'].append(checksum)
        for text in ('0' * 64 + '  wrong.tar.gz\n', ('0' * 64 + '  ' + release['assets'][0]['name'] + '\n') * 2):
            with self.subTest(text=text):
                self.network.responses[checksum_url] = text.encode()
                with self.assertRaises(ReleaseStoreError):
                    self.prepare(release)
                self.assert_no_partial()

    def test_wrong_version_repo_asset_url_and_ambiguous_archive_are_rejected(self):
        variants = []
        for field, value in [('tag_name', 'v2026.9.22'), ('html_url', 'https://github.com/other/repo/releases/tag/' + TAG), ('draft', True)]:
            release = copy.deepcopy(self.release)
            release[field] = value
            variants.append(release)
        for url in (self.url.replace('github.com', 'github.com.evil.test'), self.url + '?token=bad', self.url.replace('/' + TAG + '/', '/v2026.9.22/'), self.url.replace('https://', 'https://user:secret@')):
            release = copy.deepcopy(self.release)
            release['assets'][0]['browser_download_url'] = url
            variants.append(release)
        duplicate = copy.deepcopy(self.release)
        duplicate['assets'].append(copy.deepcopy(duplicate['assets'][0]))
        variants.append(duplicate)
        for release in variants:
            with self.subTest(release=release):
                with self.assertRaises(ReleaseStoreError):
                    self.prepare(release)
        self.assertFalse(self.root.exists())
        self.assertEqual(self.network.calls, [])

    def test_invalid_versions_never_reach_io(self):
        for version in ('../outside', '--upload-pack=evil', 'v../../bad', '2026.10.12/other', '2026..10', None):
            with self.subTest(version=version):
                with self.assertRaises(ReleaseStoreError):
                    self.store.prepare(version, release=self.release)
        self.assertFalse(self.root.exists())

    def test_safe_nested_archive_and_root_marker_are_accepted(self):
        data = archive_bytes([('./', b'', tarfile.DIRTYPE), ('package/', b'', tarfile.DIRTYPE), ('package/wuji', b'never run', tarfile.REGTYPE)])
        release = release_metadata(data)
        self.network.responses[self.url] = data
        self.assertEqual(self.prepare(release)['paths']['binary'], 'payload/package/wuji')

    def test_unsafe_archive_paths_links_special_entries_and_collisions_fail_closed(self):
        cases = [[(name, b'payload', tarfile.REGTYPE)] for name in ('../escape', '/absolute', 'C:/outside', 'a\\outside', 'a/../outside', 'NUL.txt', 'bad:ads', 'name. ', 'a//wuji')]
        cases += [[('wuji', b'../outside', kind)] for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE)]
        cases += [[('wuji', b'a', tarfile.REGTYPE), ('WUJI', b'b', tarfile.REGTYPE)], [('wuji/x', b'a', tarfile.REGTYPE), ('wuji', b'b', tarfile.REGTYPE)]]
        for entries in cases:
            with self.subTest(entries=entries):
                data = archive_bytes(entries)
                release = release_metadata(data)
                self.network.responses[self.url] = data
                with self.assertRaises(ReleaseStoreError):
                    self.prepare(release)
                self.assert_no_partial()
        self.assertFalse((self.root.parent/'escape').exists())

    def test_expansion_and_member_count_limits_fail_without_commit(self):
        with patch.object(store_module, 'MAX_EXTRACTED', 1), self.assertRaises(ReleaseStoreError):
            self.prepare()
        self.assert_no_partial()
        with patch.object(store_module, 'MAX_MEMBERS', 0), self.assertRaises(ReleaseStoreError):
            self.prepare()
        self.assert_no_partial()

    def test_oversized_tar_extension_is_rejected_before_parsing_its_content(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            header = tarfile.TarInfo('pax-header')
            header.type = tarfile.XHDTYPE
            content = b'x' * (64 * 1024 + 1)
            header.size = len(content)
            archive.addfile(header, io.BytesIO(content))
        data = buffer.getvalue()
        self.network.responses[self.url] = data
        with self.assertRaises(ReleaseStoreError):
            self.prepare(release_metadata(data))
        self.assert_no_partial()

    def test_http_redirects_and_size_mismatch_are_rejected(self):
        self.network.responses[self.url] = lambda url: Response(self.data, 'https://example.com/asset')
        with self.assertRaises(ReleaseStoreError):
            self.prepare()
        self.assert_no_partial()
        self.network.responses[self.url] = self.data[:-1]
        with self.assertRaises(ReleaseStoreError):
            self.prepare()
        self.assert_no_partial()

    def test_git_failure_timeout_bad_origin_and_bad_commit_never_commit(self):
        for error, origin, commit in [(TimeoutError('git timeout'), GIT_URL, COMMIT), (None, 'https://github.com/other/repo.git', COMMIT), (None, GIT_URL, 'not a commit')]:
            with self.subTest(error=error, origin=origin, commit=commit):
                self.git.error, self.git.origin, self.git.commit = error, origin, commit
                with self.assertRaises((ReleaseStoreError, TimeoutError)):
                    self.prepare()
                self.assert_no_partial()

    def test_nonzero_git_exit_is_not_prepared(self):
        self.git.returncode = 1
        with self.assertRaises(ReleaseStoreError):
            self.prepare()
        self.assert_no_partial()

    def test_cache_reverified_without_downloading_or_fetching_again(self):
        first = self.prepare()
        self.network.calls.clear()
        self.git.calls.clear()
        self.assertEqual(self.prepare()['commit'], first['commit'])
        self.assertEqual(self.network.calls, [])
        self.assertEqual(len(self.git.calls), 2)
        self.assertFalse(any('fetch' in command or 'clone' in command for command, _ in self.git.calls))

    def test_corrupt_cached_payload_is_rejected_and_preserved_for_review(self):
        result = self.prepare()
        binary = Path(result['directory'])/result['paths']['binary']
        binary.write_bytes(b'tampered')
        with self.assertRaises(ReleaseStoreError):
            self.prepare()
        self.assertEqual(binary.read_bytes(), b'tampered')

    def test_preparing_another_version_keeps_existing_version_and_live_pointer(self):
        first = self.prepare()
        self.root.joinpath('live.json').write_text('{"version":"2026.10.12"}')
        version = '2026.10.13'
        release = release_metadata(self.data, version)
        self.network.responses[release['assets'][0]['browser_download_url']] = self.data
        self.store.prepare(version, release=release)
        self.assertTrue(Path(first['directory']).is_dir())
        self.assertEqual((self.root/'live.json').read_text(), '{"version":"2026.10.12"}')

    def test_store_lock_times_out_then_can_be_reused(self):
        with self.store._locked():
            other = CLIReleaseStore(self.root, http_open=self.network, runner=self.git, lock_timeout=.01)
            with self.assertRaises(TimeoutError):
                other.prepare(VERSION, release=self.release)
        self.assertEqual(self.prepare()['status'], 'prepared')

    def test_concurrent_prepare_downloads_and_clones_only_once(self):
        entered = threading.Event()
        resume = threading.Event()
        results, errors = [], []

        def blocked_download(url):
            entered.set()
            if not resume.wait(3):
                raise TimeoutError('Test did not resume the synthetic download')
            return Response(self.data, url)

        self.network.responses[self.url] = blocked_download
        other = CLIReleaseStore(self.root, http_open=self.network, runner=self.git, lock_timeout=3)

        def run(store):
            try:
                results.append(store.prepare(VERSION, release=self.release))
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=run, args=(store,)) for store in (self.store, other)]
        threads[0].start()
        try:
            self.assertTrue(entered.wait(3))
            threads[1].start()
        finally:
            resume.set()
            for thread in threads:
                if thread.ident is not None:
                    thread.join(4)
                    self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(len(self.network.calls), 1)
        self.assertEqual(sum('clone' in command for command, _ in self.git.calls), 1)

    def test_manifest_commit_failure_keeps_old_version_and_retry_is_possible(self):
        old = self.root/'2026.9.22'
        old.mkdir(parents=True)
        (old/'keep').write_text('old version')
        write = self.store._write_json

        def failed_manifest(path, value):
            if path.name == 'manifest.json':
                raise OSError('Injected disk write failure')
            write(path, value)

        with patch.object(self.store, '_write_json', side_effect=failed_manifest), self.assertRaises(OSError):
            self.prepare()
        self.assert_no_partial()
        self.assertEqual((old/'keep').read_text(), 'old version')
        self.assertEqual(self.prepare()['status'], 'prepared')

    def test_final_directory_commit_failure_cleans_readonly_git_objects(self):
        self.git.readonly = True
        replace = store_module.os.replace

        def failed_commit(source, target):
            if Path(target) == self.root/VERSION:
                raise OSError('Injected final rename failure')
            replace(source, target)

        with patch.object(store_module.os, 'replace', side_effect=failed_commit), self.assertRaises(OSError):
            self.prepare()
        self.assert_no_partial()
        result = self.prepare()
        # Release readonly flag so TemporaryDirectory can clean this successful
        # synthetic version independently of the production cleanup helper.
        (Path(result['directory'])/'metadata/source/.git/synthetic-readonly-pack').chmod(0o644)
        self.assertEqual(result['status'], 'prepared')


if __name__ == '__main__':
    unittest.main()
