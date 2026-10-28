"""Native update lifecycle using fake releases, exact leases and temp pointers."""
import copy
import json
from pathlib import Path
import tempfile
from unittest.mock import Mock,patch
import unittest

from cli_coordination import atomic,reservation
from native_cli_update import pointer,selection
from official_cli import OfficialCLI
from native_cli_maintenance import preference

OLD='2026.10.12';NEW='2026.10.26'

def component(version,char):
    return dict(version=version,binary_sha256=char*64,archive_sha256=char*64,archive_size=123,
        binary='/synthetic/'+version+'/wuji.exe',validated=True)

class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.folder=self.root/'cli';self.native=self.root/'native';self.native.mkdir()
        self.old=component(OLD,'a');self.new=component(NEW,'b');self.config=dict(mode='wsl',cli='wuji')
        self.store=Mock(folder=self.native);self.store.fetch_release.return_value={'tag_name':'v'+NEW}
        self.store.prepare.return_value=self.new;self.idle=Mock()
        self.owner=OfficialCLI(self.folder,self.idle,native_store=self.store)
        def current(**kwargs):
            value=(selection(self.native).get('current') or {}).get('version',OLD)
            return copy.deepcopy(self.old if value==OLD else self.new)
        for item in (patch('bridge_config.load_config',side_effect=lambda:copy.deepcopy(self.config)),
                     patch('official_device_discovery.use_native',return_value=True),
                     patch('native_cli.current_info',side_effect=current),patch('native_cli.prepare',side_effect=current),
                     patch('native_cli_maintenance.version_info',side_effect=lambda value,**kwargs:copy.deepcopy(self.old if value==OLD else self.new)),
                     patch('managed_runtime.image_manifest',return_value={}),patch('official_cli.logging.getLogger')):
            item.start();self.addCleanup(item.stop)
        self.addCleanup(self.owner.cancel_and_wait)

    def enable(self):atomic(self.folder/'update-preferences.json',{'native_auto_update':True})
    def saved(self):return selection(self.native)

    def test_manual_update_activates_exact_prepared_identity_and_retains_rollback(self):
        self.owner._work(False)
        self.assertEqual(self.saved()['current'],pointer(self.new));self.assertEqual(self.saved()['previous'],pointer(self.old))
        self.assertEqual(self.idle.call_count,2);self.assertEqual(reservation(self.folder),{})
        value=self.owner.snapshot();self.assertEqual(value['current']['version'],NEW)
        self.assertEqual(value['update']['update_method'],'managed_native');self.assertFalse(value['update']['auto_update'])
        self.assertEqual(value['update']['phase'],'ready')

    def test_check_and_default_automatic_check_never_install(self):
        for args in ((True,False),(True,True),(False,True)):
            with self.subTest(args=args):
                self.owner._work(*args);self.store.prepare.assert_not_called()
                self.assertEqual(self.saved(),{});self.assertTrue(self.owner.state['available'])

    def test_enabled_startup_check_can_update_when_idle(self):
        self.enable();self.owner._work(True,automatic=True)
        self.assertEqual(self.saved()['current']['version'],NEW)

    def test_busy_workspace_waits_without_download_or_pointer_change(self):
        def busy():self.owner.closed.set();raise ValueError('One workspace is still in use')
        self.idle.side_effect=busy;self.owner._work(False)
        self.store.prepare.assert_not_called();self.assertEqual(self.saved(),{});self.assertEqual(reservation(self.folder),{})

    def test_idle_recheck_failure_cannot_activate_a_prepared_candidate(self):
        self.idle.side_effect=[None,ValueError('Another workspace opened')];self.owner._work(False)
        self.store.prepare.assert_called_once();self.assertEqual(self.saved(),{})
        self.assertEqual(self.owner.state['phase'],'error');self.assertEqual(reservation(self.folder),{})

    def test_turn_off_during_automatic_download_leaves_old_current_and_manual_can_use_cache(self):
        self.enable()
        def downloaded(*args,**kwargs):
            atomic(self.folder/'update-preferences.json',{'native_auto_update':False});return self.new
        self.store.prepare.side_effect=downloaded;self.owner._work(False,automatic=True)
        self.assertEqual(self.saved(),{});self.assertEqual(self.owner.state['phase'],'checked')
        self.assertEqual(reservation(self.folder),{});self.owner._work(True)
        self.assertEqual(self.saved(),{});self.assertTrue(self.owner.state['available'])
        self.owner._work(False)
        self.assertEqual(self.saved()['current']['version'],NEW)

    def test_manual_download_is_not_cancelled_by_turning_off_automatic_updates(self):
        self.enable()
        def downloaded(*args,**kwargs):
            atomic(self.folder/'update-preferences.json',{'native_auto_update':False});return self.new
        self.store.prepare.side_effect=downloaded;self.owner._work(False)
        self.assertEqual(self.saved()['current']['version'],NEW)

    def test_controller_change_during_download_never_activates(self):
        def downloaded(*args,**kwargs):self.config['cli']='/custom/wuji';return self.new
        self.store.prepare.side_effect=downloaded;self.owner._work(False)
        self.assertEqual(self.saved(),{});self.assertEqual(self.owner.state['phase'],'error')
        self.assertEqual(reservation(self.folder),{})

    def test_failed_download_preserves_prior_then_retry_succeeds(self):
        atomic(self.native/'selection.json',{'current':pointer(self.old)})
        before=(self.native/'selection.json').read_bytes();self.store.prepare.side_effect=OSError('interrupted')
        self.owner._work(False);self.assertEqual((self.native/'selection.json').read_bytes(),before)
        self.assertEqual(reservation(self.folder),{});self.assertEqual(self.owner.state['phase'],'error')
        self.store.prepare.side_effect=None;self.owner._work(False)
        self.assertEqual(self.saved()['current']['version'],NEW)

    def test_cancelled_download_never_activates(self):
        def downloaded(*args,**kwargs):self.owner.closed.set();return self.new
        self.store.prepare.side_effect=downloaded;self.owner._work(False)
        self.assertEqual(self.saved(),{});self.assertEqual(reservation(self.folder),{})
        self.assertEqual(self.owner.state['phase'],'cancelled')

    def test_rollback_hold_blocks_auto_but_allows_explicit_update(self):
        self.owner._work(False);self.owner.rollback()
        self.assertEqual(self.saved()['current']['version'],OLD);self.assertEqual(self.saved()['previous']['version'],NEW)
        self.enable();self.store.prepare.reset_mock();self.owner._work(False,automatic=True)
        self.store.prepare.assert_not_called();self.assertEqual(self.saved()['current']['version'],OLD)
        self.owner._work(False);self.assertEqual(self.saved()['current']['version'],NEW)

    def test_opt_in_is_exact_boolean_persistent_and_queues_while_check_busy(self):
        self.assertFalse(preference(self.owner));self.owner.thread=Mock();self.owner.thread.is_alive.return_value=True
        with patch.object(self.owner,'start') as start:
            for bad in (1,'true',None):
                with self.assertRaises(ValueError):self.owner.set_auto_update(bad)
            self.owner.set_auto_update(True);start.assert_not_called()
            self.assertTrue(preference(self.owner));self.assertTrue(self.owner.state['auto_update_pending'])
            self.owner.set_auto_update(False);self.assertFalse(preference(self.owner));self.assertFalse(self.owner.state['auto_update_pending'])
        self.owner.thread=None

    def test_auto_enable_when_idle_starts_immediately(self):
        with patch.object(self.owner,'start') as start:
            self.owner.set_auto_update(True);start.assert_called_once_with(check_only=False,automatic=True)

    def test_private_task_frame_cannot_change_component_or_auto_preference(self):
        from task_view import execute
        for name in ('official_cli_auto_update','official_cli_update','official_cli_rollback'):
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'main window'):
                execute(Mock(),'x','y',{'name':name,'enabled':True})

    def test_console_auto_update_contract_rejects_unexpected_fields_and_non_boolean(self):
        from console_server import Controller
        controller=Controller.__new__(Controller);controller.official_cli=Mock()
        controller.official_cli.set_auto_update.return_value={'okay':True}
        for command in ({'name':'official_cli_auto_update'}, {'name':'official_cli_auto_update','enabled':1},
                        {'name':'official_cli_auto_update','enabled':True,'silent':True}):
            with self.assertRaises(ValueError):controller._workflow_action(command)
        controller.official_cli.set_auto_update.assert_not_called()
        controller._workflow_action({'name':'official_cli_auto_update','enabled':True})
        controller.official_cli.set_auto_update.assert_called_once_with(True)

if __name__=='__main__':unittest.main()
