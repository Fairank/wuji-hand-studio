"""Owner-context adapters and continuation wiring, no real SDK or devices."""
from contextlib import contextmanager
import copy
import hashlib
import io
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch
import numpy as np  # Keep the real extension cached outside synthetic SDK patches.

from task_control import execute
from task_sessions import context_digest
from glove_bridge import GloveBridge
from glove_protocol import validate
from test_calibration_portability import completed,MODEL


class TaskControlTests(unittest.TestCase):
    def fixture(self):
        state=dict(session_generation=1,device_id='HAND',device_profile={'id':'hand2_right'})
        return N(snapshot=lambda:copy.deepcopy(state),state=state,
            calibration_handover=N(workflow_lock=threading.RLock()),_task_context_dispatch=Mock(return_value={'ok':True}))

    def test_exact_owner_context_dispatch_and_changed_generation_rejected(self):
        c=self.fixture();scope=context_digest(c.state)
        self.assertEqual(execute(c,scope,'pause'),{'ok':True})
        c.state['session_generation']=2
        with self.assertRaisesRegex(ValueError,'session changed'):execute(c,scope,'disconnect')
        self.assertEqual(c._task_context_dispatch.call_count,1)

    def test_only_typed_server_pair_fields_and_operations_are_accepted(self):
        c=self.fixture();scope=context_digest(c.state)
        pair=dict(hand_serial='HAND',profile='hand2_right',sdk_user='owner-1')
        execute(c,scope,'pair_prepare',pair)
        for action,payload in [('glove_follow',None),('disconnect',{'serial':'OTHER'}),
                ('pair_prepare',{**pair,'path':'private'}),('pair_prepare',{**pair,'profile':'unknown'}),
                ('pair_prepare',{**pair,'sdk_user':False}),('pair_prepare',{**pair,'hand_serial':False})]:
            with self.subTest(action=action,payload=payload),self.assertRaises(ValueError):execute(c,scope,action,payload)
        self.assertEqual(c._task_context_dispatch.call_count,1)

    def test_busy_transition_never_dispatches_and_error_releases_workflow_lock(self):
        c=self.fixture();scope=context_digest(c.state)
        entered=threading.Event();release=threading.Event()
        def own():
            with c.calibration_handover.workflow_lock:entered.set();release.wait(2)
        thread=threading.Thread(target=own);thread.start();self.assertTrue(entered.wait(1))
        with self.assertRaisesRegex(ValueError,'switching'):execute(c,scope,'pause')
        release.set();thread.join(2);c._task_context_dispatch.assert_not_called()
        c._task_context_dispatch.side_effect=ValueError('expected fixture failure')
        with self.assertRaises(ValueError):execute(c,scope,'pause')
        c._task_context_dispatch.side_effect=None
        self.assertEqual(execute(c,scope,'disconnect'),{'ok':True})

    def test_exact_continuation_cancel_is_deliverable_while_its_worker_owns_workflow_lock(self):
        c=self.fixture();c.state['calibrated_teleoperation']=dict(busy=True,run_id='a'*32,attempt_id='b'*32,serial='GLOVE')
        scope=context_digest(c.state);entered=threading.Event();release=threading.Event()
        def own():
            with c.calibration_handover.workflow_lock:entered.set();release.wait(2)
        thread=threading.Thread(target=own);thread.start();self.assertTrue(entered.wait(1))
        try:
            self.assertEqual(execute(c,scope,'disconnect'),{'ok':True})
            captured=c._task_context_dispatch.call_args.args[2]['calibrated_teleoperation']
            self.assertEqual((captured['run_id'],captured['attempt_id']),('a'*32,'b'*32))
            with self.assertRaisesRegex(ValueError,'switching'):execute(c,scope,'pause')
            c.state['calibrated_teleoperation']['attempt_id']='c'*32
            with self.assertRaisesRegex(ValueError,'session changed'):execute(c,scope,'disconnect')
            self.assertEqual(c._task_context_dispatch.call_count,1)
        finally:release.set();thread.join(2)

    def test_replaced_continuation_attempt_after_snapshot_is_rejected_by_strict_cancel(self):
        from calibrated_teleoperation import CalibratedTeleoperation
        service=CalibratedTeleoperation(lambda:{},Mock(),Mock(),lambda:{},Mock(),Mock())
        service.state.update(busy=True,run_id='a'*32,attempt_id='b'*32,serial='GLOVE')
        c=self.fixture();c.state['calibrated_teleoperation']=copy.deepcopy(service.state)
        def dispatch(action,payload,captured):
            service.state['attempt_id']='c'*32
            current=captured['calibrated_teleoperation']
            return service.cancel(dict(run_id=current['run_id'],attempt_id=current['attempt_id']))
        c._task_context_dispatch.side_effect=dispatch
        with self.assertRaisesRegex(ValueError,'attempt changed'):execute(c,context_digest(c.state),'disconnect')
        self.assertFalse(service._cancel.is_set());service.stop_session.assert_not_called()


class ControllerContinuationTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        from console_server import Controller
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,WUJI_FLEET_ID='test-workspace',WUJI_OFFICIAL_CLI_DATA=folder):
            root=Path(folder);model=root/'right.urdf';model.write_bytes(MODEL)
            c=Controller(factory=Mock(side_effect=AssertionError('No controller may start')),reports=root/'reports')
            c.state['device_profile']=dict(id='hand2_right',generation='hand2',side='right')
            c.calibration.run=completed(model)['run'];c._continuation_profile='hand2_right'
            c.official_cli.snapshot=Mock(return_value={});c.studio.snapshot=Mock(return_value={})
            c.official_viz.snapshot=Mock(return_value={});c.solver_runtime.snapshot=Mock(return_value={})
            yield N(c=c,root=root,model=model)

    def receipt(self,c):
        proof=c.calibrated_teleoperation._current_proof()
        c.glove._continuation=copy.deepcopy(proof)
        return {**{k:proof[k] for k in ('source_run_id','source_workspace','source_profile','serial','side','model_sha256')},
            'owner_user_id':'owner-1','imported':True}

    def test_real_controller_uses_raw_completed_run_and_saves_exact_binding_without_robot_override(self):
        with self.environment() as e:
            c=e.c;receipt=self.receipt(c);snapshot=c.snapshot()
            self.assertEqual(snapshot['session_generation'],0)
            self.assertTrue(snapshot['calibrated_teleoperation']['can_continue'])
            c.calibration.snapshot=Mock(side_effect=AssertionError('No metadata refresh'))
            result=c._continuation_model_ready(receipt)
            binding=c.retarget.context(c.state['device_profile'])['binding']
            self.assertEqual(binding['glove_serial'],'WG-SYNTHETIC');self.assertEqual(binding['sdk_user'],'owner-1')
            self.assertEqual(result['timeout_ms'],250);self.assertEqual(set(result),{'timeout_ms','retarget','solver'})
            c.factory.assert_not_called();c.calibration.snapshot.assert_not_called()
            self.assertEqual(c.state['view_source'],'glove')
            self.assertNotIn(str(e.model),c.retarget.path.read_text(encoding='utf-8'))
            self.assertFalse((c.retarget.folder/'hand_model_path.json').exists())

    def test_changed_run_model_workspace_or_robot_profile_does_not_write_binding(self):
        for changed in ('run','model','workspace','profile','receipt_sn','controller_sn'):
            with self.subTest(changed=changed),self.environment() as e:
                c=e.c;receipt=self.receipt(c)
                if changed=='run':c.calibration.run['id']='b'*32
                elif changed=='model':e.model.write_bytes(b'changed')
                elif changed=='workspace':receipt['source_workspace']='foreign'
                elif changed=='profile':c.state['device_profile']['id']='hand1_right'
                elif changed=='receipt_sn':receipt['serial']='OTHER'
                else:c.glove._continuation['serial']='OTHER'
                with self.assertRaises(ValueError):c._continuation_model_ready(receipt)
                self.assertFalse(c.retarget.path.exists());c.factory.assert_not_called()

    def test_private_continuation_retains_original_studio_and_cli_owner_gates(self):
        for target in ('studio_component.enforce_session_owner','cli_coordination.enforce'):
            with self.subTest(target=target),self.environment() as e:
                receipt=self.receipt(e.c)
                with patch(target,side_effect=ValueError('foreign owner busy')) as gate:
                    with self.assertRaisesRegex(ValueError,'foreign owner'):e.c._continuation_model_ready(receipt)
                gate.assert_called_once_with({'name':'glove_scan'})
                self.assertFalse(e.c.retarget.path.exists());e.c.factory.assert_not_called()

    def test_public_continuation_uses_server_proof_and_busy_blocks_profile_changes(self):
        with self.environment() as e:
            c=e.c;s=c.calibrated_teleoperation.snapshot();command=dict(name='calibrated_teleoperation_continue',run_id=s['run_id'],expected_revision=s['expected_revision'])
            with patch.object(c.calibrated_teleoperation,'execute',return_value={'busy':True}) as execute:
                result=c.action(command)
            execute.assert_called_once_with({k:command[k] for k in ('run_id','expected_revision')})
            self.assertTrue(result['calibrated_teleoperation']['busy'])
            with c.calibrated_teleoperation.lock:c.calibrated_teleoperation.state['busy']=True
            for name in ('device_profile_select','calibration_profile_switch','calibration_start','glove_scan','retarget_binding_save'):
                with self.subTest(name=name),self.assertRaisesRegex(ValueError,'continuation is in progress'):c.action({'name':name})
            for name in ('task_owner','pair_prepare','task_context_action'):
                with self.assertRaisesRegex(ValueError,'private'):c.action({'name':name})

    def test_pair_adapter_reuses_atomic_binding_guard_and_never_follows(self):
        with self.environment() as e:
            c=e.c;profile=c.state['device_profile']
            binding=dict(generation='hand2',side='right',glove_serial='GLOVE',sdk_user='owner-1',hand_serial='')
            c.retarget.select(binding,profile)
            state=dict(device_profile=profile,glove=dict(connection='receiving',stream={'fresh':True},user={'user_id':'owner-1'},feedback={}))
            with patch.object(c,'action',return_value={'submitted':True}) as action:
                c._task_context_dispatch('pair_prepare',dict(hand_serial='HAND',profile=profile['id'],sdk_user='owner-1'),state)
            self.assertEqual([call.args[0]['name'] for call in action.call_args_list],['retarget_binding_save','glove_prepare'])
            with patch.object(c,'action',side_effect=ValueError('different mapping settings')) as blocked:
                with self.assertRaises(ValueError):c._task_context_dispatch('pair_prepare',dict(hand_serial='HAND',profile=profile['id'],sdk_user='owner-1'),state)
                self.assertEqual(blocked.call_count,1)

    def test_task_pause_and_disconnect_cannot_skip_pending_start_or_unknown_disable(self):
        with self.environment() as e:
            c=e.c;state=dict(glove={'busy':False},hardware={'active':False})
            c.hardware_lease='pending-start'
            with patch.object(c,'action') as action:
                c._task_context_dispatch('pause',None,state)
            self.assertEqual([call.args[0]['name'] for call in action.call_args_list],['program_stop','hardware_stop'])
            c.glove.lease='pending-start'
            for hardware in ({'active':False},{'active':True},{'active':None}):
                current=dict(glove={'busy':True,'hardware':hardware},hardware={'active':False})
                with patch.object(c,'action') as action,self.assertRaises(ValueError):c._task_context_dispatch('disconnect',None,current)
                action.assert_not_called()

    def test_controller_forwards_captured_continuation_attempt_not_a_later_snapshot(self):
        with self.environment() as e:
            c=e.c;state=dict(calibrated_teleoperation=dict(busy=True,run_id='a'*32,attempt_id='b'*32))
            with patch.object(c,'action',return_value={'cancelled':True}) as action, \
                    patch.object(c.calibrated_teleoperation,'snapshot',side_effect=AssertionError('Do not replace captured attempt')):
                self.assertEqual(c._task_context_dispatch('disconnect',None,state),{'cancelled':True})
            action.assert_called_once_with(dict(name='calibrated_teleoperation_cancel',run_id='a'*32,attempt_id='b'*32))

    def test_task_unpair_waits_for_source_preserving_cleanup_then_clears_only_target_binding(self):
        with self.environment() as e:
            c=e.c;profile=c.state['device_profile']
            binding=dict(generation='hand2',side='right',glove_serial='GLOVE',sdk_user='owner-1',hand_serial='HAND')
            c.retarget.select(binding,profile)
            state=dict(device_profile=profile,glove=dict(session_id=27))
            finished=dict(busy=True,connection='receiving',session_id=27,hand_unpaired=True,feedback={})
            with patch.object(c,'action') as action,patch.object(c.glove,'snapshot',return_value=finished):
                self.assertTrue(c._task_context_dispatch('unpair',None,state)['unpaired'])
            action.assert_called_once_with({'name':'glove_unpair'})
            self.assertEqual(c.retarget.context(profile)['binding'],dict(binding,hand_serial=''))
            c.retarget.select(binding,profile)
            with patch.object(c,'action'),patch.object(c.glove,'snapshot',return_value={**finished,'session_id':28}),self.assertRaises(ValueError):
                c._task_context_dispatch('unpair',None,state)
            self.assertEqual(c.retarget.context(profile)['binding'],binding)


class UnpairAndExitTests(unittest.TestCase):
    def bridge(self):
        bridge=GloveBridge(Mock());bridge.thread=N(is_alive=lambda:True);bridge.stdin=io.StringIO();bridge.last_update=time.monotonic()
        bridge.state.update(connection='receiving',hardware={'active':False,'probe_ready':True},feedback={'device_id':'HAND','metrics':{'age_ms':0}},
            stream={'fresh':True,'age_ms':0,'timeout_ms':250},can_prepare=False)
        return bridge

    def test_unpair_is_typed_retains_source_and_blocks_follow_until_confirmed_ack(self):
        g=self.bridge();session=g.session
        self.assertEqual(validate({'name':'glove_unpair'}),{'name':'glove_unpair'})
        with self.assertRaises(ValueError):validate({'name':'glove_unpair','serial':'OTHER'})
        self.assertTrue(g.action({'name':'glove_unpair'})['unpairing'])
        self.assertTrue(g.snapshot()['hand_unpairing']);self.assertEqual(g.session,session)
        with self.assertRaisesRegex(ValueError,'cleanup'):g.action({'name':'glove_follow','workspace_clear':True})
        g.receive({'type':'glove_state','state':dict(g.state,hand_unpaired=False,hand_unpairing=False)})
        self.assertTrue(g.snapshot()['hand_unpairing'])
        g.receive({'type':'glove_state','state':dict(g.state,hand_unpaired=True,hand_unpairing=False,feedback={},hardware={})})
        self.assertFalse(g.snapshot()['hand_unpairing']);self.assertEqual(g.state['connection'],'receiving')
        self.assertEqual(g.session,session)

    def test_pending_start_or_unknown_stop_cannot_unpair(self):
        for key,value in [('active',True),('active',None),('lease','pending')]:
            with self.subTest(key=key,value=value):
                g=self.bridge()
                if key=='lease':g.lease=value
                else:g.state['hardware'][key]=value
                with self.assertRaises(ValueError):g.action({'name':'glove_unpair'})
                self.assertEqual(g.stdin.getvalue(),'')

    def test_worker_waits_for_exact_hand_cleanup_ack_and_retains_glove_user(self):
        from glove_agent import worker_glove
        for confirmed in (True,False):
            with self.subTest(confirmed=confirmed):
                requests=queue.Queue();calls=[];user={'user_id':'owner-1'};seq=[0];phase=[0];read=[False]
                def frame():
                    read[0]=not read[0]
                    if not read[0]:return None
                    seq[0]+=1
                    return N(header=N(seq=seq[0],timestamp_us=seq[0]),joints=[N(pose=N(position=[.01,.02,.03])) for _ in range(21)])
                glove=N(serial_number='GLOVE',hand_side=lambda:N(get=lambda:'right'),disconnect=lambda:calls.append('glove-disconnected'),
                    hand_skeleton=lambda:N(subscribe=lambda:N(recv=frame,close=lambda:None)))
                manager=N(current_user=lambda:user,list_users=lambda:[user],switch_user=lambda _:None,
                    scan=lambda:[N(sn='GLOVE',device_type='glove',address='fixture')],connect=lambda **_:glove)
                sdk=N(SdkManager=N(instance=lambda:manager),DeviceType=N(WujiGlove='glove'),
                    HandModel=N(WujiHand2='hand2',WujiHand='hand1'),Handedness=N(Left='left',Right='right'),
                    RetargetSession=N(for_hand=lambda *a,**kw:N(step=lambda _: [0.]*20)))
                def hand_worker(address,serial,selected,hand_requests,events,*args):
                    events.put({'type':'identity','device_id':'HAND','profile':'hand2_right'})
                    events.put({'type':'state','connection':'connected','device_id':'HAND','hardware':{'active':False},'latest':None})
                    command=hand_requests.get(timeout=2);calls.append(command['name'])
                    events.put({'type':'closed','hardware':{'active':False}})
                    events.put({'type':'hand_session_closed','cleanup_confirmed':confirmed})
                class Events(queue.Queue):
                    def put(self,event):
                        super().put(event)
                        if event['type']!='glove_state':return
                        state=event['state']
                        if phase[0]==0 and state['stream']['fresh']:
                            phase[0]=1;requests.put({'name':'glove_prepare','serial':'HAND','address':''})
                        elif phase[0]==1 and state['feedback'].get('device_id')=='HAND':
                            phase[0]=2;requests.put({'name':'glove_unpair'})
                        elif phase[0]==2 and (state.get('hand_unpaired') or 'cleanup unconfirmed' in state.get('hand_error','')):
                            calls.append(('preview-retained',state['connection'],state['user']['user_id'],state['glove_serial']))
                            phase[0]=3;requests.put({'name':'disconnect'})
                events=Events();requests.put({'name':'glove_open','serial':'GLOVE','user_id':'owner-1','timeout_ms':250})
                with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict(os.environ,WUJI_HAND_PROFILE='hand2_right'), \
                        patch('glove_agent.paired_hand_worker',side_effect=hand_worker),patch('glove_agent.sys.platform','win32'):
                    worker_glove(requests,events)
                states=[row['state'] for row in list(events.queue) if row['type']=='glove_state']
                self.assertEqual(phase[0],3);self.assertIn('disconnect',calls)
                self.assertIn(('preview-retained','receiving','owner-1','GLOVE'),calls)
                if confirmed:
                    unpaired=next(state for state in states if state.get('hand_unpaired'))
                    self.assertIsNone(unpaired['feedback']['device_id']);self.assertEqual(unpaired['hardware'],{})
                else:self.assertFalse(any(state.get('hand_unpaired') for state in states))

    def test_busy_continuation_requires_close_confirmation_and_blocks_all_code_mutations(self):
        from desktop_lifecycle import needs_confirmation,close_sessions
        from desktop_tools import idle,dispatch
        state=dict(connection='disconnected',hardware={'active':False},calibrated_teleoperation={'busy':True},glove={})
        self.assertTrue(needs_confirmation(state));self.assertFalse(idle(state,{}))
        c=N(snapshot=Mock(return_value=state),doctor=N(snapshot=lambda:{}),calibrated_teleoperation=N(cancel_and_wait=Mock(return_value=False)))
        self.assertFalse(close_sessions(c)['ok']);c.calibrated_teleoperation.cancel_and_wait.assert_called_once()
        host=N(server=N(controller=N(snapshot=lambda:state,doctor=N(snapshot=lambda:{}))))
        for operation in ('reload_ui','close_idle','prepare_update','preview'):
            payload=dict(operation=operation)
            if operation=='preview':payload.update(action='open',speed=1.)
            with self.subTest(operation=operation),self.assertRaises(ValueError):dispatch(host,payload)


if __name__=='__main__':unittest.main()
