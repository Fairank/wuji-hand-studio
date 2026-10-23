"""Fresh stable SDK approval belongs to one exact inactive worker; no network."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import time
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from glove_bridge import GloveBridge
from sdk_release import MAX_AGE
from test_sdk_lifecycle import proof_fixture,release_fixture,VERSION


class FollowSDKGateTests(unittest.TestCase):
    def bridge(self,resolver=None):
        bridge=GloveBridge(Mock(),release_resolver=resolver or Mock(side_effect=release_fixture))
        bridge.thread=N(is_alive=lambda:True);bridge.stdin=io.StringIO();bridge.session=27
        bridge._sdk_proof=proof_fixture()
        bridge.state.update(connection='receiving',hardware=dict(active=False,stop_confirmed=True,probe_ready=True),
            feedback=dict(device_id='SYNTHETIC-HAND',metrics=dict(age_ms=0)),
            stream=dict(fresh=True,age_ms=0,timeout_ms=250,solver=dict(engine='sdk',applied=True)))
        bridge.last_update=time.monotonic()
        return bridge

    def follow(self,bridge):return bridge.action(dict(name='glove_follow',workspace_clear=True))

    def assert_not_sent(self,bridge):
        self.assertIsNone(bridge.lease);self.assertEqual(bridge.stdin.getvalue(),'')
        self.assertIs(bridge.state['hardware']['active'],False)
        self.assertIs(bridge.state['hardware']['stop_confirmed'],True)

    def new_release(self):
        value=release_fixture();value['version']='2026.10.23';value['catalog_sha256']='f'*64
        for row in value['files']:
            row['filename']=row['filename'].replace(VERSION,value['version'])
            row['url']=row['url'].replace(VERSION,value['version'])
        return value

    def test_same_stable_actual_worker_gets_one_consumed_follow_approval(self):
        bridge=self.bridge();result=bridge.verify_follow_sdk()
        self.assertEqual(result['session_id'],27);self.assertEqual(result['version'],VERSION)
        self.follow(bridge)
        self.assertEqual(json.loads(bridge.stdin.getvalue())['name'],'glove_follow')
        self.assertIsNone(bridge._follow_sdk_requirement);bridge.release_resolver.assert_called_once_with()

    def test_long_preview_cannot_reuse_prior_approval_after_new_stable(self):
        bridge=self.bridge();bridge.verify_follow_sdk()
        bridge.release_resolver=Mock(return_value=self.new_release())
        with self.assertRaisesRegex(ValueError,'2026.10.23'):bridge.verify_follow_sdk()
        with self.assertRaises(ValueError):self.follow(bridge)
        self.assert_not_sent(bridge)

    def test_expired_or_unavailable_catalog_blocks_new_follow_without_dispatch(self):
        for resolver in (Mock(return_value=release_fixture(time.time()-MAX_AGE-1)),
                         Mock(side_effect=ValueError('synthetic offline expired catalog'))):
            with self.subTest(resolver=resolver):
                bridge=self.bridge(resolver)
                with self.assertRaises(ValueError):bridge.verify_follow_sdk()
                with self.assertRaises(ValueError):self.follow(bridge)
                self.assert_not_sent(bridge)

    def test_late_verification_never_writes_replacement_session_approval(self):
        bridge=self.bridge();replacement={'replacement_session':True}
        def catalog():
            bridge.session+=1;bridge._follow_sdk_requirement=replacement;bridge._follow_sdk_proof={'replacement':True}
            return release_fixture()
        bridge.release_resolver=Mock(side_effect=catalog)
        with self.assertRaisesRegex(ValueError,'session changed'):bridge.verify_follow_sdk()
        self.assertIs(bridge._follow_sdk_requirement,replacement)
        self.assertEqual(bridge._follow_sdk_proof,{'replacement':True});self.assert_not_sent(bridge)

    def test_sdk_proof_changed_during_lookup_cannot_be_approved(self):
        bridge=self.bridge()
        def catalog():
            bridge._sdk_proof=dict(proof_fixture(),module_sha256='e'*64)
            return release_fixture()
        bridge.release_resolver=Mock(side_effect=catalog)
        with self.assertRaisesRegex(ValueError,'session changed'):bridge.verify_follow_sdk()
        self.assertIsNone(bridge._follow_sdk_requirement);self.assert_not_sent(bridge)

    def test_final_locked_check_rejects_expiry_session_and_proof_changes(self):
        for changed in ('time','session','proof'):
            with self.subTest(changed=changed):
                bridge=self.bridge();bridge.verify_follow_sdk()
                stamp=bridge._follow_sdk_requirement['checked_at']
                if changed=='session':bridge.session+=1
                if changed=='proof':bridge._sdk_proof=dict(proof_fixture(),module_sha256='e'*64)
                with patch('glove_bridge.time.time',return_value=stamp+MAX_AGE+1 if changed=='time' else stamp):
                    with self.assertRaises(ValueError):self.follow(bridge)
                bridge.release_resolver.assert_called_once_with();self.assert_not_sent(bridge)

    def test_no_controller_proof_never_starts_a_catalog_request(self):
        bridge=self.bridge();bridge._sdk_proof=None
        with self.assertRaisesRegex(ValueError,'not confirmed'):bridge.verify_follow_sdk()
        bridge.release_resolver.assert_not_called();self.assert_not_sent(bridge)

    def test_active_follow_and_keepalive_never_query_catalog(self):
        bridge=self.bridge();bridge.lease='synthetic-active-lease';bridge.state['hardware']['active']=True
        with self.assertRaisesRegex(ValueError,'already requested'):bridge.verify_follow_sdk()
        bridge.action(dict(name='glove_keepalive',lease=bridge.lease))
        bridge.release_resolver.assert_not_called()
        self.assertEqual(json.loads(bridge.stdin.getvalue())['name'],'glove_keepalive')
        self.assertIs(bridge.state['hardware']['active'],True)

    def test_final_follow_needs_approval_but_stop_does_not(self):
        bridge=self.bridge()
        with self.assertRaises(ValueError):self.follow(bridge)
        self.assert_not_sent(bridge)
        bridge.action(dict(name='glove_stop'))
        self.assertEqual(json.loads(bridge.stdin.getvalue())['name'],'glove_stop')
        bridge.release_resolver.assert_not_called()

    def test_latest_installed_sdk_cannot_authorize_open_or_unapplied_solver(self):
        for solver in (dict(engine='official_open',applied=True),dict(engine='sdk',applied=False),{}):
            with self.subTest(solver=solver):
                bridge=self.bridge();bridge.state['stream']['solver']=solver;bridge.state['can_prepare']=True
                snapshot=bridge.snapshot();self.assertFalse(snapshot['can_prepare'])
                self.assertIn('official SDK',snapshot['prepare_blocked_reason'])
                with self.assertRaisesRegex(ValueError,'official SDK'):bridge.verify_follow_sdk()
                with self.assertRaisesRegex(ValueError,'official SDK'):
                    bridge.action(dict(name='glove_prepare',serial='SYNTHETIC-HAND',address=''))
                bridge.release_resolver.assert_not_called();self.assert_not_sent(bridge)

    def test_final_locked_dispatch_rechecks_actual_solver_after_sdk_approval(self):
        bridge=self.bridge();bridge.verify_follow_sdk()
        bridge.state['stream']['solver']=dict(engine='official_open',applied=True)
        with self.assertRaisesRegex(ValueError,'official SDK'):self.follow(bridge)
        self.assert_not_sent(bridge);bridge.release_resolver.assert_called_once_with()


class SDKOnlyWorkerTests(unittest.TestCase):
    def test_open_preview_worker_never_prepares_or_dispatches_real_hand(self):
        from test_glove_transport_lifecycle import GloveWorkerLifecycleTests
        from glove_agent import worker_glove
        from retarget_tuning import defaults
        sdk,_=GloveWorkerLifecycleTests().sdk();sdk.RetargetSession.for_hand=Mock()
        commands=queue.Queue();phase=[0];deadline=time.monotonic()+3;hardware=[]
        class Source:
            def __init__(self,*args):pass
            def invalidate(self,message):pass
            def target(self,now):return [0.]*20
            def snapshot(self,now):return dict(fresh=True,q=[0.]*20,seq=1,age_ms=0,timeout_ms=250)
        class Requests(queue.Queue):
            def put(self,item,*args,**kwargs):
                if item.get('name')=='hardware_start':hardware.append(item)
                return super().put(item,*args,**kwargs)
        class Events(queue.Queue):
            def put(self,event):
                super().put(event)
                if time.monotonic()>deadline:commands.put(dict(name='glove_disconnect'));return
                if event['type']=='glove_state' and event['state']['connection']=='receiving' and phase[0]==0:
                    self_case.assertEqual(event['state']['stream']['solver']['engine'],'official_open')
                    self_case.assertFalse(event['state']['can_prepare'])
                    phase[0]=1;commands.put(dict(name='glove_prepare',serial='SYNTHETIC-HAND',address=''))
                elif event['type']=='glove_notice' and phase[0]==1:
                    self_case.assertIn('preview only',event['message'])
                    phase[0]=2;commands.put(dict(name='glove_follow',workspace_clear=True,lease='a'*32))
                elif event['type']=='glove_notice' and phase[0]==2:
                    self_case.assertIn('preview only',event['message'])
                    phase[0]=3;commands.put(dict(name='glove_disconnect'))
        self_case=self;events=Events()
        binding=dict(generation='hand2',side='right',glove_serial='SYNTHETIC-GLOVE',hand_serial='',sdk_user='owner')
        commands.put(dict(name='glove_open',serial='SYNTHETIC-GLOVE',user_id='owner',timeout_ms=250,
                          solver=dict(engine='official_open',values=defaults(binding))))
        solver=N(step=lambda _:[0.]*20,info={},close=Mock())
        with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict(os.environ,WUJI_HAND_PROFILE='hand2_right'), \
                patch('glove_agent.sys.platform','win32'),patch('glove_agent.GloveSource',Source), \
                patch('glove_agent.queue.Queue',Requests),patch('glove_agent.paired_hand_worker') as hand, \
                patch('solver_session.SolverSession',return_value=solver) as open_solver:
            worker_glove(commands,events)
        self.assertEqual(phase[0],3,list(events.queue));self.assertEqual(hardware,[])
        hand.assert_not_called();sdk.RetargetSession.for_hand.assert_not_called();open_solver.assert_called_once()
        solver.close.assert_called_once()


class SDKConnectionDefaultsTests(unittest.TestCase):
    def archived_open_profile(self,controller,binding):
        current=controller.tuning.context(binding);values=copy.deepcopy(current['values'])
        values['segment_scaling']['index'][0]=.75
        saved=controller.tuning.save(binding,values,current['revision'])
        controller.tuning.select_engine(binding,'official_open')
        files={path:path.read_bytes() for path in controller.tuning.folder.glob('*.json') if not path.name.endswith('.engine.json')}
        return saved['values'],files

    def test_new_glove_connection_uses_native_sdk_and_preserves_old_open_parameters(self):
        from console_server import Controller
        from device_profiles import profile
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,WUJI_OFFICIAL_CLI_DATA=folder):
            controller=Controller(factory=Mock(),reports=Path(folder)/'reports')
            controller.state['device_profile']=profile('hand2_right')
            binding=dict(generation='hand2',side='right',glove_serial='SYNTHETIC-GLOVE',hand_serial='',sdk_user='owner')
            controller.retarget.select(binding,controller.state['device_profile'])
            values,files=self.archived_open_profile(controller,binding)
            with patch.object(controller.glove,'action') as action, \
                    patch('studio_component.enforce_session_owner'):
                controller.action(dict(name='glove_open',serial=binding['glove_serial'],user_id='owner',timeout_ms=250))
            self.assertEqual(action.call_args.args[0]['solver']['engine'],'sdk')
            self.assertEqual(controller.tuning.context(binding)['engine'],'sdk')
            self.assertEqual(controller.tuning.context(binding)['values'],values)
            for path,data in files.items():self.assertEqual(path.read_bytes(),data)

    def test_calibration_continuation_defaults_sdk_without_deleting_old_parameter_file(self):
        from console_server import Controller
        from device_profiles import profile
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,WUJI_OFFICIAL_CLI_DATA=folder,WUJI_FLEET_ID='main'):
            controller=Controller(factory=Mock(),reports=Path(folder)/'reports')
            controller.state['device_profile']=profile('hand2_right');controller._continuation_profile='hand2_right'
            model=Path(folder)/'right_hand.urdf';model.write_bytes(b'<robot name="synthetic"/>')
            proof=dict(source_run_id='a'*32,source_workspace='main',source_profile='Alice',serial='SYNTHETIC-GLOVE',
                side='right',model_path=str(model),model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),model_bytes=model.stat().st_size)
            expected={key:proof[key] for key in ('source_run_id','source_workspace','source_profile','serial','side','model_sha256')}
            controller.calibrated_teleoperation=N(_current_proof=lambda:copy.deepcopy(proof))
            controller.glove._continuation=expected
            binding=dict(generation='hand2',side='right',glove_serial=proof['serial'],hand_serial='',sdk_user='owner')
            controller.retarget.select(binding,controller.state['device_profile'])
            values,files=self.archived_open_profile(controller,binding)
            with patch('studio_component.enforce_session_owner'):
                settings=controller._continuation_model_ready(dict(expected,owner_user_id='owner',imported=True))
            self.assertEqual(settings['solver']['engine'],'sdk');self.assertEqual(settings['solver']['values'],values)
            self.assertEqual(controller.tuning.context(binding)['engine'],'sdk')
            for path,data in files.items():self.assertEqual(path.read_bytes(),data)


if __name__=='__main__':unittest.main()
