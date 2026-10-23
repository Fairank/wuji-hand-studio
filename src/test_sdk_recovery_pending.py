"""Root-crash/late-launch SDK recovery, using synthetic roots and fake probes.

No SDK wheel is installed into an actual controller and no device is opened.
"""
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock, patch

import sdk_environment_update as updates
import test_sdk_lifecycle as fixtures

proof_fixture=fixtures.proof_fixture


TOKEN='pending-sdk-operation-0001'
OLD='2026.9.22'


class PendingSDKRecoveryTests(unittest.TestCase):
    wheel=fixtures.OwnedUpdateTests.wheel
    environment=fixtures.OwnedUpdateTests.environment
    runner=fixtures.OwnedUpdateTests.runner

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.tmp.cleanup)
        self.root,self.env,self.site,self.user,self.cli=self.environment(self.tmp.name)
        self.path,self.release=self.wheel(self.tmp.name)
        self.original=proof_fixture(OLD)

    def request(self,**changes):
        value=dict(schema=1,operation_token=TOKEN,mode='wsl',release=copy.deepcopy(self.release),original_proof=copy.deepcopy(self.original))
        value.update(changes)
        target=updates._request_path(self.root,TOKEN)
        updates._atomic_json(target,value)
        return target

    def receipt(self):return self.root/updates.RECEIPTS/(TOKEN+'.json')

    def tree_identity(self):
        return {str(path.relative_to(self.root)):hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.root.rglob('*') if path.is_file() and path.name!='hand-workbench-sdk-update.lock'
                and updates.RECEIPTS not in str(path.relative_to(self.root)).replace('\\','/')}

    def test_missing_receipt_with_exact_request_cancels_under_lock_and_verifies_original(self):
        self.request();before=self.tree_identity()
        run=self.runner(self.original)
        result=updates.recover_guest(self.release,'wsl',TOKEN,self.root,run=run)
        self.assertTrue(result['rollback_confirmed']);self.assertTrue(result['cancelled_before_start'])
        self.assertEqual(result['operation_token'],TOKEN)
        self.assertEqual(result['sdk_proof'],self.original)
        self.assertEqual(self.tree_identity(),before)
        receipt=json.loads(self.receipt().read_text())
        self.assertEqual(receipt['phase'],'cancelled_before_start')
        self.assertEqual(receipt['original_proof'],self.original)
        self.assertEqual(run.call_count,1)

    def test_missing_or_wrong_request_never_creates_a_cancellation_receipt(self):
        run=Mock()
        with self.assertRaises(ValueError):updates.recover_guest(self.release,'wsl',TOKEN,self.root,run=run)
        self.assertFalse(self.receipt().exists());run.assert_not_called()
        for changes in (dict(operation_token='another-sdk-operation'),dict(mode='macvm'),dict(schema=2),
                        dict(release={**self.release,'catalog_sha256':'f'*64}),dict(original_proof={})):
            self.request(**changes)
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                updates.recover_guest(self.release,'wsl',TOKEN,self.root,run=run)
            self.assertFalse(self.receipt().exists())
        run.assert_not_called()

    def test_changed_actual_native_module_version_or_api_never_unlocks(self):
        path=self.request();digest=hashlib.sha256(path.read_bytes()).hexdigest()
        for change in (dict(version='2026.8.3'),dict(module_sha256='f'*64),
                       dict(native_sha256={'wuji_sdk/native.so':'f'*64}),dict(api_compatible=False),dict(available=False)):
            run=self.runner(dict(self.original,**change))
            with self.subTest(change=change),self.assertRaises(ValueError):
                updates.recover_guest(self.release,'wsl',TOKEN,self.root,run=run)
            self.assertFalse(self.receipt().exists())
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),digest)

    def test_late_apply_of_cancelled_token_never_probes_unpacks_or_renames(self):
        self.request()
        updates.recover_guest(self.release,'wsl',TOKEN,self.root,run=self.runner(self.original))
        before=self.tree_identity()
        with patch('sdk_environment_update._probe') as probe,patch('sdk_environment_update.wheel_members') as unpack,patch.object(Path,'rename') as rename:
            with self.assertRaises(updates.SDKUpdateError) as caught:
                updates.apply(self.path,self.release,'wsl',self.root,operation_token=TOKEN)
        self.assertTrue(caught.exception.rollback_confirmed);self.assertFalse(caught.exception.switched)
        probe.assert_not_called();unpack.assert_not_called();rename.assert_not_called()
        self.assertEqual(self.tree_identity(),before)
        self.assertEqual(json.loads(self.receipt().read_text())['phase'],'cancelled_before_start')

    def test_live_helper_holding_apply_lock_prevents_recovery_or_probe(self):
        self.request();run=Mock()
        with updates._guest_lock(self.root):
            with self.assertRaisesRegex(ValueError,'still be running'):
                updates.recover_guest(self.release,'wsl',TOKEN,self.root,run=run)
        self.assertFalse(self.receipt().exists());run.assert_not_called()

    def test_recovery_lock_excludes_a_late_apply_until_tombstone_is_durable(self):
        self.request();late=[]
        def probe(argv,**kwargs):
            def launcher():
                try:updates.apply(self.path,self.release,'wsl',self.root,operation_token=TOKEN)
                except Exception as error:late.append(error)
            thread=threading.Thread(target=launcher);thread.start();thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(late),1);self.assertIsInstance(late[0],ValueError)
            self.assertFalse(self.receipt().exists())
            return N(stdout=json.dumps(self.original))
        result=updates.recover_guest(self.release,'wsl',TOKEN,self.root,run=probe)
        self.assertTrue(result['cancelled_before_start'])
        with self.assertRaises(updates.SDKUpdateError):
            updates.apply(self.path,self.release,'wsl',self.root,operation_token=TOKEN)
        self.assertEqual((self.site/'wuji_sdk/__init__.py').read_bytes(),b'old SDK')

    def test_repeated_recovery_preserves_cancellation_tombstone(self):
        self.request()
        for _ in range(2):
            result=updates.recover_guest(self.release,'wsl',TOKEN,self.root,run=self.runner(self.original))
            self.assertTrue(result['cancelled_before_start'])
            self.assertEqual(json.loads(self.receipt().read_text())['phase'],'cancelled_before_start')

    def test_other_used_or_mismatched_receipt_never_replays_apply(self):
        self.request()
        updates.recover_guest(self.release,'wsl',TOKEN,self.root,run=self.runner(self.original))
        data=json.loads(self.receipt().read_text());data['catalog_sha256']='f'*64
        updates._atomic_json(self.receipt(),data)
        with patch('sdk_environment_update._probe') as probe,self.assertRaises(updates.SDKUpdateError) as caught:
            updates.apply(self.path,self.release,'wsl',self.root,operation_token=TOKEN)
        self.assertFalse(caught.exception.rollback_confirmed);probe.assert_not_called()

    def owned_update(self,runner,on_dispatch=None):
        root=self.root
        class Files:
            def path(self,value):return root/PurePosixPath(value).relative_to('/')
            def __enter__(self):return self
            def __exit__(self,*args):pass
        config=dict(mode='wsl',agent_directory=updates.AGENT,python=updates.PYTHON)
        with patch('sdk_environment_update.sys.platform','win32'),patch('managed_runtime.check_ready'), \
                patch('managed_runtime.args',side_effect=lambda argv,**kwargs:argv),patch('managed_runtime.Files',Files), \
                patch('sdk_environment_update.download_wheel',return_value=(self.path,self.release['files'][0])):
            return updates.update_owned(config,self.release,idle=Mock(),run=runner,
                                        operation_token=TOKEN,on_dispatch=on_dispatch)

    def test_immutable_request_and_release_are_durable_before_dispatch_callback(self):
        calls=[]
        def runner(argv,**kwargs):
            calls.append(argv)
            if len(calls)==1:return N(stdout=json.dumps(self.original))
            release_path=self.root/PurePosixPath(argv[-3]).relative_to('/')
            self.assertTrue(release_path.name.endswith(TOKEN+'.json'))
            self.assertEqual(json.loads(release_path.read_text()),self.release)
            return N(stdout=json.dumps(dict(ok=True,operation_token=TOKEN)))
        def dispatched():
            self.assertEqual(len(calls),1)
            request=json.loads(updates._request_path(self.root,TOKEN).read_text())
            self.assertEqual(request['release'],self.release)
            self.assertEqual(request['original_proof'],self.original)
            self.assertEqual(request['operation_token'],TOKEN)
        self.assertTrue(self.owned_update(runner,dispatched)['ok'])
        self.assertEqual(len(calls),2)

    def test_root_crash_after_dispatch_before_guest_receipt_is_self_service_recoverable(self):
        calls=[]
        def runner(argv,**kwargs):
            calls.append(argv)
            if len(calls)==1:return N(stdout=json.dumps(self.original))
            raise subprocess.TimeoutExpired(argv,300)
        with self.assertRaises(updates.SDKUpdateError) as caught:
            self.owned_update(runner,Mock())
        self.assertFalse(caught.exception.rollback_confirmed);self.assertTrue(caught.exception.switched)
        self.assertFalse(self.receipt().exists())
        result=updates.recover_guest(self.release,'wsl',TOKEN,self.root,run=self.runner(self.original))
        self.assertTrue(result['cancelled_before_start'])
        with self.assertRaises(updates.SDKUpdateError):
            updates.apply(self.path,self.release,'wsl',self.root,operation_token=TOKEN)

    def test_conflicting_immutable_request_stops_before_guest_dispatch(self):
        self.request(operation_token='another-operation-token')
        runner=self.runner(self.original);callback=Mock()
        with self.assertRaises(updates.SDKUpdateError) as caught:self.owned_update(runner,callback)
        self.assertTrue(caught.exception.rollback_confirmed);self.assertFalse(caught.exception.switched)
        callback.assert_not_called();self.assertEqual(runner.call_count,1)

    def test_proven_popen_oserror_is_not_dispatched(self):
        with patch('sdk_environment_update.subprocess.Popen',side_effect=OSError('Popen creation failed')) as popen:
            with self.assertRaises(updates.SDKUpdateError) as caught:
                updates._run_apply_command(['owned-launcher'],timeout=300,env=None,creationflags=0)
        self.assertTrue(caught.exception.rollback_confirmed);self.assertFalse(caught.exception.switched)
        popen.assert_called_once()

    def test_communication_failure_after_popen_never_kills_or_claims_rollback(self):
        process=Mock();process.communicate.side_effect=subprocess.TimeoutExpired(['owned-launcher'],300)
        with patch('sdk_environment_update.subprocess.Popen',return_value=process):
            with self.assertRaises(subprocess.TimeoutExpired):
                updates._run_apply_command(['owned-launcher'],timeout=300,env=None,creationflags=0)
        process.kill.assert_not_called();process.terminate.assert_not_called()

    def test_injected_runner_oserror_is_uncertain_not_claimed_as_proven_popen_failure(self):
        calls=[]
        def runner(argv,**kwargs):
            calls.append(argv)
            if len(calls)==1:return N(stdout=json.dumps(self.original))
            raise OSError('transport error after dispatch')
        with self.assertRaises(updates.SDKUpdateError) as caught:self.owned_update(runner,Mock())
        self.assertFalse(caught.exception.rollback_confirmed);self.assertTrue(caught.exception.switched)


if __name__=='__main__':unittest.main()
