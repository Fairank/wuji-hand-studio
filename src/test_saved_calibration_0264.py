"""Existing official wearer -> verified SDK model, no real SDK or device IO."""
import copy
from contextlib import contextmanager
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch
import zipfile

from calibration_portability import (file_digest,prepare_controller_model,
    validate_continuation,verify_loaded_model)
from glove_bridge import GloveBridge
from test_calibration_portability import FakeManager,bundle_context
import numpy as np  # Cache extension before substituting only the vendor module.

MODEL=b'<robot name="saved-official-model"/>'
SN='WG_SAVED_SYNTHETIC'

class SavedCalibrationTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('saved_calibration'),
            'Existing official profiles require their own verified transfer path')
        import saved_calibration
        return saved_calibration

    @contextmanager
    def environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);registry=root/'registry.toml';model=[MODEL];owner=['owner-1'];name=['Alice'];calls=[]
            def write_registry():
                registry.write_text('current_user_id = '+json.dumps(owner[0])+'\n[[users]]\nuser_id = '+json.dumps(owner[0])+'\ndisplay_name = '+json.dumps(name[0])+'\nis_default = false\n','utf8')
            write_registry()
            metadata=dict(name='Alice',is_current=True,right_hand=dict(calibrated=True,file_name='right_hand.urdf',size_bytes=len(MODEL),calibrated_at='2026-10-20T01:00:00Z'),left_hand=dict(calibrated=False,file_name=None,size_bytes=None))
            mode=['normal'];hook=[None]
            def cli(*args):
                calls.append(args)
                if args==('user','show'):return copy.deepcopy(metadata)
                if len(args)==3 and args[:2]==('user','export'):
                    with zipfile.ZipFile(args[2],'w') as z:
                        z.writestr('models/right_hand.urdf',model[0]);z.writestr('user-note.txt',b'original-not-rewritten')
                        if mode[0]=='volatile':z.writestr('manifest.json',json.dumps({'createdAt':len(calls)}))
                        if mode[0]=='duplicate':z.writestr('other/right_hand.urdf',model[0])
                    if hook[0] is not None:hook[0]()
                    return {}
                raise AssertionError('Unexpected vendor operation '+repr(args))
            yield N(root=root,registry=registry,model=model,owner=owner,name=name,calls=calls,metadata=metadata,cli=cli,mode=mode,hook=hook,write_registry=write_registry)

    def preflight(self,e,**kw):
        return self.api().preflight_saved_profile(SN,'right','Alice',e.root/'native',json_command=e.cli,registry=e.registry,workspace='main',**kw)

    def test_saved_profile_has_separate_provenance_and_imports_original_model(self):
        with self.environment() as e:
            api=self.api();proof=self.preflight(e)
            self.assertEqual(proof['source_kind'],'saved_profile');self.assertIsNone(proof['source_run_id'])
            self.assertRegex(proof['transfer_id'],'^[0-9a-f]{32}$');self.assertNotIn('completed',proof)
            bundle=api.prepare_saved_profile(proof,json_command=e.cli,registry=e.registry)
            remote=e.root/'remote';remote.mkdir();target=remote/(bundle['bundle_sha256']+'.zip');target.write_bytes(Path(bundle['bundle_path']).read_bytes())
            context={**bundle,'bundle_path':str(target)}
            context={k:v for k,v in context.items() if k in {'source_run_id','source_workspace','serial','side','source_profile','source_user_id','model_sha256','model_bytes','bundle_sha256','bundle_bytes','bundle_path','source_kind','transfer_id'}}
            manager=FakeManager(e.root/'sdk')
            # Fake SDK confirms its own exact component/member and owner.
            receipt=prepare_controller_model(manager,context,folder=remote,receipt_folder=e.root/'receipts')
            self.assertEqual(receipt['source_kind'],'saved_profile');self.assertIsNone(receipt['source_run_id'])
            self.assertEqual(file_digest(receipt['model_path']),(hashlib.sha256(MODEL).hexdigest(),len(MODEL)))
            self.assertTrue(verify_loaded_model(N(hand_model_path=lambda:N(get=lambda:receipt['model_path'])),receipt))
            self.assertEqual(manager.imports,1)
            self.assertTrue(api.verify_saved_snapshot(proof,json_command=e.cli,registry=e.registry))
            self.assertTrue(all(c[:2] in [('user','show'),('user','export')] for c in e.calls))
            self.assertFalse(any('--force' in c or '--yes' in c for c in e.calls))

    def test_absent_or_unverifiable_model_keeps_original_profile_and_zero_transfer(self):
        for change in ({'calibrated':False},{'file_name':None},{'file_name':'../right_hand.urdf'}, {'size_bytes':True},{'size_bytes':len(MODEL)+1}):
            with self.subTest(change=change),self.environment() as e:
                e.metadata['right_hand'].update(change)
                with self.assertRaises(ValueError):self.preflight(e)
                self.assertEqual(e.model[0],MODEL)
        with self.environment() as e:
            with self.assertRaises(ValueError):self.api().preflight_saved_profile(SN,'left','Alice',e.root/'native',json_command=e.cli,registry=e.registry)
            self.assertFalse(any(c[1]=='export' for c in e.calls))

    def test_owner_change_during_export_and_same_size_model_change_cannot_apply(self):
        with self.environment() as e:
            def changed():e.owner[0]='other-owner';e.write_registry()
            e.hook[0]=changed
            with self.assertRaises(ValueError):self.preflight(e)
        with self.environment() as e:
            proof=self.preflight(e);original=Path(proof['bundle_path']).read_bytes();e.model[0]=b'X'*len(MODEL)
            with self.assertRaisesRegex(ValueError,'changed|改变'):self.api().prepare_saved_profile(proof,json_command=e.cli,registry=e.registry)
            self.assertEqual(Path(proof['bundle_path']).read_bytes(),original)

    def test_same_name_new_id_and_wrong_side_never_borrow_saved_identity(self):
        with self.environment() as e:
            proof=self.preflight(e);e.owner[0]='same-name-other-id';e.write_registry()
            with self.assertRaises(ValueError):self.api().verify_saved_snapshot(proof,json_command=e.cli,registry=e.registry)
        with self.environment() as e:
            proof=self.preflight(e);bad=copy.deepcopy(proof);bad['side']='left'
            with self.assertRaises(ValueError):self.api().prepare_saved_profile(bad,json_command=e.cli,registry=e.registry)

    def test_duplicate_model_names_and_cancelled_export_never_produce_bundle(self):
        with self.environment() as e:
            e.mode[0]='duplicate'
            with self.assertRaises(ValueError):self.preflight(e)
        with self.environment() as e:
            cancelled=[False]
            e.hook[0]=lambda:cancelled.__setitem__(0,True)
            def check():
                if cancelled[0]:raise ValueError('cancelled explicit transfer')
            with self.assertRaisesRegex(ValueError,'cancelled'):self.preflight(e,cancel_check=check)
            self.assertEqual(e.model[0],MODEL)

    def test_fresh_export_packaging_metadata_is_not_selected_model_identity(self):
        with self.environment() as e:
            e.mode[0]='volatile';proof=self.preflight(e);original=Path(proof['bundle_path']).read_bytes()
            self.assertTrue(self.api().verify_saved_snapshot(proof,json_command=e.cli,registry=e.registry))
            self.assertEqual(Path(proof['bundle_path']).read_bytes(),original)
            self.assertEqual(proof['model_sha256'],hashlib.sha256(MODEL).hexdigest())

    def test_legacy_context_is_unchanged_and_saved_context_cannot_claim_a_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            old=bundle_context(Path(tmp)/'old');self.assertEqual(validate_continuation(old,path=True),old)
            saved=dict(old,source_kind='saved_profile',source_run_id=None,transfer_id='c'*32)
            self.assertEqual(validate_continuation(saved,path=True),saved)
            for bad in (dict(saved,source_run_id='a'*32),{k:v for k,v in saved.items() if k!='transfer_id'},dict(saved,source_kind='completed'),dict(saved,transfer_id=True),dict(old,transfer_id='c'*32)):
                with self.subTest(bad=bad),self.assertRaises(ValueError):validate_continuation(bad,path=True)

    def test_bridge_receipt_carries_saved_kind_but_sdk_wire_has_no_source_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            context=dict(bundle_context(Path(tmp)),source_kind='saved_profile',source_run_id=None,transfer_id='d'*32)
            receipt={k:context[k] for k in ('source_run_id','source_workspace','source_profile','serial','side','model_sha256','source_kind','transfer_id')}
            receipt.update(owner_user_id='owner-1',imported=True)
            bridge=GloveBridge(Mock());bridge.stdin=io.StringIO();bridge._on_model_ready=Mock(return_value={'timeout_ms':250})
            bridge._calibrated_ready({'receipt':receipt},context)
            sent=json.loads(bridge.stdin.getvalue());self.assertEqual(sent,dict(name='glove_open',serial=context['serial'],user_id='owner-1',timeout_ms=250))
            bridge.stdin=io.StringIO()
            with self.assertRaises(ValueError):bridge._calibrated_ready({'receipt':dict(receipt,transfer_id='e'*32)},context)
            self.assertEqual(bridge.stdin.getvalue(),'')

    def test_actual_worker_imports_saved_archive_and_connects_only_exact_observed_route(self):
        from glove_agent import worker_glove
        for wrong_serial in (False,True):
            with self.subTest(wrong_serial=wrong_serial),self.environment() as e:
                proof=self.preflight(e);bundle=self.api().prepare_saved_profile(proof,json_command=e.cli,registry=e.registry)
                remote=e.root/'calibration-transfer';remote.mkdir();path=remote/(bundle['bundle_sha256']+'.zip');path.write_bytes(Path(bundle['bundle_path']).read_bytes())
                context={key:bundle[key] for key in ('source_run_id','source_workspace','serial','side','source_profile','source_user_id','model_sha256','model_bytes','bundle_sha256','bundle_bytes','source_kind','transfer_id')}
                context.update(bundle_path=str(path),glove_route=dict(source='official_discovery',serial=SN,address='192.0.2.25:50001'))
                manager=FakeManager(e.root/'sdk');previous=dict(user_id='',display_name='Default',is_default=True);current=[copy.deepcopy(previous)];calls=[]
                manager.current_user=lambda:copy.deepcopy(current[0])
                def switch(uid):
                    current[0]=dict(user_id=uid,display_name='Alice',is_default=False) if uid else copy.deepcopy(previous)
                    calls.append(('switch',uid))
                manager.switch_user=switch;manager.scan=Mock(side_effect=AssertionError('No broadcast prerequisite'))
                class Glove:
                    serial_number='OTHER' if wrong_serial else SN
                    def hand_side(self):return N(get=lambda:'right')
                    def hand_model_path(self):return N(get=lambda:'')
                    def hand_skeleton(self):
                        calls.append(('subscribe',));return N(subscribe=lambda:N(recv=lambda:None,close=lambda:True))
                    def disconnect(self):calls.append(('disconnect',));return True
                manager.connect=lambda **kw:(calls.append(('connect',kw)) or Glove())
                sdk=N(SdkManager=N(instance=lambda:manager),WujiGlove=Glove,
                    HandModel=N(WujiHand2='hand2',WujiHand='hand1'),Handedness=N(Left='left',Right='right'),
                    RetargetSession=N(for_hand=lambda *a,**kw:N(step=lambda kp:[0.]*20)))
                requests=queue.Queue();events_list=[]
                class Events:
                    def put(self,event):
                        events_list.append(copy.deepcopy(event))
                        if event['type']=='calibrated_ready':requests.put(dict(name='glove_open',serial=SN,user_id='owner-1',timeout_ms=250))
                        if event['type']=='glove_state' and event['state']['connection']=='receiving':requests.put(dict(name='disconnect'))
                original=prepare_controller_model
                def prepare(actual_manager,actual_context):
                    return original(actual_manager,actual_context,folder=remote,receipt_folder=e.root/'receipts')
                with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict(os.environ,WUJI_HAND_PROFILE='hand2_right'),patch('calibration_portability.prepare_controller_model',side_effect=prepare):
                    worker_glove(requests,Events(),context)
                manager.scan.assert_not_called();self.assertEqual(manager.imports,1)
                self.assertEqual([c for c in calls if c[0]=='connect'],[('connect',dict(address='192.0.2.25:50001',device_name='studio_glove'))])
                ready=next(x['receipt'] for x in events_list if x['type']=='calibrated_ready')
                self.assertEqual(ready['source_kind'],'saved_profile');self.assertEqual(ready['transfer_id'],proof['transfer_id']);self.assertIsNone(ready['source_run_id'])
                errors=[x for x in events_list if x['type']=='glove_error']
                self.assertEqual(bool(errors),wrong_serial)
                self.assertEqual(current[0],previous)
                self.assertEqual(sum(c[0]=='subscribe' for c in calls),0 if wrong_serial else 1)
                self.assertTrue(events_list[-1]['session_cleanup']['glove_disconnected'])
                self.assertFalse(any(c[0] in ('follow','prepare','enable') for c in calls))

    def test_saved_A_both_sides_B_and_updated_A_keep_separate_sdk_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);registry=root/'registry.toml';native={('Alice','right'):b'A-right-v1'}
            selected=['Alice'];ids={'Alice':'owner-A','Bob':'owner-B'}
            def metadata():
                return dict(name=selected[0],**{side+'_hand':dict(calibrated=(selected[0],side) in native,
                    file_name=side+'_hand.urdf' if (selected[0],side) in native else None,
                    size_bytes=len(native[(selected[0],side)]) if (selected[0],side) in native else None) for side in ('left','right')})
            def choose(name):
                selected[0]=name;text='current_user_id = '+json.dumps(ids[name])+'\n'
                for label,uid in ids.items():text+='[[users]]\nuser_id = '+json.dumps(uid)+'\ndisplay_name = '+json.dumps(label)+'\nis_default = false\n'
                registry.write_text(text,'utf8')
            def official(*args):
                if args==('user','show'):return metadata()
                self.assertEqual(args[:2],('user','export'))
                with zipfile.ZipFile(args[2],'w') as z:
                    z.writestr('official-owner.json',json.dumps(dict(name=selected[0],id=ids[selected[0]])))
                    for (name,side),data in native.items():
                        if name==selected[0]:z.writestr('models/'+side+'_hand.urdf',data)
                return {}
            class SDK:
                def __init__(self):self.users={};self.imports=0
                def list_users(self):return [dict(user_id=uid,display_name=name,is_default=False) for uid,name in self.users.items()]
                def preview_user_data(self,path):
                    with zipfile.ZipFile(path) as z:
                        owner=json.loads(z.read('official-owner.json'));rows=[]
                        for member in z.namelist():
                            if member.startswith('models/'):rows.append(dict(name='hand_model.'+Path(member).stem.split('_')[0],device_sn=None,state='present',path=member))
                    return dict(owner_user_id=owner['id'],owner_display_name=owner['name'],components=rows)
                def import_user_data(self,path):
                    value=self.preview_user_data(path);self.users[value['owner_user_id']]=value['owner_display_name'];self.imports+=1
                    with zipfile.ZipFile(path) as z:
                        for row in value['components']:
                            target=root/'sdk'/value['owner_user_id']/row['path'];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(row['path']));row['path']=str(target)
                    return value
                def export_user_data(self,uid,path):
                    with zipfile.ZipFile(path,'w') as z:
                        z.writestr('official-owner.json',json.dumps(dict(id=uid,name=self.users[uid])))
                        for p in (root/'sdk'/uid).rglob('*.urdf'):z.writestr(p.relative_to(root/'sdk'/uid).as_posix(),p.read_bytes())
                    return self.preview_user_data(path)
            sdk=SDK();remote=root/'remote';remote.mkdir();api=self.api()
            def apply(name,side):
                choose(name);proof=api.preflight_saved_profile(SN,side,name,root/'native',json_command=official,registry=registry)
                bundle=api.prepare_saved_profile(proof,json_command=official,registry=registry)
                path=remote/(bundle['bundle_sha256']+'.zip');path.write_bytes(Path(bundle['bundle_path']).read_bytes())
                fields={'source_kind','transfer_id','source_run_id','source_workspace','source_profile','source_user_id','serial','side','model_sha256','model_bytes','bundle_sha256','bundle_bytes'}
                context={k:bundle[k] for k in fields};context['bundle_path']=str(path)
                return prepare_controller_model(sdk,context,folder=remote,receipt_folder=root/'receipts')
            first=apply('Alice','right');self.assertTrue(first['imported'])
            native['Alice','left']=b'A-left-v1';apply('Alice','left')
            native['Bob','left']=b'B-left-v1';apply('Bob','left')
            native['Alice','right']=b'A-right-v2';last=apply('Alice','right')
            self.assertEqual((root/'sdk/owner-A/models/left_hand.urdf').read_bytes(),b'A-left-v1')
            self.assertEqual((root/'sdk/owner-A/models/right_hand.urdf').read_bytes(),b'A-right-v2')
            self.assertEqual((root/'sdk/owner-B/models/left_hand.urdf').read_bytes(),b'B-left-v1')
            self.assertEqual(last['owner_user_id'],'owner-A');self.assertIsNone(last['source_run_id'])
            self.assertNotEqual(first['transfer_id'],last['transfer_id'])
            self.assertEqual(sdk.imports,4)

    def test_new_saved_transfer_recovers_only_verified_same_owner_and_contents_without_reimport(self):
        from calibration_portability import CONTEXT_FIELDS
        for changed in (None,'serial','workspace','side','owner','model','preparing','sdk_bytes','fresh_run'):
            with self.subTest(changed=changed),self.environment() as e:
                e.mode[0]='volatile';api=self.api();remote=e.root/'remote';remote.mkdir();receipts=e.root/'receipts'
                def context():
                    proof=self.preflight(e);bundle=api.prepare_saved_profile(proof,json_command=e.cli,registry=e.registry)
                    path=remote/(bundle['bundle_sha256']+'.zip');path.write_bytes(Path(bundle['bundle_path']).read_bytes())
                    return dict({k:bundle[k] for k in CONTEXT_FIELDS|{'source_kind','transfer_id'}},bundle_path=str(path))
                first=context();manager=FakeManager(e.root/'sdk')
                if changed=='fresh_run':
                    first.pop('source_kind');first.pop('transfer_id');first['source_run_id']='a'*32
                original=Path.replace
                def fail_commit(path,target):
                    if path.suffix=='.pending' and Path(target).parent==receipts:raise PermissionError('Synthetic receipt commit failure')
                    return original(path,target)
                with patch.object(Path,'replace',fail_commit),self.assertRaisesRegex(ValueError,'receipt save is incomplete'):
                    prepare_controller_model(manager,first,folder=remote,receipt_folder=receipts)
                pending=list(receipts.glob('*.pending'));self.assertEqual(len(pending),1);self.assertEqual(manager.imports,1)
                second=context()
                if changed=='serial':second['serial']='WG_DIFFERENT'
                elif changed=='workspace':second['source_workspace']='other-workspace'
                elif changed=='side':second['side']='left'
                elif changed=='owner':second['source_user_id']='different-owner'
                elif changed=='model':
                    e.model[0]=b'Z'*len(MODEL);second=context()
                elif changed=='preparing':
                    value=json.loads(pending[0].read_text('utf8'));value['_commit']['state']='preparing';pending[0].write_text(json.dumps(value),'utf8')
                elif changed=='sdk_bytes':(e.root/'sdk/models/right_hand.urdf').write_bytes(b'Z'*len(MODEL))
                elif changed=='fresh_run':
                    second.pop('source_kind');second.pop('transfer_id');second['source_run_id']='b'*32
                if changed is not None:
                    with self.assertRaises(ValueError):prepare_controller_model(manager,second,folder=remote,receipt_folder=receipts)
                    self.assertEqual(len(list(receipts.glob('*.pending'))),1)
                else:
                    self.assertNotEqual(first['transfer_id'],second['transfer_id'])
                    self.assertNotEqual(first['bundle_sha256'],second['bundle_sha256'])
                    recovered=prepare_controller_model(manager,second,folder=remote,receipt_folder=receipts)
                    self.assertFalse(recovered['imported']);self.assertEqual(recovered['transfer_id'],second['transfer_id'])
                    self.assertIsNone(recovered['source_run_id']);self.assertEqual(len(list(receipts.glob('*.pending'))),0)
                    # The receipt was already fully verified before its first
                    # failed commit; explicit retry never needs another import.
                    self.assertEqual(file_digest(recovered['model_path']),(second['model_sha256'],second['model_bytes']))
                self.assertEqual(manager.imports,1)

if __name__=='__main__':unittest.main()
