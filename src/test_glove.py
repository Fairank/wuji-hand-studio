import tempfile
import queue
import sys
import numpy as np
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from glove_agent import GloveSource,read_latest,worker_glove
from glove_protocol import validate
from glove_bridge import GloveBridge
from glove_motion import GloveMotion
from glove_view import pose
from test_hardware_showcase import FakeHand,feedback,diagnostics
from hardware_trial import LABELS

def ready(now):
    d=diagnostics(now)
    for j in d['joints']:j.update(state=1,state_name='Ready')
    return d

class GloveTests(unittest.TestCase):
    def test_sdk_glove_preview_side_selection_cleanup_and_no_hand_connect(self):
        for actual in ('left','right'):
            calls=[];user=dict(user_id='user-1',display_name='Test user',is_default=False)
            frames=iter([SimpleNamespace(header=SimpleNamespace(seq=1,timestamp_us=1),joints=[SimpleNamespace(pose=SimpleNamespace(position=[.01,.02,.03])) for _ in range(21)]),None])
            glove=SimpleNamespace(hand_side=lambda:SimpleNamespace(get=lambda:actual),disconnect=lambda:calls.append('disconnect-glove'),
                                  hand_skeleton=lambda:SimpleNamespace(subscribe=lambda:SimpleNamespace(recv=lambda:next(frames,None),close=lambda:None)))
            def connect(**k):calls.append(k['sn']);return glove
            manager=SimpleNamespace(current_user=lambda:user,list_users=lambda:[user],switch_user=lambda u:calls.append('user:'+u),
                scan=lambda:[SimpleNamespace(sn='GLOVE',device_type='glove',address='example'),SimpleNamespace(sn='HAND',device_type='hand',address='example')],connect=connect)
            sdk=SimpleNamespace(SdkManager=SimpleNamespace(instance=lambda:manager),DeviceType=SimpleNamespace(WujiGlove='glove'),
                HandModel=SimpleNamespace(WujiHand2='hand2',WujiHand='hand1'),Handedness=SimpleNamespace(Left='left',Right='right'),
                RetargetSession=SimpleNamespace(for_hand=lambda model,side:SimpleNamespace(step=lambda kp:[0.]*20)))
            requests,events=queue.Queue(),queue.Queue();requests.put(dict(name='glove_open',serial='GLOVE',user_id='user-1',timeout_ms=250));requests.put(dict(name='disconnect'))
            with patch.dict(sys.modules,{'wuji_sdk':sdk}),patch.dict('os.environ',{'WUJI_HAND_PROFILE':'hand2_left'}):worker_glove(requests,events)
            result=[]
            while not events.empty():result.append(events.get())
            self.assertNotIn('HAND',calls);self.assertIn('disconnect-glove',calls,result)
            notices=[e['message'] for e in result if e['type']=='glove_notice']
            self.assertEqual(any('左右' in m for m in notices),actual=='right')
            self.assertEqual(calls[-1],'user:user-1')

    def test_duplicate_reordered_and_old_timestamp_cannot_refresh_target(self):
        s=GloveSource(250);s.update([.1]*20,8,100,1.)
        for seq,stamp in [(8,110),(7,110),(9,99)]:self.assertFalse(s.update([.2]*20,seq,stamp,1.2))
        self.assertEqual(s.target(1.24),[.1]*20)
        with self.assertRaises(ValueError):s.target(1.26)
        self.assertFalse(s.snapshot(1.26)['fresh'])

    def test_thread_timestamp_race_does_not_mark_a_newer_frame_stale(self):
        s=GloveSource();s.update([.1]*20,8,100,1.00001)
        self.assertEqual(s.target(1.),[.1]*20)
        self.assertTrue(s.snapshot(1.)['fresh'])

    def test_nonfinite_output_invalidates_instead_of_being_sent(self):
        s=GloveSource()
        with self.assertRaises(ValueError):s.update([float('nan')]*20,1,1,0)
        s.invalidate('bad frame')
        with self.assertRaisesRegex(ValueError,'bad frame'):s.target(0)

    def test_input_queue_is_drained_and_never_called_live_if_backlogged(self):
        frames=iter([1,2,3,None]);self.assertEqual(read_latest(SimpleNamespace(recv=lambda:next(frames))),3)
        with self.assertRaises(ValueError):read_latest(SimpleNamespace(recv=lambda:1))

    def test_protocol_rejects_pose_injection_shell_text_and_bad_timeout(self):
        for c in [dict(name='glove_open',serial='SN;echo',timeout_ms=250),
                  dict(name='glove_open',serial='SN',timeout_ms=True),
                  dict(name='glove_open',serial='SN',timeout_ms=0),
                  dict(name='glove_follow',workspace_clear=True,q=[0]*20),
                  dict(name='glove_follow',workspace_clear=False),
                  dict(name='glove_prepare',address='a;ls'),dict(name='glove_bad')]:
            with self.subTest(c=c),self.assertRaises(ValueError):validate(c)
        validate(dict(name='glove_open',serial='WUJI-TEST',user_id='user-1',timeout_ms=500))

    def make_driver(self):
        hand=FakeHand();hand.joints=lambda:[SimpleNamespace(label=label,index=i) for i,label in enumerate(LABELS)]
        s=GloveSource(250);s.update([.3]*20,1,1,1.)
        d=GloveMotion(hand,'test',lambda **k:SimpleNamespace(**k),profile_path=Path('missing-test-profile'),source=s)
        return hand,s,d

    def start(self,d):d.start(dict(lease='a'*32,workspace_clear=True),feedback(1.,.01),ready(1.),1.)

    def test_connect_and_preview_never_enable_hand(self):
        hand,s,d=self.make_driver();s.snapshot(1.);d.check_probe_ready(feedback(1.),ready(1.),1.)
        self.assertEqual(hand.calls,[])

    def test_follow_seeds_measured_pose_uses_current_settings_and_no_jump(self):
        from official_policy import KP,KD,CURRENT_LIMIT_A
        hand,s,d=self.make_driver();self.start(d)
        sent=[c[1] for c in hand.calls if c[0]=='send'];self.assertEqual(sent[0],[.01]*20)
        names=[x[0] for x in hand.calls];self.assertLess(names.index('send'),names.index('enable'))
        self.assertEqual(hand.params,(KP,KD));self.assertEqual(hand.cap,CURRENT_LIMIT_A)
        d.tick(feedback(1.01,.01),diagnostics(1.01),1.01)
        d.tick(feedback(1.02,.01),diagnostics(1.02),1.02)
        last=[c[1] for c in hand.calls if c[0]=='send'][-1]
        self.assertLessEqual(max(last),.01+d.run_profile['max_velocity_rad_s']*.02)
        self.assertTrue(d.active);d.stop('test')

    def test_glove_loss_disables_no_send_and_no_automatic_resume(self):
        hand,s,d=self.make_driver();self.start(d)
        count=sum(c[0]=='send' for c in hand.calls)
        d.tick(feedback(1.3),diagnostics(1.3),1.3)
        self.assertFalse(d.active);self.assertIn(('disable',),hand.calls)
        self.assertEqual(count,sum(c[0]=='send' for c in hand.calls))
        s.update([.3]*20,2,2,1.31);d.tick(feedback(1.32),diagnostics(1.32),1.32)
        self.assertFalse(d.active);self.assertEqual(count,sum(c[0]=='send' for c in hand.calls))

    def test_loss_during_parameter_write_does_not_enable(self):
        hand,s,d=self.make_driver()
        d.refresh_before_enable=lambda:(feedback(2.),ready(2.),2.)
        with self.assertRaises(ValueError):self.start(d)
        self.assertFalse(any(c[0]=='enable' for c in hand.calls))

    def test_lease_and_official_fault_stop_while_warning_only_records(self):
        for kind in ('lease','fault','warning'):
            hand,s,d=self.make_driver();self.start(d)
            now=2. if kind=='lease' else 1.02;s.update([.2]*20,2,2,now)
            diag=diagnostics(now)
            if kind!='lease':diag['joints'][0].update(error=33,error_name='Example',severity='Warning' if kind=='warning' else 'ImmediateStop')
            d.tick(feedback(now),diag,now)
            self.assertEqual(d.active,kind=='warning')
            d.stop('test')

    def test_unknown_disable_remains_unconfirmed(self):
        hand,s,d=self.make_driver();self.start(d)
        hand.disable=lambda:(_ for _ in ()).throw(RuntimeError('offline'))
        d.stop('test');self.assertIsNone(d.status()['active']);self.assertFalse(d.status()['stop_confirmed'])

    def test_intended_pose_never_impersonates_missing_measured_feedback(self):
        target=dict(stream=dict(q=[.5]*20,fresh=True))
        q,mode,_=pose(target);self.assertEqual(mode,'glove_preview');self.assertEqual(q,[.5]*20)
        target['feedback']=dict(device_id='test',stale=True,latest=feedback(1.))
        q,mode,_=pose(target);self.assertEqual(mode,'glove_live');self.assertIsNone(q)
        target['feedback']['stale']=False
        self.assertEqual(pose(target)[0],[0.]*20)

    def test_bridge_transport_age_expires_live_indicators(self):
        b=GloveBridge(lambda:None);b.state['stream']=dict(age_ms=1,fresh=True,timeout_ms=250,q=[0]*20)
        b.last_update=time.monotonic()-1
        self.assertFalse(b.snapshot()['stream']['fresh'])

    def test_console_exclusive_modes_and_no_target_api(self):
        from console_server import Controller
        with tempfile.TemporaryDirectory() as td:
            c=Controller(factory=lambda:None,reports=Path(td)/'reports')
            c.glove.thread=SimpleNamespace(is_alive=lambda:True)
            for name in ('connect','device_profile_select','bridge_config_save','parameters_sync','doctor_version','demo_start'):
                with self.subTest(name=name),self.assertRaisesRegex(ValueError,'手套'):c.action(dict(name=name))
            with patch.object(c.glove,'action') as action:
                c.action(dict(name='hardware_stop'))
                action.assert_called_once_with(dict(name='glove_stop'))
            with self.assertRaises(ValueError):c.action(dict(name='glove_follow',q=[0]*20,workspace_clear=True))
            c.glove.thread=None
            self.assertEqual(c.state['connection'],'disconnected')

if __name__=='__main__':unittest.main()
