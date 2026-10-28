"""Removal failures retain truthful process/catalog state and support retry."""
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from device_fleet import DeviceFleet


class Process:
    def __init__(self):
        self.code=None;self.events=[];self.wait_failures=0;self.terminate_error=None;self.kill_error=None;self.wait_hook=None
    def poll(self):return self.code
    def terminate(self):
        self.events.append('terminate')
        if self.terminate_error:raise self.terminate_error
    def kill(self):
        self.events.append('kill')
        if self.kill_error:raise self.kill_error
    def wait(self,timeout):
        self.events.append(('wait',timeout))
        if self.wait_hook:self.wait_hook()
        if self.wait_failures:
            self.wait_failures-=1;raise subprocess.TimeoutExpired('synthetic-child',timeout)
        self.code=0;return 0


class FleetRemovalPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name);self.ident='a'*16;self.other='b'*16
        self.fleet=DeviceFleet.__new__(DeviceFleet);self.fleet.lock=threading.RLock();self.fleet.data=self.data
        self.fleet.records=self.data/'device_workspaces.json'
        self.proc=Process();self.record=dict(id=self.ident,label='Synthetic',port=49101,profile='hand2_left',process=self.proc)
        self.fleet.children={self.ident:self.record}
        self.rows=[dict(id=self.ident,label='Synthetic',port=49101,profile='hand2_left'),dict(id=self.other,label='Other',port=49102,profile='hand2_right')]
        self.fleet.records.write_text(json.dumps(self.rows),'utf-8')
        self.history=self.data/'devices'/self.ident/'user-history.json';self.history.parent.mkdir(parents=True);self.history.write_bytes(b'preserve original history')
        self.calls=[]
        def post(record,command):
            self.calls.append((record,command));return dict(ok=True)
        self.fleet.post=post
    def saved(self):return json.loads(self.fleet.records.read_text('utf-8'))
    def removed(self):
        self.assertNotIn(self.ident,self.fleet.children)
        self.assertEqual(self.saved(),[self.rows[1]])
        self.assertEqual(self.history.read_bytes(),b'preserve original history')
    def test_normal_remove_confirms_exit_and_preserves_other_catalog_and_user_data(self):
        self.assertEqual(self.fleet.remove(self.ident),dict(removed=True))
        self.assertEqual(self.proc.events,['terminate',('wait',5)]);self.assertEqual(len(self.calls),1);self.removed()
    def test_write_failure_retains_confirmed_dead_record_and_retry_does_not_call_dead_service(self):
        original=Path.write_text
        def fail(path,*args,**kwargs):
            if path==self.fleet.records.with_suffix('.pending'):raise OSError('Synthetic disk full')
            return original(path,*args,**kwargs)
        with patch.object(Path,'write_text',fail),self.assertRaises(OSError):self.fleet.remove(self.ident)
        self.assertIs(self.fleet.children[self.ident],self.record);self.assertEqual(self.proc.code,0);self.assertEqual(self.saved(),self.rows)
        self.assertEqual(self.fleet.remove(self.ident),dict(removed=True));self.assertEqual(len(self.calls),1);self.removed()
    def test_replace_failure_retains_record_and_retry_commits_once(self):
        original=Path.replace
        def fail(path,target):
            if path==self.fleet.records.with_suffix('.pending'):raise PermissionError('Synthetic replace failure')
            return original(path,target)
        with patch.object(Path,'replace',fail),self.assertRaises(PermissionError):self.fleet.remove(self.ident)
        self.assertIs(self.fleet.children[self.ident],self.record);self.assertEqual(self.saved(),self.rows)
        self.fleet.remove(self.ident);self.assertEqual(len(self.calls),1);self.removed()
    def test_rejected_close_keeps_live_child_and_catalog(self):
        self.fleet.post=lambda *_:dict(ok=False,error='cleanup unknown')
        self.assertEqual(self.fleet.remove(self.ident),dict(ok=False,error='cleanup unknown'))
        self.assertEqual(self.proc.events,[]);self.assertEqual(self.saved(),self.rows);self.assertNotIn('_removal_close_process',self.record)
    def test_dead_child_without_close_confirmation_is_not_assumed_removed(self):
        self.proc.code=1
        def unavailable(*_):raise OSError('dead service')
        self.fleet.post=unavailable
        with self.assertRaises(OSError):self.fleet.remove(self.ident)
        self.assertEqual(self.saved(),self.rows);self.assertIs(self.fleet.children[self.ident],self.record)
    def test_terminate_failure_retains_confirmation_for_exact_process_retry(self):
        self.proc.terminate_error=OSError('cannot terminate')
        with self.assertRaises(OSError):self.fleet.remove(self.ident)
        self.assertEqual(self.saved(),self.rows);self.assertIs(self.fleet.children[self.ident],self.record)
        self.proc.terminate_error=None;self.fleet.remove(self.ident);self.assertEqual(len(self.calls),2);self.removed()
    def test_timeout_kills_then_waits_for_confirmed_exit(self):
        self.proc.wait_failures=1
        self.fleet.remove(self.ident);self.assertEqual(self.proc.events,['terminate',('wait',5),'kill',('wait',5)]);self.removed()
    def test_kill_and_final_wait_failures_never_report_removed(self):
        for kind in ('kill','wait'):
            with self.subTest(kind=kind):
                self.proc.events=[];self.proc.code=None;self.proc.wait_failures=1 if kind=='kill' else 2
                self.proc.kill_error=OSError('cannot kill') if kind=='kill' else None
                with self.assertRaises((OSError,subprocess.TimeoutExpired)):self.fleet.remove(self.ident)
                self.assertEqual(self.saved(),self.rows);self.assertIs(self.fleet.children[self.ident],self.record)
        self.proc.wait_failures=0;self.fleet.remove(self.ident);self.assertEqual(len(self.calls),3);self.removed()
    def test_replaced_record_after_close_is_not_terminated_or_deleted(self):
        replacement=dict(self.record,process=Process())
        def replace(*_):self.fleet.children[self.ident]=replacement;return dict(ok=True)
        self.fleet.post=replace
        with self.assertRaisesRegex(ValueError,'Workspace changed'):self.fleet.remove(self.ident)
        self.assertIs(self.fleet.children[self.ident],replacement);self.assertEqual(self.proc.events,[]);self.assertEqual(self.saved(),self.rows)
    def test_replaced_record_during_exit_wait_is_not_deleted(self):
        replacement=dict(self.record,process=Process())
        self.proc.wait_hook=lambda:self.fleet.children.update({self.ident:replacement})
        with self.assertRaisesRegex(ValueError,'Workspace changed'):self.fleet.remove(self.ident)
        self.assertIs(self.fleet.children[self.ident],replacement);self.assertEqual(self.saved(),self.rows)
    def test_process_replacement_cannot_reuse_prior_close_confirmation(self):
        with patch.object(Path,'replace',side_effect=OSError('save failed')),self.assertRaises(OSError):self.fleet.remove(self.ident)
        newer=Process();self.record['process']=newer
        self.fleet.remove(self.ident);self.assertEqual(len(self.calls),2);self.assertEqual(newer.events,['terminate',('wait',5)]);self.removed()
    def test_wait_without_confirmed_exit_does_not_commit_removal(self):
        self.proc.wait=lambda timeout:None
        with self.assertRaisesRegex(ValueError,'exit was not confirmed'):self.fleet.remove(self.ident)
        self.assertIs(self.fleet.children[self.ident],self.record);self.assertEqual(self.saved(),self.rows)
    def test_live_retry_rechecks_close_and_does_not_terminate_after_rejection(self):
        self.proc.terminate_error=OSError('still running')
        with self.assertRaises(OSError):self.fleet.remove(self.ident)
        self.proc.terminate_error=None;self.proc.events=[]
        self.fleet.post=lambda *_:dict(ok=False,error='new session cleanup unconfirmed')
        self.assertEqual(self.fleet.remove(self.ident),dict(ok=False,error='new session cleanup unconfirmed'))
        self.assertEqual(self.proc.events,[]);self.assertEqual(self.saved(),self.rows)
        self.assertNotIn('_removal_close_process',self.record)
        self.proc.code=1
        def unavailable(*_):raise OSError('new close never confirmed')
        self.fleet.post=unavailable
        with self.assertRaises(OSError):self.fleet.remove(self.ident)
        self.assertEqual(self.saved(),self.rows);self.assertIs(self.fleet.children[self.ident],self.record)


if __name__=='__main__':unittest.main()
