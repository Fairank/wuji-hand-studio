"""Discovery yields to an explicit task; synthetic process/device I/O only."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import subprocess
import threading
import time
import unittest
from unittest.mock import Mock,patch

from cli_coordination import reservation
from official_device_discovery import DiscoveryDeferred,DiscoveryCleanupUnconfirmed,native_devices
from official_visualization import OfficialVisualization


class DiscoveryPriorityTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.viewer=OfficialVisualization(Path(self.temp.name))

    def test_running_scan_yields_before_maintenance_and_does_not_restart(self):
        entered=threading.Event();calls=[]
        def read(config,*args,cancelled=None):
            calls.append(args);entered.set()
            if cancelled is None:return {'devices':[]}
            limit=time.monotonic()+3
            while not cancelled():
                if time.monotonic()>limit:raise AssertionError('Explicit operation did not cancel discovery')
                time.sleep(.005)
            raise DiscoveryDeferred('Yield to explicit firmware request')
        with patch('bridge_config.load_config',return_value={'mode':'local'}),patch('calibration_cli.cli_json',side_effect=read):
            self.viewer.scan();self.assertTrue(entered.wait(1))
            with self.viewer.discovery_priority(timeout=2):
                self.assertTrue(self.viewer.scan_done.is_set())
                self.assertEqual(reservation(self.viewer.folder),{})
                with self.assertRaisesRegex(ValueError,'paused'):self.viewer.scan()
                with self.assertRaisesRegex(ValueError,'paused'):self.viewer.check_scan()
                # Exact-device maintenance may explicitly refresh expired
                # discovery while automatic scans remain suspended.
                self.assertEqual(self.viewer.discover_inline(),[])
            self.assertFalse(self.viewer.snapshot()['busy'])
            self.assertIsNone(self.viewer.snapshot()['error'])
            self.assertFalse(self.viewer.snapshot()['active'])
            self.assertEqual(calls,[('devices',),('devices',)])

    def test_nested_holds_release_on_exception_without_creating_a_session(self):
        with self.assertRaisesRegex(ValueError,'explicit failure'):
            with self.viewer.discovery_priority(),self.viewer.discovery_priority():
                self.assertEqual(self.viewer.scan_pauses,2)
                raise ValueError('explicit failure')
        self.assertEqual(self.viewer.scan_pauses,0)
        self.assertEqual(reservation(self.viewer.folder),{})
        self.assertIsNone(self.viewer.token)

    def test_unconfirmed_child_exit_keeps_discovery_gate_closed(self):
        with patch('bridge_config.load_config',return_value={'mode':'local'}),patch('calibration_cli.cli_json',side_effect=DiscoveryCleanupUnconfirmed('exit unknown')):
            self.viewer.scan()
            limit=time.monotonic()+1
            while not self.viewer.snapshot().get('error') and time.monotonic()<limit:time.sleep(.005)
            self.assertEqual(reservation(self.viewer.folder)['kind'],'scan')
            self.assertTrue(self.viewer.snapshot()['busy'])
            with self.assertRaisesRegex(ValueError,'not finished'):
                with self.viewer.discovery_priority(timeout=.01):self.fail('Unconfirmed process was admitted')
        self.assertEqual(self.viewer.scan_pauses,0)


class CancellableNativeReadTests(unittest.TestCase):
    def run_read(self,owned,cancelled):
        with patch('native_cli.prepare',return_value={'binary':'synthetic-never-executed.exe'}),patch('windows_process_job.OwnedProcess',return_value=owned) as spawn:
            value=native_devices(cancelled=cancelled)
            return value,spawn

    def test_cancelled_discovery_proves_exit_without_launching_another_operation(self):
        cancel=threading.Event();owned=Mock()
        def communicate(**kwargs):
            cancel.set();raise subprocess.TimeoutExpired('synthetic discovery',.1)
        owned.process.communicate.side_effect=communicate;owned.close.return_value=True
        with patch('native_cli.prepare',return_value={'binary':'synthetic-never-executed.exe'}),patch('windows_process_job.OwnedProcess',return_value=owned) as spawn:
            with self.assertRaises(DiscoveryDeferred):native_devices(cancelled=cancel.is_set)
        self.assertEqual(spawn.call_count,1)
        self.assertEqual(spawn.call_args.args[0],['synthetic-never-executed.exe','--json','devices'])
        owned.close.assert_called_once_with(timeout=2)

    def test_failed_cleanup_is_not_released_as_a_successful_cancel(self):
        owned=Mock();owned.process.communicate.return_value=(json.dumps({'devices':[]}), '')
        owned.process.returncode=0;owned.close.return_value=False
        with self.assertRaises(DiscoveryCleanupUnconfirmed):self.run_read(owned,lambda:False)

    def test_successful_scan_only_runs_devices_and_closes_its_child(self):
        owned=Mock();owned.close.return_value=True;owned.process.returncode=0
        owned.process.communicate.return_value=(json.dumps({'devices':[{'sn':'SYNTHETIC','device_type':'wuji_glove'}]}),'')
        value,spawn=self.run_read(owned,lambda:False)
        self.assertEqual(value['devices'][0]['sn'],'SYNTHETIC')
        self.assertEqual(spawn.call_args.args[0],['synthetic-never-executed.exe','--json','devices'])
        owned.close.assert_called_once_with(timeout=2)

    def test_already_cancelled_scan_never_launches_a_child(self):
        with patch('native_cli.prepare',return_value={'binary':'synthetic-never-executed.exe'}),patch('windows_process_job.OwnedProcess') as spawn:
            with self.assertRaises(DiscoveryDeferred):native_devices(cancelled=lambda:True)
        spawn.assert_not_called()


if __name__=='__main__':unittest.main()
