"""Persisted backup ledger failures, actual REST entry, no vendor/device I/O."""
from contextlib import contextmanager
import copy,hashlib,io,json,os,socket,subprocess,tempfile
from pathlib import Path
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

import calibration_backups as backups
import test_calibration_backups_0261 as old
from console_server import Controller,Handler,ORIGIN

BASE=Path(__file__).resolve().parents[2]/'update-flow-0262/calibration-audit/test-temp'

class LedgerTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        with patch.object(old,'BASE',BASE),old.BackupTests().environment() as e:
            saved=old.BackupTests().create(e);e.saved=saved
            e.receipt=e.store.folder/(saved['id']+'.json');e.raw=e.receipt.read_bytes()
            with patch('managed_runtime.status',return_value={'busy':False}):
                e.controller=Controller(factory=old.forbidden,reports=e.folder/'controller-reports')
                e.controller.official_cli.snapshot=Mock(return_value={})
                e.controller.calibration.metadata_check=lambda:None
                yield e

    def response(self,e,method='POST'):
        cmd=dict(name='calibration_backup_restore',backup_id=e.saved['id'],expected_sha256=e.saved['sha256'])
        raw=json.dumps(cmd).encode()
        h=N(path='/api/action' if method=='POST' else '/api/calibration-backup?backup_id='+e.saved['id'],
            server=N(controller=e.controller),rfile=io.BytesIO(raw),wfile=io.BytesIO(),
            headers={'Origin':ORIGIN,'X-Console-Token':e.controller.csrf,'Content-Length':str(len(raw)),'Content-Type':'application/json'},
            allowed_host=lambda:True,_task_view_headers=lambda:None)
        h.reply=Mock()
        (Handler.do_POST if method=='POST' else Handler.do_GET)(h)
        e.last_reply=h.reply.call_args.args
        return h.reply.call_args.args[0]

    def test_each_missing_ledger_field_is_rejected_by_load_HTTP_and_restore_before_spawn(self):
        with self.environment() as e:
            original=json.loads(e.raw)
            for key in original:
                with self.subTest(key=key):
                    broken=copy.deepcopy(original);broken.pop(key);e.receipt.write_text(json.dumps(broken),'utf-8')
                    with self.assertRaises(ValueError):e.store.load(e.saved['id'])
                    self.assertEqual(self.response(e,'GET'),404)
                    self.assertEqual(self.response(e),400)
                    self.assertIn('backup',e.last_reply[1]['error'].lower())
                    e.spawn.assert_not_called()
                    self.assertEqual(json.loads(e.receipt.read_text('utf-8')),broken,'Do not repair or overwrite damaged originals')
            e.receipt.write_bytes(e.raw);self.assertEqual(self.response(e,'GET'),200)

    def test_wrong_types_and_duplicate_fields_never_start_restore(self):
        with self.environment() as e:
            mutations=[('schema',True),('run_id',None),('profile','Default'),('side','both'),('serial',None),
                ('sdk_user',{'user_id':'wrong','display_name':'Other'}),('created_at',float('nan')),('created_at',True),('created_at',10**500),
                ('bytes',True),('sha256','f'),('members',[]),('members',{'model':{'sha256':'a'*64,'bytes':False}}),
                ('profile_metadata',{}),('profile_metadata',{'name':'Other','left_hand':{'calibrated':True},'right_hand':{'calibrated':True}}),
                ('profile_metadata',dict(json.loads(e.raw)['profile_metadata'],is_default='false'))]
            for key,value in mutations:
                with self.subTest(key=key,value=value):
                    broken=json.loads(e.raw);broken[key]=value;e.receipt.write_text(json.dumps(broken),'utf-8')
                    self.assertEqual(self.response(e),400);e.spawn.assert_not_called()
            duplicate=e.raw.decode('utf8').rstrip()[:-1]+',"verified":true}'
            e.receipt.write_text(duplicate,'utf8');self.assertEqual(self.response(e),400);e.spawn.assert_not_called()

    def test_receipt_change_during_archive_read_is_rejected_and_originals_retained(self):
        with self.environment() as e:
            before=e.raw;archive=backups.archive_signature
            def changed(path):
                result=archive(path);record=json.loads(before);record['created_at']+=1
                e.receipt.write_text(json.dumps(record),'utf8');return result
            with patch.object(backups,'archive_signature',side_effect=changed),self.assertRaisesRegex(ValueError,'receipt changed'):
                e.store.load(e.saved['id'])
            self.assertNotEqual(e.receipt.read_bytes(),before);e.spawn.assert_not_called()
            e.receipt.write_bytes(before);self.assertTrue(e.store.load(e.saved['id'])[0]['verified'])

    def test_receipt_permission_failure_has_readable_400_and_explicit_retry(self):
        with self.environment() as e:
            original=Path.open
            def denied(path,*a,**kw):
                if path==e.receipt:raise PermissionError('synthetic denied ledger read')
                return original(path,*a,**kw)
            with patch.object(Path,'open',denied):
                self.assertEqual(self.response(e),400);self.assertIn('unreadable',e.last_reply[1]['error'])
            e.spawn.assert_not_called();self.assertEqual(e.receipt.read_bytes(),e.raw)
            self.assertEqual(self.response(e,'GET'),200)

    def test_new_service_reloads_original_backup_and_verified_restore_never_reuses_old_success(self):
        from calibration_cli import CalibrationCLI
        from calibration_portability import calibration_proof
        import native_cli
        with self.environment() as e:
            # New service objects read the same persisted archive. No previous
            # successful calibration DTO is reconstructed or trusted on restart.
            restarted=CalibrationCLI(reports=e.folder/'restarted-reports')
            with patch('calibration_cli.load_config',return_value={'mode':'local'}),patch('official_device_discovery.use_native',return_value=True):
                opened=restarted.restore_backup(e.saved['id'],e.saved['sha256'])
            self.assertEqual(opened['run']['source'],'official_user_import_tty')
            process=restarted.process;process.stdout=io.StringIO('\n'.join(json.dumps(x) for x in [
                {'type':'ready','serial':old.fixture.SN,'side':'left'},{'type':'exit','exitstatus':0}])+'\n')
            restarted.native._collect()
            self.assertTrue(restarted.run['restore_verified'])
            with self.assertRaises(ValueError):calibration_proof({'run':restarted.run})
            again=CalibrationCLI(reports=restarted.reports)
            self.assertEqual(again.run['status'],'idle')
            with self.assertRaises(ValueError):calibration_proof({'run':again.run})
            store=backups.CalibrationBackups(e.store.folder)
            self.assertEqual(store.load(e.saved['id'])[0]['sdk_user'],old.fixture.SDK_USER)
            # A recreated wearer with the same visible name does not gain this archive.
            with patch('calibration_identity.read_current_sdk_user',return_value={'user_id':'recreated-other-id','display_name':old.fixture.USER}):
                with self.assertRaises(ValueError):store.verify_current_owner(e.saved['id'])

    def test_managed_multiwearer_side_updates_survive_new_manager_and_reject_stale_other_side(self):
        from test_calibration_multimodel_0258 import CalibrationMultipleModelTests,FakeSDK
        from calibration_portability import prepare_controller_model
        helper=CalibrationMultipleModelTests()
        with patch.dict(os.environ,WUJI_CONTROL_QA_ROOT=str(BASE)),helper.environment() as e:
            a=helper.context(e,dict(left=b'A-L1',right=b'A-R1'));helper.apply(e,a)
            b=helper.context(e,dict(left=b'B-L1',right=b'B-R1'),owner='user-B',name='Bob');helper.apply(e,b)
            newer=helper.context(e,dict(left=b'A-L2',right=b'A-R1'),'left');helper.apply(e,newer)
            # The substitute's registry and file contents persist independently
            # of its transport object, as a real SDK service does.
            fresh=FakeSDK(e.sdk.root);fresh.owners=copy.deepcopy(e.sdk.owners);fresh.bundles=copy.deepcopy(e.sdk.bundles)
            e.sdk=fresh
            reused=helper.apply(e,a);self.assertFalse(reused['imported'])
            self.assertEqual(helper.model(e,'user-A','left'),b'A-L2')
            with self.assertRaisesRegex(ValueError,'export the current profile again'):
                helper.apply(e,helper.context(e,dict(left=b'A-L1',right=b'A-R2')))
            helper.apply(e,helper.context(e,dict(left=b'B-L2',right=b'B-R1'),'left',owner='user-B',name='Bob'))
            self.assertEqual((helper.model(e,'user-A','left'),helper.model(e,'user-A','right')),(b'A-L2',b'A-R1'))
            self.assertEqual((helper.model(e,'user-B','left'),helper.model(e,'user-B','right')),(b'B-L2',b'B-R1'))
            changed=e.sdk.root/'user-A'/e.sdk.member('hand_model.left');changed.write_bytes(b'externally-restored-A-left')
            with self.assertRaisesRegex(ValueError,'changed outside this task'):helper.apply(e,newer)
            self.assertEqual(changed.read_bytes(),b'externally-restored-A-left')
            self.assertFalse(helper.apply(e,b)['imported'])
            self.assertEqual(helper.model(e,'user-B','left'),b'B-L2')

if __name__=='__main__':unittest.main()
