"""Automatic reminders use synthetic software authorities and exact fake SNs."""
import copy
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from update_notifications import UpdateNotifications,INTERVAL,RETRY,FIRMWARE_INTERVAL


class NotificationTests(unittest.TestCase):
    def setUp(self):
        environment=patch.dict(os.environ,{'WUJI_APP_AUTO_CHECK':'1','WUJI_CLI_AUTO_CHECK':'1','WUJI_UPDATE_AUTO_CHECK':'1'})
        environment.start();self.addCleanup(environment.stop)
        self.now=100000.;self.rows=[];self.states=[]
        self.app=Mock();self.app.snapshot.return_value=dict(current_version='0.2.38',latest_version=None,busy=False,available=False)
        self.cli=Mock();self.cli.snapshot.return_value=dict(current=dict(version='2026.10.12'),update=dict(),busy=False)
        self.sdk=Mock();self.sdk.snapshot.return_value=dict(phase='unchecked');self.sdk.check_background.return_value=True
        self.firmware=Mock();self.firmware.snapshot.return_value=dict(busy=False,locked=False,phase='idle')
        self.idle=Mock()
        self.monitor=UpdateNotifications(states=lambda:copy.deepcopy(self.states),app=lambda:self.app,cli=lambda:self.cli,
            sdk=lambda:self.sdk,firmware=lambda:self.firmware,devices=lambda:copy.deepcopy(self.rows),idle=self.idle,clock=lambda:self.now)

    def connected(self,sn='SYNTHETIC-GLOVE'):
        return dict(glove=dict(connection='receiving',glove_serial=sn,
            device_version=dict(serial=sn,firmware_version='1.0.0',source='sdk_handle_info')))

    def fresh(self,sn='SYNTHETIC-GLOVE'):
        return dict(serial=sn,kind='glove',stale=False,selectable=True,conflict=False,in_use=False)

    def test_passive_get_does_not_start_checks_or_scan(self):
        self.monitor.snapshot();self.app.start.assert_not_called();self.cli.start.assert_not_called()
        self.sdk.check_background.assert_not_called();self.firmware.start_check.assert_not_called()

    def test_software_checks_do_not_prepare_install_or_reserve_device(self):
        self.monitor.tick()
        self.app.start.assert_called_once_with('check')
        self.cli.start.assert_called_once_with(check_only=True,automatic=True)
        self.sdk.check_background.assert_called_once();self.assertIn('cancelled',self.sdk.check_background.call_args.kwargs)
        self.firmware.start_check.assert_not_called()

    def test_repeated_ticks_do_not_create_a_check_storm(self):
        self.monitor.tick()
        for _ in range(20):self.now+=15;self.monitor.tick()
        self.assertEqual(self.sdk.check_background.call_count,1)
        self.assertEqual(self.cli.start.call_count,1);self.assertEqual(self.app.start.call_count,1)

    def test_failure_uses_backoff_without_an_available_notice(self):
        self.sdk.check_background.side_effect=OSError('offline')
        self.monitor.tick();self.assertEqual(self.monitor.next_check['sdk'],self.now+RETRY)
        self.assertEqual(self.monitor.snapshot()['items'],[])

    def test_discovery_alone_never_authorizes_firmware_query(self):
        self.rows=[self.fresh()];self.monitor.tick();self.now+=40;self.monitor.tick()
        self.firmware.start_check.assert_not_called()

    def test_explicit_session_defers_firmware_check_until_idle_and_fresh(self):
        self.states=[self.connected()];self.idle.side_effect=ValueError('active worker')
        self.rows=[self.fresh()];self.monitor.tick();self.firmware.start_check.assert_not_called()
        self.states=[];self.idle.side_effect=None;self.monitor.tick();self.now+=31;self.monitor.tick()
        self.firmware.start_check.assert_called_once_with('SYNTHETIC-GLOVE',notification_only=True)

    def test_stale_different_or_conflicting_device_does_not_open(self):
        self.monitor.observe([self.connected()]);self.monitor.idle_since=self.now-31
        self.rows=[self.fresh('ANOTHER-GLOVE'),dict(self.fresh(),stale=True),dict(self.fresh(),conflict=True)]
        self.monitor.tick();self.firmware.start_check.assert_not_called()

    def test_cached_official_exact_report_creates_stable_notification(self):
        self.monitor.observe([self.connected()]);self.monitor.idle_since=self.now-31
        self.firmware.snapshot.return_value=dict(notification=dict(serial='SYNTHETIC-GLOVE',current='1.0.0',latest='1.1.0',device_type='wuji_glove',update_available=True),checked_at=self.now)
        self.monitor.tick();first=self.monitor.snapshot();second=self.monitor.snapshot()
        self.assertEqual(first['items'],second['items']);self.assertEqual(first['revision'],second['revision'])
        self.assertEqual(first['items'][0]['serial'],'SYNTHETIC-GLOVE');self.assertEqual(first['items'][0]['kind'],'firmware')

    def test_changed_actual_firmware_invalidates_old_report(self):
        self.test_cached_official_exact_report_creates_stable_notification()
        state=self.connected();state['glove']['device_version']['firmware_version']='1.1.0'
        self.monitor.observe([state]);self.monitor.tick()
        self.assertEqual(self.monitor.snapshot()['items'],[])

    def test_expired_report_and_unknown_schema_never_report_latest(self):
        self.test_cached_official_exact_report_creates_stable_notification()
        self.now+=FIRMWARE_INTERVAL+1;self.assertEqual(self.monitor.snapshot()['items'],[])
        self.firmware.snapshot.return_value=dict(notification=None,checked_at=self.now)
        self.monitor.tick();self.assertEqual(self.monitor.snapshot()['items'],[])

    def test_software_notice_requires_actual_numeric_newer_release(self):
        self.app.snapshot.return_value.update(available=True,latest_version='0.2.39')
        self.cli.snapshot.return_value['update']=dict(newer_cli_available=True,latest_version='2026.11.1')
        self.sdk.snapshot.return_value=dict(phase='available',release=dict(version='2026.10.12',checked_at=self.now),proof=dict(version='2026.9.22'))
        self.assertEqual({x['kind'] for x in self.monitor.snapshot()['items']},{'workbench','cli','sdk'})
        self.sdk.snapshot.return_value['proof']['version']='2026.11.1'
        self.assertNotIn('sdk',{x['kind'] for x in self.monitor.snapshot()['items']})

    def test_newer_and_unchanged_versions_do_not_suggest_downgrade(self):
        self.app.snapshot.return_value.update(available=True,latest_version='0.2.37')
        self.cli.snapshot.return_value['update']=dict(available=True,latest_version='2026.10.12')
        self.assertEqual(self.monitor.snapshot()['items'],[])

    def test_startup_disabled_in_test_and_child_processes(self):
        for env in ({'WUJI_UPDATE_AUTO_CHECK':'0'},{'WUJI_FLEET_PARENT':'http://127.0.0.1:1'}):
            with patch.dict(os.environ,env),patch('update_notifications.threading.Thread') as thread:
                self.monitor.startup();thread.assert_not_called()

    def test_recent_manual_metadata_check_is_reused(self):
        self.app.snapshot.return_value['checked_at']=self.now-20
        self.cli.snapshot.return_value['update']['checked_at']=self.now-10
        self.monitor.tick();self.app.start.assert_not_called();self.cli.start.assert_not_called()
        self.assertEqual(self.monitor.next_check['workbench'],self.now-20+INTERVAL)

    def test_separately_disabled_software_check_is_not_restarted(self):
        with patch.dict(os.environ,{'WUJI_APP_AUTO_CHECK':'0'}):self.monitor.tick()
        self.app.start.assert_not_called();self.cli.start.assert_called_once()

    def test_expired_sdk_catalog_is_not_a_current_update_notice(self):
        self.sdk.snapshot.return_value=dict(phase='available',release=dict(version='2026.10.12',checked_at=self.now-901),proof=dict(version='2026.9.22'))
        self.assertEqual(self.monitor.snapshot()['items'],[])

    def test_exact_serial_with_conflicting_official_type_is_not_a_notice(self):
        self.monitor.observe([self.connected()]);self.monitor.idle_since=self.now-31
        self.firmware.snapshot.return_value=dict(notification=dict(serial='SYNTHETIC-GLOVE',current='1.0.0',latest='1.1.0',device_type='wuji_hand_2',update_available=True),checked_at=self.now)
        self.monitor.tick();self.assertEqual(self.monitor.snapshot()['items'],[])

    def test_failed_async_firmware_check_retries_after_backoff_instead_of_a_day(self):
        self.monitor.observe([self.connected()]);self.monitor.idle_since=self.now-31
        self.rows=[self.fresh()];self.monitor.tick();started=self.now
        self.now+=16
        self.firmware.snapshot.return_value=dict(device=dict(sn='SYNTHETIC-GLOVE'),phase='failed',finished_at=self.now,notification=None)
        self.monitor.tick();self.assertEqual(self.monitor.next_check['firmware:SYNTHETIC-GLOVE'],self.now+RETRY)
        self.assertIsNone(self.monitor.pending_firmware);self.assertEqual(self.firmware.start_check.call_count,1)

    def test_completion_for_another_serial_does_not_release_pending_check(self):
        self.monitor.pending_firmware=('SYNTHETIC-GLOVE',self.now-1);self.monitor.idle_since=self.now-31
        self.firmware.snapshot.return_value=dict(device=dict(sn='ANOTHER-GLOVE'),phase='failed',finished_at=self.now,notification=None)
        self.monitor.tick();self.assertEqual(self.monitor.pending_firmware[0],'SYNTHETIC-GLOVE')

    def test_next_background_tick_can_use_real_thirty_second_discovery_authority(self):
        from device_inventory import normalize_devices
        self.monitor.observe([self.connected()]);scanned_at=self.now
        raw=[dict(serial='SYNTHETIC-GLOVE',device_type='wuji_glove',handedness='right')]
        self.monitor.devices=lambda:normalize_devices(raw,source='official_cli',observed_at=scanned_at,now=self.now)['devices']
        self.monitor.tick();self.firmware.start_check.assert_not_called()
        self.now+=15;self.monitor.tick()
        self.firmware.start_check.assert_called_once_with('SYNTHETIC-GLOVE',notification_only=True)

    def test_close_during_idle_callback_does_not_start_a_new_device_query(self):
        self.monitor.observe([self.connected()]);self.monitor.idle_since=self.now-31;self.rows=[self.fresh()]
        self.idle.side_effect=self.monitor.close
        self.monitor.tick();self.firmware.start_check.assert_not_called()

    def test_close_during_status_read_does_not_restart_metadata_service(self):
        self.app.snapshot.side_effect=lambda:(self.monitor.close() or dict(busy=False))
        self.monitor.tick();self.app.start.assert_not_called();self.cli.start.assert_not_called()
        self.sdk.check_background.assert_not_called()


if __name__=='__main__':unittest.main()
