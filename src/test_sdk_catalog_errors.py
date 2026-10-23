"""Version metadata diagnostics retain cause and never loosen catalog trust."""
import copy
import json
from pathlib import Path
import socket
import ssl
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
import urllib.error

from sdk_release import SDKCatalogError,resolve_release,validate_release
from sdk_maintenance import SDKMaintenance
from test_sdk_lifecycle import catalog_fixture,proof_fixture


class SDKCatalogErrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'stable.json';self.now=time.time()

    def failure(self,error):
        with self.assertRaises(SDKCatalogError) as caught:
            resolve_release(self.path,force=True,fetch=Mock(side_effect=error),now=self.now)
        self.assertIs(caught.exception.__cause__,error)
        return caught.exception

    def test_missing_local_file_keeps_type_path_errno_and_is_not_a_network_instruction(self):
        error=FileNotFoundError(2,'No such file or directory','synthetic-missing/ssl-key.log')
        failure=self.failure(error)
        self.assertEqual(failure.kind,'local');self.assertIn('FileNotFoundError',failure.detail)
        self.assertIn('synthetic-missing/ssl-key.log',failure.detail);self.assertIn('Errno 2',failure.detail)
        self.assertNotIn('check the network',str(failure));self.assertNotIn('联网',str(failure));self.assertFalse(self.path.exists())

    def test_wrapped_local_file_failure_is_not_misclassified_as_url_network_failure(self):
        failure=self.failure(urllib.error.URLError(FileNotFoundError(2,'missing','synthetic-cert.pem')))
        self.assertEqual(failure.kind,'local');self.assertIn('URLError',failure.detail)
        self.assertIn('caused by FileNotFoundError',failure.detail)

    def test_dns_timeout_http_and_certificate_failures_keep_distinct_categories(self):
        cases=[(urllib.error.URLError(socket.gaierror(-2,'synthetic DNS failure')),'network'),
            (TimeoutError('synthetic timeout'),'network'),
            (urllib.error.HTTPError('https://pypi.org/pypi/wuji-sdk/json',429,'synthetic rate limit',{},None),'http'),
            (urllib.error.URLError(ssl.SSLCertVerificationError(1,'synthetic certificate failure')),'certificate')]
        for original,kind in cases:
            with self.subTest(kind=kind):
                error=self.failure(original);self.assertEqual(error.kind,kind);self.assertIn(type(original).__name__,error.detail)

    def test_noncertificate_ssl_failure_does_not_claim_certificate_verification_failed(self):
        for original in [ssl.SSLError(1,'WRONG_VERSION_NUMBER'),urllib.error.URLError(ssl.SSLEOFError(8,'Synthetic unexpected EOF'))]:
            with self.subTest(original=original):
                error=self.failure(original);self.assertEqual(error.kind,'tls')
                self.assertNotIn('certificate',str(error));self.assertNotIn('证书',str(error))
                self.assertIn('secure connection',str(error));self.assertIn(type(original).__name__,error.detail)

    def test_invalid_json_and_untrusted_wheel_metadata_remain_validation_failures(self):
        document=json.loads(catalog_fixture());document['urls'][0]['url']='https://untrusted.invalid/wheel.whl'
        for raw in [b'{bad',json.dumps(document).encode(),b'{"info":{}}']:
            with self.subTest(raw=raw),self.assertRaises(SDKCatalogError) as caught:
                resolve_release(self.path,force=True,fetch=lambda:raw,now=self.now)
            self.assertEqual(caught.exception.kind,'metadata');self.assertNotIn('check the network',str(caught.exception))
            self.assertIsNotNone(caught.exception.__cause__);self.assertFalse(self.path.exists())

    def test_recent_valid_cache_survives_failed_refresh_without_rewriting_or_extending_time(self):
        first=resolve_release(self.path,fetch=catalog_fixture,now=self.now);original=self.path.read_bytes()
        for fetch,kind in [(Mock(side_effect=FileNotFoundError(2,'synthetic file missing')),'local'),(lambda:b'{bad','metadata'),(Mock(side_effect=urllib.error.URLError('synthetic offline')),'network')]:
            with self.subTest(kind=kind):
                cached=resolve_release(self.path,force=True,fetch=fetch,now=self.now+100)
                self.assertTrue(cached['offline']);self.assertEqual(cached['checked_at'],first['checked_at'])
                self.assertEqual(cached['catalog_sha256'],first['catalog_sha256']);self.assertEqual(cached['refresh_error']['kind'],kind)
                self.assertTrue(cached['refresh_error']['detail']);self.assertEqual(self.path.read_bytes(),original)
                validate_release(cached,now=self.now+100)

    def test_expired_cache_never_becomes_current_when_refresh_fails(self):
        resolve_release(self.path,fetch=catalog_fixture,now=self.now);original=self.path.read_bytes()
        with self.assertRaises(SDKCatalogError) as caught:
            resolve_release(self.path,fetch=lambda:b'{bad',now=self.now+901)
        self.assertEqual(caught.exception.kind,'metadata');self.assertEqual(self.path.read_bytes(),original)

    def test_cache_write_failure_preserves_original_file_and_cause(self):
        parent=Path(self.tmp.name)/'blocked';parent.write_text('Original public fixture')
        with self.assertRaises(SDKCatalogError) as caught:
            resolve_release(parent/'stable.json',fetch=catalog_fixture,now=self.now)
        self.assertEqual(caught.exception.kind,'cache');self.assertIsInstance(caught.exception.__cause__,FileExistsError)
        self.assertEqual(parent.read_text(),'Original public fixture')

    def test_pending_cleanup_failure_does_not_mask_the_original_cache_write_failure(self):
        original=PermissionError('Synthetic original cache write denied')
        with patch.object(Path,'write_text',side_effect=original),patch.object(Path,'unlink',side_effect=OSError('Synthetic cleanup error')):
            with self.assertRaises(SDKCatalogError) as caught:resolve_release(self.path,fetch=catalog_fixture,now=self.now)
        self.assertEqual(caught.exception.kind,'cache');self.assertIs(caught.exception.__cause__,original)
        self.assertIn('original cache write denied',caught.exception.detail);self.assertNotIn('cleanup error',caught.exception.detail)

    def test_manual_maintenance_retains_last_facts_and_full_diagnosis_then_recovers(self):
        acquire=Mock();release=Mock();idle=Mock();updater=Mock();probe=Mock(return_value=proof_fixture())
        service=SDKMaintenance(acquire=acquire,release=release,idle=idle,updater=updater,config=lambda:dict(mode='wsl',agent_directory='/opt/hand-workbench',python='/opt/hand-workbench-venv/bin/python'),
            resolver=lambda **_:resolve_release(self.path,fetch=catalog_fixture,force=True),probe=probe,folder=Path(self.tmp.name)/'owner')
        def check():
            service.start('check');service.thread.join(2);self.assertFalse(service.thread.is_alive());return service.snapshot()
        before=check();self.assertEqual(before['phase'],'up_to_date')
        # Expire rather than silently reusing a recent record; a parse failure
        # must remain a failed current check, not another up-to-date claim.
        saved=json.loads(self.path.read_text());saved['checked_at']=time.time()-901;self.path.write_text(json.dumps(saved))
        service.resolver=lambda **_:resolve_release(self.path,fetch=lambda:b'{bad',force=True)
        after=check();self.assertEqual(after['phase'],'error');self.assertFalse(after['compatible']);self.assertFalse(after['can_update'])
        self.assertEqual(after['proof'],before['proof']);self.assertEqual(after['release'],before['release'])
        self.assertIn('[SDK_CATALOG_METADATA]',after['error']);self.assertIn('JSONDecodeError',after['error'])
        service.resolver=lambda **_:resolve_release(self.path,fetch=catalog_fixture,force=True)
        recovered=check();self.assertEqual(recovered['phase'],'up_to_date');self.assertIsNone(recovered['error'])
        acquire.assert_not_called();release.assert_not_called();idle.assert_not_called();updater.assert_not_called()


if __name__=='__main__':unittest.main()
