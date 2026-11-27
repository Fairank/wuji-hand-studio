"""A failed pre-driver prepare releases resources without inventing motor stop."""
import copy,io,json,queue,sys,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace as N
from unittest.mock import Mock,patch
from glove_agent import worker_glove,paired_hand_worker
from glove_bridge import GloveBridge
from device_termination import TerminationJournal
from test_sdk_lifecycle import release_fixture,proof_fixture
from test_glove_cleanup_confirmation import cleanup
import glove_protocol

SN='WG-NO-DRIVER';USER='owner-no-driver';PROFILE='hand2_right';HAND='WH-NO-DRIVER'
def no_driver():return dict(version=1,kind='hand_driver_not_created',requested_serial=HAND,profile=PROFILE,enable_attempted=False,resources_released=True)

class NoDriverCleanupTests(unittest.TestCase):
    def test_actual_failed_prepare_worker_reader_cleanup_and_explicit_restart(self):
        self.exercise_worker_cleanup()

    def test_late_prepare_release_retries_original_worker_without_anonymous_motor_record(self):
        self.exercise_worker_cleanup(late=True)

    def test_connected_readonly_hand_closes_without_motor_disable_then_restarts(self):
        self.exercise_worker_cleanup(readonly=True)

    def exercise_worker_cleanup(self, *, late=False, readonly=False):
        prior=dict(user_id='prior');user=dict(user_id=USER);current=[prior];seq=[0];alternate=[False];events=[];clients=[];threads=[]
        entered=threading.Event();release=threading.Event();prepared_threads=[]
        def recv():
            alternate[0]=not alternate[0]
            if not alternate[0]:return None
            seq[0]+=1;return N(header=N(seq=seq[0],timestamp_us=seq[0]*1000),joints=[N(pose=N(position=[.01,.02,.03])) for _ in range(21)])
        glove=N(serial_number=SN,hand_side=lambda:N(get=lambda:'right'),disconnect=Mock(),
            hand_skeleton=lambda:N(subscribe=lambda:N(recv=recv,close=Mock())))
        manager=N(current_user=lambda:current[0],list_users=lambda:[prior,user],switch_user=lambda uid:current.__setitem__(0,user if uid==USER else prior),
            scan=lambda:[N(sn=SN,address='fixture',device_type='DeviceType.WujiGlove')],connect=Mock(return_value=glove))
        sdk=N(SdkManager=N(instance=lambda:manager),DeviceType=N(WujiGlove='DeviceType.WujiGlove'),
            HandModel=N(WujiHand2='hand2',WujiHand='hand1'),Handedness=N(Left='left',Right='right'),
            RetargetSession=N(for_hand=lambda *a,**kw:N(step=lambda kp:[0.]*20,close=Mock())),
            WujiHand2=N(describe_error=lambda _:{}))
        hand=N(serial_number=HAND,handedness=lambda:N(get=lambda:'right'),
            joint_states=lambda:N(subscribe=lambda:N(recv=lambda:None,close=Mock())),
            joint_diagnostics=lambda:N(subscribe=lambda:N(recv=lambda:None,close=Mock())),
            disconnect=Mock(),enable=Mock(side_effect=AssertionError('Read only')),
            disable=Mock(side_effect=AssertionError('Read only')))
        route=N(close=Mock())
        def discover(*args):
            if readonly:
                from device_profiles import profile
                return hand,route,profile(PROFILE)
            if late:
                entered.set()
                if not release.wait(3):raise AssertionError('Synthetic delayed prepare was not released')
            raise RuntimeError('No hand discovered')
        real_thread=threading.Thread
        def make_thread(*args,**kw):
            thread=real_thread(*args,**kw)
            if late and kw.get('target') is paired_hand_worker:
                join=thread.join
                thread.join=lambda timeout=None:join(.02 if timeout==4 else timeout)
                prepared_threads.append(thread)
            return thread
        class Client:
            agent_command='synthetic-owned-worker'
            def __init__(self):self.commands=queue.Queue();self.output=queue.Queue();self.worker=None;self.sent=[];clients.append(self)
            def exec_command(self,*a,**kw):
                client=self
                class Input:
                    def write(_,text):
                        value=json.loads(text);client.sent.append(value)
                        if value['name']=='glove_session':
                            class Events:
                                def put(_,event):
                                    events.append(copy.deepcopy(event));client.output.put('WUJI_JSON:'+json.dumps(event)+'\n')
                            def run():
                                try:worker_glove(client.commands,Events(),sdk_requirement=value['sdk_requirement'])
                                finally:client.output.put(None)
                            client.worker=threading.Thread(target=run);threads.append(client.worker);client.worker.start()
                        else:client.commands.put(value)
                    def flush(_):pass
                def output():
                    yield 'WUJI_JSON:'+json.dumps(dict(type='ready',teleop_version=1,sdk_proof=proof_fixture()))+'\n'
                    while True:
                        line=client.output.get(timeout=3)
                        if line is None:return
                        yield line
                return Input(),output(),io.BytesIO()
            def close(self):
                if self.worker:self.worker.join(2)
                return not self.worker or not self.worker.is_alive()
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        journal=TerminationJournal(Path(folder.name)/'stop.json')
        old_session=journal.new_session()
        journal.observe('hand','UNRELATED-HAND',dict(active=None),old_session)
        previous=journal.snapshot()
        bridge=GloveBridge(Client,release_resolver=release_fixture,termination=journal)
        def wait(predicate):
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                state=bridge.snapshot()
                if predicate(state):return state
                time.sleep(.005)
            self.fail('Synthetic source condition did not arrive: '+repr(bridge.snapshot()))
        with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict('os.environ',WUJI_HAND_PROFILE=PROFILE),\
                patch('sdk_runtime.probe_runtime',return_value=proof_fixture()),patch('zenoh_route.managed_wsl',return_value=False),\
                patch('device_discovery.connect_discovered',side_effect=discover),\
                patch('device_versions.read_handle_version',return_value=None),\
                patch('glove_agent.threading.Thread',side_effect=make_thread):
            try:
                for cycle in range(2):
                    bridge.action(dict(name='glove_scan'));wait(lambda s:s['connection']=='ready')
                    bridge.action(dict(name='glove_open',serial=SN,user_id=USER,timeout_ms=250))
                    wait(lambda s:s['connection']=='receiving' and s['stream']['fresh'])
                    bridge.action(dict(name='glove_prepare',serial=HAND,address=''))
                    if late:
                        self.assertTrue(entered.wait(1))
                        state=bridge.snapshot()
                        session=state['session_id']
                        with self.assertRaises(ValueError):
                            bridge.stop_session_confirmed(session,expected_serial=SN,expected_user=USER,expected_profile=PROFILE)
                        pending=bridge.snapshot()
                        self.assertEqual(journal.snapshot(),previous,'Resource-only wait must not invent a motor stop row')
                        self.assertTrue(pending['session_cleanup']['retryable'])
                        self.assertFalse(pending['session_cleanup']['cleanup_confirmed'])
                        self.assertTrue(bridge.busy)
                        self.assertEqual([c['name'] for c in clients[-1].sent].count('glove_disconnect'),1)
                        release.set()
                        prepared_threads[-1].join(1)
                    elif readonly:
                        state=wait(lambda s:s['feedback'].get('device_id')==HAND)
                        self.assertIs(state['hardware']['active'],False)
                        self.assertIs(state['hardware']['stop_confirmed'],False)
                        self.assertTrue(state['feedback_release_allowed'])
                    else:
                        state=wait(lambda s:s.get('hand_error')=='No hand discovered' and s.get('hand_cleanup_confirmed') is True)
                        self.assertEqual(state['hardware'],{});self.assertIsNone(state['feedback'].get('device_id'))
                    session=state['session_id']
                    result=bridge.stop_session_confirmed(session,expected_serial=SN,expected_user=USER,expected_profile=PROFILE)
                    self.assertTrue(result['cleanup_confirmed']);self.assertFalse(bridge.busy)
                    self.assertEqual(current[0],prior)
                    self.assertEqual(journal.snapshot(),previous)
                    last=[e for e in events if e['type']=='glove_closed'][-1]
                    self.assertTrue(last['session_cleanup']['hand_released'])
                    if readonly:
                        self.assertEqual(last['device_id'],HAND)
                        self.assertFalse(last['hardware']['stop_confirmed'])
                        self.assertFalse(last['hardware']['enable_attempted'])
                        self.assertTrue(bridge.snapshot()['session_cleanup']['readonly_hand_released'])
                    else:
                        self.assertEqual(last.get('hand_driver_cleanup'),no_driver())
                        self.assertEqual(last['hardware'],{})
                        self.assertEqual(bridge.snapshot()['hardware'],{})
                    entered.clear();release.clear()
            finally:
                release.set()
                for client in clients:client.commands.put(dict(name='glove_disconnect'))
                if bridge.thread:bridge.thread.join(3)
                for thread in threads:thread.join(3)
                self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(manager.connect.call_count,2)
        hand.enable.assert_not_called();hand.disable.assert_not_called()
        if readonly:
            self.assertEqual(hand.disconnect.call_count,2)
            self.assertEqual(route.close.call_count,2)
        self.assertFalse(any(c['name'] in ('glove_follow','hardware_start','hardware_stop') for client in clients for c in client.sent))

    def test_no_driver_receipt_is_exact_typed_identity_and_never_stop_ack(self):
        validator=getattr(glove_protocol,'no_hand_driver_cleanup',None);self.assertTrue(callable(validator))
        self.assertTrue(validator(no_driver(),serial=HAND,profile=PROFILE))
        for field,value in [('version',True),('kind','readonly_feedback'),('requested_serial','OTHER'),('profile','hand2_left'),('enable_attempted',True),('resources_released',False)]:
            bad=no_driver();bad[field]=value
            with self.subTest(field=field):self.assertFalse(validator(bad,serial=HAND,profile=PROFILE))
        self.assertFalse(validator(dict(no_driver(),stop_confirmed=True),serial=HAND,profile=PROFILE))

    def test_readonly_receipt_never_overrides_current_motor_uncertainty_or_another_pair(self):
        for change in ('old_session','serial','side','lease','enable_attempted','unknown','active'):
            with self.subTest(change=change):
                bridge=GloveBridge(Mock());bridge.session=17;bridge._hand_prepare=(17,HAND,PROFILE)
                observed=dict(active=False,stop_confirmed=False,enable_attempted=False)
                feedback=dict(device_id=HAND,identified_device=dict(serial=HAND,profile=PROFILE,source='sdk_identity'))
                bridge.state.update(connection='receiving',glove_serial=SN,user={'user_id':USER},profile=PROFILE,
                    hardware=copy.deepcopy(observed),feedback=feedback)
                receipt=dict(version=1,kind='readonly_feedback',serial=HAND,profile=PROFILE,
                    enable_attempted=False,resources_released=True)
                event=dict(type='glove_closed',hardware=observed,device_id=HAND,hand_cleanup_confirmed=True,
                    feedback_cleanup=receipt,session_cleanup=cleanup(glove_serial=SN,sdk_user_id=USER,profile=PROFILE))
                session=17
                if change=='old_session':session=16
                elif change=='serial':receipt['serial']='ANOTHER-HAND'
                elif change=='side':receipt['profile']='hand2_left'
                elif change=='lease':bridge.lease='a'*32
                elif change=='enable_attempted':bridge.state['hardware']['enable_attempted']=True
                else:bridge.state['hardware']['active']=None if change=='unknown' else True
                bridge.receive(event,session=session)
                bridge._reader_finished=True;bridge._transport_closed=True
                proof=bridge.snapshot()['session_cleanup']
                self.assertFalse(proof['cleanup_confirmed']);self.assertNotIn('readonly_hand_released',proof)

    def test_pre_driver_resource_close_false_cannot_issue_no_driver_receipt(self):
        hand=N(serial_number=HAND,disconnect=Mock(return_value=False));route=N(close=Mock());records=[]
        sdk=N(SdkManager=N(instance=lambda:object()))
        with patch.dict(sys.modules,wuji_sdk=sdk),patch('device_discovery.connect_discovered',return_value=(hand,route,{'id':'hand2_left'})):
            paired_hand_worker('',HAND,{'id':PROFILE},queue.Queue(),N(put=lambda e:records.append(e)),Mock())
        self.assertFalse(records[-1]['cleanup_confirmed'])
        self.assertIsNone(records[-1].get('hand_driver_cleanup'))
        route.close.assert_called_once()

    def test_bridge_no_driver_proof_cannot_release_another_request_or_actual_motor_uncertainty(self):
        for changed in ('none','serial','profile','old_session','replacement','unknown','device','lease','follow','missing_hand_ack'):
            with self.subTest(changed=changed):
                bridge=GloveBridge(Mock());bridge.session=17;bridge.thread=N(is_alive=lambda:True)
                bridge.stdin=io.StringIO();bridge.last_update=time.monotonic()
                bridge.state=dict(connection='receiving',glove_serial=SN,user={'user_id':USER},profile=PROFILE,
                    devices=[],users=[],hardware={},feedback={},can_prepare=True,
                    stream=dict(age_ms=0,fresh=True,timeout_ms=250,solver=dict(engine='sdk',applied=True)))
                bridge.action(dict(name='glove_prepare',serial=HAND,address=''))
                proof=no_driver();event=dict(type='glove_closed',hardware={},device_id=None,hand_cleanup_confirmed=True,
                    hand_driver_cleanup=proof,session_cleanup=cleanup(glove_serial=SN,sdk_user_id=USER,profile=PROFILE))
                session=17
                if changed=='serial':proof['requested_serial']='OTHER'
                elif changed=='profile':proof['profile']='hand2_left'
                elif changed=='old_session':session=16
                elif changed=='replacement':bridge.action(dict(name='glove_prepare',serial='OTHER',address=''))
                elif changed=='unknown':event['hardware']={'active':None,'stop_confirmed':False}
                elif changed=='device':bridge.state['feedback']={'device_id':'OTHER'}
                elif changed=='lease':bridge.lease='a'*32
                elif changed=='follow':bridge._follow_unconfirmed=True
                elif changed=='missing_hand_ack':event['hand_cleanup_confirmed']=False
                bridge.receive(event,session=session)
                bridge.thread=None;bridge._reader_finished=True;bridge._transport_closed=True
                if changed=='none':
                    self.assertTrue(bridge.snapshot()['session_cleanup']['cleanup_confirmed'])
                    self.assertTrue(bridge.stop_session_confirmed(17,expected_serial=SN,expected_user=USER,expected_profile=PROFILE)['cleanup_confirmed'])
                    self.assertEqual(bridge.state['hardware'],{})
                else:
                    self.assertFalse(bridge.snapshot()['session_cleanup']['cleanup_confirmed'])
                    with self.assertRaises(ValueError):bridge.stop_session_confirmed(17,expected_serial=SN,expected_user=USER,expected_profile=PROFILE)

if __name__=='__main__':unittest.main()
