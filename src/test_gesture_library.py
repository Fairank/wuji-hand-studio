"""No device connection: geometry, timestamps, limits and command whitelist."""
import math
import unittest
import tempfile
import io
import json
import time
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
import gesture_library as g
from hardware_trial import make_trial
from official_policy import LOWER_RAD,UPPER_RAD
from motion_timing import segment_position


class GestureTests(unittest.TestCase):
    def test_catalog_unique_and_24_static_letters(self):
        self.assertEqual(len(g.LETTERS),24)
        self.assertEqual(set(g.LETTERS),set('ABCDEFGHIJKLMNOPQRSTUVWXYZ')-set('JZ'))
        self.assertEqual(len(g.CATALOG),len({x['id'] for x in g.CATALOG}))
        self.assertFalse(g.catalog()['interaction']['hardware_grasp_ready'])

    def test_all_candidate_targets_are_finite_and_documented_range(self):
        for action in g.CUSTOM_IDS:
            for _,q,_ in g.route(action):
                self.assertEqual(len(q),20)
                self.assertTrue(all(math.isfinite(v) and a<=v<=b for v,a,b in zip(q,LOWER_RAD,UPPER_RAD)),action)

    def test_clock_uses_requested_local_hour_and_zero_padding(self):
        at=datetime(2026,9,19,0,7,tzinfo=timezone(timedelta(hours=8)))
        steps=g.route('clock',at=at)
        self.assertEqual([x[0].split(' = ')[-1] for x in steps[::2]],list('0007'))
        self.assertTrue(all('00:07' in x[0] for x in steps[::2]))

    def test_every_real_plan_returns_to_measured_start_and_retains_parameters(self):
        start=[.1]*20
        with patch('hardware_trial.MAX_TRIAL_DURATION_S',100000.):
            for key in g.CUSTOM_IDS:
                plan=make_trial(start,key,.5,1,clock_at='2026-09-19T14:35:00+08:00')
                self.assertEqual(plan['points'][0]['q'],start,key)
                self.assertEqual(plan['points'][-1]['q'],start,key)
                self.assertFalse(plan['physical_contact_verified'])
                for a,b in zip(plan['points'],plan['points'][1:]):
                    self.assertGreater(b['t'],a['t'],key)
                    mid=segment_position(a,b,(a['t']+b['t'])/2)
                    self.assertTrue(all(lo<=q<=hi for q,lo,hi in zip(mid,LOWER_RAD,UPPER_RAD)),key)

    def test_sweep_preserves_cosine_and_other_zero_axes(self):
        samples=g.route('official_sweep')
        self.assertEqual(len(samples),101)
        self.assertAlmostEqual(samples[50][1][0],.02)
        self.assertTrue(all(all(x==0 for x in q[1:]) for _,q,_ in samples))
        p=make_trial([0.]*20,'official_sweep',1.,1)
        middle=[v for v in p['points'] if v.get('interpolation')=='linear']
        self.assertEqual(len(middle),100)

    def test_speed_retimes_without_amplitude_change_and_invalid_id_rejected(self):
        a=make_trial([0.]*20,'digit_1',.25,1,speed=1.)
        b=make_trial([0.]*20,'digit_1',.25,1,speed=.5)
        self.assertAlmostEqual(b['points'][-1]['t'],a['points'][-1]['t']*2)
        self.assertEqual([x['q'] for x in a['points']],[x['q'] for x in b['points']])
        for bad in ('letter_?','digit_99','touch_flow','../../foo'):
            with self.assertRaises(ValueError):make_trial([0.]*20,bad,.25,1)

    def test_preview_clock_freezes_time_per_start(self):
        from demo_player import DemoPlayer
        d=DemoPlayer();d.command(dict(name='demo_start',action='clock',cycles=1,speed=1.))
        old=d.snapshot()['clock_at'];self.assertEqual(d.snapshot()['clock_at'],old)
        self.assertFalse(d.snapshot()['hardware_motion'])
        from demo_player import PERIOD
        d.elapsed=PERIOD['clock'];d.running=False
        end=d.snapshot()
        self.assertLess(end['pose_elapsed_s'],PERIOD['clock'])
        self.assertIn('分隔',end['label'])

    def test_clock_command_uses_server_timestamp_not_browser_supplied_time(self):
        from console_server import Controller
        with tempfile.TemporaryDirectory() as tmp:
            c=Controller(reports=Path(tmp)/'reports');c.stdin=io.StringIO()
            c.state.update(connection='connected',metrics=dict(age_ms=0.))
            c.last_update=time.monotonic();c.state['hardware'].update(active=False,trial_ready=True,trial_controls_version=3,gesture_library_version=1)
            c.action(dict(name='hardware_trial',action='clock',amplitude=.25,speed=1.,cycles=1,workspace_clear=True,clock_at='invalid browser time'))
            sent=json.loads(c.stdin.getvalue())
            self.assertLess(abs(datetime.fromisoformat(sent['clock_at']).timestamp()-time.time()),2.)
            self.assertEqual(sent['action'],'clock')

    def test_new_gestures_require_matching_controller_and_no_raw_grasp_command(self):
        from console_server import Controller
        with tempfile.TemporaryDirectory() as tmp:
            c=Controller(reports=Path(tmp)/'reports');c.stdin=io.StringIO()
            c.state.update(connection='connected',metrics=dict(age_ms=0.))
            c.last_update=time.monotonic();c.state['hardware'].update(active=False,trial_ready=True,trial_controls_version=3)
            for action in ('digit_1','touch_flow'):
                with self.assertRaises(ValueError):c.action(dict(name='hardware_trial',action=action,amplitude=.25,cycles=1,workspace_clear=True))
            self.assertEqual(c.stdin.getvalue(),'')


if __name__=='__main__':unittest.main()
