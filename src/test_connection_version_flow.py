"""Actual root orchestration with fake sessions, no device/controller process."""
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from console_server import ConsoleHTTPServer


class ConnectionVersionFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.sn='QA-GLOVE';self.token='a'*32;self.calls=[]
        self.viewer=N(folder=Path(self.tmp.name),connection_record=Mock(),stop_owned=Mock())
        self.controller=N(official_viz=self.viewer,calibration_handover=N(workflow_lock=threading.RLock()),
            official_cli=N(idle_check=Mock()),calibration_devices=Mock(return_value=[dict(serial=self.sn)]))
        self.service=Mock();self.service.snapshot.return_value=dict(busy=False,locked=False)
        self.server=N(controller=self.controller,firmware=self.service,firmware_transport=N(folder=self.viewer.folder),firmware_preview_idle=Mock())
        self.server.firmware_catalog=N(snapshot=Mock(return_value=dict(busy=False,phase='idle')))

    def invoke(self):return ConsoleHTTPServer.firmware_check_from_viewer(self.server,self.sn,self.token)

    def test_explicit_upgrade_preparation_finishes_only_its_exact_viewer_before_check(self):
        self.viewer.connection_record.side_effect=lambda *a,**kw:self.calls.append('verify exact viewer')
        self.server.firmware_preview_idle.side_effect=lambda *a:self.calls.append('other workspaces idle')
        self.viewer.stop_owned.side_effect=lambda *a,**kw:self.calls.append('stop exact viewer')
        self.controller.calibration_devices.side_effect=lambda **kw:(self.calls.append('fresh discovery') or [dict(serial=self.sn)])
        self.service.start_check.side_effect=lambda *a:self.calls.append('check exact sn')
        self.invoke()
        self.assertEqual(self.calls,['verify exact viewer','other workspaces idle','stop exact viewer','fresh discovery','check exact sn'])
        self.viewer.stop_owned.assert_called_once_with(self.sn,self.token,require_glove=False)
        self.service.start_check.assert_called_once_with(self.sn)
        self.service.start_upgrade.assert_not_called()

    def test_wrong_viewer_token_never_stops_or_checks_another_session(self):
        self.viewer.connection_record.side_effect=ValueError('Viewer changed')
        with self.assertRaises(ValueError):self.invoke()
        self.viewer.stop_owned.assert_not_called();self.service.start_check.assert_not_called()

    def test_active_sibling_blocks_without_ending_local_preview(self):
        self.server.firmware_preview_idle.side_effect=ValueError('Sibling active')
        with self.assertRaises(ValueError):self.invoke()
        self.viewer.stop_owned.assert_not_called();self.service.start_check.assert_not_called()

    def test_unconfirmed_viewer_stop_never_scans_or_starts_maintenance(self):
        self.viewer.stop_owned.side_effect=ValueError('Cleanup unknown')
        with self.assertRaises(ValueError):self.invoke()
        self.controller.calibration_devices.assert_not_called();self.service.start_check.assert_not_called()

    def test_missing_exact_device_after_preview_never_checks_another_device(self):
        self.controller.calibration_devices.return_value=[dict(serial='OTHER-GLOVE')]
        with self.assertRaisesRegex(ValueError,'Selected device'):self.invoke()
        self.service.start_check.assert_not_called()

    def test_explicit_check_reuses_fresh_exact_discovery(self):
        ConsoleHTTPServer.start_firmware_check(self.server,self.sn)
        self.controller.calibration_devices.assert_called_once_with()
        self.service.start_check.assert_called_once_with(self.sn)

    def test_post_upgrade_check_refreshes_expired_discovery_once(self):
        self.controller.calibration_devices.side_effect=[[],[dict(serial=self.sn)]]
        ConsoleHTTPServer.start_firmware_check(self.server,self.sn)
        self.assertEqual(self.controller.calibration_devices.call_args_list,[unittest.mock.call(),unittest.mock.call(fresh=True)])
        self.service.start_check.assert_called_once_with(self.sn)

    def test_post_upgrade_check_never_substitutes_a_different_device(self):
        self.controller.calibration_devices.side_effect=[[],[dict(serial='OTHER-GLOVE')]]
        with self.assertRaisesRegex(ValueError,'Selected device'):
            ConsoleHTTPServer.start_firmware_check(self.server,self.sn)
        self.service.start_check.assert_not_called()

    def test_explicit_check_does_not_rescan_active_sessions(self):
        self.controller.official_cli.idle_check.side_effect=ValueError('Session active')
        with self.assertRaisesRegex(ValueError,'Session active'):
            ConsoleHTTPServer.start_firmware_check(self.server,self.sn)
        self.controller.calibration_devices.assert_not_called();self.service.start_check.assert_not_called()

    def test_readback_never_renders_the_previous_official_terminal_as_current(self):
        self.viewer.snapshot=Mock(return_value={})
        self.server.firmware_transport.run_snapshot=Mock(return_value=dict(id='b'*32,serial=self.sn,source='official_tty',running=False))
        for operation,number,expected in [('check',self.sn,False),('upgrade','OTHER-GLOVE',False),('upgrade',self.sn,True)]:
            with self.subTest(operation=operation,number=number):
                self.service.snapshot.return_value=dict(operation=operation,device=dict(sn=number))
                with patch('bridge_config.load_config',return_value={}),patch('official_device_discovery.use_native',return_value=True):
                    result=ConsoleHTTPServer.maintenance_snapshot(self.server)
                self.assertEqual(result['run'] is not None,expected)

    def test_connection_check_known_recent_result_does_not_query_twice(self):
        self.service.snapshot.return_value=dict(notification=dict(serial=self.sn),checked_at=100.)
        with patch('bridge_config.load_config',return_value={}),patch('official_device_discovery.use_native',return_value=True),patch('native_cli.resolve'),patch('console_server.time.time',return_value=110.):
            ConsoleHTTPServer.check_connect_version(self.server,self.sn)
        self.service.start_check.assert_not_called()

    def test_glove_connection_does_not_open_an_extra_firmware_or_catalog_query(self):
        ConsoleHTTPServer.check_connect_version(self.server, 'WG1J000000000001')
        self.service.start_check.assert_not_called()
        self.server.firmware_catalog.snapshot.assert_not_called()
        self.controller.calibration_devices.assert_not_called()

    def test_confirmed_read_timeout_yields_to_connection_without_an_update(self):
        self.service.snapshot.return_value=dict(busy=True,locked=False)
        self.service.cancel_notification_check.return_value=True
        with patch('bridge_config.load_config',return_value={}),patch('official_device_discovery.use_native',return_value=True),patch('native_cli.resolve'):
            ConsoleHTTPServer.check_connect_version(self.server,self.sn)
        self.service.thread.join.assert_called_once();self.service.cancel_notification_check.assert_called_once_with(timeout=2)
        self.service.start_upgrade.assert_not_called()

    def test_unconfirmed_read_timeout_never_permits_competing_connection(self):
        self.service.snapshot.return_value=dict(busy=True,locked=False)
        self.service.cancel_notification_check.return_value=False
        with patch('bridge_config.load_config',return_value={}),patch('official_device_discovery.use_native',return_value=True),patch('native_cli.resolve'):
            with self.assertRaisesRegex(ValueError,'unconfirmed'):ConsoleHTTPServer.check_connect_version(self.server,self.sn)


if __name__=='__main__':unittest.main()
