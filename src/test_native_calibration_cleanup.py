"""Exact native cleanup acknowledgement, using fake processes and owned Jobs."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cli_coordination
import native_calibration as native
import test_native_calibration as fixtures


class EndedNativeCleanupTests(unittest.TestCase):
    def ended(self, env):
        env.service.start_human(fixtures.SN, 'left', 900)
        env.owner.run.update(running=False, status='error', cleanup_confirmed=False,
                             error='original result unknown', result=None)
        return env.service.token

    def test_success_acknowledges_only_cleanup_without_claiming_calibration_success(self):
        with fixtures.NativeTests().environment() as env:
            self.ended(env)
            before=copy.deepcopy(env.owner.run)
            result=env.service.cancel()
            env.job.close.assert_called_once_with(timeout=7)
            self.assertEqual(cli_coordination.reservation(env.folder),{})
            self.assertIsNone(env.service.token)
            self.assertEqual(result,{**before,'cleanup_confirmed':True})

    def test_exact_owned_job_can_confirm_cleanup_after_marker_already_disappeared(self):
        with fixtures.NativeTests().environment() as env:
            token=self.ended(env)
            cli_coordination.release(token,env.folder)
            self.assertTrue(env.service.cancel()['cleanup_confirmed'])
            env.job.close.assert_called_once_with(timeout=7)

    def test_failed_job_close_or_marker_release_never_acknowledges_cleanup(self):
        for failure in ('job','release_error','release_retained'):
            with self.subTest(failure=failure),fixtures.NativeTests().environment() as env:
                token=self.ended(env)
                marker=cli_coordination.reservation(env.folder)
                if failure=='job':env.job.close.return_value=False
                options={'side_effect':OSError('synthetic release failure')} if failure=='release_error' else {}
                if failure=='job':options={'wraps':native.release}
                with patch.object(native,'release',**options):
                    with self.assertRaises((ValueError,OSError)):env.service.cancel()
                self.assertFalse(env.owner.run['cleanup_confirmed'])
                self.assertEqual(env.service.token,token)
                self.assertEqual(cli_coordination.reservation(env.folder),marker)

    def test_foreign_or_unknown_marker_is_never_released_or_used_as_current_cleanup(self):
        for marker in ({'kind':'calibration','token':'b'*32},
                       {'kind':'viz','token':'b'*32}, {'kind':'unknown'}):
            with self.subTest(marker=marker),fixtures.NativeTests().environment() as env:
                token=self.ended(env)
                cli_coordination.atomic(env.folder/'session.json',marker)
                with patch.object(native,'release',wraps=native.release) as release:
                    with self.assertRaisesRegex(ValueError,'owner changed'):env.service.cancel()
                release.assert_not_called()
                env.job.close.assert_called_once_with(timeout=7)
                self.assertEqual(cli_coordination.reservation(env.folder),marker)
                self.assertEqual(env.service.token,token)
                self.assertFalse(env.owner.run['cleanup_confirmed'])

    def test_late_close_does_not_confirm_or_release_a_replacement_run_job_or_token(self):
        for replacement in ('run','job','token','running'):
            with self.subTest(replacement=replacement),fixtures.NativeTests().environment() as env:
                self.ended(env)
                marker=cli_coordination.reservation(env.folder)
                new_job=SimpleNamespace(close=Mock(return_value=True))
                def after_close(**_):
                    self.assertFalse(env.owner.lock._is_owned())
                    if replacement=='run':env.owner.run['id']='b'*32
                    elif replacement=='job':env.service.job=new_job
                    elif replacement=='token':env.service.token='b'*32
                    else:env.owner.run['running']=True
                    return True
                env.job.close.side_effect=after_close
                with self.assertRaisesRegex(ValueError,'owner changed'):env.service.cancel()
                self.assertFalse(env.owner.run['cleanup_confirmed'])
                self.assertEqual(cli_coordination.reservation(env.folder),marker)
                new_job.close.assert_not_called()


if __name__=='__main__':unittest.main()
