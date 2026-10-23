"""Late hand discovery on one retained SDK glove; all transport is synthetic."""
import copy
import hashlib
import io
import json
import queue
import sys
import threading
import time
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

import numpy as np
from glove_agent import GloveSource,scan_preview_hands,worker_glove
from glove_bridge import GloveBridge
from glove_protocol import validate
from test_sdk_lifecycle import proof_fixture


class GloveHandScanTests(unittest.TestCase):
    def bridge(self):
        b=GloveBridge(Mock());b.thread=N(is_alive=lambda:True);b.stdin=io.StringIO();b.session=7
        b._sdk_proof=proof_fixture();b.last_update=time.monotonic()
        b.state.update(connection='receiving',profile='hand2_left',glove_serial='WG-A',
            user={'user_id':'Alice'},can_prepare=True,model_provenance={'model_sha256':'a'*64},
            stream=dict(fresh=True,age_ms=0,timeout_ms=250,solver=dict(engine='sdk',applied=True)),
            hardware={},feedback={})
        return b

    def test_zero_argument_protocol_rejects_hidden_device_or_configuration_selection(self):
        self.assertEqual(validate({'name':'glove_hand_scan'}),{'name':'glove_hand_scan'})
        for key,value in [('serial','WH-A'),('address','example'),('user_id','Other'),('solver',{'engine':'sdk'}),('q',[0]*20),('retarget',{})]:
            with self.subTest(key=key),self.assertRaises(ValueError):validate(dict(name='glove_hand_scan',**{key:value}))

    def test_current_unpaired_sdk_preview_sends_only_scan_and_keeps_session(self):
        b=self.bridge();original=copy.deepcopy(b.state);session=b.session
        b.action({'name':'glove_hand_scan'})
        self.assertEqual(json.loads(b.stdin.getvalue()),{'name':'glove_hand_scan'})
        self.assertEqual(b.session,session);self.assertEqual(b.state,original);self.assertIsNone(b.lease)

    def test_paired_following_cleanup_unverified_and_non_sdk_states_never_send_scan(self):
        def mutations():
            return [lambda b:setattr(b,'stopping',True),lambda b:setattr(b,'lease','b'*32),
                lambda b:setattr(b,'_follow_unconfirmed',True),lambda b:setattr(b,'_unpair_pending',True),
                lambda b:b.state.update(connection='ready'),lambda b:b.state.update(can_prepare=False),
                lambda b:b.state.update(hand_unpairing=True),lambda b:b.state['feedback'].update(device_id='WH-A'),
                lambda b:b.state['hardware'].update(active=True),lambda b:b.state['hardware'].update(active=None),
                lambda b:b.state['hardware'].update(active=False,stop_confirmed=False),
                lambda b:b.state['stream'].update(fresh=False),lambda b:b.state['stream']['solver'].update(engine='official_open'),
                lambda b:setattr(b,'_sdk_proof',None),lambda b:setattr(b,'thread',None)]
        for mutate in mutations():
            with self.subTest(mutation=mutate):
                b=self.bridge();mutate(b)
                with self.assertRaises(ValueError):b.action({'name':'glove_hand_scan'})
                self.assertEqual(b.stdin.getvalue(),'')

    def test_hand_advertisements_refresh_without_glove_discovery_or_connect(self):
        source=GloveSource();source.update([0.]*20,1,1,1.)
        manager=Mock();manager.scan.return_value=[N(sn='WH-A',address='zenoh://WH-A',device_type='DeviceType.WujiHand2'),N(sn='WG-A',address='example',device_type='DeviceType.WujiGlove')]
        with patch('glove_discovery.scan_gloves',side_effect=AssertionError('No glove discovery here')):
            found=scan_preview_hands(manager,object(),'receiving','sdk',source,1.1,None,{},False)
        self.assertEqual([d['serial'] for d in found],['WH-A']);manager.scan.assert_called_once_with();manager.connect.assert_not_called()

    def test_worker_scan_gate_rejects_hand_worker_feedback_unknown_stop_and_stale_source(self):
        source=GloveSource();source.update([0.]*20,1,1,1.)
        base=dict(glove=object(),connection='receiving',solver_engine='sdk',source=source,now=1.1,hand_thread=None,hand_state={},hand_unpairing=False)
        for delta in [dict(glove=None),dict(connection='ready'),dict(solver_engine='official_open'),dict(now=2.),
                dict(hand_thread=N(is_alive=lambda:True)),dict(hand_thread=N(is_alive=lambda:False),hand_state={}),
                dict(hand_state={'device_id':'WH-A'}),dict(hand_state={'hardware':{'active':None}}),
                dict(hand_state={'hardware':{'active':False,'stop_confirmed':False}}),dict(hand_unpairing=True)]:
            with self.subTest(delta=delta):
                manager=Mock()
                with self.assertRaises(ValueError):scan_preview_hands(manager,**dict(base,**delta))
                manager.scan.assert_not_called()

    def test_failed_hand_read_can_retry_without_replacing_source(self):
        source=GloveSource();source.update([.1]*20,1,1,1.);glove=object();manager=Mock()
        manager.scan.side_effect=[RuntimeError('Synthetic search failed'),[N(sn='WH-A',address='example',device_type='DeviceType.WujiHand2')]]
        before=source.snapshot(1.1)
        with self.assertRaisesRegex(RuntimeError,'Synthetic search failed'):
            scan_preview_hands(manager,glove,'receiving','sdk',source,1.1,None,{},False)
        found=scan_preview_hands(manager,glove,'receiving','sdk',source,1.1,None,{},False)
        self.assertEqual(found[0]['serial'],'WH-A');self.assertEqual(source.snapshot(1.1),before)

    def test_stale_task_and_same_serial_replacement_epoch_reject_before_bridge_write(self):
        from task_view import scope,execute
        b=self.bridge()
        class Owner:
            calibration_handover=N(workflow_lock=threading.RLock())
            def snapshot(self):return dict(connection='disconnected',hardware={'active':False},session_generation=1,device_profile=dict(id='hand2_left',generation='hand2',side='left'),glove=b.snapshot(),official_viz={},calibration={})
            def action(self,c):return b.action(c)
        c=Owner();task=hashlib.sha256(b'glove:WG-A').hexdigest()[:24];old=scope(c,task)['owner_context'];b.session+=1
        with self.assertRaisesRegex(ValueError,'changed'):execute(c,task,old,dict(name='glove_hand_scan'))
        self.assertEqual(b.stdin.getvalue(),'')
        current=scope(c,task)['owner_context'];execute(c,task,current,dict(name='glove_hand_scan'))
        self.assertEqual(json.loads(b.stdin.getvalue()),{'name':'glove_hand_scan'})

    def test_asynchronous_search_failure_belongs_only_to_the_current_reader_epoch(self):
        b=self.bridge();event=dict(type='glove_state',state=dict(copy.deepcopy(b.state),hand_scan_error='SYNTHETIC current search failed'))
        b.receive(event,session=b.session-1)
        self.assertNotIn('hand_scan_error',b.snapshot())
        b.receive(event,session=b.session)
        self.assertEqual(b.snapshot()['hand_scan_error'],'SYNTHETIC current search failed')
        self.assertEqual(b.snapshot()['connection'],'receiving');self.assertIsNone(b.lease)
        event['state']['hand_scan_error']='';b.receive(event,session=b.session)
        self.assertEqual(b.snapshot()['hand_scan_error'],'')
        self.assertEqual(b.stdin.getvalue(),'')

    def test_retained_glove_search_failure_retry_exact_prepare_follow_and_stop(self):
        commands=queue.Queue();out=[];calls=[];current=[dict(user_id='Prior')];loaded=[None];phase=[0];scans=[0];seq=[0];alternate=[False]
        user=dict(user_id='Alice');glove_ad=N(sn='WG-A',address='example',device_type='DeviceType.WujiGlove');hand_ad=N(sn='WH-A',address='example',device_type='DeviceType.WujiHand2')
        def scan():
            scans[0]+=1;calls.append(('scan',scans[0],current[0]['user_id']))
            if scans[0]==2:raise RuntimeError('SYNTHETIC hand search failed')
            return [glove_ad] if scans[0]==1 else [glove_ad,hand_ad]
        def recv():
            alternate[0]=not alternate[0]
            if not alternate[0]:return None
            seq[0]+=1
            return N(header=N(seq=seq[0],timestamp_us=seq[0]*1000),joints=[N(pose=N(position=[.01,.02,.03])) for _ in range(21)])
        glove=N(serial_number='WG-A',hand_side=lambda:N(get=lambda:'left'),disconnect=lambda:calls.append(('glove-close',loaded[0])),hand_skeleton=lambda:N(subscribe=lambda:N(recv=recv,close=lambda:None)))
        hand=N(serial_number='WH-A',handedness=lambda:N(get=lambda:'left'),disconnect=lambda:calls.append(('hand-close',)))
        def connect(**kw):
            calls.append(('connect',kw['sn'],current[0]['user_id']))
            if kw['sn']=='WG-A':loaded[0]=current[0]['user_id'];return glove
            self.assertEqual(kw['sn'],'WH-A');return hand
        def switch(uid):current[0]=user if uid=='Alice' else dict(user_id='Prior');calls.append(('switch',uid))
        solver=N(step=lambda kp:[.1]*20,close=lambda:calls.append(('solver-close',)))
        def new_solver(*a,**kw):calls.append(('solver-new',));return solver
        manager=N(scan=scan,connect=connect,current_user=lambda:current[0],list_users=lambda:[current[0],user],switch_user=switch)
        sdk=N(SdkManager=N(instance=lambda:manager),DeviceType=N(WujiGlove='DeviceType.WujiGlove'),HandModel=N(WujiHand2='hand2',WujiHand='hand1'),Handedness=N(Left='left',Right='right'),RetargetSession=N(for_hand=new_solver))
        class Events:
            def put(self,event):
                out.append(copy.deepcopy(event));kind=event.get('type')
                if kind=='glove_notice' and 'SYNTHETIC hand search failed' in event['message']:
                    self_test.assertEqual(current[0]['user_id'],'Alice');self_test.assertEqual(loaded[0],'Alice');phase[0]=1.5
                if kind!='glove_state':return
                s=event['state']
                if phase[0]==0 and s['connection']=='receiving' and s['stream']['fresh']:
                    self_test.assertEqual(s['hands'],[]);phase[0]=1;commands.put({'name':'glove_hand_scan'})
                elif phase[0]==1.5 and s.get('hand_scan_error'):
                    self_test.assertIn('SYNTHETIC hand search failed',s['hand_scan_error']);self_test.assertEqual(s['hands'],[]);phase[0]=2;commands.put({'name':'glove_hand_scan'})
                elif phase[0]==2 and s['hands']:
                    self_test.assertEqual(s['hand_scan_error'],'');self_test.assertEqual([x['serial'] for x in s['hands']],['WH-A']);phase[0]=3;commands.put(dict(name='glove_prepare',serial='WH-A',address=''))
                elif phase[0]==3 and s['feedback'].get('device_id')=='WH-A' and s['hardware'].get('active') is False:
                    phase[0]=4;commands.put(dict(name='glove_follow',lease='b'*32,workspace_clear=True))
                elif phase[0]==4 and s['hardware'].get('active') is True:
                    phase[0]=5;commands.put({'name':'glove_hand_scan'});commands.put({'name':'glove_stop'})
                elif phase[0]==5 and s['hardware'].get('active') is False:
                    phase[0]=6;commands.put({'name':'glove_disconnect'})
        def hand_worker(address,requests,events,factory,prepared=None):
            self.assertIs(prepared[0],hand);calls.append(('prepared',prepared[0].serial_number));active=False
            def emit():events.put(dict(type='state',connection='connected',device_id='WH-A',hardware=dict(active=active,stop_confirmed=not active,probe_ready=True),metrics=dict(age_ms=0)))
            emit()
            while True:
                command=requests.get(timeout=2);calls.append(('hand-command',command['name']))
                if command['name']=='disconnect':events.put(dict(type='closed',hardware=dict(active=False,stop_confirmed=True)));return
                if command['name']=='hardware_start':active=True
                elif command['name']=='hardware_stop':active=False
                emit()
        self_test=self;commands.put(dict(name='glove_open',serial='WG-A',user_id='Alice',timeout_ms=250));timed_out=threading.Event()
        def abort_test():timed_out.set();commands.put({'name':'glove_disconnect'})
        watchdog=threading.Timer(3.,abort_test);watchdog.daemon=True;watchdog.start()
        try:
            with patch.dict(sys.modules,{'wuji_sdk':sdk}),patch.dict('os.environ',{'WUJI_HAND_PROFILE':'hand2_left'}),patch('zenoh_route.managed_wsl',return_value=False),patch('device_discovery.managed_wsl',return_value=False),patch('console_agent.worker',side_effect=hand_worker):
                worker_glove(commands,Events())
        finally:watchdog.cancel()
        self.assertFalse(timed_out.is_set(),'Synthetic worker task did not reach its explicit disconnect endpoint')
        self.assertEqual(phase[0],6);self.assertEqual([x for x in calls if x[0]=='connect'],[('connect','WG-A','Alice'),('connect','WH-A','Alice')]);self.assertEqual(calls.count(('solver-new',)),1)
        self.assertEqual([x for x in calls if x[0]=='switch'],[('switch','Alice'),('switch','Prior')]);self.assertEqual(scans[0],4)
        self.assertIn(('hand-command','hardware_start'),calls);self.assertIn(('hand-command','hardware_stop'),calls)
        self.assertTrue(any(e['type']=='glove_notice' and 'still running' in e['message'] for e in out))
        snapshots=[e['state'] for e in out if e['type']=='glove_state' and e['state']['connection']=='receiving']
        self.assertTrue(snapshots);self.assertTrue(all(s['user']['user_id']=='Alice' and s['stream']['solver']['engine']=='sdk' for s in snapshots))


if __name__=='__main__':unittest.main()
