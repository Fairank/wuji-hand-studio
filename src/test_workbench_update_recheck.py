"""A failed install helper cannot poison a later explicit update check."""
import json
import unittest

import test_workbench_update_service as fixtures


class UpdateRecheckTests(unittest.TestCase):
    def failed(self,env,*,exited=True):
        fixtures.ServiceTests().ready(env);env.service.apply()
        path,token=env.service.result_file,env.service.apply_token
        path.write_text(json.dumps(dict(token=token,phase='error',error='Synthetic failed preflight')),encoding='utf-8')
        env.helper.poll.return_value=1 if exited else None
        self.assertEqual(env.service.snapshot()['phase'],'error')
        return path,token

    def test_failed_helper_recheck_reaches_stable_ready_and_new_apply_token(self):
        with fixtures.ServiceTests().environment() as env:
            old,token=self.failed(env);history=old.read_bytes()
            env.service.start('check');env.service.thread.run_to_end()
            for _ in range(3):self.assertEqual(env.service.snapshot()['phase'],'ready')
            self.assertIsNone(env.service.result_file);self.assertEqual(old.read_bytes(),history)
            env.helper.poll.return_value=None;env.service.apply()
            self.assertNotEqual(env.service.apply_token,token)
            # A late write to the old result cannot change this new operation.
            old.write_bytes(history);self.assertEqual(env.service.snapshot()['phase'],'applying')
            self.assertNotEqual(env.service.result_file,old)

    def test_failed_helper_prepare_retry_also_preserves_history_without_poisoning(self):
        with fixtures.ServiceTests().environment() as env:
            old,_=self.failed(env)
            env.service.start('prepare');env.service.thread.run_to_end()
            self.assertEqual(env.service.snapshot()['phase'],'ready');self.assertTrue(old.is_file())
            self.assertEqual(env.service.state['prepared'],env.prepared)

    def test_running_helper_cannot_be_retired_even_with_an_error_report(self):
        with fixtures.ServiceTests().environment() as env:
            old,token=self.failed(env,exited=False)
            with self.assertRaisesRegex(ValueError,'still finishing'):env.service.start('check')
            self.assertEqual(env.service.result_file,old);self.assertEqual(env.service.apply_token,token)
            env.store.check.assert_not_called()
            env.helper.poll.return_value=1;env.service.start('check');env.service.thread.run_to_end()
            self.assertEqual(env.service.snapshot()['phase'],'ready')

    def test_live_helper_without_matching_error_remains_busy(self):
        with fixtures.ServiceTests().environment() as env:
            fixtures.ServiceTests().ready(env);env.service.apply()
            old,token=env.service.result_file,env.service.apply_token
            old.write_text(json.dumps(dict(token='other',phase='error',error='Foreign result')),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'busy'):env.service.start('check')
            self.assertEqual(env.service.result_file,old);self.assertEqual(env.service.apply_token,token)
            env.store.check.assert_not_called()

    def test_dead_helper_without_result_can_recheck_and_retry(self):
        with fixtures.ServiceTests().environment() as env:
            fixtures.ServiceTests().ready(env);env.service.apply();env.helper.poll.return_value=1
            self.assertFalse(env.service.result_file.exists())
            env.service.start('check');env.service.thread.run_to_end()
            self.assertEqual(env.service.snapshot()['phase'],'ready')
            env.helper.poll.return_value=None;env.service.apply();self.assertEqual(env.popen.call_count,2)

    def test_live_failed_helper_cannot_spawn_a_second_apply_or_replace_its_token(self):
        with fixtures.ServiceTests().environment() as env:
            old,token=self.failed(env,exited=False)
            self.assertFalse(env.service.snapshot()['can_apply'])
            with self.assertRaisesRegex(ValueError,'still finishing'):env.service.apply()
            self.assertEqual(env.popen.call_count,1);self.assertEqual(env.service.apply_token,token)
            self.assertEqual(env.service.result_file,old)
            env.helper.poll.return_value=1
            env.popen.return_value=fixtures.SimpleNamespace(poll=fixtures.Mock(return_value=None))
            env.service.apply()
            self.assertEqual(env.popen.call_count,2);self.assertNotEqual(env.service.apply_token,token)
            self.assertEqual(env.service.snapshot()['phase'],'applying')

    def test_unknown_helper_exit_cannot_enable_apply(self):
        with fixtures.ServiceTests().environment() as env:
            old,token=self.failed(env)
            env.helper.poll.side_effect=OSError('Synthetic process query failed')
            self.assertFalse(env.service.snapshot()['can_apply'])
            with self.assertRaisesRegex(ValueError,'still finishing'):env.service.apply()
            self.assertEqual(env.popen.call_count,1);self.assertEqual(env.service.apply_token,token)
            self.assertEqual(env.service.result_file,old)

    def test_unknown_helper_without_result_stays_observable_busy_until_confirmed_exit(self):
        with fixtures.ServiceTests().environment() as env:
            fixtures.ServiceTests().ready(env);env.service.apply()
            old,token=env.service.result_file,env.service.apply_token
            env.helper.poll.side_effect=OSError('Synthetic process query failed')
            state=env.service.snapshot()
            self.assertEqual(state['phase'],'applying');self.assertTrue(state['busy']);self.assertFalse(state['can_apply'])
            with self.assertRaisesRegex(ValueError,'busy'):env.service.start('check')
            with self.assertRaises(ValueError):env.service.apply()
            self.assertEqual(env.popen.call_count,1);self.assertEqual(env.service.apply_token,token)
            self.assertEqual(env.service.result_file,old)
            env.helper.poll.side_effect=None;env.helper.poll.return_value=1
            self.assertEqual(env.service.snapshot()['phase'],'error')
            env.service.start('check');env.service.thread.run_to_end()
            self.assertEqual(env.service.snapshot()['phase'],'ready')


if __name__=='__main__':unittest.main()
