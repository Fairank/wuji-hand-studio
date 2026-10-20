"""Model the official switch_user reload contract; no SDK is imported."""
import queue
import sys
import unittest
from types import SimpleNamespace as N
from unittest.mock import patch
from glove_agent import worker_glove


class GloveUserLifetimeTests(unittest.TestCase):
    def test_selected_profile_stays_loaded_until_glove_disconnect(self):
        a=dict(user_id='A');b=dict(user_id='B');current=[a];connected=[False];loaded=[None];calls=[]
        def switch(user):
            current[0]=b if user=='B' else a
            if connected[0]:loaded[0]=user
            calls.append(('switch',user))
        def connect(**_):
            connected[0]=True;loaded[0]=current[0]['user_id'];calls.append(('connect',loaded[0]));return glove
        def recv():calls.append(('recv',loaded[0]));return None
        def disconnect():calls.append(('disconnect',loaded[0]));connected[0]=False
        glove=N(serial_number='GLOVE',hand_side=lambda:N(get=lambda:'left'),disconnect=disconnect,
                hand_skeleton=lambda:N(subscribe=lambda:N(recv=recv,close=lambda:calls.append(('sub-close',loaded[0])))))
        manager=N(current_user=lambda:current[0],list_users=lambda:[a,b],switch_user=switch,
                  scan=lambda:[N(sn='GLOVE',device_type='glove',address='example')],connect=connect)
        sdk=N(SdkManager=N(instance=lambda:manager),DeviceType=N(WujiGlove='glove'),
              HandModel=N(WujiHand2='hand2',WujiHand='hand1'),Handedness=N(Left='left',Right='right'),
              RetargetSession=N(for_hand=lambda *args,**kwargs:N(step=lambda _: [0.]*20)))
        requests=queue.Queue();events=queue.Queue()
        requests.put(dict(name='glove_open',serial='GLOVE',user_id='B',timeout_ms=250));requests.put(dict(name='disconnect'))
        with patch.dict(sys.modules,{'wuji_sdk':sdk}),patch.dict('os.environ',{'WUJI_HAND_PROFILE':'hand2_left'}):worker_glove(requests,events)
        snapshots=[]
        while not events.empty():
            event=events.get()
            if event['type']=='glove_state':snapshots.append(event['state'])
        self.assertTrue(any(s['user']['user_id']=='B' and s['connection']=='receiving' for s in snapshots))
        self.assertIn(('recv','B'),calls);self.assertIn(('disconnect','B'),calls)
        self.assertLess(calls.index(('disconnect','B')),calls.index(('switch','A')))
        self.assertEqual(current[0]['user_id'],'A')


if __name__=='__main__':unittest.main()
