"""Repeated real reader lifecycles retain uncertainty, not closed transports."""
import gc
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch
import weakref

from console_server import Controller


class Output:
    channel=N(closed=True)

    def __init__(self,serial,complete):self.serial=serial;self.complete=complete

    def __iter__(self):
        rows=[dict(type='identity',device_id=self.serial,profile='hand2_left')]
        if self.complete:
            rows.append(dict(type='closed',device_id=self.serial,hardware=dict(active=False,stop_confirmed=True)))
        return iter('WUJI_JSON:'+json.dumps(row)+'\n' for row in rows)


class Transport:
    agent_command='synthetic-reader'

    def __init__(self,serial,complete=True,fail_close=False):
        self.serial=serial;self.complete=complete;self.fail_close=fail_close;self.closes=0

    def exec_command(self,command,timeout):
        assert command==self.agent_command
        return io.StringIO(),Output(self.serial,self.complete),io.BytesIO()

    def close(self):
        self.closes+=1
        return False if self.fail_close and self.closes==1 else None


class FeedbackRetirementTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.c=Controller(factory=Mock(side_effect=AssertionError('Unplanned transport')),reports=Path(self.folder.name)/'reports')

    def connect(self,serial='SYNTHETIC-REUSED',*,complete=True,fail_close=False):
        transport=Transport(serial,complete,fail_close);reference=weakref.ref(transport)
        self.c.factory=lambda:transport
        with patch('threading.excepthook') as exception:
            self.c.action(dict(name='connect',serial=serial))
            generation=self.c.generation;self.c.feedback_sessions[generation]['thread'].join(5)
            self.assertFalse(self.c.feedback_sessions[generation]['thread'].is_alive())
            self.assertEqual(exception.call_count,int(fail_close))
            # Mock call history would retain the expected exception traceback,
            # including its transport local, after the real close was retried.
            exception.reset_mock()
        self.c.factory=Mock(side_effect=AssertionError('Unplanned transport'))
        return generation,reference

    def test_repeated_confirmed_readers_release_transport_objects_and_history(self):
        references=[]
        for _ in range(40):
            _,reference=self.connect();references.append(reference)
        gc.collect()
        self.assertFalse(any(ref() is not None for ref in references))
        self.assertLessEqual(len(self.c.feedback_sessions),2)
        self.assertLessEqual(len(self.c.session_devices),2)
        self.assertLessEqual(len(self.c.session_epochs),3)
        self.assertEqual(self.c.termination.snapshot(),[])

    def test_retired_frames_cannot_recreate_ownership_but_late_reports_are_saved(self):
        old,_=self.connect()
        for _ in range(3):self.connect()
        before=(dict(self.c.session_devices),dict(self.c.session_epochs),self.c.termination.snapshot(),self.c.generation)
        self.c.event(dict(type='state',device_id='SYNTHETIC-OLD',hardware=dict(active=None,stop_confirmed=False)),old)
        self.assertEqual((self.c.session_devices,self.c.session_epochs,self.c.termination.snapshot(),self.c.generation),before)
        report=dict(id='d'*32,frames=1,reason='synthetic completed recording')
        self.c.event(dict(type='report',report=report),old)
        self.assertTrue((self.c.reports/(report['id']+'.json')).is_file())
        with self.assertRaises(ValueError):self.c.stop_feedback_confirmed(old,'SYNTHETIC-REUSED')
        self.assertEqual(self.c.generation,before[-1])

    def test_unknown_older_hole_keeps_its_identity_and_can_receive_exact_late_ack(self):
        old,reference=self.connect('SYNTHETIC-UNKNOWN',complete=False)
        self.c.termination.observe('hand','SYNTHETIC-UNKNOWN',dict(active=None),self.c.session_epochs[old])
        for _ in range(5):self.connect()
        self.assertIn(old,self.c.feedback_sessions)
        self.assertIn(old,self.c.session_epochs)
        self.assertIn(old,self.c.session_devices)
        self.assertEqual([row['device_id'] for row in self.c.termination.snapshot()],['SYNTHETIC-UNKNOWN'])
        self.c.event(dict(type='closed',device_id='SYNTHETIC-UNKNOWN',hardware=dict(active=False,stop_confirmed=True)),old)
        self.assertEqual(self.c.termination.snapshot(),[])
        self.connect()
        self.assertNotIn(old,self.c.feedback_sessions)
        self.assertNotIn(old,self.c.session_epochs)
        self.assertNotIn(old,self.c.session_devices)
        self.assertIsNone(reference())

    def test_failed_close_retains_exact_retry_client_until_confirmed(self):
        old,reference=self.connect(fail_close=True)
        self.assertIsNotNone(reference())
        self.assertFalse(self.c.feedback_sessions[old]['transport_closed'])
        with patch('console_server.threading.Timer'):
            self.c.action(dict(name='disconnect'))
        result=self.c.stop_feedback_confirmed(old,'SYNTHETIC-REUSED',allow_unidentified=True)
        self.assertTrue(result['cleanup_confirmed'])
        self.assertTrue(self.c.feedback_sessions[old]['transport_closed'])
        gc.collect();self.assertIsNone(reference())


if __name__=='__main__':unittest.main()
