"""Multiple wearers/sides via documented fake SDK reports; no real SDK or device."""
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import patch
import zipfile

from calibration_portability import prepare_controller_model,file_digest


class FakeSDK:
    """Only models the official preview/export/import API contract, not its ZIP format."""
    def __init__(self,root):
        self.root=root;self.owners={};self.bundles={};self.imports=0;self.counter=0;self.current='original'
    def member(self,key):return 'fixtures/'+key+'.data'
    def bundle(self,path,owner,name,models):
        with zipfile.ZipFile(path,'w') as archive:
            for key,data in models.items():archive.writestr(self.member(key),data)
        digest,size=file_digest(path);self.bundles[digest]=(owner,name,copy.deepcopy(models));return digest,size
    def preview_user_data(self,path):
        owner,name,models=self.bundles[file_digest(path)[0]]
        return dict(owner_user_id=owner,owner_display_name=name,components=[dict(name=key,device_sn=None,state='present',path=self.member(key)) for key in models])
    def list_users(self):return [dict(user_id=uid,display_name=row['name'],is_default=False) for uid,row in self.owners.items()]
    def import_user_data(self,path):
        owner,name,models=self.bundles[file_digest(path)[0]];self.imports+=1
        self.owners.setdefault(owner,dict(name=name,keys=set()));rows=[]
        for key,data in models.items():
            target=self.root/owner/self.member(key);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            self.owners[owner]['keys'].add(key);rows.append(dict(name=key,device_sn=None,state='present',path=str(target)))
        return dict(owner_user_id=owner,owner_display_name=name,components=rows)
    def export_user_data(self,owner,path):
        row=self.owners[owner];models={key:(self.root/owner/self.member(key)).read_bytes() for key in row['keys']}
        self.bundle(Path(path),owner,row['name'],models)
        return dict(owner_user_id=owner,owner_display_name=row['name'],components=[dict(name=key,device_sn=None,state='present',path=self.member(key)) for key in models])


def forbidden(*args,**kwargs):raise AssertionError('No network, SDK or device processes')


class CalibrationMultipleModelTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        root=Path(os.environ.get('WUJI_CONTROL_QA_ROOT',str(Path(__file__).resolve().parents[2]/'update-flow-0258/independent-audit/test-temp')))
        root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='multimodel-',dir=root) as temporary,patch.object(socket,'socket',forbidden),patch.object(socket,'create_connection',forbidden),patch.object(subprocess,'Popen',forbidden):
            folder=Path(temporary)
            yield N(root=folder,sdk=FakeSDK(folder/'sdk'),transfer=folder/'transfer',receipts=folder/'receipts')

    def context(self,env,models,side='right',owner='user-A',name='Alice'):
        env.transfer.mkdir(exist_ok=True);env.sdk.counter+=1;stage=env.transfer/('stage-'+str(env.sdk.counter)+'.zip')
        digest,size=env.sdk.bundle(stage,owner,name,{'hand_model.'+key:value for key,value in models.items()})
        target=env.transfer/(digest+'.zip');stage.replace(target);model=models[side]
        return dict(source_run_id=hashlib.sha256((owner+str(env.sdk.counter)).encode()).hexdigest()[:32],source_workspace='main',
            serial='WG_SYNTHETIC_MULTI_0258',side=side,source_profile=name,source_user_id=owner,
            model_sha256=hashlib.sha256(model).hexdigest(),model_bytes=len(model),bundle_path=str(target),bundle_sha256=digest,bundle_bytes=size)

    def apply(self,env,context):return prepare_controller_model(env.sdk,context,folder=env.transfer,receipt_folder=env.receipts)
    def model(self,env,owner,side):return (env.sdk.root/owner/env.sdk.member('hand_model.'+side)).read_bytes()
    def receipt_path(self,env,owner='user-A'):return env.receipts/(hashlib.sha256(owner.encode()).hexdigest()+'.json')

    def test_add_other_side_preserves_original_side_and_other_wearer(self):
        with self.environment() as e:
            self.apply(e,self.context(e,dict(right=b'A-right-v1')))
            self.apply(e,self.context(e,dict(right=b'B-right-v1'),owner='user-B',name='Bob'))
            result=self.apply(e,self.context(e,dict(right=b'A-right-v1',left=b'A-left-v1'),'left'))
            self.assertTrue(result['imported']);self.assertEqual(self.model(e,'user-A','left'),b'A-left-v1')
            self.assertEqual(self.model(e,'user-A','right'),b'A-right-v1');self.assertEqual(self.model(e,'user-B','right'),b'B-right-v1')
            self.assertEqual(set(json.loads(self.receipt_path(e).read_text())['components']),{'hand_model.right|','hand_model.left|'})
            self.assertEqual(e.sdk.current,'original')

    def test_old_other_side_is_rejected_before_import_and_current_export_retry_succeeds(self):
        with self.environment() as e:
            self.apply(e,self.context(e,dict(right=b'R1',left=b'L1')))
            before=self.receipt_path(e).read_bytes()
            with self.assertRaisesRegex(ValueError,'export the current profile again'):
                self.apply(e,self.context(e,dict(right=b'R2',left=b'L-old')))
            self.assertEqual(e.sdk.imports,1);self.assertEqual(self.model(e,'user-A','left'),b'L1')
            self.assertEqual(self.model(e,'user-A','right'),b'R1');self.assertEqual(self.receipt_path(e).read_bytes(),before)
            self.apply(e,self.context(e,dict(right=b'R2',left=b'L1')))
            self.assertEqual(self.model(e,'user-A','right'),b'R2');self.assertEqual(self.model(e,'user-A','left'),b'L1')

    def test_partial_update_keeps_other_owned_receipt_and_later_other_side_update(self):
        with self.environment() as e:
            self.apply(e,self.context(e,dict(right=b'R1',left=b'L1')))
            self.apply(e,self.context(e,dict(right=b'R2')))
            self.assertEqual(set(json.loads(self.receipt_path(e).read_text())['components']),{'hand_model.right|','hand_model.left|'})
            self.apply(e,self.context(e,dict(right=b'R2',left=b'L2'),'left'))
            self.assertEqual((self.model(e,'user-A','right'),self.model(e,'user-A','left')),(b'R2',b'L2'))

    def test_left_then_right_also_preserves_both_models(self):
        with self.environment() as e:
            self.apply(e,self.context(e,dict(left=b'L1'),'left'))
            self.apply(e,self.context(e,dict(left=b'L1',right=b'R1'),'right'))
            self.assertEqual((self.model(e,'user-A','right'),self.model(e,'user-A','left')),(b'R1',b'L1'))

    def test_equal_selected_model_can_be_reused_without_reimporting_stale_other_side(self):
        with self.environment() as e:
            self.apply(e,self.context(e,dict(right=b'R1',left=b'L1')))
            result=self.apply(e,self.context(e,dict(right=b'R1',left=b'L-old')))
            self.assertFalse(result['imported']);self.assertEqual(e.sdk.imports,1)
            self.assertEqual(self.model(e,'user-A','left'),b'L1')

    def test_same_display_name_different_id_cannot_replace_selected_wearer(self):
        with self.environment() as e:
            a=self.context(e,dict(right=b'R1'));self.apply(e,a)
            b=self.context(e,dict(right=b'B1'),owner='user-B',name='Alice')
            with self.assertRaisesRegex(ValueError,'another SDK user'):self.apply(e,{**b,'source_user_id':'user-A'})
            self.assertEqual(e.sdk.imports,1);self.assertEqual(self.model(e,'user-A','right'),b'R1')

    def test_unmanaged_same_id_is_not_adopted_without_owned_verified_pending(self):
        with self.environment() as e:
            context=self.context(e,dict(right=b'R1'));e.sdk.import_user_data(context['bundle_path'])
            with self.assertRaisesRegex(ValueError,'unmanaged'):self.apply(e,context)
            self.assertEqual(e.sdk.imports,1);self.assertFalse(self.receipt_path(e).exists())

    def test_staging_save_failure_precedes_import_and_can_explicitly_retry(self):
        with self.environment() as e:
            context=self.context(e,dict(right=b'R1'));original=Path.open
            def denied(path,*args,**kwargs):
                if path.suffix=='.pending' and args and args[0]=='x':raise PermissionError('synthetic disk denied')
                return original(path,*args,**kwargs)
            with patch.object(Path,'open',denied),self.assertRaisesRegex(ValueError,'nothing was imported'):self.apply(e,context)
            self.assertEqual(e.sdk.imports,0);self.assertEqual(e.sdk.owners,{})
            self.assertTrue(self.apply(e,context)['imported']);self.assertEqual(e.sdk.imports,1)

    def fail_commit(self,e,context):
        original=Path.replace
        def denied(path,target):
            if path.suffix=='.pending' and Path(target)==self.receipt_path(e):raise PermissionError('synthetic receipt commit denied')
            return original(path,target)
        with patch.object(Path,'replace',denied),self.assertRaisesRegex(ValueError,'receipt save is incomplete'):self.apply(e,context)

    def test_verified_commit_failure_retries_only_receipt_after_official_export(self):
        with self.environment() as e:
            context=self.context(e,dict(right=b'R1'));self.fail_commit(e,context)
            self.assertEqual(e.sdk.imports,1);self.assertFalse(self.receipt_path(e).exists())
            pending=list(e.receipts.glob('*.pending'));self.assertEqual(len(pending),1)
            self.assertEqual(json.loads(pending[0].read_text())['_commit']['state'],'verified')
            result=self.apply(e,context)
            self.assertFalse(result['imported']);self.assertEqual(e.sdk.imports,1);self.assertTrue(self.receipt_path(e).is_file())

    def test_update_commit_failure_keeps_old_receipt_until_exact_recovery_and_preserves_other_users(self):
        with self.environment() as e:
            self.apply(e,self.context(e,dict(left=b'L1',right=b'R1')))
            self.apply(e,self.context(e,dict(right=b'B1'),owner='user-B',name='Bob'))
            before=self.receipt_path(e).read_bytes()
            context=self.context(e,dict(right=b'R2'))
            self.fail_commit(e,context)
            self.assertEqual(self.receipt_path(e).read_bytes(),before);self.assertEqual(e.sdk.imports,3)
            self.assertFalse(self.apply(e,context)['imported']);self.assertEqual(e.sdk.imports,3)
            self.assertEqual((self.model(e,'user-A','left'),self.model(e,'user-A','right'),self.model(e,'user-B','right')),(b'L1',b'R2',b'B1'))
            self.assertEqual(set(json.loads(self.receipt_path(e).read_text())['components']),{'hand_model.left|','hand_model.right|'})

    def test_verified_pending_does_not_claim_additional_unmanaged_component(self):
        with self.environment() as e:
            context=self.context(e,dict(right=b'R1'));self.fail_commit(e,context)
            key='hand_model.left';path=e.sdk.root/'user-A'/e.sdk.member(key);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'unmanaged-left')
            e.sdk.owners['user-A']['keys'].add(key)
            with self.assertRaisesRegex(ValueError,'differs from official data'):self.apply(e,context)
            self.assertEqual(e.sdk.imports,1);self.assertEqual(path.read_bytes(),b'unmanaged-left')
            self.assertFalse(self.receipt_path(e).exists())

    def test_corrupt_owned_receipt_paths_do_not_write_sdk_or_raise_raw_type_error(self):
        with self.environment() as e:
            context=self.context(e,dict(right=b'R1'));self.apply(e,context)
            path=self.receipt_path(e);receipt=json.loads(path.read_text());receipt['components']['hand_model.right|']['path']=None
            path.write_text(json.dumps(receipt),'utf8')
            with self.assertRaisesRegex(ValueError,'changed outside this task'):self.apply(e,context)
            self.assertEqual(e.sdk.imports,1);self.assertEqual(self.model(e,'user-A','right'),b'R1')

    def test_pending_recovery_is_exact_run_bundle_and_current_component_set(self):
        with self.environment() as e:
            context=self.context(e,dict(right=b'R1'));self.fail_commit(e,context)
            for changes in (dict(source_run_id='f'*32),dict(model_sha256='f'*64)):
                with self.subTest(changes=changes),self.assertRaises(ValueError):self.apply(e,{**context,**changes})
            path=e.sdk.root/'user-A'/e.sdk.member('hand_model.right');path.write_bytes(b'external')
            with self.assertRaises(ValueError):self.apply(e,context)
            self.assertEqual(e.sdk.imports,1);self.assertFalse(self.receipt_path(e).exists())
            path.write_bytes(b'R1');self.assertFalse(self.apply(e,context)['imported'])

    def test_unconfirmed_import_cannot_borrow_preparing_receipt_to_adopt_user(self):
        with self.environment() as e:
            context=self.context(e,dict(right=b'R1'));original=e.sdk.import_user_data
            def failed(path):original(path);raise OSError('synthetic unconfirmed SDK completion')
            with patch.object(e.sdk,'import_user_data',side_effect=failed),self.assertRaisesRegex(ValueError,'unconfirmed'):self.apply(e,context)
            self.assertEqual(json.loads(next(e.receipts.glob('*.pending')).read_text())['_commit']['state'],'preparing')
            with self.assertRaisesRegex(ValueError,'unmanaged'):self.apply(e,context)
            self.assertEqual(e.sdk.imports,1)


if __name__=='__main__':unittest.main()
