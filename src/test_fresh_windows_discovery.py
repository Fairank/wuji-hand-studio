"""Actual config loader/discovery chain, isolated files and synthetic external I/O.

No real CLI, WSL, SDK, socket, connection, Viewer or motor command is started.
"""
from contextlib import contextmanager, ExitStack
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import calibration_cli
import cli_coordination
import official_device_discovery
import official_visualization


SOURCE=Path(__file__).resolve().with_name('bridge_config.py')
SERIAL='WG_SYNTHETIC_FRESH_INSTALL'
COMPONENT={'binary':'C:/synthetic-never-executed/wuji.exe','version':'2026.10.12'}


def config_module(folder, platform):
    """Execute the unchanged module initializer for each target platform."""
    paths=ModuleType('runtime_paths')
    paths.DATA=folder
    paths.RESOURCE=SOURCE.parent
    module=ModuleType('bridge_config')
    module.__file__=str(SOURCE)
    with patch.object(sys,'platform',platform),patch.dict(sys.modules,{'runtime_paths':paths}):
        exec(compile(SOURCE.read_text(encoding='utf-8'),str(SOURCE),'exec'),module.__dict__)
    return module


class FreshWindowsDiscoveryTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory(prefix='.fresh-windows-discovery-',dir=SOURCE.parent) as temporary,ExitStack() as stack:
            folder=Path(temporary)
            config=config_module(folder/'data','win32')
            stack.enter_context(patch.dict(sys.modules,{'bridge_config':config}))
            stack.enter_context(patch.object(sys,'platform','win32'))
            prepared=stack.enter_context(patch('native_cli.prepare',return_value=COMPONENT))
            run=stack.enter_context(patch.object(official_device_discovery.subprocess,'run'))
            remote=stack.enter_context(patch.object(calibration_cli,'remote_run',side_effect=AssertionError('No remote connection')))
            local=stack.enter_context(patch.object(calibration_cli,'local_run',side_effect=AssertionError('No controller process')))
            ready=stack.enter_context(patch('managed_runtime.check_ready',side_effect=AssertionError('No WSL setup required')))
            controller=stack.enter_context(patch('managed_runtime.WslController',side_effect=AssertionError('No WSL controller')))
            connect=stack.enter_context(patch.object(config,'bridge_client',side_effect=AssertionError('No device connection')))
            start=stack.enter_context(patch.object(official_visualization.OfficialVisualization,'start',side_effect=AssertionError('No automatic Viewer')))
            popen=stack.enter_context(patch('subprocess.Popen',side_effect=AssertionError('No real subprocess')))
            viewer=official_visualization.OfficialVisualization(folder/'coordination')
            yield SimpleNamespace(folder=folder,config=config,prepared=prepared,run=run,remote=remote,local=local,
                                  ready=ready,controller=controller,connect=connect,start=start,popen=popen,viewer=viewer)
            for forbidden in (remote,local,ready,controller,connect,start,popen):forbidden.assert_not_called()

    def reply(self,devices):
        return SimpleNamespace(returncode=0,stdout=json.dumps({'devices':devices}),stderr='')

    def assert_scan_only(self,fixture):
        for call in fixture.run.call_args_list:
            self.assertEqual(call.args[0],[COMPONENT['binary'],'--json','devices'])
            self.assertFalse(call.kwargs.get('shell',False))
        self.assertFalse(fixture.viewer.snapshot()['active'])
        self.assertIsNone(fixture.viewer.process)
        self.assertIsNone(fixture.viewer.native_job)
        self.assertEqual(cli_coordination.reservation(fixture.viewer.folder),{})
        self.assertFalse((fixture.config.DATA/'connection.json').exists())

    def test_fresh_windows_loader_routes_native_without_wsl_or_connection(self):
        with self.fixture() as fixture:
            loaded=fixture.config.load_config()
            self.assertEqual(loaded['mode'],'wsl')
            self.assertEqual(loaded['cli'],'wuji')
            self.assertTrue(official_device_discovery.use_native(loaded))
            fixture.run.return_value=self.reply([{'sn':SERIAL,'device_type':'wuji_glove'}])
            self.assertEqual(calibration_cli.cli_json(loaded,'devices')['devices'][0]['sn'],SERIAL)
            self.assertEqual(fixture.viewer.discover_inline()[0]['sn'],SERIAL)
            self.assertEqual(fixture.viewer.snapshot()['backend'],'windows_native')
            self.assertTrue(fixture.viewer.snapshot()['devices_fresh'])
            self.assert_scan_only(fixture)

    def test_failed_fresh_scan_releases_ownership_then_explicit_retry_succeeds(self):
        with self.fixture() as fixture:
            fixture.run.side_effect=[SimpleNamespace(returncode=1,stdout='',stderr='Synthetic discovery failure'),
                                     self.reply([{'sn':SERIAL,'device_type':'wuji_glove'}])]
            with self.assertRaisesRegex(ValueError,'Synthetic discovery failure'):fixture.viewer.discover_inline()
            failed=fixture.viewer.snapshot()
            self.assertEqual(failed['devices'],[])
            self.assertFalse(failed['busy'])
            self.assertFalse(failed['scanning'])
            self.assertFalse(failed['can_start'])
            self.assert_scan_only(fixture)
            self.assertEqual(fixture.viewer.discover_inline()[0]['sn'],SERIAL)
            self.assertIsNone(fixture.viewer.snapshot()['error'])
            self.assertTrue(fixture.viewer.snapshot()['devices_fresh'])
            self.assertEqual(fixture.run.call_count,2)
            self.assert_scan_only(fixture)

    def test_empty_result_stays_empty_after_existing_bounded_retry(self):
        with self.fixture() as fixture,patch.object(official_device_discovery.time,'sleep') as sleep:
            fixture.run.return_value=self.reply([])
            self.assertEqual(fixture.viewer.discover_inline(),[])
            self.assertEqual(fixture.run.call_count,2)
            sleep.assert_called_once_with(.25)
            self.assertIsNone(fixture.viewer.snapshot()['error'])
            self.assert_scan_only(fixture)

    def test_existing_explicit_or_legacy_ssh_is_preserved_and_uses_remote_cli(self):
        for values in ({'mode':'ssh','host':'controller.invalid','username':'operator','cli':'wuji'},
                       {'host':'controller.invalid','username':'operator','cli':'/custom/wuji'}):
            with self.subTest(values=values),tempfile.TemporaryDirectory(prefix='.fresh-config-',dir=SOURCE.parent) as temporary:
                folder=Path(temporary)
                config=config_module(folder,'win32')
                path=folder/'connection.json'
                path.write_text(json.dumps(values),encoding='utf-8')
                before=path.read_bytes()
                with patch.object(sys,'platform','win32'):
                    loaded=config.load_config()
                    self.assertEqual(loaded['mode'],'ssh')
                    with patch.object(official_device_discovery,'native_devices',side_effect=AssertionError('Remote route must remain remote')) as native,\
                         patch.object(calibration_cli,'remote_run',return_value=(0,'{"devices":[]}','')) as remote,\
                         patch.object(calibration_cli,'local_run',side_effect=AssertionError('No local route')) as local:
                        self.assertEqual(calibration_cli.cli_json(loaded,'devices'),{'devices':[]})
                    remote.assert_called_once_with(loaded,[values['cli'],'--json','devices'])
                    native.assert_not_called();local.assert_not_called()
                self.assertEqual(path.read_bytes(),before)

    def test_existing_custom_wsl_cli_uses_its_original_controller(self):
        with tempfile.TemporaryDirectory(prefix='.fresh-config-',dir=SOURCE.parent) as temporary:
            folder=Path(temporary)
            config=config_module(folder,'win32')
            path=folder/'connection.json'
            path.write_text(json.dumps({'mode':'wsl','cli':'/custom/wuji','agent_directory':'/opt/custom','python':'/opt/custom/bin/python'}),encoding='utf-8')
            before=path.read_bytes()
            with patch.object(sys,'platform','win32'):
                loaded=config.load_config()
                with patch.object(official_device_discovery,'native_devices',side_effect=AssertionError('Custom route must remain custom')) as native,\
                     patch.object(calibration_cli,'controller_args',side_effect=lambda config,args:['synthetic-controller',*args]) as controller,\
                     patch.object(calibration_cli,'local_run',return_value=(0,'{"devices":[]}','')) as local,\
                     patch.object(calibration_cli,'remote_run',side_effect=AssertionError('No SSH route')) as remote:
                    self.assertEqual(calibration_cli.cli_json(loaded,'devices'),{'devices':[]})
                self.assertEqual(controller.call_args.args[0],loaded)
                local.assert_called_once_with(['synthetic-controller','/custom/wuji','--json','devices'])
                native.assert_not_called();remote.assert_not_called()
            self.assertEqual(path.read_bytes(),before)

    def test_non_windows_defaults_and_existing_empty_legacy_file_are_unchanged(self):
        with tempfile.TemporaryDirectory(prefix='.fresh-config-',dir=SOURCE.parent) as temporary:
            folder=Path(temporary)
            for platform,expected in [('linux','local'),('darwin','ssh')]:
                with self.subTest(platform=platform):self.assertEqual(config_module(folder,platform).DEFAULT['mode'],expected)
            config=config_module(folder,'win32')
            path=folder/'connection.json';path.write_text('{}',encoding='utf-8')
            with patch.object(sys,'platform','win32'):self.assertEqual(config.load_config()['mode'],'ssh')
            self.assertEqual(path.read_text(encoding='utf-8'),'{}')


if __name__=='__main__':unittest.main()
