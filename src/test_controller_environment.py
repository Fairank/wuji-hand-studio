"""Software environment contracts; only stubs and a tiny synthetic Python child."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from bridge_config import controller_executable
from calibration_cli import controller_args
from doctor import Doctor,sdk_command
from official_tools import OfficialTools,target_versions
from sdk_environment_update import probe_selected


class LocalEnvironmentTests(unittest.TestCase):
    def test_local_relative_executable_uses_worker_directory_without_changing_path_lookup(self):
        config=dict(mode='local',agent_directory='/fixture/controller')
        self.assertEqual(controller_executable(config,'venv/bin/python'),'/fixture/controller/venv/bin/python')
        self.assertEqual(controller_executable(config,'python3'),'python3')
        self.assertEqual(controller_executable(config,'/opt/python'),'/opt/python')
        self.assertEqual(controller_executable(dict(config,mode='ssh'),'venv/bin/python'),'venv/bin/python')
        self.assertEqual(controller_args(config,['bin/wuji','--version']),['/fixture/controller/bin/wuji','--version'])

    def test_relative_local_python_probe_has_same_cwd_and_telemetry_setting_as_worker(self):
        runner=Mock(return_value=N(stdout='{"proof_version":1}'))
        config=dict(mode='local',agent_directory='/fixture/controller',python='venv/bin/python')
        with patch('sdk_environment_update.sys.platform','linux'):
            probe_selected(config,run=runner)
        self.assertEqual(runner.call_args.args[0][0],'venv/bin/python')
        self.assertEqual(runner.call_args.kwargs['cwd'],config['agent_directory'])
        self.assertEqual(runner.call_args.kwargs['env']['WUJI_TELEMETRY_DISABLE'],'1')

    def test_probe_rejects_missing_or_relative_local_directory_before_child_launch(self):
        runner=Mock()
        with patch('sdk_environment_update.sys.platform','linux'):
            for directory in ('','relative'):
                with self.subTest(directory=directory),self.assertRaisesRegex(ValueError,'absolute'):
                    probe_selected(dict(mode='local',agent_directory=directory,python='python3'),run=runner)
        runner.assert_not_called()

    def test_probe_cwd_reaches_real_synthetic_child_without_importing_sdk(self):
        # Replace only the probe payload. The real child returns its cwd, never
        # imports a vendor package, opens a device, or runs a vendor command.
        with tempfile.TemporaryDirectory() as folder:
            absolute=Path(folder).resolve()
            directory=absolute.as_posix()
            if absolute.drive:directory=directory[len(absolute.drive):]
            def runner(_command,**kwargs):
                return subprocess.run([sys.executable,'-c',
                    'import json,os;print(json.dumps({"proof_version":1,"cwd":os.getcwd()}))'],**kwargs)
            with patch('sdk_environment_update.sys.platform','linux'):
                proof=probe_selected(dict(mode='local',agent_directory=directory,python='venv/bin/python'),run=runner)
            self.assertEqual(Path(proof['cwd']),absolute)

    def test_doctor_selects_relative_worker_python_bin_and_custom_cli(self):
        config=dict(mode='local',agent_directory='/fixture/controller',python='venv/bin/python')
        self.assertEqual(sdk_command(config,['wuji','--version'])[:2],
                         ['/usr/bin/env','PATH=/fixture/controller/venv/bin:/usr/local/bin:/usr/bin:/bin'])
        with tempfile.TemporaryDirectory() as folder,patch('doctor.DATA',Path(folder)), \
                patch('doctor.local_run',return_value=(0,'wuji synthetic','')) as runner:
            service=Doctor();service.work(config,['tools/wuji','--version'],'version')
        self.assertIn('/fixture/controller/tools/wuji',runner.call_args.args[0])
        self.assertEqual(service.snapshot()['status'],'completed')


class MacEnvironmentTests(unittest.TestCase):
    environment=dict(LIMA_HOME='/owned/lima',LIMA_SHELL='/bin/bash',
                     LIMA_WORKDIR='/opt/hand-workbench',SSH_AUTH_SOCK='')

    def test_mac_wrapper_passes_each_runtime_override_as_one_argument(self):
        with patch('macos_runtime.ensure_running') as boot, \
                patch('macos_runtime.env',return_value=self.environment), \
                patch('macos_runtime.args',side_effect=lambda args:['/synthetic/limactl',*args]):
            actual=controller_args(dict(mode='macvm'),['/usr/local/bin/wuji','--version'])
        for key,value in self.environment.items():self.assertIn(key+'='+value,actual)
        self.assertEqual(actual[0],'/usr/bin/env');boot.assert_called_once()

    def test_mac_software_checks_keep_owned_env_and_mac_cli_selection(self):
        results=[N(returncode=0,stdout=b'{"sdk_version":"2026.10.12","sdk_imported":true}'),
                 N(returncode=0,stdout=b'wuji 2026.10.12\n')]
        with patch('official_tools.sys.platform','darwin'),patch('macos_runtime.ensure_running'), \
                patch('macos_runtime.env',return_value=self.environment), \
                patch('macos_runtime.args',side_effect=lambda args:['/synthetic/limactl',*args]), \
                patch('macos_runtime.manifest',return_value=dict(cli_version='2026.10.12',sdk_version='2026.10.12')), \
                patch('managed_runtime.image_manifest',side_effect=AssertionError('Mac must use its own manifest')), \
                patch('official_cli.selected',return_value={}), \
                patch('official_cli.cli_path',return_value='/usr/local/bin/wuji') as cli, \
                patch('official_tools.subprocess.run',side_effect=results) as run:
            service=OfficialTools();service.run()
        self.assertTrue(service.check['compatible'])
        self.assertEqual(cli.call_args.args[0]['mode'],'macvm')
        self.assertEqual(run.call_count,2)
        for call in run.call_args_list:
            for key,value in self.environment.items():self.assertIn(key+'='+value,call.args[0])

    def test_original_mac_payload_gets_its_pinned_cli_baseline_only(self):
        with patch('official_tools.sys.platform','darwin'),patch('official_cli.selected',return_value={}), \
                patch('macos_runtime.manifest',return_value=dict(payload_id='mac-linux-1.1.0',sdk_version='2026.10.12')):
            self.assertEqual(target_versions(),dict(cli_version='2026.10.12',sdk_version='2026.10.12'))
        with patch('official_tools.sys.platform','darwin'),patch('official_cli.selected',return_value={}), \
                patch('macos_runtime.manifest',return_value=dict(payload_id='different',sdk_version='2027.1.1')):
            self.assertIsNone(target_versions()['cli_version'])

    def test_doctor_mac_uses_same_full_wrapper(self):
        with tempfile.TemporaryDirectory() as folder,patch('doctor.DATA',Path(folder)), \
                patch('macos_runtime.ensure_running'),patch('macos_runtime.env',return_value=self.environment), \
                patch('macos_runtime.args',side_effect=lambda args:['/synthetic/limactl',*args]), \
                patch('doctor.local_run',return_value=(0,'wuji synthetic','')) as runner:
            service=Doctor();service.work(dict(mode='macvm'),['wuji','--version'],'version')
        for key,value in self.environment.items():self.assertIn(key+'='+value,runner.call_args.args[0])


class DoctorReportRetentionTests(unittest.TestCase):
    def test_failed_report_persistence_retains_both_original_streams_without_retry(self):
        for operation in ('version','diagnose'):
            with self.subTest(operation=operation),tempfile.TemporaryDirectory() as folder, \
                    patch('doctor.DATA',Path(folder)), \
                    patch('doctor.local_run',return_value=(1,'ORIGINAL_STDOUT','ORIGINAL_STDERR')) as runner, \
                    patch.object(Path,'write_text',side_effect=PermissionError('synthetic storage failure')):
                service=Doctor();service.work(dict(mode='local'),['wuji','--version'],operation)
                state=service.snapshot()
            self.assertEqual(state['status'],'completed');self.assertFalse(state['running'])
            self.assertEqual(state['report']['stdout'],'ORIGINAL_STDOUT')
            self.assertEqual(state['report']['stderr'],'ORIGINAL_STDERR')
            self.assertEqual(state['report']['exit_code'],1)
            self.assertIn('could not be saved',state['error']);runner.assert_called_once()


if __name__=='__main__':unittest.main()
