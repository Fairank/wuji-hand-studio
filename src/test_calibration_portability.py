"""Official bundle adapters use temporary data and a fake SDK only."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock
import zipfile

from calibration_portability import (calibration_proof,export_native_bundle,file_digest,
    prepare_controller_model,validate_continuation,verify_loaded_model,controller_transfer_directory,prepare_transfer_directory)


MODEL=b'<robot name="synthetic-test" />'


def completed(path):
    result=dict(handedness='right',sdk_user='Alice',calibrated_urdf=str(path))
    return dict(cleanup_required=False,run=dict(id='a'*32,running=False,status='completed',
        source='official_jsonl_native',exit_code=0,cleanup_confirmed=True,error=None,
        serial='WG-SYNTHETIC',side='right',expected_side='right',user='Alice',cli_version='2026.10.12',
        result=dict(event='result',operation='calibrate',calibration='hand_model',schema_version=2,status='ok',result=result)))


def bundle_context(root,model=MODEL,side='right'):
    root.mkdir(parents=True,exist_ok=True)
    stage=root/'fixture.zip'
    with zipfile.ZipFile(stage,'w') as archive:archive.writestr('models/'+side+'_hand.urdf',model)
    digest,size=file_digest(stage,1024*1024)
    target=root/(digest+'.zip')
    if target.exists():stage.unlink()
    else:stage.replace(target)
    return dict(source_run_id='a'*32,source_workspace='test-workspace',serial='WG-SYNTHETIC',
        side=side,source_profile='Alice',model_sha256=hashlib.sha256(model).hexdigest(),model_bytes=len(model),
        bundle_path=str(target),bundle_sha256=digest,bundle_bytes=size)


class FakeManager:
    def __init__(self,root):
        self.root=Path(root);self.users=[];self.imports=0;self.extra=False
        self.preview_owner='owner-1';self.preview_name='Alice';self.import_owner='owner-1'
    def list_users(self):return copy.deepcopy(self.users)
    def preview_user_data(self,path):
        rows=[dict(name='hand_model.right',device_sn=None,state='present',path='models/right_hand.urdf')]
        if self.extra and Path(path).parent.name=='receipts':
            rows.append(dict(name='tactile.model',device_sn='OTHER',state='present',path='tactile/OTHER'))
        return dict(owner_user_id=self.preview_owner,owner_display_name=self.preview_name,components=rows)
    def import_user_data(self,path):
        self.imports+=1
        with zipfile.ZipFile(path) as archive:model=archive.read('models/right_hand.urdf')
        target=self.root/'models/right_hand.urdf';target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(model)
        self.users=[dict(user_id='owner-1',display_name='Alice',is_default=False)]
        return dict(owner_user_id=self.import_owner,owner_display_name='Alice',components=[
            dict(name='hand_model.right',device_sn=None,state='present',path=str(target))])
    def export_user_data(self,owner,path):
        assert owner=='owner-1'
        with zipfile.ZipFile(path,'w') as archive:
            archive.writestr('models/right_hand.urdf',(self.root/'models/right_hand.urdf').read_bytes())
            if self.extra:archive.writestr('tactile/OTHER/model.bin',b'extra')
        rows=[dict(name='hand_model.right',device_sn=None,state='present',path='models/right_hand.urdf')]
        if self.extra:rows.append(dict(name='tactile.model',device_sn='OTHER',state='present',path='tactile/OTHER'))
        return dict(owner_user_id=owner,components=rows)


class PortabilityTests(unittest.TestCase):
    def test_private_transfer_root_is_shared_outside_immutable_code_version(self):
        self.assertEqual(controller_transfer_directory('/opt/hand-workbench/code/'+'a'*16),'/opt/hand-workbench/calibration-transfer')
        self.assertEqual(controller_transfer_directory('/home/test/agent'),'/home/test/agent/calibration-transfer')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'agent';root.mkdir()
            prepared=Path(prepare_transfer_directory(root))
            self.assertTrue(prepared.is_dir());self.assertEqual(prepared.parent,root)

    def test_only_exact_completed_guided_result_produces_proof(self):
        with tempfile.TemporaryDirectory() as folder:
            model=Path(folder)/'right_hand.urdf';model.write_bytes(MODEL);good=completed(model)
            proof=calibration_proof(good,workspace='test')
            self.assertEqual(proof['model_sha256'],hashlib.sha256(MODEL).hexdigest())
            changes=[('running',True),('status','session_ended'),('source','official_tty'),('exit_code',True),
                ('exit_code',9),('cleanup_confirmed',False),('side','left'),('user','Default'),('cli_version','old')]
            for key,value in changes:
                bad=copy.deepcopy(good);bad['run'][key]=value
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):calibration_proof(bad)
            bad=copy.deepcopy(good);bad['cleanup_required']=True
            with self.assertRaises(ValueError):calibration_proof(bad)
            bad=copy.deepcopy(good);bad['run']['result']['result']['sn']='OTHER'
            with self.assertRaises(ValueError):calibration_proof(bad)

    def test_model_missing_or_redirected_never_produces_proof(self):
        with tempfile.TemporaryDirectory() as folder:
            model=Path(folder)/'missing.urdf'
            with self.assertRaisesRegex(ValueError,'missing'):calibration_proof(completed(model))

    def test_official_export_checks_current_user_before_and_after_and_exact_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);model=root/'right_hand.urdf';model.write_bytes(MODEL)
            proof=calibration_proof(completed(model));calls=[]
            def cli(*args):
                calls.append(args)
                if args==('user','show'):return dict(name='Alice',right_hand=dict(calibrated=True))
                with zipfile.ZipFile(args[2],'w') as archive:archive.writestr('models/right_hand.urdf',MODEL)
                return {}
            result=export_native_bundle(proof,root/'transfer',json_command=cli)
            self.assertEqual([row[:2] for row in calls],[('user','show'),('user','export'),('user','show')])
            self.assertEqual(file_digest(result['bundle_path'],1024*1024),(result['bundle_sha256'],result['bundle_bytes']))
            self.assertNotIn('--force',calls[1])

    def test_changed_windows_user_or_model_blocks_export_without_device_command(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);model=root/'right_hand.urdf';model.write_bytes(MODEL)
            proof=calibration_proof(completed(model))
            cli=Mock(return_value=dict(name='Bob',right_hand=dict(calibrated=True)))
            with self.assertRaises(ValueError):export_native_bundle(proof,root/'transfer',json_command=cli)
            cli.assert_called_once_with('user','show')
            model.write_bytes(b'changed')
            cli=Mock(return_value=dict(name='Alice',right_hand=dict(calibrated=True)))
            with self.assertRaises(ValueError):export_native_bundle(proof,root/'transfer',json_command=cli)
            cli.assert_called_once_with('user','show')

    def test_new_owner_imports_once_then_same_source_model_skips_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);context=bundle_context(root/'transfer');manager=FakeManager(root/'sdk')
            receipt=prepare_controller_model(manager,context,folder=root/'transfer',receipt_folder=root/'receipts')
            self.assertEqual(manager.imports,1);self.assertTrue(receipt['imported']);self.assertEqual(receipt['owner_user_id'],'owner-1')
            again=prepare_controller_model(manager,context,folder=root/'transfer',receipt_folder=root/'receipts')
            self.assertFalse(again['imported']);self.assertEqual(manager.imports,1)

    def test_unmanaged_same_owner_is_never_overwritten_or_matched_only_by_name(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);context=bundle_context(root/'transfer');manager=FakeManager(root/'sdk')
            manager.users=[dict(user_id='owner-1',display_name='Alice')]
            with self.assertRaisesRegex(ValueError,'unmanaged'):
                prepare_controller_model(manager,context,folder=root/'transfer',receipt_folder=root/'receipts')
            self.assertEqual(manager.imports,0)

    def test_managed_model_update_requires_original_components_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);context=bundle_context(root/'transfer');manager=FakeManager(root/'sdk')
            prior=prepare_controller_model(manager,context,folder=root/'transfer',receipt_folder=root/'receipts')
            newer=bundle_context(root/'transfer',b'<robot name="new-synthetic" />')
            manager.extra=True
            with self.assertRaisesRegex(ValueError,'additional unmanaged'):
                prepare_controller_model(manager,newer,folder=root/'transfer',receipt_folder=root/'receipts')
            self.assertEqual(manager.imports,1)
            manager.extra=False
            updated=prepare_controller_model(manager,newer,folder=root/'transfer',receipt_folder=root/'receipts')
            self.assertTrue(updated['imported']);self.assertEqual(manager.imports,2)
            self.assertEqual(file_digest(updated['model_path'])[0],newer['model_sha256'])

    def test_tamper_owner_model_side_and_transfer_path_fail_before_import(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);context=bundle_context(root/'transfer')
            cases=[dict(model_sha256='b'*64),dict(side='left'),dict(bundle_sha256='c'*64),dict(bundle_path=str(root/'outside.zip'))]
            for changes in cases:
                manager=FakeManager(root/'sdk')
                with self.subTest(changes=changes),self.assertRaises((ValueError,KeyError)):
                    prepare_controller_model(manager,{**context,**changes},folder=root/'transfer',receipt_folder=root/'receipts')
                self.assertEqual(manager.imports,0)
            manager=FakeManager(root/'sdk');manager.preview_name='Bob'
            with self.assertRaises(ValueError):prepare_controller_model(manager,context,folder=root/'transfer',receipt_folder=root/'receipts')
            self.assertEqual(manager.imports,0)

    def test_external_model_edit_or_wrong_import_owner_never_receives_managed_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);context=bundle_context(root/'transfer');manager=FakeManager(root/'sdk');manager.import_owner='other'
            with self.assertRaisesRegex(ValueError,'owner'):
                prepare_controller_model(manager,context,folder=root/'transfer',receipt_folder=root/'receipts')
            self.assertEqual(list((root/'receipts').glob('*.json')),[])
            manager=FakeManager(root/'sdk2')
            receipt=prepare_controller_model(manager,context,folder=root/'transfer',receipt_folder=root/'receipts2')
            Path(receipt['model_path']).write_bytes(b'external edit')
            with self.assertRaisesRegex(ValueError,'outside'):
                prepare_controller_model(manager,context,folder=root/'transfer',receipt_folder=root/'receipts2')
            self.assertEqual(manager.imports,1)

    def test_custom_override_is_verified_and_never_cleared_or_written(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);model=root/'right_hand.urdf';model.write_bytes(MODEL)
            other=root/'custom.urdf';other.write_bytes(b'<robot name="different" />')
            resource=N(get=lambda:str(other),set=Mock());glove=N(hand_model_path=lambda:resource)
            receipt=dict(model_path=str(model),model_sha256=hashlib.sha256(MODEL).hexdigest(),model_bytes=len(MODEL))
            with self.assertRaisesRegex(ValueError,'custom'):verify_loaded_model(glove,receipt)
            resource.set.assert_not_called()
            other.write_bytes(MODEL);self.assertTrue(verify_loaded_model(glove,receipt))
            ignored=root/'sdk/legacy/right_hand.urdf'
            resource.get=lambda:str(ignored)
            self.assertTrue(verify_loaded_model(glove,receipt,sdk_root=root/'sdk'))
            resource.set.assert_not_called()

    def test_import_owner_renamed_on_controller_is_not_reselected_by_display_name(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);context=bundle_context(root/'transfer');manager=FakeManager(root/'sdk')
            prepare_controller_model(manager,context,folder=root/'transfer',receipt_folder=root/'receipts')
            manager.users[0]['display_name']='Bob'
            with self.assertRaisesRegex(ValueError,'identity changed'):
                prepare_controller_model(manager,context,folder=root/'transfer',receipt_folder=root/'receipts')
            self.assertEqual(manager.imports,1)

    def test_private_context_rejects_extra_fields_empty_owner_and_unbounded_values(self):
        with tempfile.TemporaryDirectory() as folder:
            context=bundle_context(Path(folder))
            for changed in ({**context,'joint_targets':[0]*20},{**context,'model_bytes':True},
                            {**context,'source_profile':'Default'},{**context,'source_run_id':'old'},
                            {**context,'bundle_path':'relative.zip'}):
                with self.subTest(changed=changed),self.assertRaises(ValueError):validate_continuation(changed,path=True)


if __name__=='__main__':unittest.main()
