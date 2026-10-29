"""Exact discovery cleanup retries; no real process, CLI, device or network."""
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import cli_coordination as coordination
from official_device_discovery import DiscoveryCleanupUnconfirmed,_cancellable_read
from official_visualization import OfficialVisualization


class DiscoveryCleanupRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.viewer=OfficialVisualization(Path(temp.name))
        self.process=Mock();self.process.poll.return_value=None
        def communicate(**_):
            self.viewer.scan_cancel.set()
            raise subprocess.TimeoutExpired('synthetic-never-executed',.1)
        self.process.communicate.side_effect=communicate
        self.owned=SimpleNamespace(process=self.process,close=Mock(return_value=False))
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch('windows_process_job.OwnedProcess',return_value=self.owned))
        stack.enter_context(patch('bridge_config.load_config',return_value={'mode':'local'}))
        self.cli=stack.enter_context(patch('calibration_cli.cli_json',side_effect=self.read))

    def read(self,_config,*_args,**kwargs):
        result=_cancellable_read(['synthetic-never-executed'],kwargs['cancelled'])
        return json.loads(result.stdout)

    def collect(self):
        token=self.viewer._begin_scan()
        self.assertIsNone(self.viewer._collect_scan(token,True))
        return token

    def retained(self,token):
        self.assertEqual(coordination.reservation(self.viewer.folder)['token'],token)
        self.assertIs(self.viewer.scan_job,self.owned)
        state=self.viewer.snapshot()
        self.assertTrue(state['busy']);self.assertTrue(state['scanning'])
        self.assertTrue(state['scan_cleanup_required']);self.assertFalse(self.viewer.scan_done.is_set())
        self.assertTrue(self.viewer.scan_finished.is_set())
        with coordination.gate(self.viewer.folder),self.assertRaises(ValueError):coordination.reserve('firmware',self.viewer.folder)

    def released(self):
        self.assertEqual(coordination.reservation(self.viewer.folder),{})
        self.assertIsNone(self.viewer.scan_token);self.assertIsNone(self.viewer.scan_job)
        state=self.viewer.snapshot()
        self.assertFalse(state['busy']);self.assertFalse(state['scanning'])
        self.assertFalse(state['scan_cleanup_required']);self.assertTrue(self.viewer.scan_done.is_set())

    def test_close_exception_retains_exact_job_until_successful_explicit_retry(self):
        self.owned.close.side_effect=[OSError('synthetic CloseHandle failure'),False,True]
        token=self.collect();self.retained(token)
        with self.assertRaisesRegex(ValueError,'unconfirmed'):self.viewer.stop()
        self.retained(token)
        self.viewer.stop();self.released()
        self.assertEqual(self.owned.close.call_count,3);self.assertEqual(self.cli.call_count,1)

    def test_false_close_then_exception_never_releases_or_forgets_original_job(self):
        token=self.collect();self.owned.close.side_effect=OSError('synthetic second failure')
        with self.assertRaisesRegex(ValueError,'second failure'):self.viewer.stop()
        self.retained(token)
        self.owned.close.side_effect=None;self.owned.close.return_value=True
        self.viewer.stop();self.released()

    def test_foreign_or_unknown_marker_never_closes_job_or_releases_other_owner(self):
        token=self.collect();self.owned.close.reset_mock()
        for marker in ({'kind':'firmware','token':'b'*32},{'kind':'scan','token':'b'*32},{'kind':'unknown'}):
            coordination.atomic(self.viewer.folder/'session.json',marker)
            with self.subTest(marker=marker),self.assertRaisesRegex(ValueError,'owner changed'):self.viewer.stop()
            self.assertEqual(coordination.reservation(self.viewer.folder),marker)
            self.owned.close.assert_not_called();self.assertEqual(self.viewer.scan_token,token)

    def test_original_job_can_be_cleaned_after_its_marker_disappears(self):
        token=self.collect();coordination.release(token,self.viewer.folder)
        self.owned.close.return_value=True
        self.viewer.stop();self.released()

    def test_constructor_unknown_exit_remains_fail_closed_without_a_recoverable_job(self):
        self.cli.side_effect=DiscoveryCleanupUnconfirmed('startup exit unconfirmed')
        token=self.collect()
        with self.assertRaisesRegex(ValueError,'startup exit remains unconfirmed'):self.viewer.stop()
        self.assertEqual(coordination.reservation(self.viewer.folder)['token'],token)
        self.assertTrue(self.viewer.snapshot()['scan_cleanup_required'])

    def test_successful_read_with_release_error_retains_known_safe_cleanup_retry(self):
        self.cli.side_effect=None;self.cli.return_value={'devices':[]}
        token=self.viewer._begin_scan()
        with patch('official_visualization.release',side_effect=PermissionError('synthetic locked marker')):
            self.assertIsNone(self.viewer._collect_scan(token,True))
        self.assertTrue(self.viewer.snapshot()['scan_cleanup_required'])
        self.assertFalse(self.viewer.scan_needs_close);self.assertIsNone(self.viewer.scan_job)
        self.viewer.stop();self.released();self.owned.close.assert_not_called()

    def test_thread_start_plus_release_failure_keeps_truthful_retryable_state(self):
        thread=Mock();thread.start.side_effect=RuntimeError('synthetic unavailable thread')
        with patch('official_visualization.threading.Thread',return_value=thread), \
                patch('official_visualization.release',side_effect=PermissionError('synthetic marker locked')):
            with self.assertRaisesRegex(RuntimeError,'unavailable thread'):self.viewer.scan()
        self.assertTrue(self.viewer.snapshot()['busy'])
        self.assertTrue(self.viewer.snapshot()['scan_cleanup_required'])
        self.assertTrue(self.viewer.scan_finished.is_set());self.assertFalse(self.viewer.scan_done.is_set())
        self.viewer.stop();self.released();self.cli.assert_not_called();self.owned.close.assert_not_called()

    def test_release_noop_is_not_acknowledged_as_success(self):
        self.cli.side_effect=None;self.cli.return_value={'devices':[]}
        token=self.viewer._begin_scan()
        with patch('official_visualization.release'):
            self.assertIsNone(self.viewer._collect_scan(token,True))
            with self.assertRaisesRegex(ValueError,'release is unconfirmed'):self.viewer.stop()
        self.assertEqual(coordination.reservation(self.viewer.folder)['token'],token)
        self.viewer.stop();self.released()

    def test_delayed_old_completion_does_not_finish_a_replacement_scan(self):
        old=self.viewer._begin_scan();self.assertTrue(self.viewer._finish_scan(old))
        current=self.viewer._begin_scan()
        self.assertFalse(self.viewer._finish_scan(old))
        self.assertEqual(self.viewer.scan_token,current)
        self.assertFalse(self.viewer.scan_finished.is_set());self.assertFalse(self.viewer.scan_done.is_set())
        self.assertFalse(self.viewer.snapshot()['scan_cleanup_required'])
        self.viewer._finish_scan(current)

    def test_delayed_stop_cannot_release_scan_started_after_original_finished(self):
        old=self.viewer._begin_scan();current={}
        class Completion:
            def wait(_self,_timeout):
                self.viewer._finish_scan(old)
                current['token']=self.viewer._begin_scan()
                return True
        self.viewer.scan_finished=Completion()
        # Keep the original finish hook independent from the delayed wait fake.
        self.viewer.scan_finished.set=Mock()
        with self.assertRaisesRegex(ValueError,'owner changed'):self.viewer.stop()
        self.assertEqual(coordination.reservation(self.viewer.folder)['token'],current['token'])
        self.assertEqual(self.viewer.scan_token,current['token']);self.assertFalse(self.viewer.scan_done.is_set())
        self.viewer._finish_scan(current['token'])


if __name__=='__main__':unittest.main()
