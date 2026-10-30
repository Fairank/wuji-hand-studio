"""Purpose use selects current native wearer before historical run/resume hints."""
import copy
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from test_connection_switch_0264 import PurposeSwitchTests,SN,USER


class CurrentWearerSwitchTests(unittest.TestCase):
    environment=PurposeSwitchTests.environment
    command=PurposeSwitchTests.command

    def helpers(self,e):
        proof=dict(source_kind='saved_profile',source_run_id=None,transfer_id='f'*32,
            serial=SN,side='left',source_profile='Blair',source_user_id='current-native-Blair')
        def preflight(serial,side,profile=None,cancel_check=None):
            self.assertEqual((serial,side,profile),(SN,'left',None))
            self.assertTrue(e.vs['active']);cancel_check();return copy.deepcopy(proof)
        e.c._saved_profile_preflight=Mock(side_effect=preflight)
        e.c._prepare_saved_profile=Mock(side_effect=lambda p,**kw:copy.deepcopy(p))
        def start(bundle,actual,**kw):
            self.assertEqual(bundle,proof);self.assertEqual(actual,proof)
            e.gs['session_id']+=1;e.sdk(SN,proof['source_user_id'])
            e.gs['stream']['solver']=dict(engine='sdk',applied=True)
            return e.gs['session_id']
        e.c._start_saved_profile_session=Mock(side_effect=start)
        return proof

    def old_offer(self,e):
        e.state['calibrated_teleoperation']=dict(can_continue=True,serial=SN,side='left',profile='Alex',
            run_id='a'*32,expected_revision='b'*64)
        e.c.calibrated_teleoperation=N(_current_proof=Mock(),
            execute=Mock(side_effect=AssertionError('Ordinary purpose must not reuse the old Alex result')))

    def test_new_current_native_wearer_precedes_old_completed_run_before_cli_release(self):
        with self.environment('official') as e:
            self.old_offer(e);proof=self.helpers(e)
            result=e.service.start(self.command(e,'teleoperation'))
            self.assertEqual(result['connection_switch']['current_owner']['user_id'],proof['source_user_id'])
            self.assertEqual(e.c._saved_profile_preflight.call_count,1)
            e.c.calibrated_teleoperation.execute.assert_not_called()

    def test_current_native_wearer_precedes_old_resume_hint_without_a_named_profile_override(self):
        with self.environment('official') as e:
            e.service.resume=dict(serial=SN,profile='hand2_left',user_id=USER)
            e.service.resume_config=e.service._config()
            e.service._saved_resume={**e.service.resume,'source_profile':'Alex','config':e.service._config()}
            proof=self.helpers(e)
            result=e.service.start(self.command(e,'teleoperation'))
            self.assertEqual(result['connection_switch']['current_owner']['user_id'],proof['source_user_id'])
            self.assertEqual(e.c._saved_profile_preflight.call_count,1)

    def test_uncalibrated_current_native_wearer_never_falls_back_to_old_completed_model(self):
        with self.environment('official') as e:
            self.old_offer(e);self.helpers(e)
            e.c._saved_profile_preflight.side_effect=ValueError('Current Blair needs calibration')
            with self.assertRaisesRegex(ValueError,'Current Blair needs calibration'):
                e.service.start(self.command(e,'teleoperation'))
            self.assertTrue(e.vs['active']);e.c.official_viz.stop_owned.assert_not_called()
            e.c._start_saved_profile_session.assert_not_called();e.c.calibrated_teleoperation.execute.assert_not_called()

    def test_actual_sdk_owner_precedes_new_native_wearer_without_any_profile_read(self):
        with self.environment('teleoperation') as e:
            self.old_offer(e);self.helpers(e)
            result=e.service.start(self.command(e,'teleoperation'))
            self.assertTrue(result['connection_switch']['unchanged'])
            self.assertEqual(result['connection_switch']['current_owner']['user_id'],USER)
            e.c._saved_profile_preflight.assert_not_called();e.c.calibrated_teleoperation.execute.assert_not_called()

    def test_explicit_sdk_wearer_is_not_overridden_by_current_native_wearer(self):
        with self.environment('official') as e:
            self.old_offer(e);self.helpers(e)
            result=e.service.start(self.command(e,'teleoperation',user_id=USER))
            self.assertEqual(result['connection_switch']['current_owner']['user_id'],USER)
            e.c._saved_profile_preflight.assert_not_called();e.c.calibrated_teleoperation.execute.assert_not_called()


# No base TestCase class is exported for duplicate discovery.
del PurposeSwitchTests
