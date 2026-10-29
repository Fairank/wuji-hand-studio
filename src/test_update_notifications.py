"""Automatic reminders use synthetic software authorities and exact fake SNs."""
import copy
import json
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
        return dict(glove=dict(connection='receiving',busy=True,session_id=1,glove_serial=sn,
            device_version=dict(serial=sn,firmware_version='1.0.0',source='sdk_handle_info')))

    def observe(self,states):
        # The same current fleet is visible to GET and the explicit observer.
        self.states=copy.deepcopy(states)
        self.monitor.observe(states)

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

    def test_disconnection_never_reopens_a_previously_connected_device(self):
        self.states=[self.connected()];self.idle.side_effect=ValueError('active worker')
        self.rows=[self.fresh()];self.monitor.tick();self.firmware.start_check.assert_not_called()
        self.states=[];self.idle.side_effect=None;self.monitor.tick();self.now+=31;self.monitor.tick()
        self.firmware.start_check.assert_not_called();self.idle.assert_not_called()

    def test_stale_different_or_conflicting_device_does_not_open(self):
        self.observe([self.connected()]);self.monitor.idle_since=self.now-31
        self.rows=[self.fresh('ANOTHER-GLOVE'),dict(self.fresh(),stale=True),dict(self.fresh(),conflict=True)]
        self.monitor.tick();self.firmware.start_check.assert_not_called()

    def test_cached_official_exact_report_creates_stable_notification(self):
        self.observe([self.connected()]);self.monitor.idle_since=self.now-31
        self.firmware.snapshot.return_value=dict(notification=dict(serial='SYNTHETIC-GLOVE',current='1.0.0',latest='1.1.0',device_type='wuji_glove',update_available=True),checked_at=self.now)
        self.monitor.tick();first=self.monitor.snapshot();second=self.monitor.snapshot()
        self.assertEqual(first['items'],second['items']);self.assertEqual(first['revision'],second['revision'])
        self.assertEqual(first['items'][0]['serial'],'SYNTHETIC-GLOVE');self.assertEqual(first['items'][0]['kind'],'firmware')

    def test_changed_actual_firmware_invalidates_old_report(self):
        self.test_cached_official_exact_report_creates_stable_notification()
        state=self.connected();state['glove']['device_version']['firmware_version']='1.1.0'
        self.observe([state]);self.monitor.tick()
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
        self.observe([self.connected()]);self.monitor.idle_since=self.now-31
        self.firmware.snapshot.return_value=dict(notification=dict(serial='SYNTHETIC-GLOVE',current='1.0.0',latest='1.1.0',device_type='wuji_hand_2',update_available=True),checked_at=self.now)
        self.monitor.tick();self.assertEqual(self.monitor.snapshot()['items'],[])

    def test_failed_async_firmware_check_retries_after_backoff_instead_of_a_day(self):
        self.observe([self.connected()]);self.monitor.idle_since=self.now-31
        self.rows=[self.fresh()];self.monitor.pending_firmware=('SYNTHETIC-GLOVE',self.now)
        self.now+=16
        self.firmware.snapshot.return_value=dict(device=dict(sn='SYNTHETIC-GLOVE'),phase='failed',finished_at=self.now,notification=None)
        self.monitor.tick();self.assertEqual(self.monitor.next_check['firmware:SYNTHETIC-GLOVE'],self.now+RETRY)
        self.assertIsNone(self.monitor.pending_firmware);self.firmware.start_check.assert_not_called()

    def test_completion_for_another_serial_does_not_release_pending_check(self):
        self.monitor.pending_firmware=('SYNTHETIC-GLOVE',self.now-1);self.monitor.idle_since=self.now-31
        self.firmware.snapshot.return_value=dict(device=dict(sn='ANOTHER-GLOVE'),phase='failed',finished_at=self.now,notification=None)
        self.monitor.tick();self.assertEqual(self.monitor.pending_firmware[0],'SYNTHETIC-GLOVE')

    def test_fresh_discovery_and_connection_history_never_authorize_a_background_query(self):
        from device_inventory import normalize_devices
        self.observe([self.connected()]);scanned_at=self.now
        raw=[dict(serial='SYNTHETIC-GLOVE',device_type='wuji_glove',handedness='right')]
        self.monitor.devices=lambda:normalize_devices(raw,source='official_cli',observed_at=scanned_at,now=self.now)['devices']
        self.monitor.tick();self.firmware.start_check.assert_not_called()
        self.now+=15;self.monitor.tick()
        self.firmware.start_check.assert_not_called()

    def test_close_during_idle_callback_does_not_start_a_new_device_query(self):
        self.observe([self.connected()]);self.monitor.idle_since=self.now-31;self.rows=[self.fresh()]
        self.idle.side_effect=self.monitor.close
        self.monitor.tick();self.firmware.start_check.assert_not_called()

    def test_close_during_status_read_does_not_restart_metadata_service(self):
        self.app.snapshot.side_effect=lambda:(self.monitor.close() or dict(busy=False))
        self.monitor.tick();self.app.start.assert_not_called();self.cli.start.assert_not_called()
        self.sdk.check_background.assert_not_called()

    def viewer(self,sn='SYNTHETIC-GLOVE'):
        return dict(official_viz=dict(active=True,phase='running',session_id='owned-viewer',
            reported_device=dict(sn=sn,handedness='right'),
            device=dict(sn=sn,device_type='wuji_glove',transport='Udp',address='192.0.2.1:50001',handedness='right')))

    def real_check(self,sn='SYNTHETIC-GLOVE',current='1.0.0',latest='1.0.3'):
        report=dict(devices=[dict(sn=sn,device_type='wuji_glove',current_version=current,
                    latest_version=latest,status='update available',error=None)])
        return dict(device=dict(sn=sn,kind='glove',generation='glove1'),check_report=dict(returncode=0,stdout=json.dumps(report),stderr=''),
                    checked_at=self.now,phase='failed',busy=False,locked=False)

    def test_real_viewer_shape_remembers_exact_family_without_guessing_serial(self):
        self.observe([self.viewer()])
        self.assertEqual(self.monitor.known['SYNTHETIC-GLOVE']['kind'],'glove')
        for changed in (dict(sn='ANOTHER',device_type='wuji_glove'),dict(sn='SYNTHETIC-GLOVE',device_type='unknown'),
                        dict(sn='SYNTHETIC-GLOVE',device_type='wuji_hand_2')):
            self.monitor.known.clear();state=self.viewer();state['official_viz']['device']=changed
            self.observe([state])
            if changed.get('device_type')=='wuji_hand_2':self.assertEqual(self.monitor.known['SYNTHETIC-GLOVE']['kind'],'hand')
            else:self.assertEqual(self.monitor.known,{})

    def test_successful_manual_json_check_is_a_notice_even_while_viewer_is_active(self):
        self.states=[self.viewer()];self.idle.side_effect=ValueError('Viewer owns glove')
        self.firmware.snapshot.return_value=self.real_check()
        self.monitor.tick();items=self.monitor.snapshot()['items']
        self.assertEqual(len(items),1);self.assertEqual(items[0]['kind'],'firmware')
        self.assertEqual(items[0]['current_version'],'1.0.0');self.assertEqual(items[0]['version'],'1.0.3')
        self.firmware.start_check.assert_not_called()

    def test_first_connected_viewer_exposes_completed_preflight_on_next_get_without_tick(self):
        self.observe([self.viewer()]);self.firmware.snapshot.return_value=self.real_check()
        items=self.monitor.snapshot()['items']
        self.assertEqual([(item['current_version'],item['version']) for item in items],[('1.0.0','1.0.3')])
        self.firmware.start_check.assert_not_called();self.app.start.assert_not_called();self.cli.start.assert_not_called()
        self.sdk.check_background.assert_not_called();self.idle.assert_not_called()

    def test_wrong_or_failed_check_cannot_create_notice_from_active_viewer(self):
        self.states=[self.viewer()];self.idle.side_effect=ValueError('Viewer owns glove')
        for state in (self.real_check('ANOTHER'),dict(self.real_check(),check_report=dict(returncode=1,stdout=''))):
            self.monitor.reports.clear();self.firmware.snapshot.return_value=state
            self.monitor.tick();self.assertEqual(self.monitor.snapshot()['items'],[])
        self.firmware.start_check.assert_not_called()

    def test_user_action_cancels_only_background_read_and_defers_new_checks(self):
        self.firmware.cancel_notification_check.return_value=True
        self.observe([self.connected()]);self.monitor.idle_since=self.now-31;self.rows=[self.fresh()]
        self.assertTrue(self.monitor.give_user_priority(timeout=2))
        self.firmware.cancel_notification_check.assert_called_once_with(timeout=2)
        self.monitor.tick();self.firmware.start_check.assert_not_called()
        self.now+=61;self.monitor.tick()
        self.firmware.start_check.assert_not_called()

    def test_user_action_is_not_authorized_when_read_exit_is_unconfirmed(self):
        self.firmware.cancel_notification_check.return_value=False
        self.assertFalse(self.monitor.give_user_priority())
        self.assertGreater(self.monitor.user_priority_until,self.now)

    def test_late_scheduler_dispatch_cannot_overtake_a_user_action(self):
        self.observe([self.connected()]);self.monitor.idle_since=self.now-31;self.rows=[self.fresh()]
        self.firmware.cancel_notification_check.return_value=True
        self.monitor.devices=lambda:(self.monitor.give_user_priority() or copy.deepcopy(self.rows))
        # Return rows after the user priority callback occurs inside inventory
        # reading, after the first deadline check but before final dispatch.
        def rows():self.monitor.give_user_priority();return copy.deepcopy(self.rows)
        self.monitor.devices=rows;self.monitor.tick();self.firmware.start_check.assert_not_called()

    def test_new_official_check_overrides_old_handle_metadata_after_upgrade(self):
        self.observe([self.connected()]);self.firmware.snapshot.return_value=self.real_check()
        self.assertEqual(len(self.monitor.snapshot()['items']),1)
        self.now+=10
        state=self.real_check(current='1.0.3',latest='1.0.3')
        report=json.loads(state['check_report']['stdout']);report['devices'][0]['status']='up to date'
        state['check_report']['stdout']=json.dumps(report);self.firmware.snapshot.return_value=state
        self.assertEqual(self.monitor.snapshot()['items'],[])
        self.assertEqual(self.monitor.snapshot()['devices'][0]['firmware_version'],'1.0.3')
        for _ in range(3):
            self.now+=1;self.observe([self.connected()])
            self.assertEqual(self.monitor.snapshot()['items'],[])
            self.assertEqual(self.monitor.snapshot()['devices'][0]['firmware_version'],'1.0.3')
        self.assertFalse(any(key.startswith('_') for key in self.monitor.snapshot()['devices'][0]))

    def test_new_check_can_update_current_and_available_version_over_older_metadata(self):
        self.observe([self.connected()]);self.firmware.snapshot.return_value=self.real_check()
        self.monitor.snapshot();self.now+=10
        self.firmware.snapshot.return_value=self.real_check(current='1.0.3',latest='1.0.4')
        items=self.monitor.snapshot()['items']
        self.assertEqual([(item['current_version'],item['version']) for item in items],[('1.0.3','1.0.4')])

    def test_new_connection_metadata_invalidates_an_older_contradictory_check(self):
        state=self.connected();state['glove']['session_id']=1
        self.observe([state]);self.firmware.snapshot.return_value=self.real_check()
        self.monitor.snapshot();self.now+=10
        state['glove']['session_id']=2;state['glove']['device_version']['firmware_version']='1.0.3'
        self.observe([state])
        self.assertEqual(self.monitor.snapshot()['items'],[])
        self.assertEqual(self.monitor.snapshot()['devices'][0]['firmware_version'],'1.0.3')


if __name__=='__main__':unittest.main()
