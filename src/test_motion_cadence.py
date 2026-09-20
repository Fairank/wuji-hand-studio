"""Synthetic timing/trajectory tests. Never imports an actual device client."""
import math
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from motion_timing import CommandCadence,PublishTiming,segment_position,COMMAND_HZ
from hardware_trial import make_trial,LABELS
import test_hardware_showcase as fixtures


class SmoothPlanTests(unittest.TestCase):
    def test_peak_speed_and_no_overshoot_even_when_command_speed_is_slower(self):
        with patch('hardware_trial.PATH_SPEED_RAD_S',.5),patch('hardware_trial.COMMAND_SPEED_RAD_S',.2):
            for name in ('open','fist','opposition','sequence'):
                plan=make_trial([0.]*20,name,.25,1)
                for a,b in zip(plan['points'],plan['points'][1:]):
                    dt=(b['t']-a['t'])/100
                    last=a['q']
                    for i in range(1,101):
                        q=segment_position(a,b,a['t']+i*dt)
                        self.assertLessEqual(max(abs(x-y)/dt for x,y in zip(q,last)),.20000001)
                        self.assertTrue(all(min(x,y)-1e-12<=z<=max(x,y)+1e-12 for x,y,z in zip(a['q'],b['q'],q)))
                        last=q
                    self.assertLess(max(abs(x-y) for x,y in zip(last,b['q'])),1e-12)

    def test_zero_velocity_and_acceleration_at_ends(self):
        a=dict(t=0.,q=[0.]);b=dict(t=1.,q=[1.],interpolation='minimum_jerk')
        e=1e-5
        self.assertLess(segment_position(a,b,e)[0]/e,1e-7)
        self.assertLess((1-segment_position(a,b,1-e)[0])/e,1e-7)
        self.assertLess(abs(segment_position(a,b,2*e)[0]-2*segment_position(a,b,e)[0])/e**2,.001)

    def test_official_interior_is_not_restarted_at_each_knot(self):
        plan=make_trial([0.]*20,'official_opposition',.25,1)
        eased=[b for b in plan['points'] if b.get('interpolation')=='minimum_jerk']
        self.assertEqual(len(eased),2)
        self.assertEqual(plan['points'][0]['q'],plan['points'][-1]['q'])


class CadenceTests(unittest.TestCase):
    def test_selectable_frequencies_do_not_change_elapsed_time(self):
        for hz in (100,250,500,1000):
            c=CommandCadence(0.,hz=hz)
            count=sum(c.due(i*.0001) for i in range(1,10001))
            self.assertEqual(count,hz)

    def test_frequency_setting_validates_and_persists_without_device_io(self):
        from pathlib import Path
        from parameter_file import parse_parameters,render_parameters
        from parameter_store import FIELDS
        source=Path(__file__).with_name('motion_parameters.py').read_text(encoding='utf-8')
        values=parse_parameters(source)
        self.assertEqual(len([f for f in FIELDS if f['key']=='COMMAND_RATE_HZ']),1)
        for hz in (100,250,500,1000):
            actual=parse_parameters(render_parameters(source,dict(values,COMMAND_RATE_HZ=hz)))
            self.assertEqual(actual,dict(values,COMMAND_RATE_HZ=hz))
        for hz in (0,-1,1001,True,float('nan')):
            with self.assertRaises(ValueError):render_parameters(source,dict(values,COMMAND_RATE_HZ=hz))

    def test_one_thousand_deadlines_without_float_loss(self):
        self.assertEqual(COMMAND_HZ,1000)
        c=CommandCadence(1.)
        hits=[i for i in range(1,10001) if c.due(1.+i*.0001)]
        self.assertEqual(hits,list(range(10,10001,10)))
        self.assertEqual(c.missed,0)

    def test_late_callback_skips_slots_and_never_replays_them(self):
        c=CommandCadence(0.)
        self.assertTrue(c.due(.021));self.assertEqual(c.missed,20)
        self.assertFalse(c.due(.021));self.assertFalse(c.due(.0212))
        self.assertTrue(c.due(.022))

    def test_late_callback_does_not_create_sub_half_period_burst(self):
        c=CommandCadence(0.)
        self.assertTrue(c.due(.0019));self.assertFalse(c.due(.002))
        self.assertTrue(c.due(.0029))

    def test_timing_is_measured_not_substituted_with_target(self):
        s=PublishTiming()
        for t in (1.,1.002,1.004):s.add(t,t+.0001)
        r=s.report()
        self.assertEqual(r['target_hz'],1000)
        self.assertAlmostEqual(r['host_publish_hz'],500)
        self.assertAlmostEqual(r['intervals']['max_ms'],2)
        self.assertAlmostEqual(r['sdk_call']['mean_ms'],.1)
        self.assertEqual(r['commands'],3)


class DriverTimingTests(unittest.TestCase):
    setUp=fixtures.HardwareTests.setUp
    tearDown=fixtures.HardwareTests.tearDown
    driver=fixtures.HardwareTests.driver
    ready_diag=fixtures.HardwareTests.ready_diag

    def trial(self):
        d=self.driver(False)
        self.hand.joints=lambda:[SimpleNamespace(index=i,label=x) for i,x in enumerate(LABELS)]
        d.start_trial(dict(action='opposition',amplitude=.25,cycles=1,workspace_clear=True,
            lease='a'*32),fixtures.feedback(1.),self.ready_diag(1.),1.)
        return d

    def test_one_second_thousand_commands_preserves_gains_and_pause(self):
        d=self.trial();initial=len([c for c in self.hand.calls if c[0]=='send'])
        cap=self.hand.cap;params=self.hand.params
        for i in range(1,1001):
            now=1+i*.001;row=fixtures.feedback(now)
            for j,q in zip(row['joints'],d.target):j['position_rad']=q
            d.last_beat=now;d.tick(row,fixtures.diagnostics(now),now)
        self.assertTrue(d.active,d.reason)
        self.assertEqual(len([c for c in self.hand.calls if c[0]=='send'])-initial,1000)
        self.assertEqual(self.hand.cap,cap);self.assertEqual(self.hand.params,params)
        d.command(dict(name='hardware_pause',lease='a'*32),row,fixtures.diagnostics(2.),2.)
        held=d.target[:]
        for i in range(1,101):
            now=2+i*.001;row=fixtures.feedback(now)
            for j,q in zip(row['joints'],held):j['position_rad']=q
            d.last_beat=now;d.tick(row,fixtures.diagnostics(now),now)
            self.assertEqual(d.target,held)
        d.stop('offline test');self.assertTrue(d.pending_report['stop_confirmed'])
        self.assertIn('command_timing',d.pending_report)

    def test_fault_between_publish_deadlines_still_stops_immediately(self):
        d=self.trial();before=d.trial['commands_sent']
        diag=fixtures.diagnostics(1.0005);diag['joints'][0].update(error=123,error_name='Synthetic',severity='ImmediateStop')
        d.tick(fixtures.feedback(1.0005),diag,1.0005)
        self.assertFalse(d.active);self.assertEqual(d.trial['commands_sent'],before)

    def test_short_holds_are_not_scored_before_actual_hold_begins(self):
        with patch('hardware_trial.POSE_HOLD_S',.03):d=self.trial()
        i,b=d.holds[0];start=d.points[i]['t']
        d.elapsed=start-.02;d.phase='playing'
        d.observe_trial(fixtures.feedback(1.),[0.]*20,1.)
        self.assertEqual(d.trial['pose_checks'],[])
        d.elapsed=b['t']-.003
        d.observe_trial(fixtures.feedback(1.1),b['q'],1.1)
        self.assertEqual(len(d.trial['pose_checks']),1)
        d.stop('offline test')


if __name__=='__main__':unittest.main()
