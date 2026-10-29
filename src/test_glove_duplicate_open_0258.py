"""An accepted open cannot be duplicated or tear down an existing SDK preview."""
from contextlib import nullcontext
import copy
import os
from pathlib import Path
import queue
import sys
import tempfile
import time
from types import SimpleNamespace as N
import unittest
from unittest.mock import patch

import numpy as np  # Real extension is loaded before the isolated synthetic SDK.
from glove_agent import worker_glove
import test_glove_primary_intent_0258 as primary_fixture
from test_calibration_portability import MODEL,bundle_context

TEMP=Path(__file__).resolve().parents[2]/'update-flow-0258/backend-audit/duplicate-test-temp'
TEMP.mkdir(parents=True,exist_ok=True)


class GloveDuplicateOpenTests(unittest.TestCase):
    def test_pending_old_ready_and_receiving_all_reject_duplicate_before_binding_changes(self):
        base=primary_fixture.GlovePrimaryIntentTests()
        with base.environment() as e:
            e.b.state.update(connection='ready',glove_serial='')
            first=e.c.action(base.command(e,'glove_open'))
            self.assertEqual(first,dict(session_id=44,action_revision=1,open_pending=True))
            saved=e.c.retarget.path.read_bytes()
            for phase in ('pending','old_ready','receiving'):
                with self.subTest(phase=phase):
                    if phase=='old_ready':
                        old=e.base.worker_state();old.update(connection='ready',glove_serial='')
                        e.b.receive(dict(type='glove_state',state=old),session=44)
                    elif phase=='receiving':e.base.ack(e.c)
                    snapshot=e.b.snapshot()
                    self.assertEqual(snapshot['open_pending'],phase!='receiving')
                    self.assertEqual(snapshot['connection'],'receiving' if phase=='receiving' else 'connecting')
                    with self.assertRaisesRegex(ValueError,'already connected'):
                        e.c.action(base.command(e,'glove_open'))
                    self.assertEqual(len(e.base.wires(e.c)),1);self.assertEqual(e.b.action_revision,1)
                    self.assertEqual(e.c.retarget.path.read_bytes(),saved)
            with patch('glove_bridge.threading.Thread',return_value=N(start=lambda:None,is_alive=lambda:False)):
                e.b._begin_session()
            self.assertIsNone(e.b._open_intent);self.assertFalse(e.b.snapshot()['open_pending'])
            self.assertEqual(e.b.action_revision,0)

    def run_worker_duplicate(self,continuation):
        with tempfile.TemporaryDirectory(dir=TEMP) as temporary:
            root=Path(temporary);context=bundle_context(root/'source')
            model=root/'right_hand.urdf';model.write_bytes(MODEL)
            prior={'user_id':'','display_name':'Default','is_default':True}
            wearer={'user_id':'owner-1','display_name':'Alice','is_default':False}
            current=[copy.deepcopy(prior)];calls=[];requests=queue.Queue();snapshots=[];notices=[]
            serial=context['serial'];open_command=dict(name='glove_open',serial=serial,user_id='owner-1',timeout_ms=250)
            flags={'opened':False,'duplicated':False,'closed':False,'seq_before':None,'seq_after':None}
            class Subscription:
                def __init__(self):self.seq=0;self.empty=False
                def recv(self):
                    self.empty=not self.empty
                    if not self.empty:return None
                    self.seq+=1
                    return N(header=N(seq=self.seq,timestamp_us=self.seq*10000),
                        joints=[N(pose=N(position=[0.,0.,float(i)/100])) for i in range(21)])
                def close(self):calls.append(('subscription_close',));return True
            class Glove:
                serial_number=serial
                def hand_side(self):return N(get=lambda:'right')
                def hand_model_path(self):return N(get=lambda:'')
                def hand_skeleton(self):
                    def subscribe():calls.append(('subscribe',));return Subscription()
                    return N(subscribe=subscribe)
                def disconnect(self):calls.append(('glove_close',));return True
            def switch(user):current[0]=copy.deepcopy(wearer if user=='owner-1' else prior);calls.append(('user',user))
            def connect(**values):calls.append(('connect',values));return Glove()
            def scan():calls.append(('scan',));return [N(sn=serial,address='udp/192.0.2.10:50001',device_type='glove')]
            manager=N(current_user=lambda:copy.deepcopy(current[0]),list_users=lambda:[prior,wearer],
                switch_user=switch,scan=scan,connect=connect)
            def solver(*args,**kwargs):
                calls.append(('solver',));return N(step=lambda kp:[0.]*20,close=lambda:calls.append(('solver_close',)) or True)
            sdk=N(SdkManager=N(instance=lambda:manager),DeviceType=N(WujiGlove='glove'),WujiGlove=Glove,
                HandModel=N(WujiHand2='hand2',WujiHand='hand1'),Handedness=N(Left='left',Right='right'),
                RetargetSession=N(for_hand=solver))
            started=time.monotonic()
            class Events:
                def put(self,event):
                    if event['type']=='calibrated_ready':requests.put(copy.deepcopy(open_command));flags['opened']=True
                    if event['type']=='glove_notice':notices.append(event['message'])
                    if event['type']=='glove_state':
                        state=event['state'];snapshots.append(copy.deepcopy(state))
                        if not flags['opened'] and state['connection']=='ready':
                            requests.put(copy.deepcopy(open_command));flags['opened']=True
                        stream=state.get('stream') or {}
                        if state['connection']=='receiving' and stream.get('fresh'):
                            if not flags['duplicated']:
                                flags['seq_before']=stream['seq'];flags['duplicated']=True
                                requests.put(copy.deepcopy(open_command))
                            elif notices and stream['seq']>flags['seq_before']:
                                flags['seq_after']=stream['seq']
                                self_test.assertFalse(any(c[0].endswith('close') for c in calls))
                                self_test.assertEqual(current[0],wearer)
                                flags['closed']=True;requests.put({'name':'glove_disconnect'})
                        if time.monotonic()-started>3:requests.put({'name':'glove_disconnect'})
                    if event['type']=='glove_error':calls.append(('unexpected_error',event['message']))
                    if event['type']=='glove_closed':calls.append(('cleanup',copy.deepcopy(event['session_cleanup'])))
            self_test=self
            receipt=dict(context,owner_user_id='owner-1',model_path=str(model),imported=True)
            with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict(os.environ,WUJI_HAND_PROFILE='hand2_right'), \
                    patch('calibration_portability.prepare_controller_model',return_value=receipt), \
                    patch('sdk_session.ProfileLease',return_value=nullcontext()):
                worker_glove(requests,Events(),context if continuation else None)
            self.assertTrue(flags['closed'],(flags,calls,notices));self.assertGreater(flags['seq_after'],flags['seq_before'])
            self.assertEqual(sum(c[0]=='connect' for c in calls),1);self.assertEqual(sum(c[0]=='subscribe' for c in calls),1)
            self.assertEqual(sum(c[0]=='solver' for c in calls),1);self.assertEqual(sum(c[0]=='glove_close' for c in calls),1)
            self.assertFalse(any(c[0]=='unexpected_error' for c in calls),calls)
            self.assertEqual(len(notices),1);self.assertIn('手套已连接',notices[0]);self.assertEqual(current[0],prior)
            cleanup=next(c[1] for c in calls if c[0]=='cleanup')
            self.assertFalse(cleanup['retryable']);self.assertTrue(cleanup['subscription_closed'])
            self.assertTrue(cleanup['glove_disconnected']);self.assertTrue(cleanup['user_restored'])

    def test_normal_worker_duplicate_notices_and_keeps_fresh_frames_until_explicit_end(self):self.run_worker_duplicate(False)

    def test_calibrated_worker_duplicate_notices_and_keeps_model_and_user_until_explicit_end(self):self.run_worker_duplicate(True)


if __name__=='__main__':unittest.main()
