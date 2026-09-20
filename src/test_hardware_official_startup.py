"""Official commissioning semantics with a synthetic SDK; never moves hardware."""
import math
import hardware_showcase as hw
import unittest
from types import SimpleNamespace
import test_hardware_showcase as fixtures
from test_hardware_showcase import feedback,diagnostics
from hardware_trial import LABELS
from official_policy import POLICY_ID,UPPER_RAD


class OfficialStartupTests(unittest.TestCase):
    setUp=fixtures.HardwareTests.setUp
    tearDown=fixtures.HardwareTests.tearDown
    driver=fixtures.HardwareTests.driver
    ready_diag=fixtures.HardwareTests.ready_diag

    def diag(self,t,code=6,name='BusFrameLossHigh',severity='Warning',enabled=False):
        d=diagnostics(t) if enabled else self.ready_diag(t)
        for j in d['joints']:
            j.update(error=code,error_name=name,severity=severity)
        return d

    def start_commissioning(self,trial=False,refresh_q=.02,refresh_diag=None):
        d=self.driver(False)
        self.hand.joints=lambda:[SimpleNamespace(index=i,label=v) for i,v in enumerate(LABELS)]
        d.refresh_before_enable=lambda:(feedback(1.3,refresh_q),refresh_diag or self.diag(1.3),1.3)
        command=(dict(name='hardware_trial',action='official_opposition',amplitude=.25,cycles=1)
            if trial else dict(name='hardware_probe',index=6,direction=1))
        command.update(workspace_clear=True,lease='a'*32)
        d.command(command,feedback(1.),self.diag(1.),1.)
        return d

    def test_warning_starts_without_stability_timer_and_uses_latest_pose(self):
        for trial in (False,True):
            self.hand.calls=[]
            d=self.start_commissioning(trial)
            self.assertTrue(d.active);self.assertEqual(d.phase,'enabling')
            self.assertFalse(d.comm_healthy)
            names=[c[0] for c in self.hand.calls]
            self.assertLess(names.index('send'),names.index('enable'))
            first=next(c[1] for c in self.hand.calls if c[0]=='send')
            self.assertEqual(first,[.02]*20)
            self.assertEqual(d.points[0]['q'],first)
            self.assertEqual(d.points[-1]['q'],first)
            self.assertEqual(self.hand.cap,.5);self.assertEqual(self.hand.params,(hw.KP,hw.KD))
            self.assertEqual(d.run_profile['control_policy']['id'],POLICY_ID)
            if trial:
                self.assertEqual(d.trial['baseline'],first)
                self.assertEqual(d.trial['peak'],[0.]*20)
            else:
                self.assertEqual(d.probe['initial'],first)
                self.assertAlmostEqual(d.points[1]['q'][6],.02+math.radians(3))
            d.stop('offline test complete')

    def test_sdk_stall_warning_does_not_become_custom_fault(self):
        d=self.start_commissioning(trial=True)
        for k in range(70):
            now=1.32+k*.01
            d.last_beat=now
            row=feedback(now,.02);row['joints'][3]['effort_A']=.56
            diag=self.diag(now,7,'Stall','Warning',enabled=True)
            d.observe_diagnostics(diag,now);d.tick(row,diag,now)
        self.assertTrue(d.active);self.assertIsNone(d.trial['fault'])
        self.assertEqual(d.warnings[0]['code'],7)
        self.assertAlmostEqual(d.trial['peak_current'][3],.56)
        self.assertEqual(self.hand.cap,.5)
        d.stop('offline test complete')

    def test_nonwarning_and_unknown_fault_never_enable(self):
        for name,severity in [('SyntheticFault','DeferredStop'),('SyntheticFault','ImmediateStop'),
                ('SyntheticFault','Fatal'),('Unknown','Warning'),('BusFrameLossHigh',None)]:
            with self.subTest(name=name,severity=severity):
                self.hand.calls=[]
                with self.assertRaises(ValueError):
                    self.start_commissioning(trial=True,refresh_diag=self.diag(1.3,101,name,severity))
                self.assertFalse(any(c[0] in ('send','enable') for c in self.hand.calls))
                self.assertIn(('disable',),self.hand.calls)

    def test_official_stop_fault_during_playback_disables_without_more_commands(self):
        for severity in ('DeferredStop','ImmediateStop','Fatal'):
            self.hand.calls=[];d=self.start_commissioning(trial=True)
            count=sum(c[0]=='send' for c in self.hand.calls)
            diag=self.diag(1.32,101,'SyntheticFault',severity,enabled=True)
            d.tick(feedback(1.32,.02),diag,1.32)
            self.assertFalse(d.active);self.assertFalse(d.trial_result['accepted'])
            self.assertEqual(sum(c[0]=='send' for c in self.hand.calls),count)
            self.assertEqual(d.pending_report['fault']['diagnostics']['joints'][0]['severity'],severity)
            self.assertFalse(d.trial_result['control_policy']['firmware_deferred_stop_emulated'])

    def test_bad_feedback_or_session_end_stops_without_more_commands(self):
        for issue in ('stale','nan','missing','lease','stop'):
            self.hand.calls=[];d=self.start_commissioning(trial=True)
            count=sum(c[0]=='send' for c in self.hand.calls)
            now=2.2 if issue=='lease' else 1.32
            row=feedback(now,.02);diag=self.diag(now,enabled=True)
            if issue=='stale':row['host_s']=0
            if issue=='nan':row['joints'][0]['effort_A']=float('nan')
            if issue=='missing':row['joints'].pop()
            if issue=='stop':d.command(dict(name='hardware_stop'),row,diag,now)
            else:d.tick(row,diag,now)
            self.assertFalse(d.active,issue)
            self.assertEqual(sum(c[0]=='send' for c in self.hand.calls),count,issue)

    def test_rebased_probe_cannot_exceed_documented_joint_range(self):
        d=self.driver(False)
        row=feedback(1.3);row['joints'][6]['position_rad']=UPPER_RAD[6]-.01
        d.refresh_before_enable=lambda:(row,self.diag(1.3),1.3)
        with self.assertRaisesRegex(ValueError,'官方文档'):
            d.command(dict(name='hardware_probe',index=6,direction=1,workspace_clear=True,lease='a'*32),
                feedback(1.),self.diag(1.),1.)
        self.assertFalse(any(c[0] in ('send','enable') for c in self.hand.calls))

    def test_gain_readback_mismatch_never_enables(self):
        self.hand.mit_params=lambda:SimpleNamespace(set=lambda x:None,
            get=lambda:[SimpleNamespace(kp=5.,kd=.05)]*20)
        with self.assertRaisesRegex(ValueError,'增益读回'):self.start_commissioning()
        self.assertFalse(any(c[0] in ('send','enable') for c in self.hand.calls))


if __name__=='__main__':unittest.main()
