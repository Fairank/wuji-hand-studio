"""SDK startup failures retain their exact lease and permit explicit safe cleanup."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from cli_coordination import atomic,gate,reservation,reserve,release
from firmware_service import FirmwareReservationHeld
from sdk_maintenance import SDKMaintenance
from test_sdk_lifecycle import release_fixture,proof_fixture


class SDKStartupRecoveryTests(unittest.TestCase):
    def setUp(self):
        qa=Path(__file__).resolve().parents[2]/'update-flow-0261/update-audit/test-temp';qa.mkdir(parents=True,exist_ok=True)
        self.temporary=tempfile.TemporaryDirectory(dir=qa);self.addCleanup(self.temporary.cleanup)
        self.folder=Path(self.temporary.name);self.fail_release=True;self.release_calls=[];self.held=None
        self.proof=proof_fixture('2026.9.22');self.config={'mode':'wsl','agent_directory':'/opt/hand-workbench','python':'/opt/hand-workbench-venv/bin/python'}
        self.updater=Mock(side_effect=self.updated)
        self.service=SDKMaintenance(acquire=self.acquire,release=self.released,idle=Mock(),config=lambda:copy.deepcopy(self.config),
            resolver=lambda **kw:release_fixture(),probe=lambda c:copy.deepcopy(self.proof),updater=self.updater,folder=self.folder)
        self.service.start('check');self.service.thread.join(2);self.assertTrue(self.service.snapshot()['can_update'])

    def acquire(self,kind):
        with gate(self.folder):self.held=reserve(kind,self.folder,metadata={'stage':'pending'})
        return self.held

    def released(self,token):
        self.release_calls.append(token)
        if self.fail_release:raise PermissionError('synthetic maintenance marker is still locked')
        release(token,self.folder)

    def updated(self,config,expected,**kwargs):
        kwargs['on_dispatch']();self.proof=proof_fixture();return {'ok':True}

    def fail_start(self,*,constructor=False,started=False):
        thread=N(ident=44 if started else None,is_alive=lambda:started,start=Mock(side_effect=OSError('synthetic thread start failed')))
        with patch('sdk_maintenance.threading.Thread',side_effect=OSError('synthetic thread constructor failed') if constructor else None,return_value=thread):
            with self.assertRaises(OSError):self.service.start('update')
        return self.service.snapshot()

    def assert_held(self):
        state=self.service.snapshot();self.assertEqual(state['phase'],'unknown');self.assertTrue(state['recovery_required'])
        self.assertFalse(state['busy']);self.assertFalse(state['compatible']);self.assertFalse(state['can_update'])
        self.assertEqual(self.service.token,self.held);self.assertEqual(reservation(self.folder)['token'],self.held)
        self.updater.assert_not_called()

    def test_acquire_typed_held_token_is_not_lost_or_automatically_released(self):
        def acquire_failed(kind):
            token=self.acquire(kind);raise FirmwareReservationHeld(token)
        self.service.acquire=acquire_failed
        with self.assertRaisesRegex(FirmwareReservationHeld,'Component update preparation release'):self.service.start('update')
        self.assert_held();self.assertEqual(self.release_calls,[])
        self.assertNotIn('固件',self.service.snapshot()['error'])
        self.fail_release=False;state=self.service.recover()
        self.assertEqual(state['phase'],'unchecked');self.assertFalse(state['recovery_required']);self.assertIsNone(self.service.token)
        self.assertEqual(reservation(self.folder),{});self.updater.assert_not_called()

    def test_thread_start_release_failure_then_explicit_retry_preserves_marker_until_ack_and_allows_new_update(self):
        self.fail_start();self.assert_held();original=reservation(self.folder)
        self.assertEqual(self.release_calls,[self.held])
        with self.assertRaises(PermissionError):self.service.recover()
        self.assertEqual(reservation(self.folder),original);self.assert_held()
        self.fail_release=False;self.service.recover();self.assertEqual(reservation(self.folder),{})
        self.service.start('check');self.service.thread.join(2)
        self.service.start('update');self.service.thread.join(2)
        state=self.service.snapshot();self.assertEqual(state['phase'],'up_to_date');self.assertTrue(state['compatible'])
        self.assertFalse(state['recovery_required']);self.assertEqual(reservation(self.folder),{});self.updater.assert_called_once()

    def test_constructor_failure_has_the_same_never_started_cleanup_proof(self):
        self.fail_start(constructor=True);self.assert_held();self.fail_release=False
        self.assertEqual(self.service.recover()['phase'],'unchecked');self.assertEqual(reservation(self.folder),{})

    def test_changed_token_or_same_token_owner_cannot_inherit_local_never_started_proof(self):
        self.fail_start();original=reservation(self.folder);calls=len(self.release_calls)
        for change in ({'token':'f'*32},{'start':'changed-owner'},{'pid':987654321},{'stage':'apply'}):
            with self.subTest(change=change):
                altered={**original,**change};atomic(self.folder/'session.json',altered)
                with patch('sdk_maintenance._owner_exited',return_value=True),self.assertRaises(ValueError):self.service.recover()
                self.assertEqual(reservation(self.folder),altered);self.assertEqual(len(self.release_calls),calls)
        atomic(self.folder/'session.json',original);self.fail_release=False;self.service.recover()
        self.assertEqual(reservation(self.folder),{})

    def test_possible_started_thread_keeps_lease_and_requires_owner_exit_instead_of_local_cleanup_proof(self):
        self.fail_start(started=True);self.assert_held();self.assertEqual(self.release_calls,[])
        self.assertIsNone(self.service._not_started_owner)
        with patch('sdk_maintenance._owner_exited',return_value=False),self.assertRaisesRegex(ValueError,'exit is unconfirmed'):
            self.service.recover()
        self.assertEqual(self.release_calls,[]);self.assertEqual(reservation(self.folder)['token'],self.held)

    def test_restored_pending_record_cannot_inherit_local_never_started_proof(self):
        self.fail_start();marker=reservation(self.folder);self.assertIsNotNone(self.service._not_started_owner)
        self.service.restore_unknown(marker);self.assertIsNone(self.service._not_started_owner)
        calls=len(self.release_calls)
        with patch('sdk_maintenance._owner_exited',return_value=False),self.assertRaisesRegex(ValueError,'exit is unconfirmed'):
            self.service.recover()
        self.assertEqual(len(self.release_calls),calls);self.assertEqual(reservation(self.folder),marker)
        self.fail_release=False
        with patch('sdk_maintenance._owner_exited',return_value=True):self.service.recover()
        self.assertEqual(reservation(self.folder),{})


if __name__=='__main__':unittest.main()
