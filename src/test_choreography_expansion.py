"""Real trajectory arithmetic and controller compatibility; no device I/O."""
import io,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from performance_program import DANCES,NEW_DANCE_IDS,program,segment_peak,sample
from hardware_trial import make_trial
from motion_timing import segment_position
from official_policy import LOWER_RAD,UPPER_RAD
from desktop_tools import dispatch

class ExpansionTests(unittest.TestCase):
    def test_nine_distinct_paths_keep_joint_bounds_at_1khz(self):
        signatures=set()
        for action in DANCES:
            p=program(action);self.assertFalse(p['hardware_validated'])
            signatures.add(tuple(round(x,4) for t in (3.1,4.7,7.3) for x in sample(action,t)['q']))
            pts=p['points']
            for a,b in zip(pts,pts[1:]):
                for k in range(10):
                    q=segment_position(a,b,a['t']+k*.001)
                    self.assertTrue(all(lo-1e-9<=v<=hi+1e-9 for v,lo,hi in zip(q,LOWER_RAD,UPPER_RAD)),action)
        self.assertEqual(len(signatures),9)

    def test_double_speed_halves_all_time_without_changing_path_gains_or_current(self):
        with patch('hardware_trial.MAX_TRIAL_DURATION_S',10000):
            for action in [*DANCES,'open','text_sequence','official_opposition']:
                slow=make_trial([.1]*20,action,.5,1,speed=1.)
                fast=make_trial([.1]*20,action,.5,1,speed=2.)
                self.assertEqual(slow['current_limit_A'],fast['current_limit_A'])
                self.assertEqual(slow['control_policy'],fast['control_policy'])
                for a,b in zip(slow['points'],fast['points']):
                    self.assertEqual(a['q'],b['q']);self.assertEqual(a['t'],2*b['t'])
                    if 'v' in a:self.assertEqual([2*x for x in a['v']],b['v'])
                for a,b in zip(fast['points'],fast['points'][1:]):
                    self.assertLessEqual(segment_peak(a,b),fast['max_velocity_rad_s']+1e-8)

    def test_old_controller_rejected_before_any_command_new_version_can_receive(self):
        from console_server import Controller
        for action,speed in [('open',2.),('dance_piano',1.)]:
            with tempfile.TemporaryDirectory() as tmp:
                c=Controller(reports=Path(tmp)/'reports');c.stdin=io.StringIO()
                c.state.update(connection='connected',metrics=dict(age_ms=0.))
                c.state['device_profile']['generation']='hand2';c.last_update=time.monotonic()
                c.state['hardware'].update(active=False,trial_ready=True,trial_controls_version=3,gesture_library_version=2)
                command=dict(name='hardware_trial',action=action,speed=speed,amplitude=.25,cycles=1,workspace_clear=True)
                with self.assertRaisesRegex(ValueError,'动作库版本'):c.action(command)
                self.assertEqual(c.stdin.getvalue(),'');self.assertIsNone(c.hardware_lease)
                c.state['hardware']['gesture_library_version']=4;c.action(command)
                self.assertEqual(json.loads(c.stdin.getvalue())['speed'],speed)

    def test_code_preview_never_controls_active_hardware_or_accepts_raw_values(self):
        host=Mock();host.server.controller.snapshot.return_value=dict(connection='connected')
        host.server.controller.doctor.snapshot.return_value={}
        for payload in [dict(operation='preview',action='dance_piano'),dict(operation='preview',action='hardware_start'),dict(operation='picker',kind='anything')]:
            with self.assertRaises(ValueError):dispatch(host,payload)
        host.preview.assert_not_called();host.picker.assert_not_called()
        host.server.controller.snapshot.return_value=dict(connection='disconnected',hardware=dict(active=False))
        dispatch(host,dict(operation='preview',action='dance_piano',speed=1.5))
        host.preview.assert_called_once_with('dance_piano',1.5)

if __name__=='__main__':unittest.main()
