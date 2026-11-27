"""Repeated real reader lifecycles retain uncertainty, not closed transports."""
import gc
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch
import weakref

from console_server import Controller
from device_profiles import profile


class Output:
    channel=N(closed=True)

    def __init__(self,serial,complete,*,not_opened=False):
        self.serial=serial;self.complete=complete;self.not_opened=not_opened

    def __iter__(self):
        if self.not_opened:
            rows=[dict(type='error',message='Selected device is no longer available'),
                dict(type='closed',hardware=dict(active=False,stop_confirmed=False,enable_attempted=False),
                    feedback_cleanup=dict(version=1,kind='feedback_not_opened',serial=self.serial,
                        enable_attempted=False,resources_released=True))]
            return iter('WUJI_JSON:'+json.dumps(row)+'\n' for row in rows)
        rows=[dict(type='identity',device_id=self.serial,profile='hand2_left')]
        if self.complete:
            rows.append(dict(type='closed',device_id=self.serial,hardware=dict(active=False,stop_confirmed=True)))
        return iter('WUJI_JSON:'+json.dumps(row)+'\n' for row in rows)


class Transport:
    agent_command='synthetic-reader'

    def __init__(self,serial,complete=True,fail_close=False,*,not_opened=False):
        self.serial=serial;self.complete=complete;self.fail_close=fail_close;self.closes=0;self.not_opened=not_opened

    def exec_command(self,command,timeout):
        assert command==self.agent_command
        return io.StringIO(),Output(self.serial,self.complete,not_opened=self.not_opened),io.BytesIO()

    def close(self):
        self.closes+=1
        return False if self.fail_close and self.closes==1 else None


class ActiveTransport(Transport):
    def __init__(self, serial):
        super().__init__(serial)
        self.stop=threading.Event()
        self.online=threading.Event()
        self.sent=[]

    def exec_command(self,command,timeout):
        assert command==self.agent_command
        def write(line):
            request=json.loads(line);self.sent.append(request['name'])
            if request['name']=='disconnect':self.stop.set()
        return N(write=write,flush=lambda:None),self,io.BytesIO()

    channel=N(closed=True)

    def __iter__(self):
        rows=list(Output(self.serial,True))
        yield rows[0]
        state=dict(type='state',connection='connected',message='Synthetic feedback',latest={},
            metrics=dict(age_ms=0),recording=dict(active=False),device_id=self.serial,joint_rates=[],
            hardware=dict(active=False,stop_confirmed=True))
        yield 'WUJI_JSON:'+json.dumps(state)+'\n'
        self.online.set()
        self.stop.wait(3)
        yield rows[1]


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

    def test_failed_readonly_switch_allows_retry_only_after_its_exact_close_ack(self):
        for acknowledged,retry_same,not_opened in ((True,True,False),(True,False,False),(False,False,False),(True,True,True)):
            with self.subTest(acknowledged=acknowledged,retry_same=retry_same,not_opened=not_opened):
                with (tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',
                    WUJI_OFFICIAL_CLI_DATA=str(Path(folder)/'cli'),WUJI_STUDIO_COMPONENT_DATA=str(Path(folder)/'studio'),
                    WUJI_FLEET_PARENT=''),patch('device_profiles.selected_profile',return_value=profile('hand2_left')),
                    patch('device_profiles.save_profile',side_effect=profile),
                    patch('managed_runtime.status',return_value={'busy':False,'ready':True}),
                    patch('bridge_config.load_config',return_value={'mode':'wsl'}),
                    patch('threading.Timer',return_value=Mock())):
                    first='WH-FAILED-FIXTURE';second=first if retry_same else 'WH-HEALTHY-FIXTURE'
                    closed_transport=Transport(first,complete=acknowledged,not_opened=not_opened);live_transport=ActiveTransport(second)
                    c=Controller(factory=Mock(side_effect=[closed_transport,live_transport]),reports=Path(folder)/'reports')
                    c.teleoperation_handover._runtime=Mock()
                    c.device_inventory=Mock(return_value=dict(devices=[
                        dict(sn=serial,kind='hand',generation='hand2',side='left',stale=False) for serial in dict.fromkeys((first,second))],
                        workspace_errors=[]))
                    service=c.connection_switch
                    now=[0.]
                    service.clock=lambda:now[0]
                    def sleep(_):
                        if c.factory.call_count==1:
                            c.feedback_sessions[c.generation]['thread'].join(2)
                            now[0]+=12
                        else:
                            self.assertTrue(live_transport.online.wait(2))
                    service.sleep=sleep
                    def switch(serial,request_id):
                        return c.action(dict(name='connection_switch',request_id=request_id,
                            expected_owner=c.snapshot()['connection_switch']['expected_owner'],target=dict(mode='hand',serial=serial)))
                    try:
                        error='Selected device is no longer available' if not_opened else '反馈连接已结束'
                        with self.assertRaisesRegex(ValueError,error):switch(first,'a'*32)
                        failed=c.snapshot()
                        self.assertEqual(closed_transport.closes,1)
                        self.assertEqual(c.factory.call_count,1,'No implicit retry')
                        self.assertIs(failed['connection_switch']['can_switch'],acknowledged,failed['connection_switch'])
                        if acknowledged:
                            old_generation=min(c.feedback_sessions)
                            result=switch(second,'b'*32)
                            self.assertEqual(result['connection_switch_receipt']['target']['serial'],second)
                            c.event(dict(type='identity',device_id=first,profile='hand2_left'),old_generation)
                            self.assertEqual(c.snapshot()['connection_switch']['current_owner']['serial'],second)
                            self.assertEqual(c.snapshot()['session_generation'],result['connection_switch']['current_owner']['session'])
                            self.assertEqual(live_transport.sent,['connect'])
                        else:
                            self.assertIs(failed['hand_feedback_session']['exit_confirmed'],False)
                            with self.assertRaisesRegex(ValueError,'ownership is not confirmed'):switch(second,'b'*32)
                            self.assertEqual(c.factory.call_count,1)
                    finally:
                        live_transport.stop.set()
                        for row in c.feedback_sessions.values():row['thread'].join(3)

    def test_ended_unknown_reader_releases_thread_without_inventing_a_close_ack(self):
        old,_=self.connect('SYNTHETIC-UNKNOWN',complete=False)
        thread=weakref.ref(self.c.feedback_sessions[old]['thread'])
        epoch=self.c.session_epochs[old]
        self.c.termination.observe('hand','SYNTHETIC-UNKNOWN',dict(active=None),epoch)
        for _ in range(4):self.connect()
        gc.collect()
        self.assertIsNone(thread())
        record=self.c.feedback_sessions[old]
        self.assertFalse(record['thread'].is_alive())
        record['thread'].join(0)
        self.assertFalse(record['worker_closed'])
        self.assertEqual(self.c.session_epochs[old],epoch)
        self.assertEqual(self.c.session_devices[old],'SYNTHETIC-UNKNOWN')
        self.assertEqual([r['device_id'] for r in self.c.termination.snapshot()],['SYNTHETIC-UNKNOWN'])
        with self.assertRaises(ValueError):self.c.stop_feedback_confirmed(old,'SYNTHETIC-UNKNOWN',allow_unidentified=True)

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
