import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import queue
import sys

from console_agent import Recorder, validate_command, worker
from console_server import Controller
from joint_stats import JointRates
from pose_view import validate_mapping


def frame(seq, nids=(1, 2)):
    return dict(seq=seq, host_s=seq/1000, device_timestamp_us=seq*1000,
                joints=[dict(nid=nid, position_rad=.1, velocity_rad_s=0., effort_A=.01) for nid in nids])


class ConsoleTests(unittest.TestCase):
    def test_no_motor_commands(self):
        for name in ('enable', 'disable', 'motion', 'joint_command', 'emergency_stop'):
            with self.assertRaises(ValueError):
                validate_command(dict(name=name))

    def test_addresses_and_durations(self):
        for address in ('x;command', 'localhost/evil', '192.168.1.110:1234'):
            with self.assertRaises(ValueError):
                validate_command(dict(name='connect', address=address))
        for seconds in (-1, 30000, True, '30'):
            with self.assertRaises(ValueError):
                validate_command(dict(name='record', label='baseline', seconds=seconds))

    def test_recorder_stops_without_claiming_recognition(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = Recorder(tmp)
            rec.start('thumb', 30, 0)
            rec.add(frame(1))
            report = rec.finish('user_stop', .5)
            self.assertEqual(report['frames'], 1)
            self.assertFalse(report['motion_commands_sent'])
            self.assertFalse(report['recognition_enabled'])
            self.assertEqual(report['annotation_source'], 'human_selection_not_model_prediction')
            self.assertIsNone(rec.finish('again', 1.))
            self.assertEqual(len((Path(tmp)/report['id']/'feedback.jsonl').read_text().splitlines()), 1)

    def test_per_joint_missing_entries_are_not_hidden(self):
        rates = JointRates()
        for i in range(1001):
            rates.add(frame(i, (1, 2) if i%2==0 else (1,)))
        result = {r['nid']:r for r in rates.snapshot(1.)}
        self.assertAlmostEqual(result[1]['host_hz'], 1000.)
        self.assertAlmostEqual(result[2]['host_hz'], 500.)
        self.assertEqual(result[2]['missing_in_received_frames'], 500)
        self.assertEqual(result[2]['missing_stream_slots'], 500)

    def test_joint_stale_has_no_fabricated_rate(self):
        rates = JointRates()
        rates.add(frame(1))
        self.assertIsNone(rates.snapshot(.001)[0]['host_hz'])
        self.assertEqual(rates.snapshot(2.)[0]['status'], 'stale')
        self.assertIsNone(rates.snapshot(2.)[0]['host_hz'])

    def test_mapping_rejects_duplicates_and_unverified(self):
        item = dict(index=0, nid=1, sign=1, offset=0, verified=True)
        self.assertEqual(validate_mapping([item]), [item])
        for entries in ([item, item], [dict(item, verified=False)], [dict(item, sign=0)], [dict(item, offset=float('nan'))]):
            with self.assertRaises(ValueError):
                validate_mapping(entries)

    def test_no_autoconnect_and_reject_record_without_feedback(self):
        with tempfile.TemporaryDirectory() as tmp:
            called = []
            c = Controller(factory=lambda:called.append(True), reports=Path(tmp)/'reports')
            self.assertFalse(called)
            self.assertEqual(c.snapshot()['connection'], 'disconnected')
            with self.assertRaises(ValueError):
                c.action(dict(name='record', label='baseline', seconds=30))

    def test_stale_feedback_cannot_be_presented_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = Controller(reports=Path(tmp)/'reports')
            c.state.update(connection='connected', latest=frame(1), metrics=dict(host_hz=1000, device_hz=1000, age_ms=0))
            c.last_update = time.monotonic()-2
            state = c.snapshot()
            self.assertTrue(state['stale'])
            self.assertIsNone(state['latest'])
            self.assertIsNone(state['metrics']['host_hz'])

    def test_late_old_session_cannot_revive_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = Controller(reports=Path(tmp)/'reports')
            c.generation = 3
            c.event(dict(type='state', connection='connected'), 2)
            self.assertEqual(c.snapshot()['connection'], 'disconnected')

    def test_real_showcase_requires_calibration_and_cannot_be_hidden_by_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=Controller(reports=Path(tmp)/'reports')
            c.state.update(connection='connected',latest=frame(1),metrics=dict(age_ms=0))
            c.last_update=time.monotonic()
            with self.assertRaises(ValueError):
                c.action(dict(name='hardware_start',action='fist',speed=.5,cycles=1))
            c.state['hardware']['active']=True
            with self.assertRaises(ValueError):c.action(dict(name='view_source',source='preview'))
            with self.assertRaises(ValueError):c.action(dict(name='demo_start',action='fist',speed=.5,cycles=1))

    def test_stop_is_forwarded_even_when_feedback_is_stale(self):
        import io
        with tempfile.TemporaryDirectory() as tmp:
            c=Controller(reports=Path(tmp)/'reports')
            c.stdin=io.StringIO();c.state['connection']='error'
            c.action(dict(name='hardware_stop'))
            self.assertEqual(json.loads(c.stdin.getvalue()),dict(name='hardware_stop'))

    def test_probe_has_own_explicit_gate_and_does_not_unlock_full_actions(self):
        import io
        with tempfile.TemporaryDirectory() as tmp:
            c=Controller(reports=Path(tmp)/'reports');c.stdin=io.StringIO()
            c.state.update(connection='connected',latest=frame(1),metrics=dict(age_ms=0))
            c.last_update=time.monotonic();c.state['hardware'].update(probe_ready=True,trial_controls_version=3)
            command=dict(name='hardware_probe',index=6,direction=1,workspace_clear=False)
            with self.assertRaises(ValueError):c.action(command)
            self.assertEqual(c.stdin.getvalue(),'')
            command['workspace_clear']=True
            result=c.action(command)
            self.assertEqual(len(result['lease']),32)
            self.assertEqual(json.loads(c.stdin.getvalue())['index'],6)
            self.assertFalse(c.state['hardware']['ready']);self.assertEqual(c.state['view_source'],'feedback')
            with self.assertRaises(ValueError):c.action(command)

    def test_trial_requires_its_own_limits_and_confirmation(self):
        import io
        with tempfile.TemporaryDirectory() as tmp:
            c=Controller(reports=Path(tmp)/'reports');c.stdin=io.StringIO()
            c.state.update(connection='connected',latest=frame(1),metrics=dict(age_ms=0))
            c.last_update=time.monotonic();c.state['hardware']['trial_ready']=True
            c.state['hardware']['trial_controls_version']=3
            command=dict(name='hardware_trial',action='sequence',amplitude=.25,cycles=3,workspace_clear=True)
            for changes in ({'cycles':0},{'amplitude':2},{'workspace_clear':False},{'action':'arbitrary'}):
                with self.assertRaises(ValueError):c.action(dict(command,**changes))
            self.assertEqual(c.stdin.getvalue(),'')
            result=c.action(command)
            self.assertEqual(len(result['lease']),32)
            sent=json.loads(c.stdin.getvalue())
            self.assertEqual(sent['name'],'hardware_trial');self.assertEqual(sent['amplitude'],.25)
            self.assertFalse(c.state['hardware']['ready'])

    def test_unconfirmed_stop_cannot_hide_live_view_with_animation(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=Controller(reports=Path(tmp)/'reports')
            c.state['hardware']['active']=None
            with self.assertRaises(ValueError):c.action(dict(name='view_source',source='preview'))
            with self.assertRaises(ValueError):c.action(dict(name='demo_start',action='fist',speed=.5,cycles=1))

    def test_trial_speed_requires_updated_agent_and_cannot_raise_limits(self):
        import io
        with tempfile.TemporaryDirectory() as tmp:
            c=Controller(reports=Path(tmp)/'reports');c.stdin=io.StringIO()
            c.state.update(connection='connected',latest=frame(1),metrics=dict(age_ms=0))
            c.last_update=time.monotonic();c.state['hardware']['trial_ready']=True
            command=dict(name='hardware_trial',action='open',speed=.5,amplitude=.25,cycles=1,workspace_clear=True)
            with self.assertRaisesRegex(ValueError,'重新连接'):c.action(command)
            c.state['hardware']['trial_controls_version']=3
            for speed in (True,0,2,float('inf'),'0.5'):
                with self.assertRaises(ValueError):c.action(dict(command,speed=speed))
            self.assertEqual(c.stdin.getvalue(),'')
            c.action(dict(command,ignore_warnings=True,current_limit_A=2.0))
            sent=json.loads(c.stdin.getvalue())
            self.assertEqual(sent['speed'],.5);self.assertEqual(sent['action'],'open')
            self.assertNotIn('ignore_warnings',sent);self.assertNotIn('current_limit_A',sent)

    def test_display_mapping_requires_matching_observed_hand(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = Controller(reports=Path(tmp)/'reports')
            command = dict(name='display_mapping', entries=[dict(index=0,nid=1,sign=1,offset=0,verified=True)])
            with self.assertRaises(ValueError):
                c.action(command)
            c.state.update(connection='connected', device_id='synthetic-device', joint_rates=[dict(nid=1)])
            c.action(command)
            config = json.loads((Path(tmp)/'display_mapping.json').read_text())
            self.assertEqual(config['device_id'], 'synthetic-device')
            self.assertTrue(config['no_hardware_commands'])

    def test_sdk_worker_read_only_record_stop_and_disconnect(self):
        requests, events = queue.Queue(), queue.Queue()
        calls = []
        class Subscription:
            counter = 0
            def recv(self):
                self.counter += 1
                if self.counter==2:
                    requests.put(dict(name='record', label='index', seconds=10))
                if self.counter==18:
                    requests.put(dict(name='stop'))
                if self.counter==24:
                    requests.put(dict(name='disconnect'))
                return SimpleNamespace(header=SimpleNamespace(seq=self.counter,timestamp_us=self.counter*1000,frame_id='left'),
                    num_joints=1,joints=[SimpleNamespace(nid=5,position=.2,velocity=.1,effort=.02)])
            def close(self):calls.append('subscription_close')
        class Hand:
            serial_number='synthetic-left'
            def handedness(self):return SimpleNamespace(get=lambda:'left')
            def joint_states(self):return SimpleNamespace(subscribe=Subscription)
            def joint_diagnostics(self):return SimpleNamespace(subscribe=lambda:SimpleNamespace(recv=lambda:None,close=lambda:None))
            def disconnect(self):calls.append('disconnect')
        class Manager:
            def connect(self,**kw):
                calls.append(kw)
                return Hand()
        sdk=SimpleNamespace(SdkManager=SimpleNamespace(instance=Manager),Handedness=SimpleNamespace(Left='left'),WujiHand2=SimpleNamespace())
        original=Recorder
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules,{'wuji_sdk':sdk}), patch('console_agent.Recorder',lambda:original(tmp)):
            worker('',requests,events)
            emitted=[]
            while not events.empty():emitted.append(events.get())
            reports=[e['report'] for e in emitted if e['type']=='report']
            self.assertEqual(len(reports),1)
            self.assertEqual(reports[0]['reason'],'user_stop')
            self.assertGreater(reports[0]['frames'],0)
            self.assertFalse(any(e['type']=='error' for e in emitted))
            self.assertEqual(calls[0],dict(device_name='wuji_hand_2',handedness='left'))
            self.assertEqual(calls[-2:],['subscription_close','disconnect'])


if __name__ == '__main__':
    unittest.main()
