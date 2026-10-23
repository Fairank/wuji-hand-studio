"""Root lifecycle and restart recovery, with synthetic SDK/control evidence."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from sdk_maintenance import SDKMaintenance
from sdk_environment_update import SDKUpdateError
from test_sdk_lifecycle import release_fixture,proof_fixture


class SDKMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name);self.token='a'*32
        self.config=dict(mode='wsl',agent_directory='/opt/hand-workbench',python='/opt/hand-workbench-venv/bin/python')
        self.release=release_fixture();self.proof=proof_fixture('2026.9.22')
        self.lease=Mock(return_value=self.token);self.unlock=Mock();self.updater=Mock()
        self.service=SDKMaintenance(acquire=self.lease,release=self.unlock,idle=Mock(),
            config=lambda:copy.deepcopy(self.config),resolver=lambda **kw:copy.deepcopy(self.release),
            probe=lambda c:copy.deepcopy(self.proof),updater=self.updater,folder=self.root)

    def finish(self):
        self.service.thread.join(2);self.assertFalse(self.service.thread.is_alive())
        return self.service.snapshot()

    def test_check_uses_actual_selected_proof_and_never_installs(self):
        self.service.start('check');state=self.finish()
        self.assertEqual(state['phase'],'available');self.assertTrue(state['can_update'])
        self.assertEqual(self.lease.call_args.args,('sdk_check',));self.updater.assert_not_called()
        self.unlock.assert_called_once_with(self.token)

    def test_background_check_only_reads_catalog_and_selected_sdk_without_reservation(self):
        self.assertTrue(self.service.check_background())
        state=self.service.snapshot();self.assertEqual(state['phase'],'available')
        self.assertTrue(state['can_update']);self.lease.assert_not_called()
        self.unlock.assert_not_called();self.updater.assert_not_called()

    def test_background_result_discarded_when_controller_changes_during_probe(self):
        def probe(config):
            self.config['mode']='ssh'
            return self.proof
        self.service.background_probe=probe
        self.assertFalse(self.service.check_background())
        self.assertEqual(self.service.snapshot()['phase'],'unchecked')
        self.assertIsNone(self.service.snapshot()['proof']);self.lease.assert_not_called()

    def test_manual_operation_generation_supersedes_background_result(self):
        def probe(config):
            with self.service.lock:
                self.service.check_generation+=1
                self.service.state.update(phase='up_to_date',compatible=True)
            return self.proof
        self.service.background_probe=probe
        self.assertFalse(self.service.check_background())
        self.assertEqual(self.service.snapshot()['phase'],'up_to_date')
        self.assertIsNone(self.service.snapshot()['proof']);self.lease.assert_not_called()

    def test_background_never_probes_during_unknown_update_recovery(self):
        self.service.state.update(recovery_required=True)
        self.service.background_probe=Mock()
        self.assertFalse(self.service.check_background());self.service.background_probe.assert_not_called()

    def test_background_cancelled_after_metadata_does_not_launch_controller_probe(self):
        stop=[False]
        self.service.resolver=lambda **kw:(stop.__setitem__(0,True) or self.release)
        self.service.background_probe=Mock()
        self.assertFalse(self.service.check_background(cancelled=lambda:stop[0]))
        self.service.background_probe.assert_not_called();self.lease.assert_not_called()

    def test_already_stable_never_reinstalls(self):
        self.proof=proof_fixture();self.service.start('check');state=self.finish()
        self.assertEqual(state['phase'],'up_to_date');self.assertFalse(state['can_update'])
        self.updater.assert_not_called()

    def test_update_bound_to_exact_lease_and_verifies_actual_final_python(self):
        self.service.start('check');self.finish();self.unlock.reset_mock()
        def update(config,release,**kwargs):
            self.assertEqual(kwargs['operation_token'],self.token)
            saved=json.loads((self.root/'sdk-maintenance.json').read_text())
            self.assertEqual(saved['release']['catalog_sha256'],self.release['catalog_sha256'])
            self.proof=proof_fixture();return dict(ok=True)
        self.updater.side_effect=update
        self.service.start('update');state=self.finish()
        self.assertEqual(state['phase'],'up_to_date');self.unlock.assert_called_once_with(self.token)

    def test_unknown_update_cannot_be_retried_or_unlocked_by_normal_check(self):
        self.service.start('check');self.finish();self.unlock.reset_mock()
        self.updater.side_effect=SDKUpdateError('transport lost',rollback_confirmed=False,switched=True)
        self.service.start('update');state=self.finish()
        self.assertTrue(state['recovery_required']);self.unlock.assert_not_called()
        with self.assertRaises(ValueError):self.service.start('check')
        with self.assertRaises(ValueError):self.service.start('update')

    def test_confirmed_rollback_releases_without_claiming_stable(self):
        self.service.start('check');self.finish();self.unlock.reset_mock()
        self.updater.side_effect=SDKUpdateError('restored',rollback_confirmed=True,switched=True)
        self.service.start('update');state=self.finish()
        self.assertFalse(state['compatible']);self.assertFalse(state['recovery_required'])
        self.unlock.assert_called_once_with(self.token)

    def test_restart_recovery_requires_same_config_exact_marker_and_guest_proof(self):
        self.service.start('check');self.finish();self.unlock.reset_mock()
        self.updater.side_effect=SDKUpdateError('transport lost',rollback_confirmed=False,switched=True)
        self.service.start('update');self.finish()
        recoverer=Mock(return_value=dict(ok=True,sdk_proof=proof_fixture()))
        self.service.recoverer=recoverer
        with patch('cli_coordination.reservation',return_value=dict(kind='sdk_update',token='b'*32)):
            with self.assertRaises(ValueError):self.service.recover()
        recoverer.assert_not_called();self.unlock.assert_not_called()
        with patch('cli_coordination.reservation',return_value=dict(kind='sdk_update',token=self.token)):
            state=self.service.recover()
        recoverer.assert_called_once();self.assertEqual(recoverer.call_args.kwargs['operation_token'],self.token)
        self.assertFalse(state['recovery_required']);self.assertEqual(state['phase'],'unchecked')
        self.unlock.assert_called_once_with(self.token)

    def test_external_environment_is_reported_and_never_modified(self):
        self.config['mode']='ssh';self.service.start('check');state=self.finish()
        self.assertEqual(state['phase'],'external');self.assertFalse(state['can_update'])
        with self.assertRaises(ValueError):self.service.start('update')
        self.updater.assert_not_called()

    def test_offline_check_fails_without_install_or_retained_lock(self):
        self.service.resolver=Mock(side_effect=ValueError('offline'))
        self.service.start('check');state=self.finish()
        self.assertEqual(state['phase'],'error');self.assertFalse(state['recovery_required'])
        self.updater.assert_not_called();self.unlock.assert_called_once_with(self.token)

    def test_changed_controller_invalidates_old_proof_and_update_action(self):
        self.service.start('check');self.finish()
        self.config['mode']='ssh'
        snapshot=self.service.snapshot()
        self.assertEqual(snapshot['phase'],'changed');self.assertIsNone(snapshot['proof'])
        self.assertFalse(snapshot['compatible']);self.assertFalse(snapshot['can_update'])
        with self.assertRaisesRegex(ValueError,'Controller changed'):self.service.start('update')
        self.updater.assert_not_called()

    def test_pre_dispatch_crash_needs_confirmed_owner_exit_before_clearing(self):
        marker=dict(kind='sdk_update',token=self.token,stage='pending',pid=123456,start='101')
        self.service.restore_unknown(marker)
        self.service.recoverer=Mock()
        with patch('cli_coordination.reservation',return_value=marker),patch('sdk_maintenance._owner_exited',return_value=False):
            with self.assertRaisesRegex(ValueError,'exit is unconfirmed'):self.service.recover()
        self.unlock.assert_not_called()
        with patch('cli_coordination.reservation',return_value=marker),patch('sdk_maintenance._owner_exited',return_value=True):
            state=self.service.recover()
        self.service.recoverer.assert_not_called();self.unlock.assert_called_once_with(self.token)
        self.assertFalse(state['recovery_required']);self.assertEqual(state['phase'],'unchecked')


if __name__=='__main__':unittest.main()
