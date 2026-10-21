"""Synthetic Windows release-store checks: no GitHub, executable or devices."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import native_cli
from cli_release_store import API_URL, REPO_URL, ReleaseStoreError
from native_cli import NativeCLIStore, PINNED_VERSION, ARCHIVE_NAME, ASSET_URL


BINARY = b'synthetic Windows CLI bytes, not executable'


def zip_bytes(entries=None, *, compression=zipfile.ZIP_STORED):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', compression=compression) as archive:
        for name, content, mode in entries or [('wuji.exe', BINARY, stat.S_IFREG | 0o755)]:
            member = zipfile.ZipInfo(name)
            # Windows ZipInfo normalizes backslashes at construction; write the
            # deliberately hostile raw filename rather than a sanitized fixture.
            member.filename = name
            member.create_system = 3
            member.external_attr = mode << 16
            member.compress_type = compression
            archive.writestr(member, content)
    return data.getvalue()


def release_metadata(data):
    return dict(tag_name='v' + PINNED_VERSION, html_url=REPO_URL + '/releases/tag/v' + PINNED_VERSION,
                draft=False, assets=[dict(name=ARCHIVE_NAME, browser_download_url=ASSET_URL,
                                        size=len(data), digest='sha256:' + hashlib.sha256(data).hexdigest())])


class Response(io.BytesIO):
    status = 200

    def __init__(self, data, url):
        super().__init__(data)
        self.url = url
        self.headers = {'Content-Length': str(len(data))}

    def geturl(self):
        return self.url


class FakeNetwork:
    def __init__(self):
        self.calls, self.responses = [], {}

    def __call__(self, request, *, timeout):
        self.calls.append((request.full_url, dict(request.header_items())))
        response = self.responses[request.full_url]
        if isinstance(response, BaseException):
            raise response
        return Response(response, request.full_url)


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.fail = None
        self.outputs = dict(version='version: ' + PINNED_VERSION, help='Global options: --jsonl',
                            viz='Visualize --no-zero --no-open --jsonl')

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if isinstance(self.fail, BaseException):
            raise self.fail
        label = 'version' if command[-1] == '--version' else 'viz' if command[-2:] == ['viz', '--help'] else 'help'
        return SimpleNamespace(returncode=1 if self.fail == label else 0,
                               stdout=self.outputs[label], stderr='')


class NativeCLIStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)
        self.folder = self.parent / 'official-cli-native'
        self.network, self.runner = FakeNetwork(), FakeRunner()
        self.store = NativeCLIStore(self.folder, http_open=self.network, runner=self.runner,
                                    timeout=2, socket_timeout=1, lock_timeout=.1)
        self.data = zip_bytes()
        self.pin(self.data)

    def pin(self, data, *, binary=BINARY):
        for key, value in dict(ARCHIVE_SHA256=hashlib.sha256(data).hexdigest(), ARCHIVE_SIZE=len(data),
                               BINARY_SHA256=hashlib.sha256(binary).hexdigest()).items():
            patcher = patch.object(native_cli, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.data, self.release = data, release_metadata(data)
        self.network.responses[ASSET_URL] = data
        self.network.responses[API_URL + '/releases/tags/v' + PINNED_VERSION] = json.dumps(self.release).encode()

    def prepare(self, **kwargs):
        return self.store.prepare(release=kwargs.pop('release', self.release), **kwargs)

    def assert_no_partial(self):
        self.assertFalse((self.store.root / PINNED_VERSION).exists())
        self.assertEqual(list(self.store.root.glob('.prepare-*')), [])

    def test_constructor_has_no_filesystem_network_or_process_side_effect(self):
        self.assertFalse(self.folder.exists())
        self.assertEqual(self.network.calls, [])
        self.assertEqual(self.runner.calls, [])

    def test_resolve_missing_never_creates_or_downloads(self):
        with self.assertRaises(ReleaseStoreError):
            self.store.resolve()
        self.assertFalse(self.folder.exists())
        self.assertEqual(self.network.calls, [])
        self.assertEqual(self.runner.calls, [])

    def test_prepares_verified_independent_store(self):
        result = self.prepare()
        self.assertEqual(Path(result['directory']), self.folder / 'versions' / PINNED_VERSION)
        self.assertEqual(Path(result['binary']).read_bytes(), BINARY)
        self.assertEqual(result['asset_url'], ASSET_URL)
        self.assertTrue(result['validated'])
        self.assertFalse(result['device_commands_executed'])
        self.assertEqual(result['archive_sha256'], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(result['binary_sha256'], hashlib.sha256(BINARY).hexdigest())
        self.assertEqual(list(self.store.root.glob('.prepare-*')), [])
        self.assertFalse((self.folder / 'selection.json').exists())
        self.assertFalse((self.parent / 'official-cli').exists())
        self.assertFalse((Path(result['directory']) / 'software-check-home').exists())

    def test_only_software_commands_and_isolated_home(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'never-forward', 'WUJI_STUDIO_DATA': 'private-dir'}):
            self.prepare()
        self.assertEqual([command[1:] for command, _ in self.runner.calls], [['--version'], ['--help'], ['viz', '--help']])
        for command, options in self.runner.calls:
            self.assertEqual(Path(command[0]).name, 'wuji.exe')
            self.assertFalse(options['shell'])
            self.assertEqual(options['stdin'], subprocess.DEVNULL)
            self.assertEqual(options['timeout'], 2)
            self.assertIn('software-check-home', options['env']['HOME'])
            self.assertNotIn('GITHUB_TOKEN', options['env'])
            self.assertNotIn('WUJI_STUDIO_DATA', options['env'])

    def test_resolve_and_cached_prepare_do_not_network_or_run(self):
        prepared = self.prepare()
        self.network.calls.clear()
        self.runner.calls.clear()
        self.assertEqual(self.store.resolve(), prepared['binary'])
        self.assertEqual(self.store.prepare()['binary'], prepared['binary'])
        self.assertEqual(self.network.calls, [])
        self.assertEqual(self.runner.calls, [])

    def test_explicit_local_archive_still_verifies_pin(self):
        local = self.parent / 'local.zip'
        local.write_bytes(self.data)
        result = self.prepare(archive=local)
        self.assertEqual(Path(result['binary']).read_bytes(), BINARY)
        self.assertEqual(self.network.calls, [])

    def test_local_archive_tampering_is_rejected(self):
        local = self.parent / 'bad.zip'
        local.write_bytes(self.data[:-1] + b'!')
        with self.assertRaisesRegex(ReleaseStoreError, 'SHA-256'):
            self.prepare(archive=local)
        self.assert_no_partial()

    def test_fetches_exact_public_tag_anonymously(self):
        self.store.prepare()
        self.assertEqual(self.network.calls[0][0], API_URL + '/releases/tags/v' + PINNED_VERSION)
        for _, headers in self.network.calls:
            self.assertFalse(any(key.lower() == 'authorization' for key in headers))

    def test_rejects_non_pinned_versions_before_io(self):
        for version in ('2026.10.13', '../2026.10.12', '', None):
            with self.subTest(version=version), self.assertRaises(ReleaseStoreError):
                self.store.prepare(version)
        self.assertFalse(self.folder.exists())
        self.assertEqual(self.network.calls, [])

    def test_rejects_forged_metadata_and_digest(self):
        mutations = [('tag_name', 'v2026.10.13'), ('draft', True), ('html_url', 'https://example.com/release')]
        for key, value in mutations:
            release = copy.deepcopy(self.release)
            release[key] = value
            with self.subTest(key=key), self.assertRaises(ReleaseStoreError):
                self.prepare(release=release)
        for key, value in [('browser_download_url', 'https://example.com/cli.zip'),
                           ('digest', 'sha256:' + '0' * 64), ('size', 123456)]:
            release = copy.deepcopy(self.release)
            release['assets'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ReleaseStoreError):
                self.prepare(release=release)
        self.assert_no_partial()

    def test_rejects_duplicate_asset(self):
        self.release['assets'].append(copy.deepcopy(self.release['assets'][0]))
        with self.assertRaises(ReleaseStoreError):
            self.prepare()
        self.assert_no_partial()

    def test_rejects_download_hash_mismatch(self):
        self.network.responses[ASSET_URL] = self.data[:-1] + b'!'
        with self.assertRaises(ReleaseStoreError):
            self.prepare()
        self.assert_no_partial()

    def test_rejects_bad_zip(self):
        self.pin(b'not a ZIP')
        with self.assertRaisesRegex(ReleaseStoreError, 'ZIP'):
            self.prepare()
        self.assert_no_partial()

    def test_rejects_zip_crc_corruption(self):
        corrupted = bytearray(self.data)
        offset = self.data.index(BINARY)
        corrupted[offset] ^= 1
        self.pin(bytes(corrupted))
        with self.assertRaisesRegex(ReleaseStoreError, 'ZIP'):
            self.prepare()
        self.assert_no_partial()

    def test_rejects_unsafe_paths(self):
        for name in ('../wuji.exe', '/wuji.exe', 'C:/wuji.exe', 'a\\wuji.exe',
                     'a/../../wuji.exe', 'AUX/wuji.exe', 'a./wuji.exe'):
            with self.subTest(name=name):
                self.pin(zip_bytes([(name, BINARY, stat.S_IFREG)]))
                with self.assertRaises(ReleaseStoreError):
                    self.prepare()
                self.assert_no_partial()

    def test_rejects_zip_symlink(self):
        self.pin(zip_bytes([('wuji.exe', b'target', stat.S_IFLNK | 0o777)]))
        with self.assertRaisesRegex(ReleaseStoreError, 'links'):
            self.prepare()
        self.assert_no_partial()

    def test_rejects_zip_special_file(self):
        self.pin(zip_bytes([('wuji.exe', BINARY, stat.S_IFIFO | 0o644)]))
        with self.assertRaises(ReleaseStoreError):
            self.prepare()
        self.assert_no_partial()

    def test_rejects_case_collisions(self):
        self.pin(zip_bytes([('wuji.exe', BINARY, stat.S_IFREG), ('WUJI.EXE', BINARY, stat.S_IFREG)]))
        with self.assertRaisesRegex(ReleaseStoreError, 'colliding'):
            self.prepare()
        self.assert_no_partial()

    def test_rejects_file_directory_collision(self):
        self.pin(zip_bytes([('a', b'file', stat.S_IFREG), ('a/wuji.exe', BINARY, stat.S_IFREG)]))
        with self.assertRaisesRegex(ReleaseStoreError, 'colliding'):
            self.prepare()
        self.assert_no_partial()

    def test_rejects_multiple_executable_candidates(self):
        self.pin(zip_bytes([('a/wuji.exe', BINARY, stat.S_IFREG), ('b/wuji.exe', BINARY, stat.S_IFREG)]))
        with self.assertRaisesRegex(ReleaseStoreError, 'exactly one'):
            self.prepare()
        self.assert_no_partial()

    def test_rejects_missing_executable(self):
        self.pin(zip_bytes([('LICENSE', b'text', stat.S_IFREG)]))
        with self.assertRaisesRegex(ReleaseStoreError, 'exactly one'):
            self.prepare()
        self.assert_no_partial()

    def test_allows_license_and_nested_executable(self):
        self.pin(zip_bytes([('package/wuji.exe', BINARY, stat.S_IFREG), ('package/LICENSE.txt', b'license', stat.S_IFREG)]))
        prepared = self.prepare()
        self.assertEqual(len(prepared['files']), 2)
        self.assertEqual(Path(prepared['binary']).parent.name, 'package')

    def test_rejects_compression_bomb(self):
        self.pin(zip_bytes([('wuji.exe', b'0' * 200000, stat.S_IFREG)], compression=zipfile.ZIP_DEFLATED), binary=b'0' * 200000)
        with self.assertRaisesRegex(ReleaseStoreError, 'compression-ratio'):
            self.prepare()
        self.assert_no_partial()

    def test_rejects_extracted_size_limit(self):
        with patch.object(native_cli, 'MAX_EXTRACTED', 1), self.assertRaises(ReleaseStoreError):
            self.prepare()
        self.assert_no_partial()

    def test_rejects_member_count_limit(self):
        with patch.object(native_cli, 'MAX_MEMBERS', 0), self.assertRaises(ReleaseStoreError):
            self.prepare()
        self.assert_no_partial()

    def test_rejects_wrong_binary_pin_before_execution(self):
        with patch.object(native_cli, 'BINARY_SHA256', '0' * 64), self.assertRaises(ReleaseStoreError):
            self.prepare()
        self.assertEqual(self.runner.calls, [])
        self.assert_no_partial()

    def test_failing_software_checks_are_not_committed(self):
        for label in ('version', 'help', 'viz'):
            self.runner.fail = label
            with self.subTest(label=label), self.assertRaises(ReleaseStoreError):
                self.prepare()
            self.assert_no_partial()

    def test_wrong_version_or_missing_interfaces_not_committed(self):
        for label, output in (('version', 'version: 2026.10.13'), ('help', 'help'), ('viz', '--no-open')):
            old = self.runner.outputs[label]
            self.runner.outputs[label] = output
            with self.subTest(label=label), self.assertRaises(ReleaseStoreError):
                self.prepare()
            self.runner.outputs[label] = old
            self.assert_no_partial()

    def test_software_timeout_not_committed(self):
        self.runner.fail = subprocess.TimeoutExpired('synthetic CLI help', 2)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.prepare()
        self.assert_no_partial()

    def test_cancellation_prevents_any_preparation(self):
        cancelled = threading.Event()
        cancelled.set()
        self.store.cancel_event = cancelled
        with self.assertRaisesRegex(ReleaseStoreError, 'cancelled'):
            self.prepare()
        self.assertFalse(self.folder.exists())

    def test_redirected_store_is_rejected_without_network(self):
        with patch.object(native_cli, '_redirect', side_effect=lambda path: path == self.folder):
            with self.assertRaisesRegex(ReleaseStoreError, 'redirect'):
                self.prepare()
        self.assertFalse(self.folder.exists())
        self.assertEqual(self.network.calls, [])

    def test_detects_modified_binary_even_if_local_manifest_rehashed(self):
        prepared = self.prepare()
        binary = Path(prepared['binary'])
        binary.write_bytes(b'modified')
        path = Path(prepared['directory']) / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
        manifest['files'][0].update(size=8, sha256=manifest['binary_sha256'])
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ReleaseStoreError):
            self.store.resolve()

    def test_detects_modified_cached_archive(self):
        prepared = self.prepare()
        archive = Path(prepared['directory']) / ARCHIVE_NAME
        archive.write_bytes(self.data[:-1] + b'!')
        with self.assertRaises(ReleaseStoreError):
            self.store.resolve()

    def test_detects_unexpected_payload(self):
        prepared = self.prepare()
        (Path(prepared['directory']) / 'payload' / 'extra.dll').write_bytes(b'not official')
        with self.assertRaises(ReleaseStoreError):
            self.store.resolve()

    def test_detects_manifest_path_escape(self):
        prepared = self.prepare()
        path = Path(prepared['directory']) / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['paths']['binary'] = '../outside.exe'
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ReleaseStoreError):
            self.store.resolve()

    def test_keeps_existing_invalid_cache_without_silent_replacement(self):
        prepared = self.prepare()
        binary = Path(prepared['binary'])
        binary.write_bytes(b'preserve failure')
        self.network.calls.clear()
        with self.assertRaises(ReleaseStoreError):
            self.store.prepare()
        self.assertEqual(binary.read_bytes(), b'preserve failure')
        self.assertEqual(self.network.calls, [])


if __name__ == '__main__':
    unittest.main()
