"""Offline packaged-TLS acceptance; no models, network, installer or device."""
from contextlib import ExitStack,contextmanager,redirect_stdout
import io
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
from types import ModuleType,SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import cli_release_store
import desktop
import device_profiles
import gesture_library
import sdk_release
from test_sdk_lifecycle import catalog_fixture


class DesktopTLSTests(unittest.TestCase):
    @contextmanager
    def diagnostic(self,*,release=False,context=None,tls_error=None,cli_error=None):
        with tempfile.TemporaryDirectory() as folder,ExitStack() as stack:
            output=Path(folder)/'self-check.json'
            argv=['desktop','--self-check','--self-check-output',str(output)]
            if release:argv.append('--release-check')
            pose=ModuleType('pose_view');pose.load_left_model=Mock(side_effect=AssertionError('No model load'))
            # The diagnostic boundary needs only the existing model/catalog
            # acceptance counts. No simulation or rendering is performed here.
            stack.enter_context(patch.dict(sys.modules,{'pose_view':pose,'numpy':ModuleType('numpy')}))
            stack.enter_context(patch.object(sys,'argv',argv))
            stack.enter_context(patch.object(device_profiles,'load_native_model',return_value=SimpleNamespace(nu=20)))
            stack.enter_context(patch.object(gesture_library,'catalog',return_value={'actions':[{}]*40}))
            stack.enter_context(patch.object(socket,'create_connection',side_effect=AssertionError('No network')))
            stack.enter_context(patch.object(subprocess,'Popen',side_effect=AssertionError('No process')))
            cli=stack.enter_context(patch.object(cli_release_store.CLIReleaseStore,'fetch_release',
                return_value={'tag_name':'v2026.10.12'},side_effect=cli_error))
            sdk=stack.enter_context(patch.object(sdk_release,'_read_official',return_value=catalog_fixture()))
            prepare=stack.enter_context(patch.object(cli_release_store.CLIReleaseStore,'prepare',side_effect=AssertionError('No prepare')))
            if tls_error is not None:
                create=stack.enter_context(patch.object(ssl,'create_default_context',side_effect=tls_error))
            elif context is not None:
                create=stack.enter_context(patch.object(ssl,'create_default_context',return_value=context))
            else:
                create=stack.enter_context(patch.object(ssl,'create_default_context',wraps=ssl.create_default_context))
            stack.enter_context(redirect_stdout(io.StringIO()))
            yield SimpleNamespace(output=output,create=create,cli=cli,sdk=sdk,prepare=prepare)

    def result(self,env):
        value=json.loads(env.output.read_text(encoding='utf-8'))
        self.assertIs(value['hardware_connected'],False)
        env.create.assert_called_once_with();env.prepare.assert_not_called()
        return value

    def test_actual_default_context_acceptance_is_offline_and_verified(self):
        with self.diagnostic() as env:
            self.assertEqual(desktop.self_check(),0);value=self.result(env)
            self.assertTrue(value['ok'])
            self.assertEqual(value['tls'],dict(ok=True,check_hostname=True,verify_mode=int(ssl.CERT_REQUIRED)))
            self.assertNotIn('release_checks',value);env.cli.assert_not_called();env.sdk.assert_not_called()

    def test_missing_runtime_policy_is_a_failed_self_check_with_the_local_cause(self):
        error=FileNotFoundError(2,'Synthetic policy file missing','synthetic-policy.pem')
        with self.diagnostic(tls_error=error) as env:
            self.assertEqual(desktop.self_check(),1);value=self.result(env)
            self.assertFalse(value['ok']);self.assertFalse(value['tls']['ok'])
            self.assertEqual(value['tls']['type'],'FileNotFoundError')
            self.assertIn('synthetic-policy.pem',value['tls']['error'])
            env.cli.assert_not_called();env.sdk.assert_not_called()

    def test_hostname_or_certificate_verification_cannot_be_disabled(self):
        for context in (SimpleNamespace(check_hostname=False,verify_mode=ssl.CERT_REQUIRED),
                        SimpleNamespace(check_hostname=True,verify_mode=ssl.CERT_NONE)):
            with self.subTest(context=context),self.diagnostic(context=context) as env:
                self.assertEqual(desktop.self_check(),1);value=self.result(env)
                self.assertFalse(value['ok']);self.assertEqual(value['tls']['type'],'AssertionError')
                env.cli.assert_not_called();env.sdk.assert_not_called()

    def test_successful_public_reads_cannot_hide_a_failed_offline_tls_check(self):
        with self.diagnostic(release=True,tls_error=FileNotFoundError('Synthetic policy missing')) as env:
            self.assertEqual(desktop.self_check(),1);value=self.result(env)
            self.assertFalse(value['ok']);self.assertFalse(value['tls']['ok'])
            self.assertTrue(all(item['ok'] for item in value['release_checks'].values()))
            env.cli.assert_called_once_with();env.sdk.assert_called_once_with()

    def test_public_metadata_failure_does_not_hide_verified_offline_tls(self):
        with self.diagnostic(release=True,cli_error=ValueError('Synthetic metadata rejected')) as env:
            self.assertEqual(desktop.self_check(),1);value=self.result(env)
            self.assertTrue(value['tls']['ok']);self.assertFalse(value['ok'])
            self.assertFalse(value['release_checks']['cli']['ok']);self.assertTrue(value['release_checks']['sdk']['ok'])
            self.assertIn('ValueError',value['release_checks']['cli']['traceback'])

    def test_failed_self_check_reaches_the_process_exit_without_app_startup(self):
        with self.diagnostic(tls_error=FileNotFoundError('Synthetic policy missing')) as env,\
                patch.object(desktop,'initialize',side_effect=AssertionError('No application startup')):
            with self.assertRaises(SystemExit) as caught:desktop.main()
            self.assertEqual(caught.exception.code,1)
            self.assertFalse(self.result(env)['ok'])


if __name__=='__main__':unittest.main()
