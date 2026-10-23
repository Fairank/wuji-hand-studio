"""Pending follow and exact-session cleanup; fake streams, no SDK or devices."""
import copy
import io
import json
import queue
import sys
import time
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock, patch
import numpy as np  # Cache the extension before temporary synthetic SDK modules.

from glove_bridge import GloveBridge
from glove_agent import worker_glove
from test_sdk_lifecycle import proof_fixture,release_fixture


def receiving(active=False,confirmed=True,device='SYNTHETIC-HAND'):
    return dict(connection='receiving',hardware=dict(active=active,stop_confirmed=confirmed,probe_ready=True),
        feedback=dict(device_id=device,metrics=dict(age_ms=0)),
        stream=dict(fresh=True,age_ms=0,timeout_ms=250,solver=dict(engine='sdk',applied=True)))


class GloveTransportLifecycleTests(unittest.TestCase):
    def bridge(self,stream=None):
        journal=Mock();journal.new_session.return_value=17
        bridge=GloveBridge(Mock(),journal);bridge.thread=N(is_alive=lambda:True)
        bridge.stdin=stream if stream is not None else io.StringIO()
        bridge.state.update(receiving());bridge.last_update=time.monotonic()
        bridge._sdk_proof=proof_fixture();bridge.release_resolver=release_fixture;bridge.verify_follow_sdk()
        return bridge,journal

    def failed_follow(self,stage='flush'):
        class FailedStream(io.StringIO):
            def write(self,value):
                if stage=='write':raise OSError('synthetic follow write failure')
                return super().write(value)
            def flush(self):raise OSError('synthetic follow flush failure')
        bridge,journal=self.bridge(FailedStream())
        with self.assertRaisesRegex(OSError,'synthetic follow'):
            bridge.action(dict(name='glove_follow',workspace_clear=True))
        return bridge,journal

    def assert_unknown(self,bridge):
        state=bridge.snapshot()
        self.assertIsNone(state['hardware'].get('active'))
        self.assertIs(state['hardware'].get('stop_confirmed'),False)
        self.assertIs(state.get('follow_unconfirmed'),True)
        with self.assertRaises(ValueError):bridge.action(dict(name='glove_follow',workspace_clear=True))

    def test_write_and_flush_failures_never_reuse_the_previous_stop_confirmation(self):
        for stage in ('write','flush'):
            with self.subTest(stage=stage):
                bridge,journal=self.failed_follow(stage)
                self.assert_unknown(bridge)
                observations=[call.args for call in journal.observe.call_args_list]
                self.assertTrue(any(source=='glove' and device=='SYNTHETIC-HAND' and session==17
                    and hardware.get('active') is None and hardware.get('stop_confirmed') is False
                    for source,device,hardware,session in observations))

    def run_transport(self,follow=False,observed_stop=False):
        bridge,journal=self.bridge();sent=io.StringIO()
        def lines():
            yield 'WUJI_JSON:'+json.dumps(dict(type='ready',teleop_version=1,sdk_proof=proof_fixture()))
            yield 'WUJI_JSON:'+json.dumps(dict(type='sdk_ready',sdk_proof=proof_fixture()))
            yield 'WUJI_JSON:'+json.dumps(dict(type='glove_state',state=receiving()))
            if follow:bridge.verify_follow_sdk();bridge.action(dict(name='glove_follow',workspace_clear=True))
            if observed_stop:
                yield 'WUJI_JSON:'+json.dumps(dict(type='glove_state',state=receiving(True,False)))
                yield 'WUJI_JSON:'+json.dumps(dict(type='glove_state',state=receiving(False,True)))
        client=N(agent_command='synthetic only',exec_command=lambda *args,**kwargs:(sent,lines(),io.StringIO()),close=Mock())
        bridge.factory=Mock(return_value=client);bridge.release_resolver=release_fixture;bridge.run()
        return bridge,journal

    def test_transport_ending_before_active_ack_keeps_exact_pending_follow_unconfirmed(self):
        bridge,_=self.run_transport(follow=True)
        self.assert_unknown(bridge)

    def test_read_only_or_observed_confirmed_stop_does_not_become_unknown_on_eof(self):
        for follow,observed_stop in ((False,False),(True,True)):
            with self.subTest(follow=follow):
                bridge,_=self.run_transport(follow,observed_stop)
                state=bridge.snapshot()
                self.assertIs(state['hardware']['active'],False)
                self.assertIs(state['hardware']['stop_confirmed'],True)
                self.assertFalse(state.get('follow_unconfirmed'))

    def test_late_ordinary_inactive_status_cannot_clear_a_failed_start(self):
        bridge,_=self.failed_follow()
        bridge.receive(dict(type='glove_state',state=receiving(False,True)))
        self.assert_unknown(bridge)

    def test_only_exact_terminal_hand_cleanup_can_clear_unknown_follow(self):
        for changed in ({'device_id':'OTHER-HAND'},{'hand_cleanup_confirmed':False},
                        {'hardware':dict(active=False,stop_confirmed=False)},{}):
            with self.subTest(changed=changed):
                bridge,_=self.failed_follow()
                event=dict(type='glove_closed',device_id='SYNTHETIC-HAND',
                    hand_cleanup_confirmed=True,hardware=dict(active=False,stop_confirmed=True))
                event.update(changed);bridge.receive(event)
                state=bridge.snapshot()
                if changed:
                    self.assertIsNone(state['hardware']['active']);self.assertTrue(state['follow_unconfirmed'])
                else:
                    self.assertIs(state['hardware']['active'],False);self.assertFalse(state['follow_unconfirmed'])
                    self.assertEqual(state['connection'],'disconnected')

    def test_no_terminal_identity_proof_cannot_reuse_an_old_stop_ack(self):
        bridge,_=self.failed_follow()
        bridge.receive(dict(type='glove_closed',hardware=dict(active=False,stop_confirmed=True)))
        state=bridge.snapshot()
        self.assertIsNone(state['hardware']['active']);self.assertTrue(state['follow_unconfirmed'])

    def test_late_prior_transport_event_cannot_mutate_a_replacement_session(self):
        bridge,journal=self.failed_follow();old=bridge.session
        bridge.thread=None;journal.new_session.return_value=18
        with patch('glove_bridge.threading.Thread',return_value=N(start=Mock(),is_alive=lambda:True)):
            bridge.action(dict(name='glove_scan'))
        before=copy.deepcopy(bridge.state)
        bridge.receive(dict(type='glove_state',state=receiving(True,False)),session=old)
        self.assertEqual(bridge.state,before);self.assertEqual(bridge.session,18)
        self.assertFalse(bridge.snapshot().get('follow_unconfirmed'))

    def test_pending_start_timeout_is_not_an_acknowledged_stop(self):
        bridge,_=self.bridge();bridge.action(dict(name='glove_follow',workspace_clear=True))
        bridge.requested-=6
        bridge.receive(dict(type='glove_state',state=receiving(False,True)))
        self.assert_unknown(bridge)

    def test_later_exact_disable_confirmation_clears_only_the_same_device_and_epoch(self):
        bridge,_=self.failed_follow();bridge.thread=None
        bridge.confirm_termination('OTHER-HAND',18);self.assertTrue(bridge.snapshot()['follow_unconfirmed'])
        bridge.confirm_termination('SYNTHETIC-HAND',16);self.assertTrue(bridge.snapshot()['follow_unconfirmed'])
        bridge.confirm_termination('SYNTHETIC-HAND',18)
        self.assertFalse(bridge.snapshot()['follow_unconfirmed'])
        self.assertIs(bridge.snapshot()['hardware']['active'],False)
        self.assertIs(bridge.snapshot()['hardware']['stop_confirmed'],True)

    def test_exact_terminal_cleanup_resolves_a_pending_request_without_seen_active(self):
        bridge,_=self.bridge();bridge.action(dict(name='glove_follow',workspace_clear=True))
        bridge.receive(dict(type='glove_closed',device_id='SYNTHETIC-HAND',hand_cleanup_confirmed=True,
            hardware=dict(active=False,stop_confirmed=True)),session=17)
        state=bridge.snapshot()
        self.assertFalse(state['follow_unconfirmed']);self.assertFalse(state['follow_pending'])
        self.assertIs(state['hardware']['active'],False);self.assertIsNone(bridge.lease)

    def test_different_target_status_cannot_acknowledge_the_original_pending_follow(self):
        bridge,journal=self.bridge();bridge.action(dict(name='glove_follow',workspace_clear=True))
        bridge.receive(dict(type='glove_state',state=receiving(True,False,'OTHER-HAND')))
        state=bridge.snapshot();self.assertTrue(state['follow_unconfirmed']);self.assertIsNone(state['hardware']['active'])
        self.assertEqual(journal.observe.call_args.args[1],'SYNTHETIC-HAND')
        bridge.receive(dict(type='glove_closed',device_id='OTHER-HAND',hand_cleanup_confirmed=True,
            hardware=dict(active=False,stop_confirmed=True)))
        self.assertTrue(bridge.snapshot()['follow_unconfirmed'])

    def rejection(self,bridge,lease=None,device='SYNTHETIC-HAND'):
        return dict(type='glove_follow_rejected',lease=lease or bridge.lease,
            device_id=device,message='synthetic source expired before hand dispatch')

    def test_exact_pre_dispatch_rejection_releases_only_pending_and_preserves_confirmation(self):
        bridge,_=self.bridge();bridge.action(dict(name='glove_follow',workspace_clear=True))
        hardware=copy.deepcopy(bridge.state['hardware'])
        bridge.receive(self.rejection(bridge),session=17)
        self.assertIsNone(bridge.lease);self.assertFalse(bridge.snapshot()['follow_pending'])
        self.assertFalse(bridge.snapshot()['follow_unconfirmed']);self.assertEqual(bridge.state['hardware'],hardware)

    def test_rejection_without_epoch_or_with_old_epoch_nonce_or_another_hand_cannot_release(self):
        for change in ('missing_epoch','old_epoch','old_nonce','another_hand'):
            with self.subTest(change=change):
                bridge,_=self.bridge();bridge.action(dict(name='glove_follow',workspace_clear=True))
                lease=bridge.lease;event=self.rejection(bridge);epoch=17
                if change=='missing_epoch':epoch=None
                elif change=='old_epoch':epoch=16
                elif change=='old_nonce':event['lease']='old-request'
                else:event['device_id']='OTHER-HAND'
                bridge.receive(event,session=epoch)
                self.assertEqual(bridge.lease,lease);self.assertTrue(bridge.snapshot()['follow_pending'])

    def test_delayed_rejection_for_previous_request_cannot_release_a_new_request(self):
        bridge,_=self.bridge();bridge.action(dict(name='glove_follow',workspace_clear=True))
        old=self.rejection(bridge);bridge.receive(old,session=17)
        bridge.verify_follow_sdk()
        bridge.action(dict(name='glove_follow',workspace_clear=True));new=bridge.lease
        self.assertNotEqual(new,old['lease']);bridge.receive(old,session=17)
        self.assertEqual(bridge.lease,new)

    def test_rejection_after_uncertain_io_active_or_unknown_stop_cannot_recover_confirmation(self):
        for boundary in ('write','flush','eof'):
            with self.subTest(boundary=boundary),patch('glove_bridge.secrets.token_hex',return_value='e'*32):
                bridge,_=self.run_transport(follow=True) if boundary=='eof' else self.failed_follow(boundary)
                hardware=copy.deepcopy(bridge.state['hardware'])
                bridge.receive(self.rejection(bridge,lease='e'*32),session=17)
                self.assertEqual(bridge.state['hardware'],hardware);self.assertTrue(bridge.snapshot()['follow_unconfirmed'])
        for active in (True,None):
            with self.subTest(active=active):
                bridge,_=self.bridge();bridge.action(dict(name='glove_follow',workspace_clear=True))
                lease=bridge.lease;bridge.receive(dict(type='glove_state',state=receiving(active,False)),session=17)
                hardware=copy.deepcopy(bridge.state['hardware'])
                bridge.receive(self.rejection(bridge),session=17)
                self.assertEqual(bridge.lease,lease);self.assertEqual(bridge.state['hardware'],hardware)


class GloveWorkerLifecycleTests(unittest.TestCase):
    def sdk(self,*,wrong_selection=False):
        owner=dict(user_id='owner');prior=dict(user_id='prior');current=[prior if wrong_selection else owner]
        calls=[]
        def switch(user):
            calls.append(('switch',user))
            if not wrong_selection:current[0]=owner if user=='owner' else prior
        glove=N(serial_number='SYNTHETIC-GLOVE',hand_side=lambda:N(get=lambda:'right'),
            hand_skeleton=lambda:N(subscribe=lambda:N(recv=lambda:None,close=lambda:None)),
            disconnect=lambda:calls.append(('disconnect-glove',)))
        def connect(**kwargs):calls.append(('connect',current[0]['user_id']));return glove
        manager=N(current_user=lambda:current[0],list_users=lambda:[prior,owner],switch_user=switch,
            scan=lambda:[N(sn='SYNTHETIC-GLOVE',device_type='glove',address='synthetic')],connect=connect)
        sdk=N(SdkManager=N(instance=lambda:manager),DeviceType=N(WujiGlove='glove'),
            HandModel=N(WujiHand2='hand2',WujiHand='hand1'),Handedness=N(Left='left',Right='right'),
            RetargetSession=N(for_hand=lambda *args,**kwargs:N(step=lambda _: [0.]*20)))
        return sdk,calls

    def test_unconfirmed_sdk_user_switch_never_opens_under_an_unselected_profile(self):
        sdk,calls=self.sdk(wrong_selection=True);commands=queue.Queue();events=queue.Queue()
        commands.put(dict(name='glove_open',serial='SYNTHETIC-GLOVE',user_id='owner',timeout_ms=250))
        commands.put(dict(name='glove_disconnect'))
        with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict('os.environ',WUJI_HAND_PROFILE='hand2_right'), \
                patch('glove_agent.sys.platform','win32'):
            worker_glove(commands,events)
        self.assertFalse(any(call[0]=='connect' for call in calls))
        self.assertTrue(any(event['type']=='glove_notice' and 'user' in event['message'].lower()
            for event in list(events.queue)))

    def test_worker_terminal_proof_preserves_exact_hand_identity_and_real_cleanup_outcome(self):
        for confirmed in (True,False):
            with self.subTest(confirmed=confirmed):
                sdk,calls=self.sdk();commands=queue.Queue();phase=[0];deadline=time.monotonic()+3
                class Source:
                    def __init__(self,*args):pass
                    def invalidate(self,message):pass
                    def target(self,now):return [0.]*20
                    def snapshot(self,now):return dict(fresh=True,q=[0.]*20,seq=1,age_ms=0,timeout_ms=250)
                def hand_worker(address,serial,selected,requests,events,*args):
                    events.put(dict(type='identity',device_id=serial,profile=selected['id']))
                    events.put(dict(type='state',device_id=serial,hardware=dict(active=False,stop_confirmed=True),latest=None))
                    self.assertEqual(requests.get(timeout=2)['name'],'disconnect')
                    events.put(dict(type='closed',hardware=dict(active=False,stop_confirmed=True)))
                    events.put(dict(type='hand_session_closed',cleanup_confirmed=confirmed))
                class Events(queue.Queue):
                    def put(self,event):
                        super().put(event)
                        if event['type']!='glove_state':return
                        state=event['state']
                        if time.monotonic()>deadline:commands.put(dict(name='glove_disconnect'));return
                        if phase[0]==0 and state['stream']['fresh']:
                            phase[0]=1;commands.put(dict(name='glove_prepare',serial='SYNTHETIC-HAND',address=''))
                        elif phase[0]==1 and state['feedback'].get('device_id')=='SYNTHETIC-HAND':
                            phase[0]=2;commands.put(dict(name='glove_disconnect'))
                events=Events();commands.put(dict(name='glove_open',serial='SYNTHETIC-GLOVE',user_id='owner',timeout_ms=250))
                with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict('os.environ',WUJI_HAND_PROFILE='hand2_right'), \
                        patch('glove_agent.sys.platform','win32'),patch('glove_agent.GloveSource',Source), \
                        patch('glove_agent.paired_hand_worker',side_effect=hand_worker):
                    worker_glove(commands,events)
                self.assertEqual(phase[0],2,list(events.queue))
                final=list(events.queue)[-1]
                self.assertEqual(final['type'],'glove_closed');self.assertEqual(final['device_id'],'SYNTHETIC-HAND')
                self.assertIs(final['hand_cleanup_confirmed'],confirmed)
                self.assertIn(('disconnect-glove',),calls)

    def test_worker_only_emits_exact_rejection_before_hand_dispatch(self):
        for reject_before,queue_error in ((True,False),(False,False),(False,True)):
            with self.subTest(reject_before=reject_before,queue_error=queue_error):
                sdk,_=self.sdk();commands=queue.Queue();phase=[0];hand_commands=[];deadline=time.monotonic()+3
                class HandQueue(queue.Queue):
                    def put(self,item,*args,**kwargs):
                        super().put(item,*args,**kwargs)
                        if queue_error and item.get('name')=='hardware_start':
                            raise OSError('synthetic hand queue failure after publication')
                class Source:
                    def __init__(self,*args):self.target_calls=0
                    def invalidate(self,message):pass
                    def target(self,now):
                        self.target_calls+=1
                        if reject_before and self.target_calls>=2:raise ValueError('synthetic source expired before dispatch')
                        return [0.]*20
                    def snapshot(self,now):return dict(fresh=True,q=[0.]*20,seq=1,age_ms=0,timeout_ms=250)
                def hand_worker(address,serial,selected,requests,events,*args):
                    events.put(dict(type='identity',device_id=serial,profile=selected['id']))
                    events.put(dict(type='state',device_id=serial,hardware=dict(active=False,stop_confirmed=True),latest=None))
                    while True:
                        command=requests.get(timeout=2);hand_commands.append(command['name'])
                        if command['name']=='disconnect':break
                        events.put(dict(type='notice',message='synthetic driver refused after dispatch'))
                        commands.put(dict(name='glove_disconnect'))
                    events.put(dict(type='closed',hardware=dict(active=False,stop_confirmed=True)))
                    events.put(dict(type='hand_session_closed',cleanup_confirmed=True))
                class Events(queue.Queue):
                    def put(self,event):
                        super().put(event)
                        if event['type']=='glove_follow_rejected':
                            phase[0]=3;commands.put(dict(name='glove_disconnect'));return
                        if event['type']!='glove_state':return
                        state=event['state']
                        if time.monotonic()>deadline:commands.put(dict(name='glove_disconnect'));return
                        if phase[0]==0 and state['stream']['fresh']:
                            phase[0]=1;commands.put(dict(name='glove_prepare',serial='SYNTHETIC-HAND',address=''))
                        elif phase[0]==1 and state['feedback'].get('device_id')=='SYNTHETIC-HAND':
                            phase[0]=2;commands.put(dict(name='glove_follow',workspace_clear=True,lease='a'*32))
                events=Events();commands.put(dict(name='glove_open',serial='SYNTHETIC-GLOVE',user_id='owner',timeout_ms=250))
                with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict('os.environ',WUJI_HAND_PROFILE='hand2_right'), \
                        patch('glove_agent.sys.platform','win32'),patch('glove_agent.GloveSource',Source), \
                        patch('glove_agent.paired_hand_worker',side_effect=hand_worker), \
                        patch('glove_agent.queue.Queue',HandQueue):
                    worker_glove(commands,events)
                rejected=[event for event in list(events.queue) if event['type']=='glove_follow_rejected']
                if reject_before:
                    self.assertEqual(hand_commands,['disconnect']);self.assertEqual(len(rejected),1)
                    self.assertEqual(rejected[0]['lease'],'a'*32);self.assertEqual(rejected[0]['device_id'],'SYNTHETIC-HAND')
                else:
                    self.assertEqual(rejected,[]);self.assertEqual(hand_commands,['hardware_start','disconnect'])


if __name__=='__main__':unittest.main()
