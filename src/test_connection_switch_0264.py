"""Explicit purpose changes retain exact device identity; all I/O is isolated."""
import copy
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace as N
from unittest.mock import Mock,patch

from device_profiles import profile
from connection_switch import owner
import test_connection_switch as fixtures
SN=fixtures.SN;USER=fixtures.USER


class PurposeSwitchTests(unittest.TestCase):
    @contextmanager
    def environment(self,mode='teleoperation'):
        root=Path(os.environ.get('WUJI_CONTROL_QA_ROOT',tempfile.gettempdir()))/'connection-switch-0264'
        root.mkdir(parents=True,exist_ok=True)
        with patch.object(fixtures,'ROOT',root),fixtures.SwitchTests.environment(self,mode) as result:
            yield result
    command=fixtures.SwitchTests.command
    # These cases exercise the real switch. The inherited fixture replaces only
    # controller/device side effects, and never creates an SDK or CLI process.
    def test_current_sdk_purpose_keeps_session_before_an_offered_calibration(self):
        with self.environment('teleoperation') as e:
            e.state['calibrated_teleoperation']=dict(can_continue=True,serial=SN,side='left',
                run_id='a'*32,expected_revision='b'*64)
            e.c.calibrated_teleoperation=N(_current_proof=Mock(),
                execute=Mock(side_effect=AssertionError('A purpose click must not reopen the current glove')))
            before=copy.deepcopy(e.gs)
            result=e.service.start(self.command(e,'teleoperation'))
            self.assertIs(result['connection_switch']['unchanged'],True)
            self.assertEqual(e.gs,before)
            self.assertEqual(e.calls,[])
            e.c.glove.stop_session_confirmed.assert_not_called()
            e.c.calibrated_teleoperation.execute.assert_not_called()

    def test_inferred_side_comes_from_exact_glove_without_changing_generation(self):
        with self.environment('official') as e:
            e.state['device_profile']=profile('hand1_right')
            original=e.c.action.side_effect
            def action(command):
                result=original(command)
                if command['name']=='glove_open':e.gs['profile']=e.state['device_profile']['id']
                return result
            e.c.action.side_effect=action
            result=e.service.start(self.command(e,'teleoperation',user_id=USER))
            self.assertEqual(result['connection_switch']['target']['profile'],'hand1_left')
            self.assertEqual(e.gs['profile'],'hand1_left')

    def test_explicit_wrong_side_never_releases_or_overrides_the_request(self):
        with self.environment('official') as e:
            with self.assertRaises(ValueError):
                e.service.start(self.command(e,'teleoperation',user_id=USER,profile='hand1_right'))
            e.c.official_viz.stop_owned.assert_not_called()
            self.assertEqual(e.calls,[])

    def test_running_native_identity_precedes_its_old_directory_side(self):
        with self.environment('official') as e:
            e.rows[0]['side']='right';e.rows[0]['stale']=True
            state=e.c.snapshot()
            result=e.service._target(self.command(e,'teleoperation',user_id=USER),state,owner(state))
            self.assertEqual(result['profile'],'hand2_left')
            e.c.official_viz.stop_owned.assert_not_called()
            self.assertEqual(e.calls,[])

    def test_snapshot_current_owner_is_actual_and_history_is_not_an_owner(self):
        with self.environment('official') as e:
            snapshot=e.service.snapshot(e.c.snapshot())
            self.assertEqual(snapshot['current_owner']['serial'],SN)
            self.assertEqual(snapshot['current_owner']['purpose'],'cli_view')
            e.c.official_viz.stop_owned(SN,'a'*32)
            e.service.resume=dict(serial=SN,user_id=USER,profile='hand2_left')
            snapshot=e.service.snapshot(e.c.snapshot())
            self.assertIsNone(snapshot['current_owner'])

    def test_slow_preflight_publishes_exact_cancel_before_releasing_the_viewer(self):
        with self.environment('official') as e:
            command=self.command(e,'teleoperation',user_id=USER)
            entered=threading.Event();release=threading.Event();errors=[]
            original=e.c.device_inventory.side_effect
            def inventory():
                entered.set()
                if not release.wait(2):raise AssertionError('QA preflight was not released')
                return original()
            e.c.device_inventory.side_effect=inventory
            def start():
                try:e.service.start(command)
                except Exception as error:errors.append(str(error))
            worker=threading.Thread(target=start);worker.start()
            try:
                self.assertTrue(entered.wait(1))
                current=e.service.snapshot(e.c.snapshot())
                self.assertIs(current['busy'],True)
                e.service.cancel(dict(name='connection_switch_cancel',request_id=command['request_id'],
                                      attempt_id=current['attempt_id']))
            finally:release.set();worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors),1)
            e.c.official_viz.stop_owned.assert_not_called()
            self.assertEqual(e.calls,[])
            with self.assertRaises(ValueError):e.service.start(command)
            e.c.official_viz.stop_owned.assert_not_called()

    def test_saved_profile_without_sdk_broadcast_uses_pinned_native_bundle(self):
        with self.environment('official') as e:
            proof=dict(source_kind='saved_profile',source_run_id=None,transfer_id='e'*32,
                serial=SN,side='left',source_profile='Alice',source_user_id=USER,
                model_sha256='d'*64,model_bytes=32)
            bundle={**proof,'bundle_path':'/synthetic-only.zip'}
            order=[]
            def preflight(serial,side,profile=None,cancel_check=None):
                self.assertEqual((serial,side),(SN,'left'))
                self.assertIn(profile,(None,'Alice'))
                self.assertTrue(e.vs['active'])
                cancel_check();order.append('preflight');return copy.deepcopy(proof)
            def prepare(actual,*,cancel_check=None):
                self.assertEqual(actual,proof);self.assertFalse(e.vs['active'])
                cancel_check();order.append('prepare');return copy.deepcopy(bundle)
            def start(actual,actual_proof,*,expected_profile,timeout_ms=250):
                self.assertEqual((actual,actual_proof,expected_profile),(bundle,proof,'hand2_left'))
                self.assertEqual(timeout_ms,650)
                order.append('sdk');e.gs['session_id']+=1;e.sdk(SN,USER)
                e.gs['stream']['solver']=dict(engine='sdk',applied=True)
                e.gs['stream']['timeout_ms']=timeout_ms
                return e.gs['session_id']
            e.c._saved_profile_preflight=preflight
            e.c._prepare_saved_profile=prepare
            e.c._start_saved_profile_session=start
            result=e.service.start(self.command(e,'teleoperation',timeout_ms=650))
            self.assertEqual(order,['preflight','prepare','sdk'])
            self.assertEqual(e.calls,[])
            self.assertEqual(result['connection_switch_receipt']['target']['user_id'],USER)
            self.assertEqual(result['connection_switch']['current_owner']['serial'],SN)
            official=self.command(e,'official');official['request_id']='c'*32
            e.service.start(official)
            resumed=self.command(e,'teleoperation',user_id=USER);resumed['request_id']='d'*32
            e.calls.clear();result=e.service.start(resumed)
            self.assertEqual(order,['preflight','prepare','sdk','preflight','prepare','sdk'])
            self.assertEqual(e.calls,[])
            self.assertEqual(result['connection_switch']['current_owner']['user_id'],USER)

    def test_saved_profile_preflight_failure_retains_cli_without_sdk_scan(self):
        with self.environment('official') as e:
            e.c._saved_profile_preflight=Mock(side_effect=ValueError('saved model is unavailable'))
            e.c._prepare_saved_profile=Mock();e.c._start_saved_profile_session=Mock()
            with self.assertRaisesRegex(ValueError,'saved model is unavailable'):
                e.service.start(self.command(e,'teleoperation'))
            e.c.official_viz.stop_owned.assert_not_called()
            self.assertTrue(e.vs['active']);self.assertEqual(e.calls,[])
            e.c._start_saved_profile_session.assert_not_called()

    def test_preflight_timeout_keeps_cli_and_an_explicit_new_request_can_retry(self):
        with self.environment('official') as e:
            now=[0.];e.service.clock=lambda:now[0]
            original=e.c.device_inventory.side_effect
            def delayed():
                result=original();now[0]=46.;return result
            e.c.device_inventory.side_effect=delayed
            with self.assertRaisesRegex(ValueError,'time budget'):
                e.service.start(self.command(e,'teleoperation',user_id=USER))
            self.assertFalse(e.service.state['busy']);self.assertTrue(e.vs['active'])
            self.assertEqual(e.calls,[]);e.c.official_viz.stop_owned.assert_not_called()
            e.c.device_inventory.side_effect=original
            command=self.command(e,'teleoperation',user_id=USER);command['request_id']='c'*32
            result=e.service.start(command)
            self.assertEqual(result['connection_switch']['current_owner']['serial'],SN)

    def test_cancelled_saved_preflight_does_not_poison_a_new_explicit_request(self):
        with self.environment('official') as e:
            proof=dict(source_kind='saved_profile',serial=SN,side='left',source_profile='Alice',source_user_id=USER)
            def cancelled(*args,**kwargs):
                e.service.cancel(dict(name='connection_switch_cancel',request_id=e.service.state['request_id'],
                                      attempt_id=e.service.state['attempt_id']))
                kwargs['cancel_check']();raise AssertionError('Cancellation was lost')
            e.c._saved_profile_preflight=cancelled
            with self.assertRaises(ValueError):e.service.start(self.command(e,'teleoperation'))
            self.assertTrue(e.vs['active']);e.c.official_viz.stop_owned.assert_not_called()
            e.c._saved_profile_preflight=lambda *args,**kwargs:copy.deepcopy(proof)
            e.c._prepare_saved_profile=lambda actual,**kwargs:copy.deepcopy(actual)
            def start(*args,**kwargs):
                e.gs['session_id']+=1;e.sdk();e.gs['stream']['solver']=dict(engine='sdk',applied=True)
                return e.gs['session_id']
            e.c._start_saved_profile_session=start
            command=self.command(e,'teleoperation');command['request_id']='c'*32
            result=e.service.start(command)
            self.assertEqual(result['connection_switch']['current_owner']['user_id'],USER)
            self.assertFalse(result['connection_switch']['cancel_requested'])

    def test_saved_resume_cannot_substitute_same_name_with_another_user_id(self):
        with self.environment('official') as e:
            e.service.resume=dict(serial=SN,profile='hand2_left',user_id=USER)
            e.service.resume_config=e.service._config()
            e.service._saved_resume=dict(**e.service.resume,source_profile='Alice',config=e.service._config())
            e.c._saved_profile_preflight=Mock(return_value=dict(source_kind='saved_profile',
                serial=SN,side='left',source_profile='Alice',source_user_id='same-name-new-user'))
            with self.assertRaises(ValueError):e.service.start(self.command(e,'teleoperation'))
            self.assertTrue(e.vs['active']);self.assertEqual(e.calls,[])
            e.c.official_viz.stop_owned.assert_not_called()

    def test_unchanged_paired_target_does_not_publish_private_mapping_context(self):
        from test_target_mapping_switch import TargetMappingSwitchTests
        with TargetMappingSwitchTests().environment(False) as e:
            e.service.start(e.command())
            command=e.command();command['request_id']='d'*32
            result=e.service.start(command)
            self.assertTrue(result['connection_switch']['unchanged'])
            self.assertFalse(any(key.startswith('_') for key in result['connection_switch']['target']))

