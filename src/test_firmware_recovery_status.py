"""Recovery publishes actual readback facts; synthetic host, no device access."""
import copy
import json
import unittest
from unittest.mock import Mock

from test_firmware_service import FakeHost,SN
from update_notifications import UpdateNotifications


class FirmwareRecoveryStatusTests(unittest.TestCase):
    def setUp(self):
        self.host=FakeHost();self.service=self.host.service()

    def report(self,current='2.5.0',latest='2.5.1'):
        return dict(exited=True,returncode=0,stderr='',stdout=json.dumps(dict(devices=[dict(
            sn=SN,device_type='wuji_hand_2',current_version=current,latest_version=latest,
            status='update available' if current!=latest else 'up to date',error=None)])))

    def join(self):
        self.service.thread.join(3);self.assertFalse(self.service.thread.is_alive())
        return self.service.snapshot()

    def unknown(self):
        self.host.results=[self.report()]
        self.service.start_check(SN);checked=self.join()
        self.host.results=[dict(exited=False,returncode=None)]
        self.service.start_upgrade(checked['preview']['token'],confirmed=True)
        self.assertEqual(self.join()['phase'],'unknown')
        return checked

    def recovery(self,report):
        self.host.recovery=dict(process_exited=True,identity_verified=True,sn=SN,
            reservation_token='reservation-1',firmware_version='2.5.1')
        if report is not None:self.host.recovery['verification_report']=report
        self.host.now+=4
        return self.service.verify_recovery()

    def monitor(self,initial):
        services=[Mock() for _ in range(4)]
        for service in services:service.snapshot.return_value={}
        app,cli,sdk,firmware=services;firmware.snapshot.side_effect=self.service.snapshot
        connected=dict(connection='connected',device_id=SN,session_generation=1,
            identified_device=dict(serial=SN,source='sdk_identity'),
            device_version=dict(serial=SN,firmware_version='2.5.0',source='sdk_handle_info'))
        mon=UpdateNotifications(states=lambda:[copy.deepcopy(connected)],app=lambda:app,cli=lambda:cli,sdk=lambda:sdk,
            firmware=lambda:firmware,devices=lambda:[],idle=lambda:None,clock=lambda:self.host.now)
        mon.observe([connected]);mon._ingest_firmware(initial,self.host.now)
        self.assertEqual(mon.snapshot()['items'][0]['current_version'],'2.5.0')
        return mon,connected

    def test_recovered_version_and_fresh_catalog_replace_preflight_for_ui_and_notice(self):
        before=self.unknown();mon,connected=self.monitor(before)
        state=self.recovery(self.report('2.5.1','2.6.0'))
        self.assertEqual(state['phase'],'verified');self.assertFalse(state['locked'])
        self.assertEqual(state['current_version'],'2.5.1');self.assertEqual(state['checked_at'],self.host.now)
        self.assertEqual(state['notification']['current'],'2.5.1')
        self.assertEqual(state['notification']['latest'],'2.6.0')
        self.assertEqual(state['result']['verdict'],'recovery_verified')
        self.assertNotIn('version_verified',state['result'])
        self.assertEqual(state['verification_report'],self.host.recovery['verification_report'])
        items=mon.snapshot()['items']
        self.assertEqual([(r['current_version'],r['version']) for r in items],[('2.5.1','2.6.0')])
        mon.observe([connected])
        self.assertEqual(mon.snapshot()['devices'][0]['firmware_version'],'2.5.1')
        self.assertEqual(len(self.host.calls),2,'Recovery does not launch another upgrade')

    def test_actual_recovery_without_catalog_clears_old_notice_without_retimestamping_preflight(self):
        before=self.unknown();mon,connected=self.monitor(before)
        state=self.recovery(None)
        self.assertEqual(state['current_version'],'2.5.1');self.assertIsNone(state['notification'])
        self.assertEqual(state['check_report'],before['check_report'],'Keep original check as history')
        self.assertEqual(mon.snapshot()['items'],[])
        mon.observe([connected]);self.assertEqual(mon.snapshot()['items'],[])
        self.assertEqual(mon.snapshot()['devices'][0]['firmware_version'],'2.5.1')

    def test_wrong_fresh_identity_or_inconsistent_version_keeps_unknown_lease(self):
        for field,value in (('sn','OTHER-HAND'),('current_version','2.5.2'),('device_type','wuji_glove')):
            with self.subTest(field=field):
                self.setUp();self.unknown()
                report=self.report('2.5.1','2.6.0');data=json.loads(report['stdout'])
                data['devices'][0][field]=value;report['stdout']=json.dumps(data)
                with self.assertRaises(ValueError):self.recovery(report)
                state=self.service.snapshot()
                self.assertEqual(state['phase'],'unknown');self.assertTrue(state['locked'])
                self.assertEqual(state['current_version'],'2.5.0')
                self.assertEqual(self.host.released,['reservation-0'])

if __name__=='__main__':unittest.main()
