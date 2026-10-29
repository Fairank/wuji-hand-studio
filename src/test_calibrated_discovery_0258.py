"""Explicit calibrated SN connection without a premature broadcast-list gate."""
from contextlib import nullcontext
import copy
import os
from pathlib import Path
import queue
import sys
import tempfile
import unittest
from types import SimpleNamespace as N
from unittest.mock import Mock, patch

import numpy as np  # Keep the real extension outside the synthetic SDK patch.
from calibrated_teleoperation import CalibratedTeleoperation
from glove_agent import worker_glove
from glove_bridge import GloveBridge
from test_calibration_portability import MODEL, bundle_context, completed

TEMP = Path(__file__).resolve().parents[2]/'update-flow-0258/backend-audit/test-temp'
TEMP.mkdir(parents=True,exist_ok=True)


class ExplicitCalibratedDiscoveryTests(unittest.TestCase):
    def run_worker(self, *, continuation=True, fault=None, wrong_command=None, cancel=False, route=False):
        with tempfile.TemporaryDirectory(dir=TEMP) as temporary:
            root=Path(temporary);context=bundle_context(root/'source')
            if route:
                context['glove_route']=dict(source='official_discovery',serial=context['serial'],address='udp/192.0.2.10:50001')
                if fault=='route_serial':context['glove_route']['serial']='other-SN'
                if fault=='route_host':context['glove_route']['address']='localhost:50001'
            model=root/'right_hand.urdf';model.write_bytes(MODEL)
            wrong=root/'old.urdf';wrong.write_bytes(MODEL+b'old')
            prior=dict(user_id='',display_name='Default',is_default=True)
            wearer=dict(user_id='owner-1',display_name='Alice',is_default=False)
            current=[copy.deepcopy(prior)];calls=[];requests=queue.Queue()
            class Glove:
                def __init__(self):
                    self.serial_number='wrong-SN' if fault in ('serial','serial_close_false','serial_close_raise') else context['serial']
                    self.close_calls=0
                def hand_side(self):return N(get=lambda:'left' if fault=='side' else 'right')
                def hand_model_path(self):return N(get=lambda:str(wrong) if fault=='model' else '')
                def hand_skeleton(self):
                    def subscribe():calls.append(('subscribe',));return N(recv=lambda:None,close=lambda:True)
                    return N(subscribe=subscribe)
                def disconnect(self):
                    self.close_calls+=1;calls.append(('close',))
                    if self.close_calls==1:
                        if fault=='serial_close_false':return False
                        if fault=='serial_close_raise':raise OSError('synthetic rejected handle close failed')
                    return True
            def switch(user):
                current[0]=copy.deepcopy(wearer if user=='owner-1' else prior)
                calls.append(('user',user))
            def connect(**values):
                calls.append(('connect',values))
                if fault=='timeout':raise TimeoutError('synthetic exact-SN resolution timeout')
                if fault=='type':return N(serial_number=context['serial'],disconnect=lambda:calls.append(('close',)))
                return Glove()
            manager=N(current_user=lambda:copy.deepcopy(current[0]),list_users=lambda:[prior,wearer],
                      switch_user=switch,scan=Mock(return_value=[]),connect=connect)
            sdk=N(SdkManager=N(instance=lambda:manager),DeviceType=N(WujiGlove='glove'),WujiGlove=Glove,
                  HandModel=N(WujiHand2='hand2',WujiHand='hand1'),Handedness=N(Left='left',Right='right'),
                  RetargetSession=N(for_hand=lambda *a,**kw:N(step=lambda kp:[0.]*20)))
            class Events(queue.Queue):
                def put(self,event):
                    super().put(event)
                    if event['type']=='calibrated_ready':
                        requests.put({'name':'glove_disconnect'} if cancel else dict(name='glove_open',
                            serial='other-SN' if wrong_command=='serial' else context['serial'],
                            user_id='other-owner' if wrong_command=='user' else 'owner-1',timeout_ms=250))
                    if event['type']=='glove_state' and event['state']['connection']=='receiving':
                        requests.put(dict(name='disconnect'))
                    if event['type']=='glove_closed' and event['session_cleanup']['retryable']:
                        calls.append(('explicit_cleanup_retry',))
                        # A rejected handle has already had one close attempt.
                        # No second attempt is allowed until this explicit input.
                        assert sum(c[0]=='close' for c in calls)==1
                        requests.put(dict(name='glove_open',serial='must-not-open',user_id='owner-1',timeout_ms=250))
                        requests.put(dict(name='glove_disconnect'))
            events=Events()
            receipt=dict(context,owner_user_id='owner-1',model_path=str(model),imported=True)
            if not continuation:
                requests.put(dict(name='glove_open',serial=context['serial'],user_id='owner-1',timeout_ms=250))
                requests.put(dict(name='disconnect'))
            with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict(os.environ,WUJI_HAND_PROFILE='hand2_right'), \
                    patch('calibration_portability.prepare_controller_model',return_value=receipt), \
                    patch('sdk_session.ProfileLease',return_value=nullcontext()):
                worker_glove(requests,events,context if continuation else None)
            self.assertEqual(current[0],prior)
            self.assertEqual(model.read_bytes(),MODEL)
            output=list(events.queue)
            return N(context=context,calls=calls,events=output,scan=manager.scan,
                     errors=[e['message'] for e in output if e['type']=='glove_error'])

    def test_explicit_continuation_connects_once_by_sn_without_prerequisite_scan(self):
        result=self.run_worker()
        result.scan.assert_not_called()
        self.assertEqual([x for x in result.calls if x[0]=='connect'],
            [('connect',dict(sn=result.context['serial'],device_name='studio_glove'))])
        self.assertIn(('subscribe',),result.calls);self.assertEqual(result.errors,[])
        self.assertTrue(any(e['type']=='glove_closed' for e in result.events))
        self.assertFalse(any('hardware_start' in str(call) for call in result.calls))

    def test_normal_empty_discovery_still_requires_a_selected_discovered_glove(self):
        result=self.run_worker(continuation=False)
        result.scan.assert_called_once()
        self.assertFalse(any(c[0]=='connect' for c in result.calls))
        self.assertTrue(any('请先扫描并选择手套' in e.get('message','') for e in result.events))

    def test_official_observed_route_uses_one_address_connect_without_broadcast_or_sn_fallback(self):
        result=self.run_worker(route=True)
        result.scan.assert_not_called()
        self.assertEqual([x for x in result.calls if x[0]=='connect'],
            [('connect',dict(address='192.0.2.10:50001',device_name='studio_glove'))])
        self.assertIn(('subscribe',),result.calls);self.assertEqual(result.errors,[])

    def test_route_for_other_serial_or_unverified_host_never_opens(self):
        for fault in ('route_serial','route_host'):
            with self.subTest(fault=fault):
                result=self.run_worker(route=True,fault=fault)
                self.assertFalse(any(c[0]=='connect' for c in result.calls))
                result.scan.assert_not_called();self.assertTrue(result.errors)

    def test_changed_identity_at_observed_route_is_closed_and_never_falls_back_to_sn(self):
        for fault in ('serial','type','side','model','timeout'):
            with self.subTest(fault=fault):
                result=self.run_worker(route=True,fault=fault)
                self.assertEqual(sum(c[0]=='connect' for c in result.calls),1)
                self.assertEqual(next(c[1] for c in result.calls if c[0]=='connect'),
                    dict(address='192.0.2.10:50001',device_name='studio_glove'))
                self.assertFalse(any(c[0]=='subscribe' for c in result.calls))
                self.assertEqual(sum(c[0]=='close' for c in result.calls),0 if fault=='timeout' else 1)
                self.assertTrue(result.errors)

    def test_wrong_serial_handle_with_failed_close_is_retained_until_explicit_cleanup_retry(self):
        for fault in ('serial_close_false','serial_close_raise'):
            with self.subTest(fault=fault):
                result=self.run_worker(route=True,fault=fault)
                self.assertEqual(sum(c[0]=='connect' for c in result.calls),1)
                self.assertEqual(sum(c[0]=='close' for c in result.calls),2)
                self.assertEqual(sum(c[0]=='explicit_cleanup_retry' for c in result.calls),1)
                self.assertFalse(any(c[0]=='subscribe' for c in result.calls))
                self.assertIn('Glove identity changed',result.errors[0])
                cleanups=[e['session_cleanup'] for e in result.events if e['type']=='glove_closed']
                self.assertEqual(len(cleanups),2)
                self.assertTrue(cleanups[0]['glove_was_open']);self.assertTrue(cleanups[0]['retryable'])
                self.assertFalse(cleanups[0]['glove_disconnected']);self.assertFalse(cleanups[0]['user_restored'])
                self.assertFalse(cleanups[1]['retryable']);self.assertTrue(cleanups[1]['glove_disconnected'])
                self.assertTrue(cleanups[1]['user_restored'])

    def test_exact_connection_failure_keeps_reason_without_retry_and_next_explicit_attempt_recovers(self):
        failed=self.run_worker(fault='timeout')
        self.assertEqual(sum(c[0]=='connect' for c in failed.calls),1)
        self.assertFalse(any(c[0]=='subscribe' for c in failed.calls))
        self.assertEqual(len(failed.errors),1)
        self.assertIn('确认电源和控制端网络',failed.errors[0])
        self.assertIn('synthetic exact-SN resolution timeout',failed.errors[0])
        recovered=self.run_worker()
        self.assertEqual(sum(c[0]=='connect' for c in recovered.calls),1)
        self.assertEqual(recovered.errors,[])

    def test_actual_serial_type_side_and_model_still_precede_any_subscription(self):
        for fault in ('serial','type','side','model'):
            with self.subTest(fault=fault):
                result=self.run_worker(fault=fault)
                self.assertEqual(sum(c[0]=='connect' for c in result.calls),1)
                self.assertFalse(any(c[0]=='subscribe' for c in result.calls))
                self.assertEqual(sum(c[0]=='close' for c in result.calls),1)
                self.assertTrue(result.errors)

    def test_different_requested_serial_or_user_is_not_authorized_by_continuation(self):
        for wrong in ('serial','user'):
            with self.subTest(wrong=wrong):
                result=self.run_worker(wrong_command=wrong)
                self.assertFalse(any(c[0]=='connect' for c in result.calls))
                self.assertTrue(result.errors)

    def test_cancel_after_model_readiness_never_opens_the_glove(self):
        result=self.run_worker(cancel=True)
        self.assertFalse(any(c[0]=='connect' for c in result.calls))
        self.assertEqual(result.errors,[])


class ContinuationFreshnessTests(unittest.TestCase):
    def test_cleanup_keeps_bounded_original_error_once_and_new_session_discards_it(self):
        bridge=GloveBridge(Mock());bridge.session=7
        bridge.state.update(profile='hand2_right',glove_serial='WG_SYNTHETIC',user={'user_id':'owner-1'})
        original='Glove identity changed: '+('x'*1200)
        bridge.receive({'type':'glove_error','message':original},session=7)
        ack=dict(version=1,glove_serial='WG_SYNTHETIC',sdk_user_id='owner-1',profile='hand2_right',
                 glove_was_open=True,subscription_closed=True,glove_disconnected=False,solver_cleanup='not_open',
                 user_restored=False,ownership_released=True,profile_lease_released=True,
                 selection_lease_released=True,hand_released=True,retryable=True)
        event=dict(type='glove_closed',hardware={},session_cleanup=ack)
        bridge.receive(event,session=7);first=bridge._cleanup_error
        self.assertIn('Glove identity changed',first);self.assertLessEqual(len(first),900)
        bridge.receive(event,session=7);self.assertEqual(bridge._cleanup_error,first)
        ack.update(glove_disconnected=True,user_restored=True,retryable=False)
        bridge.receive(event,session=7);self.assertIsNone(bridge._cleanup_error)
        with patch('glove_bridge.threading.Thread',return_value=N(start=lambda:None)):
            bridge._begin_session()
        self.assertIsNone(bridge._session_error)
        self.assertNotIn('identity changed',bridge.state['message'])

    def test_preview_requires_new_fresh_sdk_result_and_loses_success_when_stale(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as temporary:
            model=Path(temporary)/'model.urdf';model.write_bytes(MODEL)
            saved=completed(model);glove={'session_id':1,'busy':True,'connection':'connecting'}
            service=CalibratedTeleoperation(lambda:saved,lambda bundle:1,Mock(),lambda:copy.deepcopy(glove),
                lambda:None,lambda proof,**kw:proof,gate_factory=nullcontext)
            offer=service.snapshot()
            service.execute({key:offer[key] for key in ('run_id','expected_revision')},synchronous=True)
            proof=service._current_proof()
            glove.update(connection='receiving',glove_serial=proof['serial'],model_provenance=proof)
            for stream in ({},{'fresh':False},{'fresh':True,'solver':{'engine':'sdk','applied':False}},
                           {'fresh':True,'solver':{'engine':'official_open','applied':True}}):
                glove['stream']=stream
                current=service.snapshot()
                self.assertFalse(current['connected']);self.assertEqual(current['phase'],'connecting')
                self.assertFalse(current['can_continue'])
            glove['stream']={'fresh':True,'solver':{'engine':'sdk','applied':True}}
            self.assertEqual(service.snapshot()['phase'],'preview');self.assertTrue(service.snapshot()['connected'])
            glove['stream']['fresh']=False
            self.assertFalse(service.snapshot()['connected'])


if __name__=='__main__':unittest.main()
