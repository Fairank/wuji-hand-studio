"""Post-upgrade readback with synthetic official envelopes; never opens hardware."""
import json
import threading
import unittest

from firmware_service import ReadOnlyQueryUnconfirmed
from test_firmware_service import FakeHost, SN


def report(current='2.5.0',latest='2.5.1',*,serial=SN,kind='wuji_hand_2'):
    row=dict(sn=serial,device_type=kind,current_version=current,latest_version=latest,
             status='up to date' if current==latest else 'update available',error=None)
    return dict(exited=True,returncode=0,stdout=json.dumps(dict(devices=[row])),stderr='')


class FirmwareVerificationTests(unittest.TestCase):
    def setUp(self):
        self.host=FakeHost();self.service=self.host.service()

    def join(self):
        self.service.thread.join(3)
        self.assertFalse(self.service.thread.is_alive())
        return self.service.snapshot()

    def prepare(self):
        self.host.results=[report()]
        self.service.start_check(SN);state=self.join()
        self.assertEqual(state['phase'],'available')
        return state['preview']['token']

    def run_upgrade(self,after):
        token=self.prepare()
        self.host.results=[dict(exited=True,returncode=0,stdout='Official session ended',stderr=''),after]
        self.service.start_upgrade(token,confirmed=True)
        return self.join()

    def test_target_readback_finishes_without_manual_rescan_and_can_recheck(self):
        state=self.run_upgrade(report('2.5.1','2.5.1'))
        self.assertEqual(state['phase'],'verified');self.assertTrue(state['result']['version_verified'])
        self.assertEqual(state['result']['verdict'],'target_version_verified')
        self.assertEqual(state['current_version'],'2.5.1');self.assertFalse(state['notification']['update_available'])
        self.assertEqual([call[0] for call in self.host.calls],[['upgrade','--sn',SN,'--check','--json'],['upgrade','--sn',SN],['upgrade','--sn',SN,'--check','--json']])
        self.assertFalse(self.host.calls[-1][1]['mutating']);self.assertFalse(self.host.calls[-1][1]['interactive'])
        self.assertEqual(len(self.host.released),2)
        self.host.results=[report('2.5.1','2.5.1')];self.service.start_check(SN)
        self.assertEqual(self.join()['phase'],'up_to_date')

    def test_no_confirmation_or_cancel_does_not_claim_update(self):
        state=self.run_upgrade(report())
        self.assertEqual(state['phase'],'completed');self.assertEqual(state['result']['verdict'],'unchanged')
        self.assertFalse(state['result']['version_verified']);self.assertIsNone(state['error'])
        self.assertEqual(state['current_version'],'2.5.0');self.assertTrue(state['notification']['update_available'])

    def test_changed_catalog_cannot_move_original_target(self):
        state=self.run_upgrade(report('2.5.1','2.5.2'))
        self.assertEqual(state['phase'],'verified');self.assertEqual(state['target_version'],'2.5.1')
        self.assertEqual(state['notification']['latest'],'2.5.2');self.assertTrue(state['notification']['update_available'])

    def test_different_installed_version_is_not_target_success(self):
        state=self.run_upgrade(report('2.5.2','2.5.2'))
        self.assertEqual(state['phase'],'completed');self.assertFalse(state['result']['version_verified'])
        self.assertEqual(state['result']['verdict'],'version_not_matched');self.assertTrue(state['error'])

    def test_wrong_serial_family_or_unreadable_report_never_confirms(self):
        values=[report('2.5.1','2.5.1',serial='ANOTHER'),report('2.5.1','2.5.1',kind='wuji_glove'),
                dict(exited=True,returncode=0,stdout='unrecognized report',stderr=''),
                dict(exited=True,returncode=1,stdout='',stderr='Device reconnecting')]
        for value in values:
            with self.subTest(value=value):
                self.setUp();state=self.run_upgrade(value)
                self.assertEqual(state['phase'],'completed');self.assertTrue(state['error'])
                self.assertIsNone(state['current_version']);self.assertIsNone(state['notification'])
                self.assertNotEqual(state['result']['verdict'],'target_version_verified')
                self.assertFalse(state['locked']);self.assertEqual(state['verification_report']['stdout'],value['stdout'])

    def test_readback_retains_reservation_until_query_exits(self):
        token=self.prepare();entered,resume=threading.Event(),threading.Event()
        original=self.host.execute
        def execute(argv,**options):
            if not options['mutating']:
                entered.set();self.assertTrue(resume.wait(2));return report('2.5.1','2.5.1')
            return original(argv,**options)
        self.service.executor=execute;self.service.start_upgrade(token,confirmed=True)
        self.assertTrue(entered.wait(2));state=self.service.snapshot()
        self.assertTrue(state['busy']);self.assertEqual(state['phase'],'verifying');self.assertEqual(len(self.host.released),1)
        with self.assertRaises(ValueError):self.service.start_check(SN)
        resume.set();self.assertEqual(self.join()['phase'],'verified')

    def test_readback_unknown_exit_does_not_release_maintenance(self):
        state=self.run_upgrade(ReadOnlyQueryUnconfirmed('Query remains alive'))
        self.assertTrue(state['locked']);self.assertEqual(state['phase'],'unknown')
        self.assertEqual(len(self.host.released),1)
        self.assertNotEqual(state['result']['verdict'],'target_version_verified')

    def test_unavailable_readback_can_be_checked_after_device_returns_without_reflash(self):
        state=self.run_upgrade(dict(exited=True,returncode=1,stdout='',stderr='Device reconnecting'))
        self.assertEqual(state['phase'],'completed');self.assertFalse(state['locked'])
        self.host.results=[report('2.5.1','2.5.1')]
        self.service.start_check(SN);state=self.join()
        self.assertEqual(state['phase'],'up_to_date');self.assertEqual(state['current_version'],'2.5.1')
        self.assertEqual(sum(options['mutating'] for _,options in self.host.calls),1)

    def test_no_verified_preflight_target_cannot_be_invented_from_readback(self):
        self.service.start_check(SN);token=self.join()['preview']['token']
        self.host.results=[dict(exited=True,returncode=0,stdout='',stderr=''),report('2.5.1','2.5.1')]
        self.service.start_upgrade(token,confirmed=True);state=self.join()
        self.assertEqual(state['phase'],'completed');self.assertFalse(state['result']['version_verified'])
        self.assertIsNone(state['result']['expected_version'])


if __name__=='__main__':unittest.main()
