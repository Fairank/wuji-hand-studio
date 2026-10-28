"""Update receipt ownership and failure/retry boundaries. No real updater runs."""
import json
import unittest

from test_workbench_update_service import ServiceTests
import workbench_update as module


class ReceiptFlowTests(unittest.TestCase):
    def completed(self, env):
        root=env.data/'updates';root.mkdir(exist_ok=True)
        pointer=dict(app_id=module.APP_ID,repository=module.REPOSITORY,token='a'*32,
                     version=env.service.edition['version'],sha256='b'*64)
        (root/'last-application-update.json').write_text(json.dumps(pointer),'utf-8')
        receipt=root/('apply-'+pointer['token']+'.result.json')
        receipt.write_text(json.dumps(dict(pointer,phase='installed',launch_started=True,version_verified=True)),'utf-8')
        env.service._restore_completed()
        self.assertEqual(env.service.snapshot()['completed_version'],pointer['version'])
        return receipt

    def assert_retired(self, service):
        # Repeated refresh must not restore the historical success into a new flow.
        service._completion_checked_at=0
        self.assertNotIn('completed_version',service.snapshot())
        service._restore_completed()
        self.assertNotIn('completed_version',service.snapshot())

    def test_new_available_release_retires_success_but_preserves_disk_history(self):
        with ServiceTests().environment() as env:
            receipt=self.completed(env)
            before=receipt.read_bytes()
            env.service.start('check');env.service.thread.run_to_end()
            self.assertEqual(env.service.snapshot()['phase'],'available')
            self.assert_retired(env.service)
            self.assertEqual(receipt.read_bytes(),before)

    def test_current_version_check_keeps_verified_current_receipt(self):
        with ServiceTests().environment() as env:
            self.completed(env)
            env.store.check.return_value=dict(available=False,latest_version=env.service.edition['version'])
            env.service.start('check');env.service.thread.run_to_end()
            state=env.service.snapshot()
            self.assertEqual(state['phase'],'up_to_date')
            self.assertEqual(state['completed_version'],state['current_version'])

    def test_failed_check_does_not_reimport_old_success(self):
        with ServiceTests().environment() as env:
            self.completed(env);env.store.check.side_effect=OSError('metadata stalled')
            env.service.start('check');env.service.thread.run_to_end()
            state=env.service.snapshot()
            self.assertEqual(state['error_stage'],'check')
            self.assertFalse(state['cancelled']);self.assert_retired(env.service)

    def test_actual_partial_byte_count_survives_timeout_and_retry_starts_clean(self):
        with ServiceTests().environment() as env:
            self.completed(env);env.service.latest=env.latest
            def download(*args,progress,**kwargs):
                progress(dict(stage='downloading',received_bytes=171048960,total_bytes=260543489))
                raise TimeoutError('Application update preparation timed out')
            env.store.prepare.side_effect=download
            starting=env.service.start('prepare')
            self.assertNotIn('completed_version',starting)
            env.service.thread.run_to_end();state=env.service.snapshot()
            self.assertEqual(state['phase'],'error');self.assertFalse(state['busy'])
            self.assertEqual(state['error_stage'],'download');self.assertFalse(state['cancelled'])
            self.assertEqual(state['progress']['received_bytes'],171048960)
            self.assert_retired(env.service)
            env.store.prepare.side_effect=None
            retry=env.service.start('prepare')
            self.assertIsNone(retry['progress']);self.assertIsNone(retry['error_stage'])
            env.service.thread.run_to_end();self.assertEqual(env.service.snapshot()['phase'],'ready')
            self.assert_retired(env.service);env.popen.assert_not_called()

    def test_cancellation_even_after_store_returns_does_not_commit_ready(self):
        with ServiceTests().environment() as env:
            self.completed(env);env.service.latest=env.latest
            def download(*args,progress,**kwargs):
                progress(dict(stage='downloading',received_bytes=4096,total_bytes=8192))
                env.service.closed.set()
                return env.prepared
            env.store.prepare.side_effect=download
            env.service.start('prepare');env.service.thread.run_to_end()
            state=env.service.snapshot()
            self.assertEqual(state['phase'],'error');self.assertTrue(state['cancelled'])
            self.assertIsNone(state['prepared']);self.assert_retired(env.service)
            env.store.prepare.side_effect=None
            retry=env.service.start('prepare')
            self.assertFalse(retry['cancelled']);self.assertFalse(env.service.closed.is_set())
            env.service.thread.run_to_end();self.assertEqual(env.service.snapshot()['phase'],'ready')
            env.popen.assert_not_called()

    def test_apply_then_helper_failure_is_install_error_not_old_completed(self):
        with ServiceTests().environment() as env:
            self.completed(env);ServiceTests().ready(env)
            applied=env.service.apply()
            self.assertEqual(applied['phase'],'applying');self.assert_retired(env.service)
            env.service.result_file.write_text(json.dumps(dict(token=env.service.apply_token,
                phase='error',error='Installer exited with code 5')),'utf-8')
            env.helper.poll.return_value=1
            state=env.service.snapshot()
            self.assertEqual(state['phase'],'error');self.assertEqual(state['error_stage'],'install')
            self.assert_retired(env.service)
            env.service.start('check');env.service.thread.run_to_end()
            self.assertEqual(env.service.snapshot()['phase'],'ready')
            self.assert_retired(env.service)


if __name__=='__main__':unittest.main()
