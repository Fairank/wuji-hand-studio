"""Shared discovery and calibration metadata, isolated mocks without devices."""
import copy
import tempfile
import threading
import time
from pathlib import Path
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
        self.viewer.state.update(devices=[copy.deepcopy(DEVICE)],scanned_at=time.time(),started_at=123)

    def test_async_scan_clears_old_selection_before_worker_runs(self):
        with patch.object(visualization.threading,'Thread',DeferredThread):
            state=self.viewer.scan()
        self.assertTrue(state['scanning']);self.assertEqual(state['devices'],[])
        self.assertIsNone(state['scanned_at']);self.assertEqual(state['started_at'],123)
        self.assertEqual(coordination.reservation(self.folder)['kind'],'scan')

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
