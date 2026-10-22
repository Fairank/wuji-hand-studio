"""Integrity-cache regression with synthetic ZIPs, never real CLI or devices."""
import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

import native_cli
from cli_release_store import ReleaseStoreError
import test_native_cli as fixtures


class NativeCacheTests(unittest.TestCase):
    setUp=fixtures.NativeCLIStoreTests.setUp
    pin=fixtures.NativeCLIStoreTests.pin
    prepare=fixtures.NativeCLIStoreTests.prepare

    def test_successful_full_verification_is_reused_across_poll_store_instances(self):
        expected=self.prepare();self.network.calls.clear();self.runner.calls.clear()
        with patch.object(native_cli,'_hash',wraps=native_cli._hash) as digest:
            for _ in range(5):
                self.assertEqual(self.store.resolve(),expected['binary'])
                self.assertEqual(self.store.prepare()['binary'],expected['binary'])
                self.assertEqual(native_cli.NativeCLIStore(self.folder).resolve(),expected['binary'])
            digest.assert_not_called()
        self.assertEqual(self.network.calls,[]);self.assertEqual(self.runner.calls,[])

    def test_explicit_strict_verification_always_rehashes_without_running_software(self):
        self.prepare();self.runner.calls.clear();self.network.calls.clear()
        with patch.object(native_cli,'_hash',wraps=native_cli._hash) as digest:
            self.store.resolve(strict=True);first=digest.call_count
            self.assertGreaterEqual(first,3)
            self.store.prepare(strict=True);self.assertGreater(digest.call_count,first)
        self.assertEqual(self.network.calls,[]);self.assertEqual(self.runner.calls,[])

    def test_same_size_payload_change_cannot_reuse_trusted_poll_cache(self):
        result=self.prepare();binary=Path(result['binary']);binary.write_bytes(b'X'*len(fixtures.BINARY))
        with self.assertRaises(ReleaseStoreError):self.store.resolve()
        with self.assertRaises(ReleaseStoreError):self.store.prepare()

    def test_archive_manifest_and_pin_changes_invalidate_success_cache(self):
        result=self.prepare();directory=Path(result['directory'])
        archive=directory/native_cli.ARCHIVE_NAME;original=archive.read_bytes()
        archive.write_bytes(original[:-1]+b'!')
        with self.assertRaises(ReleaseStoreError):self.store.resolve()
        archive.write_bytes(original);self.store.resolve()
        path=directory/'manifest.json';data=json.loads(path.read_text());data['version']='2026.1.1'
        path.write_text(json.dumps(data))
        with self.assertRaises(ReleaseStoreError):self.store.resolve()
        with patch.object(native_cli,'ARCHIVE_SHA256','0'*64):
            with self.assertRaises(ReleaseStoreError):self.store.resolve()

    def test_entire_path_set_and_license_metadata_change_force_full_verification(self):
        result=self.prepare();directory=Path(result['directory'])
        license=directory/'LICENSE-local-not-executed.txt';license.write_text('copyright fixture')
        with patch.object(native_cli,'_hash',wraps=native_cli._hash) as digest:
            self.store.resolve();first=digest.call_count;self.assertGreaterEqual(first,3)
            license.write_text('copyright updated fixture')
            self.store.resolve();self.assertGreater(digest.call_count,first)
            before=digest.call_count;license.unlink();self.store.resolve();self.assertGreater(digest.call_count,before)
        (directory/'payload'/'new.dll').write_bytes(b'not official')
        with self.assertRaises(ReleaseStoreError):self.store.resolve()

    def test_redirect_is_checked_even_after_success_cache_is_warm(self):
        result=self.prepare();binary=Path(result['binary']);original=native_cli._redirect
        with patch.object(native_cli,'_redirect',side_effect=lambda p:p==binary or original(p)):
            with self.assertRaisesRegex(ReleaseStoreError,'redirect'):self.store.resolve()

    def test_mid_verification_change_is_not_cached_and_failed_check_is_retried(self):
        result=self.prepare();directory=Path(result['directory']);path=directory/'release.json'
        original=self.store._cached_full
        def changed(*args):
            value=original(*args);path.write_text(path.read_text()+' ');return value
        with patch.object(self.store,'_cached_full',side_effect=changed):
            with self.assertRaisesRegex(ReleaseStoreError,'changed during'):self.store.resolve(strict=True)
        with patch.object(self.store,'_cached_full',wraps=original) as full:
            self.store.resolve();full.assert_called_once()

    def test_returned_manifest_mutation_never_modifies_private_trust_cache(self):
        result=self.prepare();result['version']='malicious';result['files'].clear()
        value=self.store.prepare();self.assertEqual(value['version'],native_cli.PINNED_VERSION)
        self.assertTrue(value['files'])
        value['files'].clear();self.assertTrue(self.store.prepare()['files'])

    def test_every_binary_execution_including_profile_read_forces_fresh_hash(self):
        process=N(communicate=Mock(return_value=('{}','')),returncode=0)
        owned=N(process=process)
        context=Mock();context.__enter__=Mock(return_value=owned);context.__exit__=Mock(return_value=False)
        with patch.object(native_cli,'prepare',return_value={'binary':'synthetic.exe'}) as prepare,patch('windows_process_job.OwnedProcess',return_value=context):
            for args in [('user','list'),('user','show'),('user','show','Demo'),
                         ('get','hand_side','--sn','WG_SYNTHETIC'),('user','switch','Demo'),('user','create','New')]:
                prepare.reset_mock();native_cli.json_command(*args);prepare.assert_called_once_with(strict=True)

    def test_invalid_strict_flag_cannot_start_preparation(self):
        for flag in (1,None,'true'):
            with self.assertRaises(ValueError):self.store.prepare(strict=flag)
        self.assertEqual(self.runner.calls,[]);self.assertEqual(self.network.calls,[])


if __name__=='__main__':unittest.main()
