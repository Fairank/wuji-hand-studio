import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from official_tools import OfficialTools,SDK_CHECK


class OfficialToolsTests(unittest.TestCase):
    target=dict(cli_version='2026.10.12',sdk_version='2026.10.12')

    def results(self,sdk='2026.10.12',cli='2026.10.12'):
        return [SimpleNamespace(returncode=0,stdout=json.dumps(dict(sdk_version=sdk,sdk_imported=True)).encode()),
                SimpleNamespace(returncode=0,stdout=('wuji '+cli+'\n').encode())]

    def test_only_actual_sdk_import_and_cli_version_are_probed(self):
        tools=OfficialTools()
        with patch('managed_runtime.args',side_effect=lambda args:args),patch('managed_runtime.image_manifest',return_value=self.target),patch('official_tools.subprocess.run',side_effect=self.results()) as run:
            tools.run()
        self.assertTrue(tools.check['available']);self.assertTrue(tools.check['compatible'])
        self.assertIn(SDK_CHECK,run.call_args_list[0].args[0]);self.assertEqual(run.call_args_list[1].args[0][-1],'--version')
        self.assertNotIn('SdkManager',SDK_CHECK);self.assertNotIn('connect(',SDK_CHECK)
        self.assertIsNotNone(tools.check['checked_at'])

    def test_different_version_is_reported_without_claiming_bundled_compatibility(self):
        tools=OfficialTools()
        with patch('managed_runtime.args',side_effect=lambda args:args),patch('managed_runtime.image_manifest',return_value=self.target),patch('official_tools.subprocess.run',side_effect=self.results(sdk='2026.9.22')):tools.run()
        self.assertTrue(tools.check['available']);self.assertFalse(tools.check['compatible'])
        self.assertEqual(tools.check['sdk_version'],'2026.9.22')

    def test_install_record_cannot_turn_failed_check_into_ready(self):
        tools=OfficialTools()
        with patch('managed_runtime.status',return_value=dict(ready=True,installed=True)),patch('managed_runtime.args',side_effect=lambda args:args),patch('official_tools.subprocess.run',return_value=SimpleNamespace(returncode=1,stdout=b'')):
            tools.run();snapshot=tools.snapshot()
        self.assertTrue(snapshot['runtime']['ready']);self.assertEqual(snapshot['check']['status'],'failed')
        self.assertFalse(snapshot['check']['available']);self.assertFalse(snapshot['check']['busy'])

    def test_retry_clears_old_error_and_does_not_launch_an_installer(self):
        tools=OfficialTools();tools.check.update(status='failed',error='old')
        with patch('managed_runtime.status',return_value=dict(supported=True,installed=True,busy=False)),patch('official_tools.threading.Thread') as thread:
            tools.start_check();self.assertEqual(tools.check['status'],'checking');self.assertIsNone(tools.check['error'])
            thread.return_value.start.assert_called_once()

    def test_missing_runtime_or_install_in_progress_cannot_run_check(self):
        for runtime in (dict(busy=True),dict(busy=False,supported=True,installed=False),dict(supported=False)):
            with self.subTest(runtime=runtime),patch('managed_runtime.status',return_value=runtime),patch('official_tools.threading.Thread') as thread:
                with self.assertRaises(ValueError):OfficialTools().start_check()
                thread.assert_not_called()


if __name__=='__main__':unittest.main()
