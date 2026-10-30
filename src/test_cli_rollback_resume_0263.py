"""Explicit rollback after refused Exit resumes the existing auto-check worker."""
from contextlib import contextmanager
import json,os
from pathlib import Path
import unittest
from unittest.mock import patch
import test_cli_lease_recovery_0262 as original
from cli_coordination import atomic,reservation
from native_cli_maintenance import CLIReleaseUnconfirmed
from native_cli_update import selection
from official_cli import selected
BASE=Path(__file__).resolve().parents[2]/'update-flow-0263/update-audit/test-temp'

class RollbackMonitorTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        with patch.object(original,'BASE',BASE),original.CLILeaseRecoveryTests().environment() as values:yield values

    def test_successful_rollback_after_failed_exit_unpauses_same_monitor_and_preserves_preference(self):
        with self.environment() as (owner,store,old,new),patch.dict(os.environ,{'WUJI_CLI_AUTO_CHECK':'1','WUJI_FLEET_ID':''}):
            atomic(owner.folder/'update-preferences.json',{'native_auto_update':True});store.fetch_release.return_value={'tag_name':'v'+old['version']}
            owner.startup();monitor=owner.monitor
            try:
                owner.thread.join(3);self.assertTrue(monitor.is_alive())
                store.fetch_release.return_value={'tag_name':'v'+new['version']}
                with patch('native_cli_maintenance.release',side_effect=PermissionError('synthetic retained update lease')):
                    owner.start();owner.thread.join(3);self.assertFalse(owner.cancel_and_wait(timeout=.1))
                self.assertTrue(owner.closed.is_set());self.assertTrue(monitor.is_alive())
                owner.rollback()
                self.assertEqual(selection(store.folder)['current']['version'],old['version']);self.assertFalse(reservation(owner.folder))
                self.assertFalse(owner.closed.is_set());self.assertIs(owner.monitor,monitor);self.assertTrue(monitor.is_alive());self.assertFalse(owner.monitor_stop.is_set())
                self.assertTrue(json.loads((owner.folder/'update-preferences.json').read_text())['native_auto_update'])
            finally:owner.monitor_stop.set();monitor.join(3)

    def test_failed_rollback_cleanup_does_not_unpause_automatic_work(self):
        with self.environment() as (owner,store,old,new):
            owner._work(False);owner.closed.set()
            with patch('native_cli_maintenance.release',side_effect=PermissionError('synthetic rollback release remains held')):
                with self.assertRaises(CLIReleaseUnconfirmed):owner.rollback()
            self.assertTrue(owner.closed.is_set());self.assertTrue(owner.state['recovery_required'])
            marker=reservation(owner.folder);marker['created']=-1;atomic(owner.folder/'session.json',marker)
            with self.assertRaises(CLIReleaseUnconfirmed):owner.rollback()
            self.assertTrue(owner.closed.is_set());self.assertTrue(owner.state['recovery_required'])
            self.assertEqual(reservation(owner.folder),marker)

    def test_linux_rollback_and_committed_release_retry_clear_only_successful_exit_pause(self):
        with self.environment() as (owner,store,old,new),patch('official_device_discovery.use_native',return_value=False):
            first=dict(version=old['version'],binary='/synthetic/old',validated=True,mode='local')
            second=dict(version=new['version'],binary='/synthetic/new',validated=True,mode='local')
            atomic(owner.folder/'selection.json',dict(current=first));owner._activate(second);owner.closed.set()
            with patch('native_cli_maintenance.release',side_effect=PermissionError('synthetic rollback release held')):
                with self.assertRaises(CLIReleaseUnconfirmed):owner.rollback()
            self.assertTrue(owner.closed.is_set());self.assertEqual(selected(owner.folder)['current'],first)
            before=(owner.folder/'selection.json').read_bytes();owner.rollback()
            self.assertFalse(owner.closed.is_set());self.assertEqual((owner.folder/'selection.json').read_bytes(),before)
            owner._activate(second);owner.closed.set();owner.rollback()
            self.assertFalse(owner.closed.is_set());self.assertEqual(selected(owner.folder)['current'],first)

if __name__=='__main__':unittest.main()
