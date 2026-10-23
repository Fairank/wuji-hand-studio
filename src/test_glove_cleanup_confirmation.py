"""Exact worker resource ACK and original reader exit; all SDK/transport fake."""
from contextlib import contextmanager
import copy
import io
import json
import queue
import sys
import threading
import time
import unittest
from types import SimpleNamespace as N
from unittest.mock import Mock,patch
import numpy  # Resolve extension imports before temporary synthetic modules.

from glove_agent import worker_glove
from glove_bridge import GloveBridge
from glove_protocol import validate_cleanup,cleanup_confirmed,CancellationNotDispatched
from test_sdk_lifecycle import release_fixture,proof_fixture

SN='WG-SYNTHETIC';USER='sdk-owner';PROFILE='hand2_left'


def cleanup(**changes):
    value=dict(version=1,glove_serial=SN,sdk_user_id=USER,profile=PROFILE,glove_was_open=True,
        subscription_closed=True,glove_disconnected=True,solver_cleanup='sdk_no_close_api',
        user_restored=True,ownership_released=True,profile_lease_released=True,selection_lease_released=True,hand_released=True,retryable=False)
    return dict(value,**changes)


def receiving():
    return dict(connection='receiving',glove_serial=SN,user={'user_id':USER},profile=PROFILE,
        devices=[],users=[],stream={},hardware={},feedback={})


class CleanupProtocolTests(unittest.TestCase):
    def test_confirmation_is_strict_and_never_a_device_command(self):
        self.assertTrue(cleanup_confirmed(cleanup()))
        for field in ('subscription_closed','glove_disconnected','user_restored','ownership_released','profile_lease_released','selection_lease_released','hand_released'):
            with self.subTest(field=field):self.assertFalse(cleanup_confirmed(cleanup(**{field:False})))
        self.assertFalse(cleanup_confirmed(cleanup(solver_cleanup='unconfirmed')))

    def test_missing_version_bool_number_or_extra_fields_are_not_acknowledgements(self):
        for value in (None,{},cleanup(version=True),cleanup(glove_disconnected=1),cleanup(profile='other'),cleanup(extra=True),cleanup(glove_serial=''),cleanup(retryable=True)):
            with self.subTest(value=value),self.assertRaises(ValueError):validate_cleanup(value)


class WorkerCleanupTests(unittest.TestCase):
    def run_worker(self,fail=None,solver_has_close=True,hand_pending=False):
        calls=[];prior={'user_id':'prior'};owner={'user_id':USER};current=[prior];failing=[fail]
        def resource(name):
            calls.append(name)
            if failing[0]==name:raise OSError('Synthetic '+name+' failure')
        def switch(user):
            resource('restore' if user=='prior' else 'select')
            current[0]=prior if user=='prior' else owner
        sub=N(recv=lambda:None,close=lambda:resource('subscription'))
        glove=N(serial_number=SN,hand_side=lambda:N(get=lambda:'left'),
            hand_skeleton=lambda:N(subscribe=lambda:sub),disconnect=lambda:resource('glove'))
        manager=N(current_user=lambda:copy.deepcopy(current[0]),list_users=lambda:[prior,owner],
            scan=lambda:[N(sn=SN,device_type='glove',address='synthetic')],switch_user=switch,connect=lambda **kw:glove)
        solver=N(step=lambda _:[0.]*20)
        if solver_has_close:solver.close=lambda:resource('solver')
        sdk=N(SdkManager=N(instance=lambda:manager),DeviceType=N(WujiGlove='glove'),
            HandModel=N(WujiHand2='hand2',WujiHand='hand1'),Handedness=N(Left='left',Right='right'),
            RetargetSession=N(for_hand=lambda *args,**kw:solver))
        class Lease:
            def __init__(self,selection=False,**kw):self.selection=selection
            def close(self):resource('selection-lease' if self.selection else 'profile-lease')
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        class Ownership:
            def __init__(self,*args):pass
            def close(self):resource('ownership')
        requests=queue.Queue();events=queue.Queue()
        requests.put(dict(name='glove_open',serial=SN,user_id=USER,timeout_ms=250))
        if hand_pending:requests.put(dict(name='glove_prepare',serial='WH-SYNTHETIC',address=''))
        if fail!='selection-lease':requests.put(dict(name='glove_disconnect'))
        real_thread=threading.Thread;hand_joins=[]
        class PendingHand:
            def __init__(self,args):self.events=args[4];self.alive=True
            def start(self):pass
            def is_alive(self):return self.alive
            def join(self,timeout=None):
                hand_joins.append(True)
                if len(hand_joins)>1:
                    self.alive=False
                    self.events.put(dict(type='closed',hardware=dict(active=False,stop_confirmed=True)))
                    self.events.put(dict(type='hand_session_closed',cleanup_confirmed=True))
        def make_thread(*args,**kw):
            if kw.get('target') is __import__('glove_agent').paired_hand_worker:return PendingHand(kw['args'])
            return real_thread(*args,**kw)
        with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict('os.environ',WUJI_HAND_PROFILE=PROFILE), \
                patch('glove_agent.sys.platform','linux'),patch('sdk_session.ProfileLease',Lease), \
                patch('device_ownership.DeviceOwnership',Ownership),patch('zenoh_route.managed_wsl',return_value=False), \
                patch('device_versions.read_handle_version',return_value=None), \
                patch('glove_agent.GloveSource.target',return_value=[0.]*20),patch('glove_agent.threading.Thread',side_effect=make_thread):
            thread=threading.Thread(target=worker_glove,args=(requests,events),daemon=True);thread.start()
            try:
                while True:
                    result=events.get(timeout=2)
                    if result['type']=='glove_closed':break
                first_calls=list(calls);first_user=copy.deepcopy(current[0])
                if result['session_cleanup']['retryable']:
                    self.assertTrue(thread.is_alive());before=list(calls);time.sleep(.025)
                    self.assertEqual(calls,before,'Cleanup retried without a new explicit command')
                    failing[0]=None;requests.put({'name':'glove_disconnect'})
                    while True:
                        last=events.get(timeout=2)
                        if last['type']=='glove_closed':break
                    self.assertTrue(cleanup_confirmed(last['session_cleanup']))
                    self.last_recovery=last['session_cleanup']
                    self.hand_joins=hand_joins
                thread.join(2);self.assertFalse(thread.is_alive())
            finally:
                failing[0]=None;requests.put({'name':'glove_disconnect'});thread.join(2)
                self.assertFalse(thread.is_alive())
        return result['session_cleanup'],first_calls,first_user

    def test_real_cleanup_callbacks_precede_exact_worker_ack_and_restore_prior_user(self):
        result,calls,user=self.run_worker()
        self.assertTrue(cleanup_confirmed(result));self.assertEqual(result['glove_serial'],SN)
        self.assertEqual(result['sdk_user_id'],USER);self.assertEqual(result['profile'],PROFILE)
        self.assertEqual(result['solver_cleanup'],'closed');self.assertEqual(user['user_id'],'prior')
        self.assertLess(calls.index('subscription'),calls.index('glove'))
        self.assertLess(calls.index('glove'),calls.index('restore'))
        self.assertLess(calls.index('restore'),calls.index('profile-lease'))

    def test_pinned_sdk_without_close_api_does_not_invent_one(self):
        result,calls,_=self.run_worker(solver_has_close=False)
        self.assertTrue(cleanup_confirmed(result));self.assertEqual(result['solver_cleanup'],'sdk_no_close_api')
        self.assertNotIn('solver',calls)

    def test_each_resource_failure_prevents_confirmation(self):
        fields={'subscription':'subscription_closed','glove':'glove_disconnected','solver':'solver_cleanup',
                'restore':'user_restored','ownership':'ownership_released','profile-lease':'profile_lease_released'}
        for failure,field in fields.items():
            with self.subTest(failure=failure):
                result,calls,user=self.run_worker(failure)
                self.assertFalse(cleanup_confirmed(result))
                self.assertEqual(result[field],'unconfirmed' if failure=='solver' else False)
                if failure=='glove':
                    self.assertNotIn('restore',calls);self.assertEqual(user['user_id'],USER)

    def test_selection_lease_failure_keeps_open_handle_for_cleanup_and_never_confirms(self):
        result,calls,_=self.run_worker('selection-lease')
        self.assertFalse(cleanup_confirmed(result));self.assertFalse(result['selection_lease_released'])
        self.assertTrue(result['glove_was_open']);self.assertIn('glove',calls)

    def test_failed_original_glove_close_retains_owner_until_explicit_retry_then_releases(self):
        first,calls,user=self.run_worker('glove')
        self.assertTrue(first['retryable']);self.assertFalse(first['glove_disconnected'])
        self.assertFalse(first['profile_lease_released']);self.assertFalse(first['ownership_released'])
        self.assertEqual(user['user_id'],USER);self.assertNotIn('restore',calls)
        self.assertTrue(cleanup_confirmed(self.last_recovery));self.assertFalse(self.last_recovery['retryable'])

    def test_original_hand_thread_is_retained_until_explicit_retry_observes_its_disable_ack(self):
        first,_,_=self.run_worker(hand_pending=True)
        self.assertTrue(first['retryable']);self.assertFalse(first['hand_released'])
        self.assertTrue(self.last_recovery['hand_released']);self.assertTrue(cleanup_confirmed(self.last_recovery))
        self.assertEqual(len(self.hand_joins),2)


class BridgeCleanupTests(unittest.TestCase):
    def test_missing_worker_proof_and_cleanup_failures_preserve_both_reasons_and_block_reopen(self):
        bridge=GloveBridge(Mock(),release_resolver=release_fixture);sent=io.StringIO()
        events=[dict(type='ready',teleop_version=1,sdk_proof=proof_fixture()),
            dict(type='glove_state',state=dict(connection='ready',devices=[],users=[],hardware={},feedback={},stream={}))]
        client=N(agent_command='synthetic',exec_command=lambda *a,**kw:(sent,
            iter('WUJI_JSON:'+json.dumps(event) for event in events),io.StringIO()),
            close=Mock(side_effect=OSError('Synthetic transport close failure')))
        bridge.factory=lambda:client;bridge.run();state=bridge.snapshot()
        self.assertEqual(state['connection'],'error');self.assertIsNone(state['sdk_proof'])
        self.assertIn('Actual worker',state['message']);self.assertIn('acknowledgement is missing',state['message'])
        self.assertIn('transport cleanup is unconfirmed',state['message'])
        self.assertIn('acknowledgement is missing',state['session_cleanup']['error'])
        self.assertFalse(state['session_cleanup']['cleanup_confirmed'])
        before=sent.getvalue()
        with self.assertRaises(ValueError):bridge.action({'name':'glove_scan'})
        self.assertEqual(sent.getvalue(),before)

    def test_partial_open_ack_requires_the_exact_dispatched_identity_without_claiming_receiving(self):
        for changes in ({},{'glove_serial':'WG-OTHER'},{'sdk_user_id':'other'},{'profile':'hand2_right'}):
            with self.subTest(changes=changes):
                bridge=GloveBridge(Mock());bridge.session=17;bridge.thread=N(is_alive=lambda:True)
                bridge.state=receiving();bridge.state.update(connection='ready',glove_serial='',user={'user_id':'prior'})
                bridge.stdin=io.StringIO()
                bridge.action(dict(name='glove_open',serial=SN,user_id=USER,timeout_ms=250))
                bridge.receive(dict(type='glove_closed',hardware={},
                    session_cleanup=cleanup(selection_lease_released=False,retryable=True,**changes)),session=17)
                snapshot=bridge.snapshot();self.assertEqual(snapshot['connection'],'error')
                self.assertEqual(snapshot.get('glove_serial'),'')
                self.assertEqual(snapshot['session_cleanup']['acknowledged'],not changes)
                self.assertEqual(snapshot['session_cleanup']['retryable'],not changes)
                self.assertEqual(snapshot['session_cleanup']['identity'],None if changes else
                    dict(glove_serial=SN,sdk_user_id=USER,profile=PROFILE))
        bridge=GloveBridge(Mock());bridge.session=17;bridge.state=receiving()
        bridge.state.update(connection='ready',glove_serial='',user={'user_id':'prior'})
        bridge.receive(dict(type='glove_closed',hardware={},session_cleanup=cleanup()),session=17)
        self.assertFalse(bridge.snapshot()['session_cleanup']['acknowledged'],'Unsent open authorized an arbitrary candidate')

    @contextmanager
    def transport(self,ack=None,*,missing_ack=False,hold_exit=False,close_error=False,final_hardware=None,ready_only=False,no_terminal=False):
        started=threading.Event();disconnect=threading.Event();ack_sent=threading.Event();finish=threading.Event()
        if not hold_exit:finish.set()
        class Input(io.StringIO):
            def write(self,value):
                result=super().write(value)
                if json.loads(value).get('name')=='glove_disconnect':disconnect.set()
                return result
        sent=Input();bridge=GloveBridge(Mock(),release_resolver=release_fixture);bridge.session=17
        def lines():
            yield 'WUJI_JSON:'+json.dumps(dict(type='ready',teleop_version=1,sdk_proof=proof_fixture()))
            yield 'WUJI_JSON:'+json.dumps(dict(type='sdk_ready',sdk_proof=proof_fixture()))
            state=receiving()
            if ready_only:state.update(connection='ready',glove_serial='',user={'user_id':'prior'})
            yield 'WUJI_JSON:'+json.dumps(dict(type='glove_state',state=state))
            started.set()
            if not disconnect.wait(2):raise AssertionError('Synthetic test did not request disconnect')
            event=dict(type='glove_closed',hardware=final_hardware or {})
            if not missing_ack:
                event['session_cleanup']=cleanup(glove_serial='',sdk_user_id='',glove_was_open=False,solver_cleanup='not_open') if ready_only else cleanup() if ack is None else ack
            if not no_terminal:yield 'WUJI_JSON:'+json.dumps(event)
            ack_sent.set()
            if not finish.wait(2):raise AssertionError('Synthetic exit gate was not released')
        client=N(agent_command='synthetic-only',exec_command=lambda *a,**kw:(sent,lines(),io.StringIO()),
            close=Mock(side_effect=OSError('Synthetic transport close failure') if close_error else None))
        bridge.factory=Mock(return_value=client);bridge.thread=threading.Thread(target=bridge.run)
        bridge.thread.start();self.assertTrue(started.wait(2))
        try:yield N(bridge=bridge,sent=sent,disconnect=disconnect,ack_sent=ack_sent,finish=finish,client=client)
        finally:
            disconnect.set();finish.set();bridge.thread.join(3);self.assertFalse(bridge.thread.is_alive())

    def request(self,bridge,**kw):
        return bridge.stop_session_confirmed(17,expected_serial=SN,expected_user=USER,expected_profile=PROFILE,**kw)

    def test_exact_ack_reader_exit_and_transport_close_complete_one_original_disconnect(self):
        with self.transport() as e:
            result=self.request(e.bridge)
            self.assertEqual(result,dict(session_id=17,glove_serial=SN,sdk_user_id=USER,profile=PROFILE,cleanup_confirmed=True))
            self.assertFalse(e.bridge.busy);self.assertTrue(e.bridge.snapshot()['session_cleanup']['cleanup_confirmed'])
            self.assertEqual([json.loads(line)['name'] for line in e.sent.getvalue().splitlines()],['glove_session','glove_disconnect'])
            e.client.close.assert_called_once();self.assertEqual(self.request(e.bridge),result)

    def test_old_protocol_without_glove_ack_is_not_success_even_after_eof(self):
        with self.transport(missing_ack=True) as e:
            with self.assertRaises(ValueError):self.request(e.bridge)
            self.assertFalse(e.bridge.busy);self.assertFalse(e.bridge.snapshot()['session_cleanup']['cleanup_confirmed'])
            self.assertEqual(e.bridge.snapshot()['connection'],'error')
            self.assertIn('cleanup',e.bridge.snapshot()['message'].lower())

    def test_resource_failure_ack_does_not_become_confirmed_after_thread_exit(self):
        for key in ('glove_disconnected','subscription_closed','user_restored','hand_released'):
            with self.subTest(key=key),self.transport(ack=cleanup(**{key:False})) as e:
                with self.assertRaises(ValueError):self.request(e.bridge)
                self.assertFalse(e.bridge.snapshot()['session_cleanup']['cleanup_confirmed'])
                self.assertEqual(e.bridge.snapshot()['connection'],'error')

    def test_wrong_serial_user_profile_or_open_claim_never_authorizes_switch(self):
        for changes in ({'glove_serial':'WG-OTHER'},{'sdk_user_id':'other-user'},{'profile':'hand2_right'},{'glove_was_open':False}):
            with self.subTest(changes=changes),self.transport(ack=cleanup(**changes)) as e:
                with self.assertRaises(ValueError):self.request(e.bridge)
                self.assertFalse(e.bridge.snapshot()['session_cleanup']['acknowledged'])

    def test_ack_before_reader_exit_remains_unconfirmed_then_same_exact_stop_can_finish(self):
        with self.transport(hold_exit=True) as e:
            with self.assertRaises(ValueError):self.request(e.bridge,timeout=.02)
            self.assertTrue(e.ack_sent.wait(1));self.assertTrue(e.bridge.busy)
            self.assertFalse(e.bridge.snapshot()['session_cleanup']['cleanup_confirmed'])
            e.finish.set();self.assertTrue(self.request(e.bridge)['cleanup_confirmed'])
            self.assertEqual(e.sent.getvalue().count('glove_disconnect'),1)

    def test_transport_close_failure_is_not_a_release_even_with_valid_worker_ack(self):
        with self.transport(close_error=True) as e:
            with self.assertRaisesRegex(ValueError,'transport'):self.request(e.bridge)
            self.assertTrue(e.bridge.snapshot()['session_cleanup']['acknowledged'])
            self.assertFalse(e.bridge.snapshot()['session_cleanup']['cleanup_confirmed'])
            self.assertEqual(e.bridge.snapshot()['connection'],'error')

    def assert_restart_refused(self,bridge):
        prior=copy.deepcopy(bridge.state);session=bridge.session;thread=bridge.thread
        marker={'saved-model':'retained'};bridge._continuation=marker
        callback=Mock();bridge._on_model_ready=callback
        with self.assertRaises(ValueError):bridge.action({'name':'glove_scan'})
        self.assertIs(bridge._continuation,marker);self.assertIs(bridge._on_model_ready,callback)
        from calibration_portability import CONTEXT_FIELDS
        bundle={key:'synthetic' for key in CONTEXT_FIELDS|{'bundle_path'}}
        bundle.update(bundle_sha256='f'*64,bundle_bytes=1)
        with patch('calibration_portability.validate_continuation',return_value=bundle), \
                patch('calibration_portability.file_digest',return_value=('f'*64,1)):
            with self.assertRaises(ValueError):bridge.start_calibrated(bundle,Mock())
        with bridge.lock,self.assertRaises(ValueError):bridge._begin_session()
        self.assertEqual(bridge.state,prior);self.assertEqual(bridge.session,session)
        self.assertIs(bridge.thread,thread);self.assertIs(bridge._continuation,marker)
        self.assertIs(bridge._on_model_ready,callback)

    def test_failed_cleanup_cannot_be_erased_by_scan_continuation_or_internal_restart(self):
        for options in ({'missing_ack':True},{'close_error':True},{'ack':cleanup(glove_disconnected=False)}):
            with self.subTest(options=options),self.transport(**options) as e:
                with self.assertRaises(ValueError):self.request(e.bridge)
                e.bridge.thread.join(1);self.assertFalse(e.bridge.busy)
                self.assert_restart_refused(e.bridge);e.bridge.factory.assert_called_once()

    def test_bare_eof_after_worker_dispatch_stays_unconfirmed_and_blocks_new_session(self):
        with self.transport(no_terminal=True) as e:
            with self.assertRaises(ValueError):self.request(e.bridge)
            self.assertIn('acknowledgement',e.bridge.snapshot()['message'])
            self.assert_restart_refused(e.bridge)

    def test_pre_worker_software_failure_with_confirmed_transport_release_allows_explicit_retry(self):
        factory=Mock(side_effect=ValueError('Synthetic controller unavailable before worker dispatch'))
        bridge=GloveBridge(factory,release_resolver=release_fixture)
        bridge.action({'name':'glove_scan'});bridge.thread.join(2)
        self.assertFalse(bridge.busy);self.assertFalse(bridge._worker_dispatch_attempted)
        self.assertIsNone(bridge._cleanup_error);first=bridge.session
        bridge.action({'name':'glove_scan'});bridge.thread.join(2)
        self.assertFalse(bridge.busy);self.assertEqual(bridge.session,first+1)
        self.assertEqual(factory.call_count,2)

    def test_terminal_hardware_contradiction_never_becomes_a_successful_switch(self):
        for hardware in ({'active':True},{'active':None},{'active':False,'stop_confirmed':False}):
            with self.subTest(hardware=hardware),self.transport(final_hardware=hardware) as e:
                with self.assertRaises(ValueError):self.request(e.bridge)
                self.assertFalse(e.bridge.snapshot()['session_cleanup']['cleanup_confirmed'])
                self.assertEqual(e.bridge.snapshot()['connection'],'error')

    def test_no_follow_preflight_and_disconnect_dispatch_share_the_original_bridge_lock(self):
        with self.transport() as e:
            original=e.bridge.disconnect_session;attempts=[]
            def dispatch(*args,**kw):
                def competing_start():
                    acquired=e.bridge.lock.acquire(timeout=.03);attempts.append(acquired)
                    if acquired:
                        e.bridge.lease='competing-follow'
                        e.bridge.lock.release()
                thread=threading.Thread(target=competing_start);thread.start();thread.join(1)
                self.assertFalse(thread.is_alive())
                return original(*args,**kw)
            with patch.object(e.bridge,'disconnect_session',side_effect=dispatch):self.request(e.bridge)
            self.assertEqual(attempts,[False]);self.assertIsNone(e.bridge.lease)

    def test_ready_worker_with_no_open_handle_can_release_without_inventing_a_selected_user(self):
        with self.transport(ready_only=True) as e:
            result=e.bridge.stop_session_confirmed(17,expected_profile=PROFILE)
            self.assertEqual(result['glove_serial'],'');self.assertEqual(result['sdk_user_id'],'')
            self.assertTrue(result['cleanup_confirmed'])

    def test_stale_task_or_identity_refuses_without_sending_disconnect(self):
        for changes in ({'session':16},{'expected_serial':'WG-OTHER'},{'expected_user':'other-user'},{'expected_profile':'hand2_right'}):
            bridge=GloveBridge(Mock());bridge.session=17;bridge.thread=N(is_alive=lambda:True);bridge.stdin=io.StringIO();bridge.state=receiving()
            arguments=dict(session=17,expected_serial=SN,expected_user=USER,expected_profile=PROFILE,**{})
            arguments.update(changes)
            with self.subTest(changes=changes),self.assertRaises(CancellationNotDispatched):bridge.stop_session_confirmed(**arguments)
            self.assertEqual(bridge.stdin.getvalue(),'')

    def test_active_unknown_pending_follow_and_error_never_send_disconnect(self):
        for change in ('active','unknown','stop-unconfirmed','lease','follow-unconfirmed','error'):
            bridge=GloveBridge(Mock());bridge.session=17;bridge.thread=N(is_alive=lambda:True);bridge.stdin=io.StringIO();bridge.state=receiving()
            if change=='active':bridge.state['hardware']={'active':True}
            elif change=='unknown':bridge.state['hardware']={'active':None}
            elif change=='stop-unconfirmed':bridge.state['hardware']={'active':False,'stop_confirmed':False}
            elif change=='lease':bridge.lease='pending-follow'
            elif change=='follow-unconfirmed':bridge._follow_unconfirmed=True
            else:bridge.state['connection']='error'
            with self.subTest(change=change),self.assertRaises(ValueError):self.request(bridge)
            self.assertEqual(bridge.stdin.getvalue(),'')

    def test_late_old_reader_ack_is_ignored(self):
        bridge=GloveBridge(Mock());bridge.session=18;bridge.state=receiving()
        bridge.receive(dict(type='glove_closed',hardware={},session_cleanup=cleanup()),session=17)
        self.assertIsNone(bridge._cleanup_ack);self.assertEqual(bridge.state['connection'],'receiving')

    def test_replaced_epoch_during_wait_cannot_report_completion_for_new_owner(self):
        with self.transport(hold_exit=True) as e:
            result=[]
            def stop():
                try:self.request(e.bridge)
                except Exception as error:result.append(error)
            thread=threading.Thread(target=stop);thread.start();self.assertTrue(e.ack_sent.wait(1))
            with e.bridge.lock:e.bridge.session=18
            e.finish.set();thread.join(2);self.assertFalse(thread.is_alive())
            self.assertIsInstance(result[0],ValueError)
            self.assertNotIsInstance(result[0],CancellationNotDispatched)
            self.assertEqual(e.bridge.session,18)


class RetainedWorkerRecoveryTests(unittest.TestCase):
    def test_original_worker_retries_only_explicit_disconnect_then_same_bridge_can_open_again(self):
        self.original_worker_recovery()

    def test_partial_open_selection_release_failure_retains_identity_and_recovers_original_worker(self):
        self.original_worker_recovery(partial_open=True)

    def original_worker_recovery(self,partial_open=False):
        prior={'user_id':'prior'};selected={'user_id':USER};current=[prior];failures=[0 if partial_open else 2];calls=[];workers=[];clients=[]
        selection_failures=[2 if partial_open else 0]
        def switch(user):
            calls.append(('user',user));current[0]=selected if user==USER else prior
        def disconnect():
            calls.append(('glove-close',id(glove)))
            if failures[0]:failures[0]-=1;raise OSError('Synthetic original handle close failed')
        sub=N(recv=lambda:None,close=lambda:calls.append(('subscription-close',)))
        glove=N(serial_number=SN,hand_side=lambda:N(get=lambda:'left'),disconnect=disconnect,
            hand_skeleton=lambda:N(subscribe=lambda:sub))
        manager=N(current_user=lambda:copy.deepcopy(current[0]),list_users=lambda:[prior,selected],switch_user=switch,
            scan=lambda:[N(sn=SN,device_type='glove',address='synthetic')],connect=lambda **kw:glove)
        sdk=N(SdkManager=N(instance=lambda:manager),DeviceType=N(WujiGlove='glove'),
            HandModel=N(WujiHand2='h2',WujiHand='h1'),Handedness=N(Left='left',Right='right'),
            RetargetSession=N(for_hand=lambda *a,**kw:N(step=lambda x:[0.]*20)))
        class Lease:
            def __init__(self,selection=False,**kw):self.selection=selection
            def close(self):
                calls.append(('selection-close' if self.selection else 'profile-close',))
                if self.selection and selection_failures[0]:
                    selection_failures[0]-=1;raise OSError('Synthetic original selection release failed')
        class Ownership:
            def __init__(self,*a):pass
            def close(self):calls.append(('ownership-close',))
        def factory():
            requests=queue.Queue();events=queue.Queue();thread=[None];sent=[]
            class Input:
                def write(self,text):
                    command=json.loads(text);sent.append(command)
                    if command['name']=='glove_session':
                        thread[0]=threading.Thread(target=worker_glove,args=(requests,events),daemon=True)
                        workers.append((thread[0],requests));thread[0].start()
                    else:requests.put(command)
                def flush(self):pass
            def lines():
                yield 'WUJI_JSON:'+json.dumps(dict(type='ready',teleop_version=1,sdk_proof=proof_fixture()))
                yield 'WUJI_JSON:'+json.dumps(dict(type='sdk_ready',sdk_proof=proof_fixture()))
                while True:
                    try:event=events.get(timeout=.02)
                    except queue.Empty:
                        if thread[0] is not None and not thread[0].is_alive():break
                        continue
                    yield 'WUJI_JSON:'+json.dumps(event)
            def close():
                if thread[0] is not None:
                    thread[0].join(1)
                    if thread[0].is_alive():raise ValueError('Synthetic worker still owns its handles')
            client=N(agent_command='synthetic-only',exec_command=lambda *a,**kw:(Input(),lines(),io.StringIO()),
                close=Mock(side_effect=close),sent=sent)
            clients.append(client);return client
        bridge=GloveBridge(factory,release_resolver=release_fixture)
        def wait(connection):
            until=time.monotonic()+2
            while time.monotonic()<until:
                if bridge.snapshot()['connection']==connection:return
                time.sleep(.005)
            self.fail('Synthetic SDK did not reach '+connection)
        with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict('os.environ',WUJI_HAND_PROFILE=PROFILE), \
                patch('glove_agent.sys.platform','linux'),patch('sdk_session.ProfileLease',Lease), \
                patch('device_ownership.DeviceOwnership',Ownership),patch('zenoh_route.managed_wsl',return_value=False), \
                patch('device_versions.read_handle_version',return_value=None):
            try:
                bridge.action({'name':'glove_scan'});wait('ready')
                bridge.action(dict(name='glove_open',serial=SN,user_id=USER,timeout_ms=250))
                wait('error' if partial_open else 'receiving')
                session=bridge.session;original_reader=bridge.thread;original_worker=workers[0][0]
                if partial_open:
                    until=time.monotonic()+2
                    while not bridge.snapshot()['session_cleanup']['acknowledged'] and time.monotonic()<until:time.sleep(.005)
                    state=bridge.snapshot();self.assertTrue(state['session_cleanup']['retryable'])
                    self.assertEqual(state['session_cleanup']['identity'],dict(glove_serial=SN,sdk_user_id=USER,profile=PROFILE))
                    self.assertNotEqual(state['connection'],'receiving')
                    self.assertTrue(original_worker.is_alive());self.assertEqual(current[0]['user_id'],USER)
                    before=list(calls);time.sleep(.03);self.assertEqual(calls,before)
                for failure in range(0 if partial_open else 2):
                    with self.assertRaises(ValueError):bridge.stop_session_confirmed(session,expected_serial=SN,expected_user=USER,expected_profile=PROFILE)
                    state=bridge.snapshot();self.assertEqual(state['connection'],'error')
                    self.assertTrue(state['session_cleanup']['retryable']);self.assertTrue(bridge.busy)
                    self.assertIs(bridge.thread,original_reader);self.assertTrue(original_worker.is_alive())
                    self.assertEqual(bridge.session,session);self.assertEqual(current[0]['user_id'],USER)
                    self.assertNotIn(('profile-close',),calls);self.assertNotIn(('ownership-close',),calls)
                    with self.assertRaises(ValueError):bridge.action({'name':'glove_scan'})
                    count=len(calls);time.sleep(.035);self.assertEqual(len(calls),count,'Unrequested cleanup retry')
                    self.assertEqual(sum(x[0]=='glove-close' for x in calls),failure+1)
                result=bridge.stop_session_confirmed(session,expected_serial=SN,expected_user=USER,expected_profile=PROFILE)
                self.assertTrue(result['cleanup_confirmed']);self.assertFalse(bridge.busy)
                self.assertFalse(original_worker.is_alive());self.assertEqual(current[0]['user_id'],'prior')
                self.assertEqual(sum(x[0]=='glove-close' for x in calls),1 if partial_open else 3)
                self.assertEqual(sum(x[0]=='subscription-close' for x in calls),1)
                self.assertEqual({x[1] for x in calls if x[0]=='glove-close'},{id(glove)})
                self.assertIsNone(bridge.snapshot()['session_cleanup']['error'])
                bridge.action({'name':'glove_scan'});wait('ready')
                self.assertEqual(bridge.session,session+1)
                bridge.action(dict(name='glove_open',serial=SN,user_id=USER,timeout_ms=250));wait('receiving')
                self.assertTrue(bridge.stop_session_confirmed(bridge.session,expected_serial=SN,expected_user=USER,expected_profile=PROFILE)['cleanup_confirmed'])
                self.assertEqual(len(clients),2)
                self.assertFalse(any(c['name'] in ('glove_follow','glove_prepare','hardware_start') for client in clients for c in client.sent))
            finally:
                failures[0]=0;selection_failures[0]=0
                for thread,requests in workers:requests.put({'name':'glove_disconnect'});thread.join(2)
                if bridge.thread is not None:bridge.thread.join(3)
                self.assertTrue(all(not t.is_alive() for t,q in workers));self.assertFalse(bridge.busy)


if __name__=='__main__':unittest.main()
