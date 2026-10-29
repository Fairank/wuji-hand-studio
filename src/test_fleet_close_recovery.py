"""Workspace shutdown acknowledgement, with isolated in-memory child processes."""
import subprocess
import threading
import unittest
from unittest.mock import Mock

from device_fleet import DeviceFleet


class Process:
    def __init__(self):self.code=None;self.events=[];self.failures=0;self.error=None
    def poll(self):return self.code
    def terminate(self):self.events.append('terminate')
    def kill(self):
        self.events.append('kill')
        if self.error:raise self.error
    def wait(self,timeout):
        self.events.append(('wait',timeout))
        if self.failures:
            self.failures-=1;raise subprocess.TimeoutExpired('synthetic-never-executed',timeout)
        self.code=0;return 0


class FleetCloseRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fleet=DeviceFleet.__new__(DeviceFleet)
        self.fleet.lock=threading.RLock();self.fleet._closing=False;self.fleet._beat_pool=Mock()
        self.process=Process();self.ident='a'*16
        self.record=dict(id=self.ident,process=self.process)
        self.fleet.children={self.ident:self.record}
        self.fleet.post=Mock(return_value={'ok':True})

    def retained(self):
        self.assertIs(self.fleet.children[self.ident],self.record)
        self.assertFalse(self.fleet._closing);self.fleet._beat_pool.shutdown.assert_not_called()

    def test_kill_is_followed_by_wait_and_exit_confirmation(self):
        self.process.failures=1
        self.assertEqual(self.fleet.close(),{'ok':True})
        self.assertEqual(self.process.events,['terminate',('wait',5),'kill',('wait',5)])
        self.assertEqual(self.fleet.children,{})
        self.fleet._beat_pool.shutdown.assert_called_once_with(wait=False,cancel_futures=True)

    def test_second_wait_timeout_retains_children_and_retry_can_finish(self):
        self.process.failures=2
        self.assertFalse(self.fleet.close()['ok']);self.retained()
        self.assertIsNone(self.process.poll())
        self.assertTrue(self.fleet.close()['ok']);self.assertEqual(self.fleet.children,{})
        self.assertEqual(self.fleet.post.call_count,2)

    def test_kill_error_retains_child_and_allows_retry(self):
        self.process.failures=1;self.process.error=OSError('synthetic kill failed')
        self.assertFalse(self.fleet.close()['ok']);self.retained()
        self.process.error=None
        self.assertTrue(self.fleet.close()['ok'])

    def test_wait_without_observed_exit_is_not_success(self):
        self.process.wait=lambda timeout:None
        self.assertFalse(self.fleet.close()['ok']);self.retained()

    def test_rejected_or_unavailable_live_service_retains_children(self):
        for refused in ({'ok':False,'error':'cleanup not confirmed'},OSError('synthetic transport unavailable')):
            self.fleet.post.side_effect=refused if isinstance(refused,Exception) else None
            self.fleet.post.return_value=refused
            with self.subTest(refused=refused):
                self.assertFalse(self.fleet.close()['ok']);self.retained()
                self.assertEqual(self.process.events,[])

    def test_replacement_catalog_record_is_not_lost(self):
        replacement=dict(id=self.ident,process=Process())
        def close(*_):self.fleet.children[self.ident]=replacement;return {'ok':True}
        self.fleet.post.side_effect=close
        self.assertFalse(self.fleet.close()['ok'])
        self.assertIs(self.fleet.children[self.ident],replacement)
        self.fleet._beat_pool.shutdown.assert_not_called()

    def test_close_in_progress_rejects_new_passive_session_before_any_spawn(self):
        self.fleet._closing=True
        with self.assertRaisesRegex(ValueError,'closing'):self.fleet.create('Synthetic','hand2_left')
        self.assertFalse(self.fleet.close()['ok'])
        self.fleet.post.assert_not_called()


if __name__=='__main__':unittest.main()
