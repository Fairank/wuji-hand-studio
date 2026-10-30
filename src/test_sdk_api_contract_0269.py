"""Software-only future SDK/API refusal and unknown CLI-result preservation."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

import sdk_runtime
import test_calibration_result_0257 as native_evidence


class SDKAPIContractTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        root=Path(os.environ.get('WUJI_CONTROL_QA_ROOT',tempfile.gettempdir()))/'sdk-api-contract-0269'
        root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            site=Path(temporary);sdk=site/'wuji_sdk';sdk.mkdir()
            (sdk/'__init__.py').write_bytes(b'# synthetic SDK API')
            (sdk/'native.so').write_bytes(b'synthetic, never loaded')
            classes={name:N(**{member:Mock() for member in members}) for name,members in sdk_runtime._APIS.items()}
            classes['WujiException']=type('SyntheticSDKError',(Exception,),{})
            classes.setdefault('HandModelPathResource',N(get=Mock(),set=Mock()))
            classes['WujiGlove'].hand_model_path=Mock();classes['SdkManager'].export_user_data=Mock()
            module=N(__file__=str(sdk/'__init__.py'),**classes)
            distribution=N(version='2026.10.26',files=[PurePosixPath('wuji_sdk/native.so')],locate_file=lambda value:site/str(value))
            with patch('sdk_runtime.importlib.metadata.distribution',return_value=distribution),patch('sdk_runtime.importlib.import_module',side_effect=lambda name:module if name=='wuji_sdk' else N()):
                yield module

    def test_current_full_api_is_checked_without_constructing_or_reading_any_device(self):
        with self.environment() as sdk:
            proof=sdk_runtime.probe_runtime()
            self.assertTrue(proof['api_compatible']);self.assertEqual(proof['missing_api'],[])
            sdk.SdkManager.instance.assert_not_called();sdk.RetargetSession.for_hand.assert_not_called()
            sdk.WujiGlove.hand_model_path.assert_not_called()
            sdk.HandModelPathResource.get.assert_not_called();sdk.HandModelPathResource.set.assert_not_called()

    def test_missing_online_model_accessor_or_user_export_is_refused_before_connection(self):
        for owner,member in (('WujiGlove','hand_model_path'),('SdkManager','export_user_data')):
            with self.subTest(owner=owner),self.environment() as sdk:
                delattr(getattr(sdk,owner),member);proof=sdk_runtime.probe_runtime()
                self.assertFalse(proof['api_compatible']);self.assertIn(owner+'.'+member,proof['missing_api'])
                sdk.SdkManager.instance.assert_not_called()

    def test_missing_runtime_model_resource_type_or_methods_is_not_compatible(self):
        for member in (None,'get','set'):
            with self.subTest(member=member),self.environment() as sdk:
                if member is None:del sdk.HandModelPathResource
                else:delattr(sdk.HandModelPathResource,member)
                proof=sdk_runtime.probe_runtime();self.assertFalse(proof['api_compatible'])
                self.assertIn('HandModelPathResource'+('.'+member if member else ''),proof['missing_api'])

    def test_missing_or_non_exception_sdk_error_type_is_not_compatible(self):
        for value in (None,object,'not an exception',Exception()):
            with self.subTest(value=value),self.environment() as sdk:
                sdk.WujiException=value;proof=sdk_runtime.probe_runtime()
                self.assertFalse(proof['api_compatible']);self.assertIn('WujiException',proof['missing_api'])

    def test_replaced_noncallable_methods_are_not_compatible(self):
        for owner,member in (('RetargetSession','for_hand'),('RetargetSession','step'),
                             ('HandModelPathResource','get'),('HandModelPathResource','set'),
                             ('WujiGlove','hand_model_path')):
            with self.subTest(owner=owner,member=member),self.environment() as sdk:
                setattr(getattr(sdk,owner),member,None);proof=sdk_runtime.probe_runtime()
                self.assertFalse(proof['api_compatible']);self.assertIn(owner+'.'+member,proof['missing_api'])

    def test_unknown_future_cli_guided_schema_is_not_success_and_preserves_original_output(self):
        event=native_evidence.result();event['schema_version']=3
        raw=json.dumps(event)+'\n'
        actual=native_evidence.NativeCalibrationEvidenceTests().collect([
            native_evidence.ready(),native_evidence.output(raw),native_evidence.exited()])
        self.assertEqual(actual['run']['status'],'error');self.assertIsNone(actual['run']['result'])
        self.assertIn(raw,actual['transcript']);self.assertIsNone(actual['saved']['result'])
        self.assertEqual(actual['digest_calls'],0);self.assertTrue(actual['run']['cleanup_confirmed'])


if __name__=='__main__':unittest.main()
