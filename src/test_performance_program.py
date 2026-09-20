"""Offline regression checks for phrase transport and continuous motion curves."""
import io,json,math,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from performance_program import program,sample,DANCES,segment_peak
from motion_timing import segment_position
from hardware_trial import make_trial
from official_policy import LOWER_RAD,UPPER_RAD


class PerformanceTests(unittest.TestCase):
    def test_phrase_includes_j_spaces_and_repeat_separation(self):
        p=program('text_sequence','wuji tech')
        self.assertEqual(p['text'],'WUJI TECH')
        self.assertEqual({x['token_index'] for x in p['points'] if x['token_index']>=0},set(range(9)))
        self.assertTrue(any('fixed-wrist' in x['label'] for x in p['points']))
        self.assertTrue(any('Word pause' in x['label'] for x in p['points']))
        self.assertTrue(any('Repeat separator' in x['label'] for x in program('text_sequence','BB')['points']))
        self.assertFalse(p['sign_language_certified'])

    def test_loop_seam_and_internal_velocity_continuity(self):
        for action in DANCES:
            p=program(action);pts=p['points']
            self.assertEqual(pts[0]['q'],pts[-1]['q'])
            self.assertEqual(pts[0]['v'],[0.]*20)
            self.assertEqual(pts[-1]['v'],[0.]*20)
            for i in range(1,len(pts)-1,11):
                t=pts[i]['t'];epsilon=1e-6
                a=segment_position(pts[i-1],pts[i],t-epsilon);b=pts[i]['q'];c=segment_position(pts[i],pts[i+1],t+epsilon)
                self.assertLess(max(abs((y-x)/epsilon-(z-y)/epsilon) for x,y,z in zip(a,b,c)),.001)

    def test_retiming_respects_velocity_and_measured_start(self):
        with patch('hardware_trial.MAX_TRIAL_DURATION_S',100000):
            for action in list(DANCES)+['text_sequence','letter_J','letter_Z']:
                p=make_trial([.1]*20,action,.75,1,speed=.5,text='WUJI TECH')
                self.assertEqual(p['points'][0]['q'],[.1]*20)
                self.assertEqual(p['points'][-1]['q'],[.1]*20)
                for a,b in zip(p['points'],p['points'][1:]):
                    self.assertGreater(b['t'],a['t'])
                    self.assertLessEqual(segment_peak(a,b),min(.075,.08)*.5+1e-9)
                    for ratio in (0,.25,.5,.75,1):
                        q=segment_position(a,b,a['t']+(b['t']-a['t'])*ratio)
                        self.assertTrue(all(lo<=x<=hi for x,lo,hi in zip(q,LOWER_RAD,UPPER_RAD)))

    def test_preview_freezes_phrase_and_finishes_without_loop_jump(self):
        from demo_player import DemoPlayer
        with patch('demo_player.time.monotonic',return_value=0):
            p=DemoPlayer();p.command(dict(name='demo_start',action='text_sequence',text='wuji tech',speed=1,cycles=1))
            p.elapsed=p.duration
            state=p.snapshot()
            self.assertFalse(state['running']);self.assertEqual(state['text'],'WUJI TECH')
            self.assertIn('Complete',state['label'])
            with self.assertRaises(ValueError):p.command(dict(name='demo_start',action='text_sequence',text='中文',speed=1,cycles=1))
            self.assertEqual(p.text,'WUJI TECH')

    def test_new_controller_gate_and_normalized_forwarding(self):
        from console_server import Controller
        with tempfile.TemporaryDirectory() as tmp:
            c=Controller(reports=Path(tmp)/'reports');c.stdin=io.StringIO()
            c.state.update(connection='connected',metrics=dict(age_ms=0.));c.state['device_profile']['generation']='hand2'
            c.last_update=time.monotonic();c.state['hardware'].update(active=False,trial_ready=True,trial_controls_version=3,gesture_library_version=1)
            command=dict(name='hardware_trial',action='text_sequence',text=' wuji  tech ',speed=1,amplitude=.25,cycles=1,workspace_clear=True)
            with self.assertRaises(ValueError):c.action(command)
            self.assertEqual(c.stdin.getvalue(),'')
            c.state['hardware']['gesture_library_version']=2;c.action(command)
            self.assertEqual(json.loads(c.stdin.getvalue())['text'],'WUJI TECH')

    def test_export_never_claims_measured_data_or_hardware_validation(self):
        from performance_export import export_program
        import csv
        body=export_program('letter_J','J','hand2_left','csv').decode('utf-8-sig')
        rows=list(csv.reader(io.StringIO(body)))
        self.assertEqual(rows[0][5],'thumb_S1')
        self.assertAlmostEqual(float(rows[2][0])-float(rows[1][0]),.001)
        self.assertEqual(len(rows[-1]),25)
        stamps=[float(row[0]) for row in rows[1:]]
        self.assertTrue(all(b>a for a,b in zip(stamps,stamps[1:])))
        data=export_program('dance_jellyfish','WUJI TECH','hand1_right')
        self.assertTrue(data['hand1_preview_only']);self.assertFalse(data['hardware_validated'])


if __name__=='__main__':unittest.main()
