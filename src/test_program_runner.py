"""Observe the exact completed program without exposing its control lease."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from gesture_library import CATALOG
from program_runner import ProgramRunner


class ImmediateThread:
    def __init__(self,*,target,args,**kwargs):self.target=target;self.args=args
    def start(self):self.target(*self.args)
    def is_alive(self):return False


class HeldThread(ImmediateThread):
    def start(self):self.alive=True
    def is_alive(self):return self.alive
    def finish(self):self.target(*self.args);self.alive=False


class ProgramRunIdentityTests(unittest.TestCase):
    def setUp(self):
        self.calls=[];self.fail=False
        self.controller=SimpleNamespace(snapshot=lambda:dict(hardware=dict(active=False),glove=dict(busy=False)),
            action=self.action,player=SimpleNamespace(snapshot=lambda:dict(active=True,elapsed_s=1,duration_s=0)))
        self.runner=ProgramRunner(self.controller)
        dance=next(row['id'] for row in CATALOG if row['group']=='dance')
        self.command=dict(mode='preview',plan=dict(entries=[dict(action=dance,speed=1,cycles=1)],repeats=1))

    def action(self,value):
        self.calls.append(value)
        if self.fail and value['name']=='demo_start':raise ValueError('Synthetic playback failed')

    def test_immediate_completion_returns_the_same_public_id_and_original_lease(self):
        with patch('program_runner.threading.Thread',ImmediateThread):reply=self.runner.start(self.command)
        state=self.runner.snapshot()
        self.assertFalse(state['active']);self.assertIn('Playlist completed',state['reason'])
        self.assertRegex(reply['program_lease'],r'^[0-9a-f]{32}$')
        self.assertEqual(reply['program_run_id'],state['run_id'])
        self.assertNotEqual(reply['program_run_id'],reply['program_lease'])
        self.assertNotIn(reply['program_lease'],json.dumps(state));self.assertIsNone(self.runner.lease)

    def test_new_run_has_new_identity_and_rejects_the_old_lease(self):
        with patch('program_runner.threading.Thread',HeldThread):
            old=self.runner.start(self.command);self.runner.thread.finish()
            new=self.runner.start(self.command)
        self.addCleanup(self.runner.thread.finish)
        self.assertNotEqual(old['program_run_id'],new['program_run_id'])
        self.assertEqual(self.runner.snapshot()['run_id'],new['program_run_id'])
        with self.assertRaisesRegex(ValueError,'Expired'):self.runner.keepalive(old['program_lease'])
        self.runner.keepalive(new['program_lease']);self.assertEqual(self.runner.lease,new['program_lease'])

    def test_failed_run_retains_its_own_failure_and_can_start_again(self):
        self.fail=True
        with patch('program_runner.threading.Thread',ImmediateThread):
            failed=self.runner.start(self.command)
            self.assertEqual(self.runner.snapshot()['run_id'],failed['program_run_id'])
            self.assertEqual(self.runner.snapshot()['reason'],'Synthetic playback failed')
            self.assertFalse(self.runner.snapshot()['active']);self.fail=False
            next_run=self.runner.start(self.command)
        self.assertNotEqual(next_run['program_run_id'],failed['program_run_id'])
        self.assertIn('Playlist completed',self.runner.snapshot()['reason'])

    def test_rejected_plan_does_not_replace_completed_identity(self):
        with patch('program_runner.threading.Thread',ImmediateThread):reply=self.runner.start(self.command)
        with self.assertRaises(ValueError):self.runner.start(dict(mode='preview',plan=dict(entries=[])))
        self.assertEqual(self.runner.snapshot()['run_id'],reply['program_run_id'])

    def test_failed_thread_start_does_not_leave_an_active_program_or_lease(self):
        class BrokenThread(ImmediateThread):
            def start(self):raise RuntimeError('Synthetic thread failure')
        with patch('program_runner.threading.Thread',BrokenThread),self.assertRaisesRegex(RuntimeError,'Synthetic thread failure'):
            self.runner.start(self.command)
        self.assertFalse(self.runner.snapshot()['active']);self.assertIsNone(self.runner.lease);self.assertEqual(self.calls,[])
        with patch('program_runner.threading.Thread',ImmediateThread):self.runner.start(self.command)
        self.assertIn('Playlist completed',self.runner.snapshot()['reason'])

    def test_failed_thread_creation_also_releases_the_unstarted_attempt(self):
        with patch('program_runner.threading.Thread',side_effect=RuntimeError('Synthetic creation failure')):
            with self.assertRaisesRegex(RuntimeError,'Synthetic creation failure'):self.runner.start(self.command)
        self.assertFalse(self.runner.snapshot()['active']);self.assertIsNone(self.runner.lease);self.assertEqual(self.calls,[])
        with patch('program_runner.threading.Thread',ImmediateThread):self.runner.start(self.command)
        self.assertIn('Playlist completed',self.runner.snapshot()['reason'])


if __name__=='__main__':unittest.main()
