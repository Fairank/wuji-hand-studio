"""Successful hand advertisements own a timestamp separate from glove lists."""
import copy
import queue
import sys
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from device_inventory import build_inventory
from glove_agent import worker_glove
from glove_bridge import GloveBridge


class HandObservationClockTests(unittest.TestCase):
    def run_worker(self,second=None):
        wall=[1000.];calls=[];commands=queue.Queue();captures=[];phase=[0];scans=[0];seq=[0];alternate=[False]
        user={'user_id':'Wearer','display_name':'Wearer','is_default':False};previous={'user_id':'Prior'};current=[previous]
        hand='WH2JA00000000000';target='WG-TARGET';other='WG-OTHER'
        ads=[N(sn=value,address='fixture-'+value,device_type='DeviceType.WujiGlove') for value in (target,other)]
        hand_ad=N(sn=hand,address='udp/192.0.2.60:50001',device_type='DeviceType.WujiHand2')
        def scan():
            scans[0]+=1;calls.append(('scan',scans[0],wall[0]))
            if scans[0]==1:return ads
            if scans[0]==3 and second=='failed':raise RuntimeError('Synthetic later hand scan failed')
            return ads if scans[0]==3 and second=='empty' else ads+[hand_ad]
        def recv():
            alternate[0]=not alternate[0]
            if not alternate[0]:return None
            seq[0]+=1
            return N(header=N(seq=seq[0],timestamp_us=seq[0]*1000),joints=[N(pose=N(position=[0.,0.,.02])) for _ in range(21)])
        glove=N(serial_number=target,info=N(serial_number=target,firmware_version='1.0.4'),
            hand_side=lambda:N(get=lambda:'left'),disconnect=lambda:calls.append(('glove-close',)) or True,
            hand_skeleton=lambda:N(subscribe=lambda:N(recv=recv,close=lambda:calls.append(('sub-close',)) or True)))
        def connect(**kwargs):
            self.assertEqual(kwargs,{'sn':target,'device_name':'studio_glove'})
            calls.append(('connect-glove',target));return glove
        def switch(uid):current[0]=copy.deepcopy(user if uid=='Wearer' else previous)
        manager=N(scan=scan,connect=connect,current_user=lambda:copy.deepcopy(current[0]),list_users=lambda:[previous,user],switch_user=switch)
        sdk=N(SdkManager=N(instance=lambda:manager),DeviceType=N(WujiGlove='DeviceType.WujiGlove'),
            HandModel=N(WujiHand2='hand2',WujiHand='hand1'),Handedness=N(Left='left',Right='right'),
            RetargetSession=N(for_hand=lambda *a,**kw:N(step=lambda keypoints:[.1]*20,close=lambda:True)))
        bridge=GloveBridge(Mock());bridge.session=7;bridge.thread=N(is_alive=lambda:True)
        class Events:
            def put(self,event):
                if event['type']!='glove_state':return
                row=event['state']
                if row.get('connection')!='receiving' or not row.get('stream',{}).get('fresh'):return
                if phase[0]==0:
                    phase[0]=1;wall[0]=1061.;commands.put({'name':'glove_hand_scan'});return
                if phase[0]==1 and scans[0]>=2 or phase[0]==2 and scans[0]>=3:
                    bridge.receive(event,session=7);snapshot=bridge.snapshot()
                    inventory=build_inventory(glove=snapshot,now=wall[0])
                    captures.append(dict(state=copy.deepcopy(snapshot),inventory=inventory,now=wall[0]))
                    if phase[0]==1 and second:
                        phase[0]=2;wall[0]=1092.;commands.put({'name':'glove_hand_scan'})
                    else:phase[0]=3;commands.put({'name':'glove_disconnect'})
        commands.put(dict(name='glove_open',serial=target,user_id='Wearer',timeout_ms=250))
        timed_out=threading.Event()
        def stop():timed_out.set();commands.put({'name':'glove_disconnect'})
        timer=threading.Timer(3.,stop);timer.daemon=True;timer.start()
        try:
            with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict('os.environ',WUJI_HAND_PROFILE='hand2_left'), \
                    patch('glove_agent.time.time',side_effect=lambda:wall[0]),patch('zenoh_route.managed_wsl',return_value=False):
                worker_glove(commands,Events())
        finally:timer.cancel()
        self.assertFalse(timed_out.is_set());self.assertEqual(phase[0],3)
        self.assertEqual([call for call in calls if call[0]=='connect-glove'],[('connect-glove',target)])
        self.assertEqual(current[0],previous)
        return N(captures=captures,calls=calls,hand=hand,other=other)

    def test_successful_hand_scan_after_long_glove_preview_remains_fresh_in_canonical_inventory(self):
        result=self.run_worker();seen=result.captures[0]
        hand=next(row for row in seen['inventory']['devices'] if row['sn']==result.hand)
        self.assertFalse(hand['stale']);self.assertTrue(hand['selectable'])
        self.assertEqual(hand['observed_at'],1061.)
        self.assertEqual(seen['state']['devices_at'],1000.)

    def test_hand_scan_does_not_renew_an_old_unselected_glove_advertisement(self):
        result=self.run_worker();seen=result.captures[0]
        other=next(row for row in seen['inventory']['devices'] if row['sn']==result.other)
        self.assertTrue(other['stale']);self.assertFalse(other['selectable']);self.assertEqual(other['observed_at'],1000.)

    def test_failed_scan_keeps_prior_hand_observation_age_without_renewing_it(self):
        result=self.run_worker('failed');before,after=result.captures
        original=next(row for row in before['inventory']['devices'] if row['sn']==result.hand)
        old=next(row for row in after['inventory']['devices'] if row['sn']==result.hand)
        self.assertEqual(original['observed_at'],1061.)
        self.assertEqual(old['observed_at'],1061.);self.assertTrue(old['stale']);self.assertFalse(old['selectable'])
        self.assertIn('Synthetic later hand scan failed',after['state']['hand_scan_error'])

    def test_successful_empty_hand_scan_removes_prior_hand_without_renewing_gloves(self):
        result=self.run_worker('empty');before,after=result.captures
        self.assertTrue(any(row['sn']==result.hand for row in before['inventory']['devices']))
        self.assertEqual(after['state']['hands'],[])
        self.assertFalse(any(row['sn']==result.hand for row in after['inventory']['devices']))
        self.assertEqual(after['state']['devices_at'],1000.)

    def test_legacy_worker_without_hand_clock_retains_shared_clock_compatibility(self):
        hand=dict(serial='WH2JA00000000000',generation='hand2',side_hint='left')
        result=build_inventory(glove=dict(hands=[hand],devices_at=1061.),now=1062.)
        row=result['devices'][0]
        self.assertEqual(row['observed_at'],1061.);self.assertFalse(row['stale'])

    def test_explicit_unknown_hand_clock_does_not_borrow_a_fresh_glove_clock(self):
        hand=dict(serial='WH2JA00000000000',generation='hand2',side_hint='left')
        result=build_inventory(glove=dict(hands=[hand],hands_at=None,devices_at=1061.),now=1062.)
        row=result['devices'][0]
        self.assertIsNone(row['observed_at']);self.assertTrue(row['stale']);self.assertFalse(row['selectable'])


if __name__=='__main__':unittest.main()
