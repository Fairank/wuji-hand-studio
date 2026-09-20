"""Offline fake-SDK trials; never connects to hardware."""
import math
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from hardware_trial import make_trial,LABELS
import test_hardware_showcase as fixtures
from test_hardware_showcase import feedback,diagnostics


class TrialPlanTests(unittest.TestCase):
    def test_slower_speed_preserves_path_and_current_but_retimes_every_point(self):
        for action in ('open','fist','official_opposition'):
            normal=make_trial([0.]*20,action,.25,1)
            slow=make_trial([0.]*20,action,.25,1,speed=.5)
            self.assertEqual([p['q'] for p in normal['points']],[p['q'] for p in slow['points']])
            self.assertEqual(slow['current_limit_A'],normal['current_limit_A'])
            self.assertEqual(slow['max_velocity_rad_s'],.04)
            for a,b in zip(normal['points'],slow['points']):self.assertEqual(b['t'],2*a['t'])

    def test_invalid_speed_and_duration_still_rejected(self):
        for speed in (0,-1,True,2.1,float('nan'),'0.5'):
            with self.assertRaises(ValueError):make_trial([0.]*20,'open',.25,1,speed=speed)
        with self.assertRaisesRegex(ValueError,'10分钟'):
            make_trial([0.]*20,'official_opposition',.25,3,speed=.25)

    def test_candidates_have_continuous_bounded_cycles_and_no_contact_claim(self):
        for action in ('open','fist','opposition','sequence'):
            for amplitude in (.25,.5,.75,1.):
                for cycles in (1,3):
                    # This case checks path bounds; separate tests check the
                    # configured duration cap. Smooth full-amplitude cycles
                    # can take longer while preserving the same peak velocity.
                    with patch('hardware_trial.MAX_TRIAL_DURATION_S',1800):
                        p=make_trial([0.]*20,action,amplitude,cycles)
                    self.assertEqual(p['points'][0]['q'],p['points'][-1]['q'])
                    self.assertFalse(p['physical_contact_verified'])
                    self.assertEqual(p['current_limit_A'],.5)
                    for a,b in zip(p['points'],p['points'][1:]):
                        self.assertGreater(b['t'],a['t'])
                        self.assertTrue(all(abs(x-y)/(b['t']-a['t'])<=.075001 for x,y in zip(a['q'],b['q'])))

    def test_disallows_arbitrary_action_amplitude_infinite_loop_or_out_of_range_start(self):
        for action,amplitude,cycles in [('bad',.25,1),('fist',True,1),('fist',2.,1),('fist',.25,0),('fist',.25,True)]:
            with self.assertRaises(ValueError):make_trial([0.]*20,action,amplitude,cycles)
        with self.assertRaises(ValueError):make_trial([3.]*20,'fist',.25,1)


class TrialExecutionTests(unittest.TestCase):
    # Reuse only the fake-device fixture, without duplicating its test cases.
    setUp=fixtures.HardwareTests.setUp
    tearDown=fixtures.HardwareTests.tearDown
    driver=fixtures.HardwareTests.driver
    ready_diag=fixtures.HardwareTests.ready_diag
    def start_trial(self,cycles=1,speed=1.):
        d=self.driver(False)
        self.hand.joints=lambda:[SimpleNamespace(index=i,label=label) for i,label in enumerate(LABELS)]
        d.command(dict(name='hardware_trial',action='fist',amplitude=.25,cycles=cycles,speed=speed,
            workspace_clear=True,lease='a'*32),feedback(1.),self.ready_diag(1.),1.)
        return d

    def run_fake(self,d,respond=True,current=0.):
        q=[0.]*20
        steps=math.ceil((d.period*d.cycles+4)/.025)
        for step in range(1,steps):
            now=1+step*.025;row=feedback(now)
            for j,x in zip(row['joints'],q):j['position_rad']=x;j['effort_A']=current
            diag=diagnostics(now)
            d.command(dict(name='hardware_keepalive',lease='a'*32),row,diag,now)
            d.tick(row,diag,now)
            if not d.active:break
            if respond:q=next(c[1] for c in reversed(self.hand.calls) if c[0]=='send')[:]

    def test_full_trial_counts_only_measured_poses_and_return(self):
        d=self.start_trial(3);self.run_fake(d)
        r=d.trial_result
        self.assertFalse(d.active);self.assertTrue(r['accepted'],r)
        self.assertEqual(r['finished_cycles'],3)
        self.assertEqual(len(r['pose_checks']),12)
        self.assertFalse(r['physical_contact_verified']);self.assertFalse(r['full_action_calibrated'])
        self.assertTrue(r['stop_confirmed']);self.assertGreater(r['commands_sent'],10)
        sent=[c[1] for c in self.hand.calls if c[0]=='send']
        self.assertEqual(sent[0],[0.]*20)
        self.assertTrue(all(abs(x-y)<=.002001 for a,b in zip(sent,sent[1:]) for x,y in zip(a,b)))

    def test_no_motion_is_recorded_as_failed_acceptance_without_inventing_device_fault(self):
        d=self.start_trial();self.run_fake(d,respond=False,current=.21)
        self.assertFalse(d.trial_result['accepted']);self.assertNotIn('受阻',d.reason)
        self.assertTrue(d.trial_result['completed']);self.assertIsNone(d.pending_report['fault'])
        self.assertEqual(self.hand.cap,.5)
        self.assertIn(('disable',),self.hand.calls)

    def test_slow_execution_reaches_real_feedback_checks_and_records_speed(self):
        d=self.start_trial(speed=.5);self.run_fake(d)
        self.assertTrue(d.trial_result['accepted'],d.trial_result)
        self.assertEqual(d.trial_result['speed_factor'],.5)
        self.assertEqual(d.pending_report['control']['max_velocity_rad_s'],.04)
        self.assertEqual(d.status()['trial_controls_version'],3)

    def test_trial_stops_on_lost_browser_lease(self):
        d=self.start_trial();count=sum(c[0]=='send' for c in self.hand.calls)
        d.tick(feedback(2.),diagnostics(2.),2.)
        self.assertFalse(d.active);self.assertFalse(d.trial_result['accepted'])
        self.assertEqual(sum(c[0]=='send' for c in self.hand.calls),count)

    def test_wrong_sdk_labels_never_enable(self):
        d=self.driver(False);self.hand.joints=lambda:[]
        with self.assertRaises(ValueError):
            d.command(dict(name='hardware_trial',action='fist',amplitude=.25,cycles=1,
                workspace_clear=True,lease='a'*32),feedback(1.),self.ready_diag(1.),1.)
        self.assertEqual(self.hand.calls,[])

    def test_small_error_with_current_is_measured_without_custom_250ms_stop(self):
        d=self.start_trial();d.phase='playing';d.target=[0.]*20;d.target[3]=.03
        for k in range(28):
            now=1.1+k*.01;row=feedback(now);row['joints'][3]['effort_A']=.24
            row['seq']=k;row['device_timestamp_us']=100000+k*10000
            d.last_beat=now
            # Observe at a constant small offset below the former 0.035 rad gate.
            d.capture_observation(row,diagnostics(now),now)
            try:d.observe_trial(row,[0.]*20,now)
            except ValueError as e:
                d.trial['fault']=d.latest_observation;d.stop(str(e));break
        self.assertTrue(d.active);self.assertIsNone(d.trial['fault'])
        self.assertAlmostEqual(d.trial['peak_current'][3],.24)
        d.stop('用户停止')
        self.assertIsNone(d.trial_result['return_error_deg'])
        self.assertFalse(d.trial_result['accepted'])
        self.assertEqual(self.hand.cap,.5)

    def test_moving_joint_does_not_trip_no_progress_guard(self):
        d=self.start_trial();d.phase='playing'
        for k in range(70):
            now=1.1+k*.01;q=[0.]*20;q[3]=k*.0002
            row=feedback(now);row['joints'][3].update(position_rad=q[3],effort_A=.24)
            d.target=q[:];d.target[3]+=.03
            d.observe_trial(row,q,now)
        self.assertTrue(d.active)

    def test_alternating_joint_errors_do_not_share_one_timer(self):
        d=self.start_trial();d.phase='playing'
        for k in range(100):
            now=1.1+k*.01;d.target=[0.]*20;d.target[(k//10)%2]=.11
            d.observe_trial(feedback(now),[0.]*20,now)
        self.assertTrue(d.active)

    def test_hardware_fault_keeps_exact_feedback_and_diagnostic_frame(self):
        d=self.start_trial();row=feedback(1.02);row.update(seq=123,device_timestamp_us=456000)
        row['joints'][3]['position_rad']=.005
        diag=diagnostics(1.02);diag['joints'][3]['error']=7
        d.tick(row,diag,1.02)
        self.assertFalse(d.active);self.assertFalse(d.trial_result['accepted'])
        self.assertEqual(d.pending_report['fault']['feedback']['seq'],123)
        self.assertEqual(d.pending_report['fault']['diagnostics']['joints'][3]['error'],7)
        self.assertIsNone(d.trial_result['return_error_deg'])

    def test_recorded_motion_alone_does_not_fabricate_official_fault(self):
        path=Path(__file__).with_name('motion_reports')/'42a34b9137c349e29066ac92c8fea141.json'
        if not path.exists():self.skipTest('Original local physical record is not deployed with unit fixtures')
        report=json.loads(path.read_text(encoding='utf-8'))
        d=self.start_trial();d.phase='playing';d.trial['baseline']=report['trace'][0]['target'][:]
        stopped=None
        for item in report['trace']:
            now=1.+item['t'];row=feedback(now);d.target=item['target'][:]
            for j,q,current in zip(row['joints'],item['q'],item['current_A']):
                j.update(position_rad=q,effort_A=current)
            try:d.observe_trial(row,item['q'],now)
            except ValueError as error:stopped=(item['t'],str(error));break
        self.assertIsNone(stopped)
        self.assertTrue(d.active);self.assertIsNone(d.trial['fault'])
        self.assertGreater(d.trial['peak_current'][3],0)
        d.stop('历史轨迹离线记录结束')
        self.assertEqual(self.hand.cap,.5)


if __name__=='__main__':unittest.main()
