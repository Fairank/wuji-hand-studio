"""Synthetic application updater tests: no real network, installer, app or SDK."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import workbench_release as module
from workbench_release import LATEST_URL, REPO_URL, WorkbenchRelease, WorkbenchReleaseError


PAYLOAD = b'MZ SYNTHETIC INSTALLER, NOT EXECUTABLE\n' * 16
VERSION = '0.2.30'
NAME = 'HandWorkbench-' + VERSION + '-windows-x64-setup.exe'


def release(data=PAYLOAD, version=VERSION):
    name = 'HandWorkbench-' + version + '-windows-x64-setup.exe'
    return {'tag_name': 'v' + version, 'html_url': REPO_URL + '/releases/tag/v' + version,
            'draft': False, 'prerelease': False,
            'assets': [{'name': name, 'browser_download_url': REPO_URL + '/releases/download/v' + version + '/' + name,
                        'size': len(data), 'digest': 'sha256:' + hashlib.sha256(data).hexdigest()}]}


class Response(io.BytesIO):
    def __init__(self, data, url, *, status=200, length=None, after_read=None):
        super().__init__(data)
        self.url, self.status, self.after_read = url, status, after_read
        self.headers = {'Content-Length': str(len(data)) if length is None else length}

    def geturl(self):
        return self.url

    def read(self, amount=-1):
        result = super().read(amount)
        if self.after_read:
            self.after_read()
        return result


class Network:
    def __init__(self):
        self.responses, self.calls = {}, []

    def __call__(self, request, *, timeout):
        self.calls.append((request.full_url, dict(request.header_items()), timeout))
        result = self.responses[request.full_url]
        if isinstance(result, BaseException):
            raise result
        return result(request.full_url) if callable(result) else Response(result, request.full_url)


class WorkbenchReleaseTests(unittest.TestCase):
    def setUp(self):
        qa_root = Path(os.environ.get('WUJI_RELEASE_QA_ROOT', str(Path(__file__).resolve().parents[3] / 'outputs' / 'cli-workspace-0229' / 'update-release')))
        qa_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=qa_root)
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'data'
        self.data.mkdir()
        # Existing user data and application pointers must survive all paths.
        self.current = self.data / 'current.json'
        self.previous = self.data / 'previous.json'
        self.current.write_bytes(b'CURRENT USER OWNED POINTER')
        self.previous.write_bytes(b'PREVIOUS USER OWNED POINTER')
        self.network = Network()
        self.release = release()
        self.url = self.release['assets'][0]['browser_download_url']
        self.network.responses[LATEST_URL] = json.dumps(self.release).encode()
        self.network.responses[self.url] = PAYLOAD
        self.store = WorkbenchRelease(self.data, '0.2.29', runner=self.network, timeout=2, socket_timeout=1)

    def tearDown(self):
        self.assertEqual(self.current.read_bytes(), b'CURRENT USER OWNED POINTER')
        self.assertEqual(self.previous.read_bytes(), b'PREVIOUS USER OWNED POINTER')

    def no_partial(self):
        self.assertFalse((self.store.versions / VERSION).exists())
        self.assertEqual(list(self.store.versions.glob('.prepare-*')), [])

    def sidecar(self, data=None):
        self.release['assets'][0]['digest'] = None
        data = data if data is not None else (hashlib.sha256(PAYLOAD).hexdigest() + '  ' + NAME + '\n').encode()
        checksum = {'name': NAME + '.sha256', 'browser_download_url': self.url + '.sha256', 'size': len(data)}
        self.release['assets'].append(checksum)
        self.network.responses[self.url + '.sha256'] = data
        return checksum

    def test_construction_is_pure(self):
        self.assertEqual(self.network.calls, [])
        self.assertFalse((self.data / 'updates').exists())

    def test_check_only_reads_fixed_api_and_compares_version(self):
        result = self.store.check()
        self.assertTrue(result['available'])
        self.assertEqual(result['latest_version'], VERSION)
        self.assertEqual(result['verification'], 'github_asset_digest')
        self.assertEqual([call[0] for call in self.network.calls], [LATEST_URL])
        self.assertNotIn('Authorization', self.network.calls[0][1])
        self.assertFalse((self.data / 'updates').exists())

    def test_unpublished_release_is_distinct_from_network_errors(self):
        self.network.responses[LATEST_URL] = HTTPError(LATEST_URL, 404, 'Not found', {}, None)
        result = self.store.check()
        self.assertFalse(result['available'])
        self.assertEqual(result['reason'], 'unpublished')
        self.assertIsNone(result['latest_version'])

    def test_runner_http_404_response_is_also_unpublished(self):
        self.network.responses[LATEST_URL] = lambda url: Response(b'not found', url, status=404)
        self.assertEqual(self.store.check()['reason'], 'unpublished')

    def test_dns_forbidden_and_timeout_are_not_unpublished(self):
        for error in (URLError('synthetic DNS failure'), HTTPError(LATEST_URL, 403, 'Forbidden', {}, None), TimeoutError('synthetic timeout')):
            with self.subTest(error=error):
                self.network.responses[LATEST_URL] = error
                with self.assertRaises(type(error)):
                    self.store.check()

    def test_numeric_version_comparison_no_lexical_downgrade(self):
        older = release(version='0.2.9')
        self.network.responses[LATEST_URL] = json.dumps(older).encode()
        self.assertFalse(self.store.check()['available'])
        self.network.responses[LATEST_URL] = json.dumps(release(version='0.10.0')).encode()
        self.assertTrue(self.store.check()['available'])

    def test_equal_or_older_version_never_prepares(self):
        for version in ('0.2.29', '0.2.9', '0.1.999'):
            with self.subTest(version=version), self.assertRaisesRegex(WorkbenchReleaseError, 'newer'):
                self.store.prepare(release(version=version))
        self.assertEqual(self.network.calls, [])

    def test_unstable_or_noncanonical_versions_rejected(self):
        for tag in ('0.2.30', 'v0.2.30-rc1', 'v0.2.30+build', 'v01.2.3', 'v0.2', 'v0.2.30.1', 'v0.2.30/../evil', 'v9999999.2.3', None):
            bad = copy.deepcopy(self.release);bad['tag_name'] = tag
            with self.subTest(tag=tag), self.assertRaises(WorkbenchReleaseError):
                self.store.prepare(bad)

    def test_draft_prerelease_or_missing_flags_rejected(self):
        for key in ('draft', 'prerelease'):
            for value in (True, None, 0, 'false'):
                bad = copy.deepcopy(self.release);bad[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(WorkbenchReleaseError):
                    self.store.prepare(bad)

    def test_cross_repo_release_page_and_asset_urls_rejected(self):
        for url in ('https://github.com/Other/repo/releases/tag/v' + VERSION, REPO_URL + '/releases/tag/v0.2.31'):
            bad = copy.deepcopy(self.release);bad['html_url'] = url
            with self.subTest(url=url), self.assertRaises(WorkbenchReleaseError):
                self.store.prepare(bad)
        for url in ('http://' + self.url[8:], self.url + '?download=1', self.url.replace('Fairank', 'Other'), self.url.replace('github.com', 'github.com.evil.invalid'), self.url.replace('/v0.2.30/', '/v0.2.31/')):
            bad = copy.deepcopy(self.release);bad['assets'][0]['browser_download_url'] = url
            with self.subTest(url=url), self.assertRaises(WorkbenchReleaseError):
                self.store.prepare(bad)
        self.assertEqual(self.network.calls, [])

    def test_exact_installer_name_and_unique_asset_required(self):
        for name in ('../' + NAME, NAME.lower(), NAME.replace('x64', 'arm64'), NAME.replace('0.2.30', '0.2.31'), NAME + '.zip'):
            bad = copy.deepcopy(self.release);bad['assets'][0]['name'] = name
            with self.subTest(name=name), self.assertRaises(WorkbenchReleaseError):
                self.store.prepare(bad)
        bad = copy.deepcopy(self.release);bad['assets'] *= 2
        with self.assertRaises(WorkbenchReleaseError):
            self.store.prepare(bad)

    def test_asset_size_and_digest_validation(self):
        for value in (0, -1, True, '123', None, module.MAX_INSTALLER + 1):
            bad = copy.deepcopy(self.release);bad['assets'][0]['size'] = value
            with self.subTest(size=value), self.assertRaises(WorkbenchReleaseError):
                self.store.prepare(bad)
        for value in ('', 'md5:' + 'a' * 32, 'sha256:' + 'x' * 64, 123):
            bad = copy.deepcopy(self.release);bad['assets'][0]['digest'] = value
            with self.subTest(digest=value), self.assertRaises(WorkbenchReleaseError):
                self.store.prepare(bad)

    def test_prepare_verifies_atomically_and_returns_installer_path(self):
        prepared = self.store.prepare(self.store.check())
        target = Path(prepared['installer'])
        self.assertTrue(target.is_absolute())
        self.assertEqual(target.parent, self.store.versions / VERSION)
        self.assertEqual(target.read_bytes(), PAYLOAD)
        self.assertEqual(prepared['sha256'], hashlib.sha256(PAYLOAD).hexdigest())
        self.assertEqual(prepared['size'], len(PAYLOAD))
        self.assertEqual(prepared['repository'], module.REPOSITORY)
        self.assertEqual(json.loads(Path(prepared['manifest_path']).read_text())['installer'], NAME)
        self.assertEqual(list(self.store.versions.glob('.prepare-*')), [])

    def test_prepare_none_fetches_metadata_and_rechecks_bound_installer(self):
        self.assertEqual(self.store.prepare()['version'], VERSION)
        self.assertEqual([call[0] for call in self.network.calls], [LATEST_URL, self.url])

    def test_repeated_prepare_verifies_existing_bytes_without_redownload(self):
        first = self.store.prepare(self.release)
        calls = len(self.network.calls)
        self.assertEqual(self.store.prepare(self.release)['installer'], first['installer'])
        self.assertEqual(len(self.network.calls), calls)
        Path(first['installer']).write_bytes(PAYLOAD[:-1] + b'X')
        with self.assertRaisesRegex(WorkbenchReleaseError, 'SHA-256'):
            self.store.prepare(self.release)

    def test_existing_invalid_manifest_is_not_overwritten(self):
        prepared = self.store.prepare(self.release)
        manifest = Path(prepared['manifest_path']);manifest.write_bytes(b'not JSON')
        with self.assertRaises(WorkbenchReleaseError):
            self.store.prepare(self.release)
        self.assertEqual(manifest.read_bytes(), b'not JSON')

    def test_existing_manifest_release_and_asset_origins_are_rechecked(self):
        prepared = self.store.prepare(self.release)
        target = Path(prepared['manifest_path'])
        original = json.loads(target.read_bytes())
        for key in ('release_url', 'asset_url', 'tag', 'schema_version'):
            changed = dict(original);changed[key] = 'https://evil.invalid/metadata'
            target.write_text(json.dumps(changed), encoding='utf-8')
            with self.subTest(key=key), self.assertRaisesRegex(WorkbenchReleaseError, 'provenance'):
                self.store.prepare(self.release)
            self.assertEqual(json.loads(target.read_bytes()), changed)

    def test_manifest_write_failure_never_publishes_partial_installer(self):
        with patch.object(module.json, 'dumps', side_effect=RuntimeError('Synthetic manifest write failure')):
            with self.assertRaisesRegex(RuntimeError, 'Synthetic manifest'):
                self.store.prepare(self.release)
        self.no_partial()

    def test_sidecar_digest_only_and_filename_formats(self):
        for content in (hashlib.sha256(PAYLOAD).hexdigest().encode(), (hashlib.sha256(PAYLOAD).hexdigest() + ' *' + NAME + '\n').encode()):
            with self.subTest(content=content):
                self.release = release();self.sidecar(content)
                self.assertEqual(self.store.prepare(self.release)['verification'], 'release_sha256_asset')

    def test_checksum_missing_duplicate_wrong_file_or_multiple_lines_rejected(self):
        self.release['assets'][0]['digest'] = None
        with self.assertRaises(WorkbenchReleaseError):self.store.prepare(self.release)
        checksum = self.sidecar();self.release['assets'].append(dict(checksum))
        with self.assertRaises(WorkbenchReleaseError):self.store.prepare(self.release)
        self.release = release();self.sidecar((hashlib.sha256(PAYLOAD).hexdigest() + ' other.exe\n').encode())
        with self.assertRaises(WorkbenchReleaseError):self.store.prepare(self.release)
        self.release = release();self.sidecar((hashlib.sha256(PAYLOAD).hexdigest() + '\n') .encode() * 2)
        with self.assertRaises(WorkbenchReleaseError):self.store.prepare(self.release)
        self.no_partial()

    def test_checksum_asset_own_digest_is_checked(self):
        checksum = self.sidecar();checksum['digest'] = 'sha256:' + '0' * 64
        with self.assertRaisesRegex(WorkbenchReleaseError, 'Checksum asset'):
            self.store.prepare(self.release)

    def test_bad_download_digest_truncation_and_content_length_leave_no_partial(self):
        for response in (lambda url: Response(PAYLOAD[:-1] + b'X', url), lambda url: Response(PAYLOAD[:-1], url), lambda url: Response(PAYLOAD, url, length='99999999'), lambda url: Response(PAYLOAD, url, length='-1')):
            self.network.responses[self.url] = response
            with self.subTest(response=response), self.assertRaises(WorkbenchReleaseError):
                self.store.prepare(self.release)
            self.no_partial()

    def test_missing_content_length_still_checks_exact_stream_size(self):
        def response(url):
            value = Response(PAYLOAD + b'EXTRA', url);value.headers = {};return value
        self.network.responses[self.url] = response
        with self.assertRaisesRegex(WorkbenchReleaseError, 'extra bytes'):
            self.store.prepare(self.release)
        self.no_partial()

    def test_network_failure_mid_download_leaves_no_manifest_or_version(self):
        def fail():raise URLError('Synthetic stream interrupted')
        self.network.responses[self.url] = lambda url: Response(PAYLOAD, url, after_read=fail)
        with self.assertRaises(URLError):self.store.prepare(self.release)
        self.no_partial()

    def test_untrusted_metadata_and_installer_final_urls_rejected(self):
        self.network.responses[LATEST_URL] = lambda url: Response(json.dumps(self.release).encode(), REPO_URL + '/not-api')
        with self.assertRaises(WorkbenchReleaseError):self.store.check()
        for final in ('https://evil.invalid/installer.exe', 'https://github.com/Other/repo/blob', 'file:///tmp/installer', 'https://user:password@release-assets.githubusercontent.com/file', 'https://release-assets.githubusercontent.com:444/file'):
            self.network.responses[self.url] = lambda url, final=final: Response(PAYLOAD, final)
            with self.subTest(final=final), self.assertRaises(WorkbenchReleaseError):self.store.prepare(self.release)
            self.no_partial()

    def test_bound_github_cdn_asset_redirect_is_allowed(self):
        self.network.responses[self.url] = lambda url: Response(PAYLOAD, 'https://release-assets.githubusercontent.com/github-production-release-asset/synthetic?sig=synthetic')
        self.assertTrue(self.store.prepare(self.release)['prepared'])

    def test_redirect_handler_rejects_an_external_hop_before_request(self):
        handler = module._RedirectHandler(self.url, True)
        with self.assertRaises(WorkbenchReleaseError):
            handler.redirect_request(module.Request(self.url), None, 302, '', {}, 'https://evil.invalid/x')

    def test_cancel_before_network_and_during_stream_cleans_staging(self):
        cancel = threading.Event();cancel.set()
        with self.assertRaisesRegex(WorkbenchReleaseError, 'cancelled'):
            self.store.prepare(self.release, cancel_event=cancel)
        self.assertEqual(self.network.calls, [])
        cancel.clear();self.network.responses[self.url] = lambda url: Response(PAYLOAD, url, after_read=cancel.set)
        with self.assertRaisesRegex(WorkbenchReleaseError, 'cancelled'):
            self.store.prepare(self.release, cancel_event=cancel)
        self.no_partial()

    def test_cancel_while_rechecking_existing_installer_keeps_verified_files(self):
        prepared = self.store.prepare(self.release)
        target = Path(prepared['installer']);before = target.read_bytes()
        cancel = threading.Event();original = Path.open
        def opened(path, *args, **kwargs):
            if path == target and args and args[0] == 'rb':return Response(before, self.url, after_read=cancel.set)
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', opened):
            with self.assertRaisesRegex(WorkbenchReleaseError, 'cancelled'):
                self.store.prepare(self.release, cancel_event=cancel)
        self.assertEqual(target.read_bytes(), before)

    def test_deadline_and_transport_timeout_are_bounded(self):
        self.network.responses[self.url] = lambda url: Response(PAYLOAD, url, after_read=lambda: None)
        with patch.object(module.time, 'monotonic', side_effect=[0, 0, 0, 0, 0, 3]):
            with self.assertRaisesRegex(WorkbenchReleaseError, 'timed out'):
                self.store.prepare(self.release)
        self.no_partial()
        self.assertTrue(all(0 < call[2] <= 1 for call in self.network.calls))

    def test_malformed_metadata_and_oversize_metadata_rejected(self):
        for content in (b'not JSON', b'[]', b'null', b'"release"', b'{}', b' ' * (module.MAX_METADATA + 1)):
            self.network.responses[LATEST_URL] = content
            with self.subTest(content=content[:20]), self.assertRaises(WorkbenchReleaseError):self.store.check()

    def test_invalid_configuration_rejected_without_io(self):
        for kwargs in ({'timeout': 0}, {'timeout': True}, {'timeout': float('nan')}, {'socket_timeout': 601}, {'max_size': -1}, {'max_size': True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):WorkbenchRelease(self.data, '0.2.29', **kwargs)
        for version in ('v0.2.29', '01.2.29', '', '0.2.29-beta'):
            with self.subTest(version=version), self.assertRaises(WorkbenchReleaseError):WorkbenchRelease(self.data, version)

    def test_data_directory_and_version_symlinks_rejected(self):
        outside = Path(self.temp.name) / 'outside';outside.mkdir()
        link = Path(self.temp.name) / 'linked-data'
        def redirect(path):
            try:
                path.symlink_to(outside, target_is_directory=True)
            except OSError:
                if os.name != 'nt':raise
                # Junctions exercise the same redirect boundary without needing
                # the Windows privilege required to create symbolic links.
                import _winapi
                _winapi.CreateJunction(str(outside), str(path))
        try:
            redirect(link)
        except (OSError, NotImplementedError):
            self.skipTest('Host does not permit creating a synthetic directory redirect')
        linked = WorkbenchRelease(link, '0.2.29', runner=self.network)
        with self.assertRaisesRegex(WorkbenchReleaseError, 'redirect'):
            linked.prepare(self.release)
        self.store.versions.mkdir(parents=True)
        redirect(self.store.versions / VERSION)
        with self.assertRaisesRegex(WorkbenchReleaseError, 'redirect'):
            self.store.prepare(self.release)
        self.assertEqual(list(outside.iterdir()), [])

    def test_existing_installer_hardlink_is_rejected(self):
        prepared = self.store.prepare(self.release)
        target = Path(prepared['installer']);alias = self.data / 'outside-hardlink.exe'
        try:
            alias.hardlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest('Host does not permit synthetic hard links')
        with self.assertRaisesRegex(WorkbenchReleaseError, 'path type'):
            self.store.prepare(self.release)


if __name__ == '__main__':
    unittest.main()
