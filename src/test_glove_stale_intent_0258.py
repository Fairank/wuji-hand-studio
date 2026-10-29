"""Real controller/bridge/task dispatch with delayed intentions; no devices or network."""
import copy
import io
import json
import threading
import time
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from console_server import Controller,Handler,PORT
from connection_switch import ConnectionSwitch
from device_profiles import profile
from glove_bridge import GloveBridge
from glove_protocol import CancellationNotDispatched
from task_sessions import context_digest
from task_view import scope,execute,_task

SN='WG_SYNTHETIC_INTENT';HAND='WH_SYNTHETIC_A';REQUEST='a'*32;ATTEMPT='b'*32


class GloveStaleIntentTests(unittest.TestCase):
    def controller(self):
        c=object.__new__(Controller);c.lock=threading.RLock();c.csrf='synthetic-token'
        c.glove=GloveBridge(Mock());b=c.glove;b.session=44;b.thread=N(is_alive=lambda:True)
        b.stdin=io.StringIO();b._require_follow_sdk=Mock()
        b.state=self.worker_state();b.last_update=time.monotonic()
        c.calibration_handover=N(workflow_lock=threading.Lock(),request_cancel=Mock())
        c.teleoperation_handover=N(request_cancel=Mock())
        c.calibrated_teleoperation=N(cancel=Mock())
        c.state={'device_profile':profile('hand2_left'),'hardware':{'active':False}}
        c.connection_switch=ConnectionSwitch(c);c.connection_switch._config=lambda:'synthetic-config'
        c.connection_switch.request_cancel=Mock()
        c.snapshot=lambda:dict(connection='disconnected',device_profile=profile('hand2_left'),hardware={'active':False},
            glove=b.snapshot(),official_viz={'active':False},calibration={'running':False},
            connection_switch=copy.deepcopy(c.connection_switch.state))
        return c

    def worker_state(self,*,hand=HAND,active=False,unpaired=False):
        return dict(connection='receiving',glove_serial=SN,profile='hand2_left',user={'user_id':'owner-A'},
            stream={'q':[0.]*20,'fresh':True,'age_ms':0,'timeout_ms':2000,'solver':{'engine':'sdk','applied':True}},
            hardware={} if unpaired else {'active':active,'stop_confirmed':not active,'probe_ready':True},
            feedback={} if unpaired else {'device_id':hand,'connection':'connected',
                'identified_device':{'source':'sdk_identity','serial':hand,'profile':'hand2_left'},'metrics':{'age_ms':0}},
            can_prepare=unpaired,hand_unpaired=unpaired)

    def intent(self,c,name='glove_stop'):
        return dict(name=name,expected_session=c.glove.session,expected_revision=c.glove.action_revision)

    def wires(self,c):return [json.loads(line) for line in c.glove.stdin.getvalue().splitlines()]

    def ack(self,c,**kw):c.glove.receive({'type':'glove_state','state':self.worker_state(**kw)},session=c.glove.session)

    def assert_no_cancellation(self,c):
        c.connection_switch.request_cancel.assert_not_called();c.calibration_handover.request_cancel.assert_not_called()
        c.teleoperation_handover.request_cancel.assert_not_called();c.calibrated_teleoperation.cancel.assert_not_called()

    def test_stale_epoch_and_invalid_or_partial_ownership_never_cancel_or_send(self):
        c=self.controller()
        bad=[dict(name='glove_stop',expected_session=43,expected_revision=0),
             dict(name='glove_disconnect',expected_session=43,expected_revision=0),
             dict(name='glove_stop',expected_session=44),dict(name='glove_stop',expected_revision=0),
             dict(name='hardware_stop',expected_session=44,expected_revision=0)]
        for key in ('expected_session','expected_revision'):
            for value in (None,False,-1,'44',2**53,1.5):bad.append(dict(self.intent(c),**{key:value}))
        for command in bad:
            with self.subTest(command=command),self.assertRaises(ValueError):c.action(command)
        self.assertEqual(self.wires(c),[]);self.assertEqual(c.glove.action_revision,0)
        self.assertFalse(c.glove.stopping);self.assert_no_cancellation(c)

    def test_current_stop_and_disconnect_strip_owner_fields_and_return_actual_receipt(self):
        for name in ('glove_stop','glove_disconnect'):
            with self.subTest(name=name):
                c=self.controller();reply=c.action(self.intent(c,name))
                self.assertEqual(self.wires(c),[{'name':name}])
                self.assertEqual(reply,dict(session_id=44,action_revision=1))
                self.assert_no_cancellation(c)

    def test_final_bridge_recheck_rejects_change_after_controller_early_check(self):
        for change in ('session','revision'):
            with self.subTest(change=change):
                c=self.controller();command=self.intent(c);original=c.glove.action
                def replaced(value):
                    if change=='session':c.glove.session=45
                    else:c.glove.action_revision=1
                    return original(value)
                with patch.object(c.glove,'action',side_effect=replaced),self.assertRaises(CancellationNotDispatched):c.action(command)
                self.assertEqual(self.wires(c),[]);self.assert_no_cancellation(c)

    def test_same_epoch_new_follow_or_repaired_hand_rejects_old_local_stop(self):
        for replace_hand in (False,True):
            with self.subTest(replace_hand=replace_hand):
                c=self.controller();b=c.glove
                first=b.action(dict(name='glove_follow',workspace_clear=True));old=self.intent(c)
                self.assertEqual((first['lease_session'],first['lease_revision']),(44,1))
                self.ack(c,active=True);b.action(dict(name='glove_stop'));self.ack(c,active=False)
                if replace_hand:
                    b.action(dict(name='glove_unpair'));self.ack(c,unpaired=True)
                    b.action(dict(name='glove_prepare',serial='WH_SYNTHETIC_B'));self.ack(c,hand='WH_SYNTHETIC_B')
                new=b.action(dict(name='glove_follow',workspace_clear=True))
                self.assertNotEqual(first['lease'],new['lease']);count=len(self.wires(c))
                with self.assertRaises(CancellationNotDispatched):c.action(old)
                self.assertEqual(len(self.wires(c)),count);self.assertEqual(b.lease,new['lease'])
                c.action(self.intent(c));self.assertEqual(len(self.wires(c)),count+1)

    def test_frames_keepalive_and_preflight_failure_do_not_advance_control_revision(self):
        c=self.controller();b=c.glove;lease=b.action(dict(name='glove_follow',workspace_clear=True))
        revision=b.action_revision;digest=context_digest(c.snapshot())
        self.ack(c,active=True)
        b.action(dict(name='glove_keepalive',lease=lease['lease']))
        self.assertEqual(b.action_revision,revision);self.assertEqual(context_digest(c.snapshot()),digest)
        with self.assertRaises(ValueError):b.action(dict(name='glove_prepare',serial=HAND))
        self.assertEqual(b.action_revision,revision)

    def test_attempted_write_failure_consumes_revision_but_no_channel_failure_does_not(self):
        c=self.controller();b=c.glove;b.stdin=N(write=Mock(side_effect=OSError('synthetic write failure')),flush=Mock())
        with self.assertRaises(OSError):b.action(self.intent(c))
        self.assertEqual(b.action_revision,1)
        with self.assertRaises(CancellationNotDispatched):b.action(dict(name='glove_stop',expected_session=44,expected_revision=0))
        b.stdin=None;before=b.action_revision
        with self.assertRaises(ValueError):b.action(self.intent(c))
        self.assertEqual(b.action_revision,before)

    def test_existing_transition_same_sdk_epoch_rejects_local_stop_and_exact_cancel_only_marks_current_attempt(self):
        c=self.controller();switch=c.connection_switch;entered=threading.Event();release=threading.Event()
        def transition():
            with c.calibration_handover.workflow_lock:
                with switch.lock:switch.state.update(busy=True,phase='preflight',request_id=REQUEST,attempt_id=ATTEMPT)
                entered.set();release.wait(3)
        thread=threading.Thread(target=transition);thread.start();self.assertTrue(entered.wait(2))
        try:
            for name in ('glove_stop','glove_disconnect'):
                with self.assertRaisesRegex(ValueError,'Cancel button'):c.action(self.intent(c,name))
            self.assertFalse(switch.cancelled);self.assertEqual(self.wires(c),[]);self.assert_no_cancellation(c)
            reply=c.action(dict(name='connection_switch_cancel',request_id=REQUEST,attempt_id=ATTEMPT))
            self.assertTrue(reply['connection_switch']['cancel_requested']);self.assertTrue(switch.cancelled)
            self.assertEqual(self.wires(c),[])
            switch.cancelled=False;switch.state.update(request_id='c'*32,attempt_id='d'*32,cancel_requested=False)
            with self.assertRaises(ValueError):c.action(dict(name='connection_switch_cancel',request_id=REQUEST,attempt_id=ATTEMPT))
            self.assertFalse(switch.cancelled)
            switch.state.update(phase='connected')
            with self.assertRaises(ValueError):c.action(dict(name='connection_switch_cancel',request_id='c'*32,attempt_id='d'*32))
        finally:release.set();thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_actual_task_scope_nonreentrant_workflow_has_current_receipt_and_rejects_old_context(self):
        c=self.controller();task=_task('glove',SN);before=scope(c,task);self.assertTrue(before['current'])
        result=execute(c,task,before['owner_context'],self.intent(c))
        self.assertEqual(len(self.wires(c)),1);self.assertIn('task_receipt',result)
        after=scope(c,task);self.assertNotEqual(before['owner_context'],after['owner_context'])
        self.assertEqual(result['task_receipt']['after_context'],after['owner_context'])
        with self.assertRaises(ValueError):execute(c,task,before['owner_context'],dict(name='glove_stop'))
        self.assertEqual(len(self.wires(c)),1)

    def test_task_exact_switch_cancel_works_while_nonreentrant_workflow_is_held(self):
        c=self.controller();switch=c.connection_switch;task=_task('glove',SN);context=scope(c,task)['owner_context']
        with c.calibration_handover.workflow_lock:
            switch.state.update(busy=True,phase='connecting',request_id=REQUEST,attempt_id=ATTEMPT)
            result=execute(c,task,context,dict(name='connection_switch_cancel',request_id=REQUEST,attempt_id=ATTEMPT))
        self.assertTrue(result['connection_switch']['cancel_requested']);self.assertTrue(switch.cancelled)
        self.assertNotIn('task_receipt',result);self.assertEqual(self.wires(c),[])

    def test_controller_to_bridge_lock_order_does_not_deadlock_a_concurrent_reader(self):
        c=self.controller();reader_ready=threading.Event();reader_continue=threading.Event();outcomes=[]
        def reader():
            with c.lock:
                reader_ready.set();reader_continue.wait(2)
                with c.glove.lock:outcomes.append('reader')
        def stop():
            try:c.action(self.intent(c));outcomes.append('stop')
            except Exception as error:outcomes.append(type(error).__name__)
        first=threading.Thread(target=reader,daemon=True);second=threading.Thread(target=stop,daemon=True)
        first.start();self.assertTrue(reader_ready.wait(1));second.start()
        deadline=time.monotonic()+1
        while not c.calibration_handover.workflow_lock.locked() and time.monotonic()<deadline:time.sleep(.001)
        self.assertTrue(c.calibration_handover.workflow_lock.locked())
        reader_continue.set();first.join(1);second.join(1)
        self.assertFalse(first.is_alive());self.assertFalse(second.is_alive())
        self.assertEqual(outcomes,['reader','stop']);self.assertEqual(self.wires(c),[{'name':'glove_stop'}])

    def test_accepted_exact_cancel_at_final_receipt_boundary_cannot_commit_success(self):
        import task_view
        from test_connection_switch import SwitchTests,USER
        fixture=SwitchTests()
        with fixture.environment('official') as e:
            original=task_view._owner_context
            def cancel_before_commit(state):
                e.service.cancel(dict(name='connection_switch_cancel',request_id=e.service.state['request_id'],
                    attempt_id=e.service.state['attempt_id']))
                return original(state)
            with patch.object(task_view,'_owner_context',side_effect=cancel_before_commit),self.assertRaisesRegex(ValueError,'cancellation requested'):
                e.service.start(fixture.command(e,'teleoperation',user_id=USER))
            self.assertEqual(e.service.state['phase'],'failed');self.assertIsNone(e.service.state['receipt'])
            self.assertTrue(e.service.state['cancel_requested']);self.assertFalse(e.service.state['busy'])

    def test_cancel_after_hand_or_viewer_open_keeps_observed_owner_and_never_claims_disconnected(self):
        import task_view
        from connection_switch import owner
        from test_connection_switch import SwitchTests,HAND as TARGET_HAND,SN as TARGET_GLOVE
        fixture=SwitchTests()
        for mode,number in (('hand',TARGET_HAND),('official',TARGET_GLOVE)):
            with self.subTest(mode=mode),fixture.environment('teleoperation') as e:
                original=task_view._owner_context
                def cancel_before_commit(state):
                    e.service.cancel(dict(name='connection_switch_cancel',request_id=e.service.state['request_id'],
                        attempt_id=e.service.state['attempt_id']))
                    return original(state)
                with patch.object(task_view,'_owner_context',side_effect=cancel_before_commit),self.assertRaisesRegex(ValueError,'review the current device session'):
                    e.service.start(fixture.command(e,mode,sn=number))
                observed=owner(e.c.snapshot())
                self.assertEqual((observed['mode'],observed['serial']),(mode,number))
                self.assertEqual(e.service.state['phase'],'failed');self.assertIsNone(e.service.state['receipt'])
                self.assertTrue(e.service.state['cancel_requested'])

    def test_new_unchanged_switch_clears_previous_cancel_flag(self):
        from test_connection_switch import SwitchTests,USER
        fixture=SwitchTests()
        with fixture.environment('teleoperation') as e:
            e.service.state['cancel_requested']=True
            result=e.service.start(fixture.command(e,'teleoperation',user_id=USER))
            self.assertTrue(result['connection_switch']['unchanged'])
            self.assertFalse(result['connection_switch']['cancel_requested'])

    def test_http_delayed_intent_is_rejected_before_any_cancellation_and_current_intent_succeeds(self):
        c=self.controller()
        def post(command):
            body=json.dumps(command).encode();handler=object.__new__(Handler);handler.path='/api/action'
            handler.headers={'Host':f'127.0.0.1:{PORT}','X-Console-Token':c.csrf,
                'Content-Length':str(len(body)),'Content-Type':'application/json'}
            handler.rfile=io.BytesIO(body);handler.server=N(controller=c);replies=[]
            handler.reply=lambda *args,**kw:replies.append((args,kw));handler.do_POST()
            self.assertEqual(len(replies),1);return replies[0][0]
        self.assertEqual(post(dict(name='glove_disconnect',expected_session=43,expected_revision=0))[0],400)
        self.assertEqual(self.wires(c),[]);self.assert_no_cancellation(c)
        response=post(self.intent(c));self.assertEqual(response[0],200)
        self.assertEqual(response[1]['action_revision'],1);self.assertEqual(self.wires(c),[{'name':'glove_stop'}])

    def test_cleanup_retry_keeps_epoch_and_current_revision_while_new_session_clears_revision(self):
        c=self.controller();b=c.glove
        b.action(self.intent(c,'glove_disconnect'));first_revision=b.action_revision
        b.action(dict(name='glove_disconnect',expected_session=44,expected_revision=first_revision))
        self.assertEqual(b.session,44);self.assertEqual(b.action_revision,first_revision+1)
        b.thread=None;b._worker_dispatch_attempted=False;b._cleanup_error=None
        with patch('glove_bridge.threading.Thread',return_value=N(start=lambda:None)):
            b._begin_session()
        self.assertEqual(b.session,45);self.assertEqual(b.action_revision,0)
        with self.assertRaises(CancellationNotDispatched):b.action(dict(name='glove_disconnect',expected_session=44,expected_revision=first_revision+1))


if __name__=='__main__':unittest.main()
