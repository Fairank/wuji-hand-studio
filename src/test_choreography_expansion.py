"""Real trajectory arithmetic and controller compatibility; no device I/O."""
import io,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from performance_program import DANCES,NEW_DANCE_IDS,PREVIEW_ONLY_IDS,program,segment_peak,sample
from hardware_trial import make_trial
from motion_timing import segment_position
from official_policy import LOWER_RAD,UPPER_RAD
from desktop_tools import dispatch

class ExpansionTests(unittest.TestCase):
    def test_all_distinct_paths_keep_joint_bounds_at_1khz(self):
        signatures=set()
        for action in DANCES:
            p=program(action);self.assertFalse(p['hardware_validated'])
            signatures.add(tuple(round(x,4) for t in (3.1,4.7,7.3) for x in sample(action,t)['q']))
            pts=p['points']
            for a,b in zip(pts,pts[1:]):
                for k in range(10):
                    q=segment_position(a,b,a['t']+k*.001)
                    self.assertTrue(all(lo-1e-9<=v<=hi+1e-9 for v,lo,hi in zip(q,LOWER_RAD,UPPER_RAD)),action)
        self.assertEqual(len(signatures),len(DANCES))

    def test_double_speed_halves_all_time_without_changing_path_gains_or_current(self):
        with patch('hardware_trial.MAX_TRIAL_DURATION_S',10000):
            for action in [*DANCES,'open','text_sequence','official_opposition']:
                if action in PREVIEW_ONLY_IDS:
                    with self.assertRaisesRegex(ValueError,'preview-only'):
                        make_trial([.1]*20,action,.5,1,speed=1.)
                    continue
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

    def test_preview_only_actions_are_rejected_at_hardware_controller_boundary(self):
        from console_server import Controller
        from gesture_library import CATALOG
        preview_id=next(item['id'] for item in CATALOG if item.get('hardware') is False)
        with tempfile.TemporaryDirectory() as tmp:
            c=Controller(reports=Path(tmp)/'reports');c.stdin=io.StringIO()
            c.state.update(connection='connected',metrics=dict(age_ms=0.))
            c.state['device_profile']['generation']='hand2';c.last_update=time.monotonic()
            c.state['hardware'].update(active=False,ready=True,actions=[preview_id],trial_ready=True,
                trial_controls_version=3,gesture_library_version=4)
            # The same dispatch boundary serves direct, group-internal and fleet-child starts.
            for grouped in (False,True):
                c.group.active=grouped;c.group.internal=grouped;c.group.mode='hardware'
                for name in ('hardware_start','hardware_trial'):
                    with self.assertRaisesRegex(ValueError,'Preview-only'):
                        c.action(dict(name=name,action=preview_id,speed=1.,amplitude=.25,cycles=1,workspace_clear=True))
                    self.assertEqual(c.stdin.getvalue(),'')
                    self.assertIsNone(c.hardware_lease)
                    self.assertFalse(c.state['hardware'].get('active'))

    def test_playlist_preview_mode_allows_preview_only_but_hardware_fails_closed(self):
        from gesture_library import CATALOG
        from program_runner import ProgramRunner
        preview_id=next(item['id'] for item in CATALOG if item.get('hardware') is False)
        controller=Mock()
        controller.snapshot.return_value=dict(hardware=dict(active=False),glove=dict(busy=False))
        runner=ProgramRunner(controller)
        plan=dict(entries=[dict(action=preview_id,speed=1.,cycles=1)])
        with self.assertRaisesRegex(ValueError,'Preview-only'):
            runner.start(dict(mode='hardware',plan=plan,workspace_clear=True))
        self.assertIsNone(runner.lease)
        self.assertFalse(runner.snapshot()['active'])
        controller.action.assert_not_called()
        controller.snapshot.assert_not_called()
        with patch('program_runner.threading.Thread') as thread:
            runner.start(dict(mode='preview',plan=plan))
            thread.return_value.start.assert_called_once()
        self.assertTrue(runner.snapshot()['active'])
        controller.action.assert_not_called()

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
