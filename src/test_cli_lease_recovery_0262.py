"""A finished CLI operation may retry only its exact retained software lease."""
from contextlib import ExitStack,contextmanager
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from cli_coordination import atomic,reservation
from native_cli_maintenance import CLIReleaseUnconfirmed,retry_release
from native_cli_update import selection,pointer
from official_cli import OfficialCLI,selected
from test_native_cli_maintenance import component,OLD,NEW

BASE=Path(__file__).resolve().parents[2]/'update-flow-0262/update-audit/test-temp'

class CLILeaseRecoveryTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        BASE.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=BASE) as name,ExitStack() as stack:
            root=Path(name);native=root/'native';native.mkdir();old=component(OLD,'a');new=component(NEW,'b')
            config=dict(mode='wsl',cli='wuji');store=Mock(folder=native);store.fetch_release.return_value={'tag_name':'v'+NEW};store.prepare.return_value=new
            owner=OfficialCLI(root/'cli',Mock(),native_store=store)
            def current(**kw):return copy.deepcopy(new if (selection(native).get('current') or {}).get('version')==NEW else old)
            for effect in (patch('bridge_config.load_config',return_value=config),patch('official_device_discovery.use_native',return_value=True),patch('native_cli.current_info',side_effect=current),patch('native_cli.prepare',side_effect=current),patch('native_cli_maintenance.version_info',side_effect=lambda v,**kw:copy.deepcopy(new if v==NEW else old)),patch('managed_runtime.image_manifest',return_value={}),patch('official_cli.logging.getLogger'),patch('subprocess.Popen',side_effect=AssertionError('No vendor process')),patch('subprocess.run',side_effect=AssertionError('No vendor process')),patch('urllib.request.urlopen',side_effect=AssertionError('No network'))):stack.enter_context(effect)
            yield owner,store,old,new

    def retained(self,owner):
        with patch('native_cli_maintenance.release',side_effect=PermissionError('synthetic persistent sharing denial')):owner._work(False)
        self.assertTrue(owner.state['recovery_required']);self.assertEqual(owner.state['phase'],'error')
        return copy.deepcopy(reservation(owner.folder))

    def test_applied_update_retained_lease_is_not_cleared_by_poll_or_automatic_work(self):
        with self.environment() as (owner,store,old,new):
            marker=self.retained(owner);self.assertEqual(selection(store.folder)['current'],pointer(new))
            checked=owner.snapshot();self.assertTrue(checked['update']['recovery_required']);self.assertEqual(reservation(owner.folder),marker)
            calls=store.fetch_release.call_count;owner._work(True,automatic=True)
            self.assertEqual(store.fetch_release.call_count,calls);self.assertEqual(reservation(owner.folder),marker)
            with self.assertRaisesRegex(ValueError,'explicitly'):owner.start(automatic=True)
            owner._work(True);self.assertFalse(owner.state['recovery_required']);self.assertEqual(reservation(owner.folder),{})
            owner.rollback();self.assertEqual(selection(store.folder)['current'],pointer(old))

    def test_transfer_failure_retains_original_selection_then_explicit_retry_updates(self):
        with self.environment() as (owner,store,old,new):
            atomic(store.folder/'selection.json',dict(current=pointer(old)));before=(store.folder/'selection.json').read_bytes()
            store.prepare.side_effect=OSError('synthetic interrupted transfer');self.retained(owner)
            self.assertEqual((store.folder/'selection.json').read_bytes(),before)
            store.prepare.side_effect=None;owner._work(False)
            self.assertFalse(owner.state['recovery_required']);self.assertEqual(reservation(owner.folder),{})
            self.assertEqual(selection(store.folder)['current'],pointer(new));self.assertEqual(selection(store.folder)['previous'],pointer(old))

    def test_rollback_written_before_release_failure_does_not_toggle_again_on_retry(self):
        with self.environment() as (owner,store,old,new):
            owner._work(False)
            with patch('native_cli_maintenance.release',side_effect=PermissionError('synthetic rollback release denial')):
                with self.assertRaises(CLIReleaseUnconfirmed):owner.rollback()
            self.assertEqual(selection(store.folder)['current'],pointer(old));before=(store.folder/'selection.json').read_bytes()
            owner.rollback()
            self.assertEqual((store.folder/'selection.json').read_bytes(),before)
            self.assertFalse(owner.state['recovery_required']);self.assertEqual(reservation(owner.folder),{})

    def test_changed_complete_marker_and_foreign_workspace_are_never_released(self):
        for difference in ({'token':'f'*32},{'created':-1},{'start':'another-process'},{'kind':'firmware'}):
            with self.subTest(difference=difference),self.environment() as (owner,store,old,new):
                marker=self.retained(owner);changed={**marker,**difference};atomic(owner.folder/'session.json',changed)
                owner._work(True);self.assertTrue(owner.state['recovery_required']);self.assertEqual(json.loads((owner.folder/'session.json').read_text()),changed)
                self.assertFalse(owner.cancel_and_wait(timeout=0));self.assertEqual(json.loads((owner.folder/'session.json').read_text()),changed)

    def test_worker_still_alive_cannot_release_or_reuse_its_receipt(self):
        with self.environment() as (owner,store,old,new):
            marker=self.retained(owner);worker=Mock();worker.is_alive.return_value=True;owner.thread=worker
            with self.assertRaisesRegex(ValueError,'worker'):retry_release(owner)
            with self.assertRaisesRegex(ValueError,'progress'):owner.start(check_only=True)
            self.assertFalse(owner.cancel_and_wait(timeout=.01));self.assertEqual(reservation(owner.folder),marker)
            worker.is_alive.return_value=False;self.assertTrue(owner.cancel_and_wait(timeout=.01))
            self.assertEqual(reservation(owner.folder),{});self.assertFalse(owner.state['recovery_required'])

    def test_explicit_exit_retains_failure_then_retry_releases_only_original_owner(self):
        with self.environment() as (owner,store,old,new):
            marker=self.retained(owner)
            with patch('native_cli_maintenance.release',side_effect=PermissionError('synthetic denial still present')):
                self.assertFalse(owner.cancel_and_wait(timeout=0))
            self.assertEqual(reservation(owner.folder),marker);self.assertEqual(owner.state['phase'],'error')
            self.assertTrue(owner.cancel_and_wait(timeout=0));self.assertEqual(reservation(owner.folder),{})
            self.assertFalse(owner.state['recovery_required']);self.assertEqual(owner.state['phase'],'cancelled')

    def test_another_live_instance_cannot_claim_receipt_but_dead_process_restart_can_use_gate(self):
        with self.environment() as (owner,store,old,new):
            marker=self.retained(owner);other=OfficialCLI(owner.folder,Mock(),native_store=store)
            with self.assertRaises(ValueError):other.rollback()
            self.assertEqual(reservation(owner.folder),marker);self.assertIsNone(other._maintenance_marker)
            # Model original process death in only the isolated marker. A new
            # object must use normal cross-process identity rules, not copy the
            # original object's private recovery permission.
            atomic(owner.folder/'session.json',{**marker,'pid':2**30,'start':'known-dead-QA-process'})
            restarted=OfficialCLI(owner.folder,Mock(),native_store=store);restarted.rollback()
            self.assertEqual(selection(store.folder)['current'],pointer(old));self.assertEqual(reservation(owner.folder),{})
            self.assertIsNone(restarted._maintenance_marker)

    def test_linux_rollback_retains_exact_target_and_explicit_retry_never_flips_back(self):
        with self.environment() as (owner,store,old,new),patch('official_device_discovery.use_native',return_value=False):
            first=dict(version=OLD,binary='/synthetic/old',validated=True,mode='local');second=dict(version=NEW,binary='/synthetic/new',validated=True,mode='local')
            atomic(owner.folder/'selection.json',dict(current=first));owner._activate(second)
            with patch('native_cli_maintenance.release',side_effect=PermissionError('synthetic Linux rollback release denial')):
                with self.assertRaises(CLIReleaseUnconfirmed):owner.rollback()
            self.assertEqual(selected(owner.folder)['current'],first);before=(owner.folder/'selection.json').read_bytes()
            owner.rollback();self.assertEqual((owner.folder/'selection.json').read_bytes(),before)
            self.assertEqual(reservation(owner.folder),{});self.assertFalse(owner.state['recovery_required'])

    def test_failed_exit_preserves_live_monitor_and_explicit_check_resumes_same_monitor(self):
        with self.environment() as (owner,store,old,new),patch.dict(os.environ,{'WUJI_CLI_AUTO_CHECK':'1','WUJI_FLEET_ID':''}):
            atomic(owner.folder/'update-preferences.json',{'native_auto_update':True})
            store.fetch_release.return_value={'tag_name':'v'+OLD}
            owner.startup();monitor=owner.monitor
            try:
                owner.thread.join(3);self.assertTrue(monitor.is_alive());self.assertFalse(owner.monitor_stop.is_set())
                store.fetch_release.return_value={'tag_name':'v'+NEW}
                with patch('native_cli_maintenance.release',side_effect=PermissionError('synthetic close release still held')):
                    owner.start();owner.thread.join(3);self.assertTrue(owner.state['recovery_required'])
                    self.assertFalse(owner.cancel_and_wait(timeout=.1))
                self.assertTrue(monitor.is_alive());self.assertFalse(owner.monitor_stop.is_set());self.assertTrue(owner.closed.is_set())
                before=store.fetch_release.call_count
                with self.assertRaises(ValueError):owner.start(check_only=True,automatic=True)
                self.assertEqual(store.fetch_release.call_count,before)
                owner.start(check_only=True);owner.thread.join(3)
                self.assertFalse(owner.state['recovery_required']);self.assertFalse(owner.closed.is_set());self.assertIs(owner.monitor,monitor)
                self.assertTrue(monitor.is_alive());self.assertFalse(owner.monitor_stop.is_set())
                self.assertTrue(json.loads((owner.folder/'update-preferences.json').read_text())['native_auto_update'])
            finally:
                owner.monitor_stop.set();monitor.join(3)

    def test_confirmed_exit_stops_real_monitor_and_closed_tick_cannot_restart_automatic_work(self):
        with self.environment() as (owner,store,old,new),patch.dict(os.environ,{'WUJI_CLI_AUTO_CHECK':'1','WUJI_FLEET_ID':''}):
            atomic(owner.folder/'update-preferences.json',{'native_auto_update':True})
            store.fetch_release.return_value={'tag_name':'v'+OLD}
            owner.startup();monitor=owner.monitor
            try:
                owner.thread.join(3);self.assertTrue(monitor.is_alive())
                self.assertTrue(owner.cancel_and_wait(timeout=.1));monitor.join(3)
                self.assertFalse(monitor.is_alive());self.assertTrue(owner.monitor_stop.is_set());self.assertTrue(owner.closed.is_set())
                before=store.fetch_release.call_count
                with self.assertRaisesRegex(ValueError,'closing'):owner.start(check_only=True,automatic=True)
                self.assertEqual(store.fetch_release.call_count,before)
                # One deterministic timer wake, then stop. It must respect the
                # same real object's closed flag rather than clear it in start.
                with patch.object(owner.monitor_stop,'wait',side_effect=[False,True]),patch.object(owner,'start') as start:
                    owner._monitor();start.assert_not_called()
            finally:
                owner.monitor_stop.set();monitor.join(3)

if __name__=='__main__':unittest.main()
