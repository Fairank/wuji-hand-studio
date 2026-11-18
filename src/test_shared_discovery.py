"""Shared discovery and calibration metadata, isolated mocks without devices."""
import copy
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

import calibration_cli as calibration
import cli_coordination as coordination
import official_visualization as visualization


SERIAL='WG_SYNTHETIC_LEFT'
DEVICE=dict(sn=SERIAL,model='Wuji Glove',handedness='left',address='192.168.1.100:50001')
CONFIG=dict(mode='local',cli='wuji')


class DeferredThread:
    def __init__(self,target=None,args=(),**kwargs):
        self.target,self.args,self.started=target,args,False
    def start(self):self.started=True
    def is_alive(self):return False


class ImmediateThread(DeferredThread):
    def start(self):
        self.started=True;self.target(*self.args)


class SharedScanTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.folder=Path(temp.name)/'cli'
        self.viewer=visualization.OfficialVisualization(self.folder)
        self.viewer.observation_config=copy.deepcopy(CONFIG)
        self.viewer.state.update(devices=[copy.deepcopy(DEVICE)],scanned_at=time.time(),started_at=123)

    def test_async_scan_retains_completed_observations_without_renewing_them(self):
        before=self.viewer.snapshot()
        with patch('bridge_config.load_config',return_value=CONFIG),patch.object(visualization.threading,'Thread',DeferredThread):
            state=self.viewer.scan()
        self.assertTrue(state['scanning']);self.assertEqual(state['devices'],before['devices'])
        self.assertEqual(state['scanned_at'],before['scanned_at']);self.assertEqual(state['started_at'],123)
        self.assertEqual(coordination.reservation(self.folder)['kind'],'scan')

    def seed_sdk(self):
        row=dict(sn='WG_OLD_TYPED',device_type='wuji_glove')
        self.viewer.state.update(sdk_devices=[row],sdk_scanned_at=time.time()-2,scan_completed_at=time.time()-1)
        self.viewer.observation_config=copy.deepcopy(CONFIG)
        return copy.deepcopy(self.viewer.state)

    def test_native_result_publishes_without_renewing_the_pending_sdk_source(self):
        old=self.seed_sdk();native=dict(sn='WG_NEW_NATIVE',device_type='wuji_glove')
        sdk=dict(sn='WG_NEW_TYPED',device_type='wuji_glove');observed=[]
        def supplement(*_):
            interim=self.viewer.snapshot();observed.append(time.time())
            self.assertEqual(interim['devices'],[native])
            self.assertEqual(interim['sdk_devices'],old['sdk_devices'])
            self.assertGreaterEqual(interim['scanned_at'],old['scanned_at'])
            self.assertEqual(interim['sdk_scanned_at'],old['sdk_scanned_at'])
            self.assertEqual(interim['scan_completed_at'],old['scan_completed_at'])
            return dict(devices=[sdk],observed_at=observed[0])
        self.viewer.supplement_scan=supplement
        with patch('bridge_config.load_config',return_value=CONFIG),patch.object(calibration,'cli_json',return_value={'devices':[native]}):
            token=self.viewer._begin_scan();self.assertEqual(self.viewer._collect_scan(token,True,True),[native])
        state=self.viewer.snapshot()
        self.assertEqual(state['devices'],[native]);self.assertEqual(state['sdk_devices'],[sdk])
        self.assertEqual(state['sdk_scanned_at'],observed[0])
        self.assertGreaterEqual(state['scan_completed_at'],observed[0]);self.assertFalse(state['scanning'])

    def test_native_only_hand_is_selectable_during_long_sdk_preparation_without_extending_ttl(self):
        from device_inventory import build_inventory
        clock=SimpleNamespace(now=1000.);observed=[]
        native=dict(sn='WH2_NATIVE_ONLY',generation='hand2',handedness='right')
        self.viewer.state.update(devices=[],scanned_at=None,sdk_devices=[],sdk_scanned_at=None)
        self.viewer.observation_config=None
        def supplement(*_):
            state=self.viewer.snapshot();directory=build_inventory(viz=state,now=clock.now)
            observed.append(copy.deepcopy(directory))
            self.assertEqual([row['sn'] for row in directory['devices']],[native['sn']])
            self.assertTrue(directory['devices'][0]['selectable'])
            self.assertEqual(state['scanned_at'],1000.);self.assertIsNone(state['scan_completed_at'])
            clock.now=1035.
            return dict(devices=[],observed_at=clock.now)
        self.viewer.supplement_scan=supplement
        with patch.object(visualization,'time',SimpleNamespace(time=lambda:clock.now)), \
                patch('bridge_config.load_config',return_value=CONFIG),patch.object(calibration,'cli_json',return_value={'devices':[native]}):
            token=self.viewer._begin_scan();self.assertEqual(self.viewer._collect_scan(token,True,True),[native])
            state=self.viewer.snapshot()
        self.assertTrue(observed);self.assertIsNone(state['sdk_error'])
        self.assertEqual(state['scanned_at'],1000.);self.assertEqual(state['scan_completed_at'],1035.)
        expired=build_inventory(viz=state,now=1035.)['devices'][0]
        self.assertTrue(expired['stale']);self.assertFalse(expired['selectable'])

    def test_completed_empty_directory_withdraws_both_previous_sources(self):
        old=self.seed_sdk();self.viewer.supplement_scan=lambda *_:dict(devices=[],observed_at=time.time())
        with patch('bridge_config.load_config',return_value=CONFIG),patch.object(calibration,'cli_json',return_value={'devices':[]}):
            token=self.viewer._begin_scan();self.assertEqual(self.viewer._collect_scan(token,True,True),[])
        state=self.viewer.snapshot()
        self.assertEqual(state['devices'],[]);self.assertEqual(state['sdk_devices'],[])
        self.assertGreater(state['scan_completed_at'],old['scan_completed_at']);self.assertFalse(state['scanning'])

    def test_soft_sdk_failure_publishes_only_current_native_observations(self):
        self.seed_sdk();native=dict(sn='WG_NEW_NATIVE',device_type='wuji_glove')
        self.viewer.supplement_scan=Mock(side_effect=ValueError('synthetic SDK unavailable'))
        with patch('bridge_config.load_config',return_value=CONFIG),patch.object(calibration,'cli_json',return_value={'devices':[native]}):
            token=self.viewer._begin_scan();self.viewer._collect_scan(token,True,True)
        state=self.viewer.snapshot()
        self.assertEqual(state['devices'],[native]);self.assertEqual(state['sdk_devices'],[])
        self.assertIsNone(state['sdk_scanned_at']);self.assertEqual(state['sdk_error'],'synthetic SDK unavailable')

    def test_cancel_or_configuration_change_discards_previous_and_current_rows(self):
        for change in ('cancel','config'):
            with self.subTest(change=change):
                self.viewer.state.update(devices=[copy.deepcopy(DEVICE)],scanned_at=time.time())
                old=self.seed_sdk();config=[dict(CONFIG)]
                def supplement(*_):
                    if change=='cancel':self.viewer.scan_cancel.set()
                    else:config[0]['cli']='different-command'
                    return dict(devices=[dict(sn='WG_NEW_TYPED',device_type='wuji_glove')],observed_at=time.time())
                self.viewer.supplement_scan=supplement
                with patch('bridge_config.load_config',side_effect=lambda:copy.deepcopy(config[0])),patch.object(calibration,'cli_json',return_value={'devices':[DEVICE]}):
                    token=self.viewer._begin_scan();self.assertIsNone(self.viewer._collect_scan(token,True,True))
                state=self.viewer.snapshot()
                self.assertEqual(state['devices'],[]);self.assertEqual(state['sdk_devices'],[])
                self.assertIsNone(state['scanned_at']);self.assertIsNone(state['sdk_scanned_at'])
                self.assertFalse(state['scanning']);self.assertGreater(state['scan_completed_at'],old['scan_completed_at'])

    def test_refresh_retention_does_not_extend_the_device_expiry(self):
        from device_inventory import build_inventory
        before=self.seed_sdk()
        with patch('bridge_config.load_config',return_value=CONFIG):token=self.viewer._begin_scan()
        state=self.viewer.snapshot()
        inventory=build_inventory(viz=state,now=max(before['scanned_at'],before['sdk_scanned_at'])+31)
        self.assertTrue(inventory['devices']);self.assertTrue(all(row['stale'] and not row['selectable'] for row in inventory['devices']))
        self.assertEqual(state['scanned_at'],before['scanned_at']);self.assertEqual(state['sdk_scanned_at'],before['sdk_scanned_at'])
        self.viewer._finish_scan(token)

    def test_new_controller_configuration_cannot_retain_another_directory(self):
        self.seed_sdk()
        with patch('bridge_config.load_config',return_value=dict(CONFIG,cli='other-command')):
            token=self.viewer._begin_scan()
        state=self.viewer.snapshot()
        self.assertEqual(state['devices'],[]);self.assertEqual(state['sdk_devices'],[])
        self.assertIsNone(state['scanned_at']);self.assertIsNone(state['sdk_scanned_at'])
        self.assertIsNone(state['scan_completed_at']);self.viewer._finish_scan(token)

    def test_explicit_discovery_priority_retains_only_same_config_completed_rows(self):
        for changed in (False,True):
            with self.subTest(configuration_changed=changed):
                self.viewer.state.update(devices=[copy.deepcopy(DEVICE)],scanned_at=time.time())
                old=self.seed_sdk();config=[copy.deepcopy(CONFIG)];entered=threading.Event()
                def supplement(*_):
                    entered.set()
                    if not self.viewer.scan_cancel.wait(2):raise TimeoutError('Synthetic priority was not requested')
                    if changed:config[0]['cli']='different-command'
                    return dict(devices=[],observed_at=time.time())
                self.viewer.supplement_scan=supplement
                with patch('bridge_config.load_config',side_effect=lambda:copy.deepcopy(config[0])),patch.object(calibration,'cli_json',return_value={'devices':[]}):
                    token=self.viewer._begin_scan()
                    reader=threading.Thread(target=self.viewer._collect_scan,args=(token,True,True),daemon=True)
                    reader.start();self.assertTrue(entered.wait(2))
                    partial=self.viewer.snapshot()
                    with self.viewer.discovery_priority(timeout=3):
                        state=self.viewer.snapshot();self.assertFalse(state['scanning'])
                        self.assertEqual(state['devices'],[])
                        self.assertEqual(state['sdk_devices'],[] if changed else old['sdk_devices'])
                        self.assertEqual(state['scanned_at'],None if changed else partial['scanned_at'])
                        self.assertEqual(state['sdk_scanned_at'],None if changed else old['sdk_scanned_at'])
                    reader.join(2);self.assertFalse(reader.is_alive())

    def test_failed_scan_clears_all_selectable_devices(self):
        with patch('bridge_config.load_config',return_value=CONFIG),patch.object(calibration,'cli_json',side_effect=ValueError('synthetic failure')), \
             patch.object(visualization.threading,'Thread',ImmediateThread):
            state=self.viewer.scan()
        self.assertEqual(state['devices'],[]);self.assertIsNone(state['scanned_at'])
        self.assertEqual(state['error'],'synthetic failure');self.assertFalse(state['scanning'])
        self.assertEqual(state['started_at'],123);self.assertEqual(coordination.reservation(self.folder),{})

    def test_inline_scan_returns_same_verified_result_without_thread(self):
        raw={'devices':[copy.deepcopy(DEVICE)]}
        with patch('bridge_config.load_config',return_value=CONFIG),patch.object(calibration,'cli_json',return_value=raw) as cli, \
             patch.object(visualization.threading,'Thread') as worker:
            devices=self.viewer.discover_inline()
        cli.assert_called_once_with(CONFIG,'devices');worker.assert_not_called()
        self.assertEqual(devices,[DEVICE]);self.assertEqual(self.viewer.snapshot()['devices'],[DEVICE])
        devices[0]['sn']='caller edit';raw['devices'][0]['sn']='metadata edit'
        self.assertEqual(self.viewer.snapshot()['devices'],[DEVICE])
        self.assertFalse(self.viewer.snapshot()['busy']);self.assertEqual(coordination.reservation(self.folder),{})

    def test_inline_scan_error_is_not_empty_success(self):
        with patch('bridge_config.load_config',return_value=CONFIG),patch.object(calibration,'cli_json',side_effect=ValueError('not discovered')):
            with self.assertRaisesRegex(ValueError,'not discovered'):self.viewer.discover_inline()
        self.assertEqual(self.viewer.snapshot()['devices'],[]);self.assertEqual(coordination.reservation(self.folder),{})

    def test_inline_empty_scan_is_valid_empty_result(self):
        with patch('bridge_config.load_config',return_value=CONFIG),patch.object(calibration,'cli_json',return_value={'devices':[]}):
            self.assertEqual(self.viewer.discover_inline(),[])
        self.assertIsNotNone(self.viewer.snapshot()['scanned_at'])

    def test_worker_start_failure_releases_reservation(self):
        with patch.object(visualization.threading,'Thread') as worker:
            worker.return_value.start.side_effect=RuntimeError('thread unavailable')
            with self.assertRaisesRegex(RuntimeError,'thread unavailable'):self.viewer.scan()
        state=self.viewer.snapshot()
        self.assertFalse(state['busy']);self.assertFalse(state['scanning'])
        self.assertEqual(state['devices'],[]);self.assertEqual(coordination.reservation(self.folder),{})

    def test_idle_rejection_never_runs_scan_and_releases_reservation(self):
        self.viewer.idle_check=Mock(side_effect=ValueError('other workspace busy'))
        with patch.object(calibration,'cli_json') as cli:
            with self.assertRaisesRegex(ValueError,'workspace busy'):self.viewer.discover_inline()
        cli.assert_not_called();self.assertEqual(coordination.reservation(self.folder),{})

    def test_existing_viewer_marker_cannot_be_replaced_by_scan(self):
        with coordination.gate(self.folder):token=coordination.reserve('viz',self.folder)
        with patch.object(calibration,'cli_json') as cli:
            with self.assertRaises(ValueError):self.viewer.discover_inline()
        cli.assert_not_called();self.assertEqual(coordination.reservation(self.folder)['token'],token)

    def test_start_requires_recent_successful_scan_and_exact_identity(self):
        for scanned,error in ((None,None),(time.time()-31,None),(time.time()+10,None),(time.time(),'failed scan')):
            self.viewer.state.update(scanned_at=scanned,error=error)
            with self.subTest(scanned=scanned,error=error),patch('bridge_config.load_config',return_value=CONFIG),patch.object(visualization.threading,'Thread') as worker:
                with self.assertRaisesRegex(ValueError,'Find devices again'):self.viewer.start(SERIAL)
                worker.assert_not_called()
            self.assertEqual(coordination.reservation(self.folder),{})

    def test_priority_confirms_its_late_factory_cleanup_before_reusing_completed_rows(self):
        from official_device_discovery import sdk_devices
        old=self.seed_sdk();entered=threading.Event();release=threading.Event();unconfirmed=[]
        client=SimpleNamespace(close=Mock(return_value=True),exec_command=Mock(side_effect=AssertionError('No late SDK launch')))
        def factory():
            entered.set()
            if not release.wait(7):raise TimeoutError('Fixture factory not released')
            return client
        self.viewer.supplement_scan=lambda config,cancelled,token:sdk_devices(factory,cancelled=cancelled)
        with patch('bridge_config.load_config',return_value=CONFIG),patch.object(calibration,'cli_json',return_value={'devices':old['devices']}):
            token=self.viewer._begin_scan();finished=self.viewer.scan_finished
            worker=threading.Thread(target=self.viewer._collect_scan,args=(token,True,True),daemon=True);worker.start()
            self.assertTrue(entered.wait(2))
            partial=self.viewer.snapshot()
            def release_after_unknown():
                if finished.wait(5):
                    unconfirmed.append(self.viewer.snapshot()['scan_cleanup_required']);release.set()
            releaser=threading.Thread(target=release_after_unknown,daemon=True);releaser.start()
            try:
                with self.viewer.discovery_priority(timeout=4):
                    state=self.viewer.snapshot()
                    self.assertFalse(state['scanning']);self.assertFalse(state['scan_cleanup_required'])
                    self.assertIsNone(state['error']);self.assertEqual(coordination.reservation(self.folder),{})
                    self.assertEqual(state['devices'],old['devices']);self.assertEqual(state['sdk_devices'],old['sdk_devices'])
                    self.assertEqual(state['scanned_at'],partial['scanned_at']);self.assertEqual(state['sdk_scanned_at'],old['sdk_scanned_at'])
            finally:release.set();worker.join(2);releaser.join(2)
        self.assertEqual(unconfirmed,[True]);client.exec_command.assert_not_called();client.close.assert_called()

    def test_expected_scan_cleanup_never_closes_or_releases_a_replacement(self):
        with patch('bridge_config.load_config',return_value=CONFIG):
            old=self.viewer._begin_scan();self.viewer._finish_scan(old)
            current=self.viewer._begin_scan()
        job=SimpleNamespace(close=Mock(return_value=True));self.viewer.scan_job=job;self.viewer.scan_needs_close=True
        with self.assertRaisesRegex(ValueError,'owner changed'):
            self.viewer._stop_scan(expected_scan=old,cleanup_timeout=0)
        job.close.assert_not_called();self.assertEqual(self.viewer.scan_token,current)
        self.assertEqual(coordination.reservation(self.folder)['token'],current)
        self.viewer.scan_needs_close=False;self.viewer._finish_scan(current)
        self.viewer.state.update(scanned_at=time.time(),error=None)
        with patch('bridge_config.load_config',return_value=CONFIG),patch.object(visualization.threading,'Thread') as worker:
            with self.assertRaisesRegex(ValueError,'select a discovered'):self.viewer.start('WG_OTHER')
            worker.assert_not_called()


class SharedCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        self.provider=Mock(return_value=[copy.deepcopy(DEVICE)])
        self.check=Mock()
        self.service=calibration.CalibrationCLI(device_provider=self.provider,metadata_check=self.check)
        self.config_patch=patch.object(calibration,'load_config',return_value=CONFIG)
        self.config_patch.start();self.addCleanup(self.config_patch.stop)
        self.cli_patch=patch.object(calibration,'cli_json',side_effect=self.cli)
        self.mock_cli=self.cli_patch.start();self.addCleanup(self.cli_patch.stop)

    def cli(self,config,*args):
        self.calls.append(args)
        if args==('user','list'):return {'users':[{'name':'Demo','is_default':False}]}
        if args==('user','show'):return {'name':'Demo','left_hand':{'calibrated':False},'right_hand':{'calibrated':False}}
        raise AssertionError('Metadata must never perform discovery: '+repr(args))

    def test_snapshot_only_consumes_cached_devices(self):
        state=self.service.snapshot(refresh=True)
        self.assertTrue(state['available']);self.assertTrue(state['metadata_current'])
        self.assertEqual(state['devices'],[DEVICE]);self.assertIsNotNone(state['metadata_checked_at'])
        self.provider.assert_called_once_with(False)
        self.assertEqual(self.calls,[('user','list'),('user','show')])

    def test_fresh_shared_inventory_replaces_cached_selection_without_cli_refresh(self):
        self.service.snapshot(refresh=True);self.mock_cli.reset_mock()
        self.provider.return_value=[]
        self.assertEqual(self.service.snapshot()['devices'],[])
        self.mock_cli.assert_not_called()

    def test_provider_failure_does_not_destroy_readable_metadata(self):
        self.provider.side_effect=ValueError('inventory unavailable')
        state=self.service.snapshot(refresh=True)
        self.assertTrue(state['available']);self.assertEqual(state['devices'],[])
        self.assertEqual(state['devices_error'],'inventory unavailable')

    def test_metadata_busy_preserves_profiles_but_marks_them_unverified(self):
        first=self.service.snapshot(refresh=True);self.mock_cli.reset_mock()
        self.check.side_effect=ValueError('Viewer owns devices')
        state=self.service.snapshot(refresh=True)
        self.assertFalse(state['available']);self.assertFalse(state['metadata_current'])
        self.assertEqual(state['metadata_error'],'Viewer owns devices')
        self.assertEqual(state['users'],first['users']);self.assertEqual(state['current'],first['current'])
        self.assertEqual(state['metadata_checked_at'],first['metadata_checked_at'])
        self.assertEqual(state['devices'],[DEVICE]);self.mock_cli.assert_not_called()
        self.check.side_effect=None
        self.assertTrue(self.service.snapshot(refresh=True)['metadata_current'])

    def test_metadata_failure_still_keeps_shared_devices(self):
        self.mock_cli.side_effect=ValueError('Linux metadata unavailable')
        state=self.service.snapshot(refresh=True)
        self.assertFalse(state['available']);self.assertEqual(state['devices'],[DEVICE])
        self.assertEqual(state['metadata_error'],'Linux metadata unavailable')

    def test_concurrent_refresh_returns_unverified_cache_without_second_cli(self):
        entered,finish=threading.Event(),threading.Event()
        first_result=[]
        def blocking(config,*args):
            if args==('user','list'):
                entered.set()
                if not finish.wait(3):raise TimeoutError('test timeout')
            return self.cli(config,*args)
        self.mock_cli.side_effect=blocking
        worker=threading.Thread(target=lambda:first_result.append(self.service.snapshot(refresh=True)))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            state=self.service.snapshot(refresh=True)
            self.assertFalse(state['available']);self.assertTrue(state['metadata_refreshing'])
            self.assertFalse(state['metadata_current']);self.assertEqual(state['devices'],[DEVICE])
        finally:finish.set();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertTrue(first_result[0]['available'])
        self.assertEqual(self.calls,[('user','list'),('user','show')])

    def test_start_does_not_deadlock_against_inflight_metadata_publisher(self):
        entered,finish=threading.Event(),threading.Event()
        first_result=[]
        def blocking(config,*args):
            if args==('user','list'):
                entered.set()
                if not finish.wait(3):raise TimeoutError('test timeout')
            return self.cli(config,*args)
        self.mock_cli.side_effect=blocking
        worker=threading.Thread(target=lambda:first_result.append(self.service.snapshot(refresh=True)))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            with patch.object(calibration.subprocess,'Popen') as spawn:
                with self.assertRaisesRegex(ValueError,'refreshing'):self.service.start('left',SERIAL)
                spawn.assert_not_called()
        finally:finish.set();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertTrue(first_result[0]['available'])

    def test_start_requires_explicit_new_shared_discovery_before_spawn(self):
        self.provider.side_effect=lambda fresh=False:[copy.deepcopy(DEVICE)] if not fresh else []
        with patch.object(calibration.subprocess,'Popen') as spawn:
            with self.assertRaisesRegex(ValueError,'latest official scan'):self.service.start('left',SERIAL)
            spawn.assert_not_called()
        self.assertEqual([call.args for call in self.provider.call_args_list],[(False,),(True,)])

    def test_new_shared_discovery_side_mismatch_rejects(self):
        self.provider.side_effect=lambda fresh=False:[dict(DEVICE,handedness='right')] if fresh else [copy.deepcopy(DEVICE)]
        with patch.object(calibration.subprocess,'Popen') as spawn:
            with self.assertRaisesRegex(ValueError,'side does not match'):self.service.start('left',SERIAL)
            spawn.assert_not_called()

    def test_new_shared_discovery_failure_prevents_spawn(self):
        def provider(fresh=False):
            if fresh:raise ValueError('scan ownership rejected')
            return [copy.deepcopy(DEVICE)]
        self.provider.side_effect=provider
        with patch.object(calibration.subprocess,'Popen') as spawn:
            with self.assertRaisesRegex(ValueError,'ownership rejected'):self.service.start('left',SERIAL)
            spawn.assert_not_called()

    def test_valid_new_shared_discovery_still_uses_named_user_and_serial(self):
        with patch.object(calibration.subprocess,'Popen') as spawn,patch.object(calibration.threading,'Thread',DeferredThread):
            self.service.start('left',SERIAL)
        self.assertEqual(spawn.call_count,1)
        command=spawn.call_args.args[0]
        self.assertIn(SERIAL,command);self.assertIn('Demo',command)
        self.assertEqual([call.args for call in self.provider.call_args_list],[(False,),(True,),(False,)])
        self.assertTrue(self.service.run['running'])

    def test_default_user_rejection_does_not_request_fresh_scan(self):
        def cli(config,*args):
            value=self.cli(config,*args)
            if args==('user','show'):value['name']='Default'
            return value
        self.mock_cli.side_effect=cli
        with patch.object(calibration.subprocess,'Popen') as spawn:
            with self.assertRaisesRegex(ValueError,'named SDK user'):self.service.start('left',SERIAL)
            spawn.assert_not_called()
        self.provider.assert_called_once_with(False)


if __name__=='__main__':unittest.main()
