"""One explicit saved-profile handoff reuses its pin, then reads live data before open."""
import copy
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from console_server import Controller
import test_saved_calibration_0264 as fixtures
import saved_calibration as saved


class SavedProfileHandoffTests(unittest.TestCase):
    def fixture(self):return fixtures.SavedCalibrationTests()

    def prepare(self,proof,e,**kw):
        method=getattr(saved,'_prepare_pinned_saved_profile',None)
        self.assertTrue(callable(method),'An exact in-flight handoff must prepare its pinned archive without another CLI export')
        return method(proof,registry=e.registry,**kw)

    def test_handoff_has_only_before_and_final_live_exports(self):
        fixture=self.fixture()
        with fixture.environment() as e:
            proof=fixture.preflight(e)
            self.assertEqual(len(e.calls),3)
            prepared=self.prepare(proof,e)
            self.assertEqual(prepared,proof);self.assertIsNot(prepared,proof)
            self.assertEqual(len(e.calls),3,'Pinned local prepare must not launch another CLI')
            self.assertTrue(saved.verify_saved_snapshot(proof,json_command=e.cli,registry=e.registry))
            self.assertEqual(len(e.calls),6)
            self.assertEqual(sum(row[:2]==('user','export') for row in e.calls),2)

    def test_local_pin_keeps_archive_identity_and_cancellation_guards(self):
        fixture=self.fixture()
        for changed in ('archive','owner','proof','cancel'):
            with self.subTest(changed=changed),fixture.environment() as e:
                proof=fixture.preflight(e)
                check=None
                if changed=='archive':
                    from pathlib import Path
                    archive=Path(proof['bundle_path']);data=archive.read_bytes()
                    archive.write_bytes(data.replace(b'original-not-rewritten',b'original-WAS-rewritten'))
                elif changed=='owner':e.owner[0]='same-name-other-owner';e.write_registry()
                elif changed=='proof':proof['serial']='ANOTHER-GLOVE'
                else:check=Mock(side_effect=ValueError('explicit attempt cancelled'))
                with self.assertRaises(ValueError):self.prepare(proof,e,cancel_check=check)
                self.assertEqual(len(e.calls),3)

    def test_final_live_readback_still_detects_changes_after_local_prepare(self):
        fixture=self.fixture()
        for changed in ('same-size-model','owner'):
            with self.subTest(changed=changed),fixture.environment() as e:
                proof=fixture.preflight(e);self.prepare(proof,e)
                if changed=='same-size-model':e.model[0]=b'X'*len(e.model[0])
                else:e.owner[0]='replacement-owner';e.write_registry()
                with self.assertRaises(ValueError):
                    saved.verify_saved_snapshot(proof,json_command=e.cli,registry=e.registry)

    def test_public_prepare_keeps_its_full_live_readback(self):
        fixture=self.fixture()
        with fixture.environment() as e:
            proof=fixture.preflight(e)
            saved.prepare_saved_profile(proof,json_command=e.cli,registry=e.registry)
            self.assertEqual(len(e.calls),6)

    def test_actual_controller_prepare_and_final_callback_keep_only_two_live_exports(self):
        import test_saved_profile_controller_0264 as controller_fixtures
        actual_identity=saved.read_current_sdk_user
        with self.fixture().environment() as e,controller_fixtures.SavedControllerTests().environment() as c:
            proof=self.fixture().preflight(e)
            def identity(profile,**kw):return actual_identity(profile,registry=e.registry)
            with patch('saved_calibration.read_current_sdk_user',side_effect=identity),patch('saved_calibration._command',return_value=e.cli):
                bundle=Controller._prepare_saved_profile(c.c,proof)
                self.assertEqual(len(e.calls),3)
                Controller._start_saved_profile_session(c.c,bundle,proof,expected_profile='hand2_right')
                receipt={k:proof[k] for k in ('source_run_id','source_workspace','source_profile','serial','side','model_sha256','source_kind','transfer_id')}
                receipt.update(owner_user_id=proof['source_user_id'],imported=False)
                settings=c.callbacks[0](receipt)
            self.assertEqual(len(e.calls),6);self.assertEqual(sum(row[:2]==('user','export') for row in e.calls),2)
            self.assertEqual(settings['solver']['engine'],'sdk')
            self.assertEqual(c.c.retarget.selection(c.c.state['device_profile'])['sdk_user'],proof['source_user_id'])

    def test_controller_uses_pin_only_inside_exact_attempt_and_checks_after_copy(self):
        c=Controller.__new__(Controller);proof={'server-private-proof':'synthetic'}
        c.connection_switch=N(_config=Mock(return_value='current-config'))
        c._saved_profile_scope=Mock(return_value={'attempt_id':'a'*32})
        check=Mock();value=copy.deepcopy(proof)
        def pinned(actual,*,cancel_check=None):
            self.assertIs(actual,proof);cancel_check();return value
        with (patch('saved_calibration._prepare_pinned_saved_profile',side_effect=pinned,create=True) as prepare,
                patch('saved_calibration.prepare_saved_profile',side_effect=AssertionError('Redundant live prepare'))):
            result=Controller._prepare_saved_profile(c,proof,cancel_check=check)
        self.assertIs(result,value);prepare.assert_called_once();check.assert_called_once()
        self.assertEqual(c._saved_profile_scope.call_args_list[0].kwargs,{'internal':True})
        self.assertEqual(c._saved_profile_scope.call_args_list[1].kwargs,{'attempt':'a'*32,'config':'current-config'})


if __name__=='__main__':unittest.main()
