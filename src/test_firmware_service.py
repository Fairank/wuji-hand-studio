"""Firmware service tests use injected synthetic reports; no real CLI/device.

The deliberately opaque check/catalog fixture is not represented as a captured
official schema. The only write route is the original interactive confirmation.
"""
import copy
import json
import threading
import unittest

from firmware_service import FirmwareService


SN = 'WH2KA01000000001'


class FakeHost:
    def __init__(self):
        self.now = 1000.
        self.row = dict(sn=SN, serial=SN, kind='hand', generation='hand2', side='right',
                        model='Wuji Hand 2', observed_at=self.now, stale=False,
                        selectable=True, conflict=False, in_use=False,
                        source='official_cli', identity_source='official_cli')
        self.cli = dict(version='2026.10.12', sha256='test-digest', platform='fake')
        self.calls, self.acquired, self.released = [], [], []
        self.results = []
        self.idle = True
        self.release_error = False
        self.inside = threading.Event()
        self.resume = threading.Event()
        self.block = False
        self.recovery = None

    def device(self, sn):
        return copy.deepcopy(self.row)

    def identity(self):
        return copy.deepcopy(self.cli)

    def idle_check(self):
        if not self.idle:
            raise ValueError('A workspace is active')

    def acquire(self, kind):
        token = 'reservation-' + str(len(self.acquired))
        self.acquired.append((kind, token))
        return token

    def release(self, token):
        if self.release_error:
            raise RuntimeError('release unconfirmed')
        self.released.append(token)

    def execute(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if kwargs['interactive'] and self.block:
            self.inside.set()
            if not self.resume.wait(3):
                raise RuntimeError('fake fixture was not resumed')
        if self.results:
            value = self.results.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value
        return dict(exited=True, returncode=0, stdout='opaque official report', stderr='')

    def recover(self, sn, *, reservation_token, read_only=False):
        result = self.recovery
        if read_only and result is None:
            return dict(read_only=True,process_exited=True,sn=sn,reservation_token=reservation_token)
        if result is None:
            result = dict(process_exited=True, identity_verified=True, sn=sn,
                          reservation_token=reservation_token, firmware_version='2.5.1')
        return copy.deepcopy(result)

    def service(self, **kwargs):
        return FirmwareService(self.execute, self.identity, self.idle_check,
                               self.acquire, self.release, self.device,
                               recovery_check=self.recover,
                               clock=lambda:self.now, **kwargs)


class FirmwareServiceTests(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost()
        self.service = self.host.service()

    def join(self):
        thread = self.service.thread
        if thread is not None:
            thread.join(3)
            self.assertFalse(thread.is_alive(), 'fake worker did not finish')
        return self.service.snapshot()

    def ready(self):
        self.service.start_check(SN)
        state = self.join()
        self.assertEqual(state['phase'], 'ready', state)
        return state['preview']['token']

    def test_automatic_notice_uses_only_exact_serial_read_and_never_prepares_write(self):
        from test_device_versions import table
        text=table([(SN,'wuji_hand_2','2.5.0','2.5.1','update available')])
        self.host.results=[dict(exited=True,returncode=0,stdout=text,stderr='')]
        self.service.start_check(SN,notification_only=True);state=self.join()
        self.assertEqual(state['phase'],'available');self.assertTrue(state['notification']['update_available'])
        self.assertIsNone(state['preview']);self.assertFalse(state['can_upgrade'])
        self.assertEqual([x[0] for x in self.host.calls],[['upgrade','--sn',SN,'--check','--json']])
        self.assertFalse(self.host.calls[0][1]['mutating']);self.assertFalse(self.host.calls[0][1]['interactive'])
        self.assertEqual(len(self.host.released),1)

    def test_unknown_notification_format_is_unknown_not_up_to_date(self):
        self.service.start_check(SN,notification_only=True);state=self.join()
        self.assertEqual(state['phase'],'checked');self.assertIsNone(state['notification'])
        self.assertIsNone(state['preview']);self.assertFalse(state['can_upgrade'])

    def test_check_exact_sn_and_opaque_reports_never_invent_plan(self):
        token = self.ready()
        state = self.service.snapshot()
        self.assertTrue(state['can_upgrade'])
        self.assertEqual(state['preview']['token'], token)
        self.assertIsNone(state['preview']['target_version'])
        self.assertFalse(state['machine_write_supported'])
        self.assertTrue(state['preview']['requires_official_confirmation'])
        self.assertEqual(state['check_report']['stdout'], 'opaque official report')
        self.assertEqual([call[0] for call in self.host.calls], [
            ['upgrade', '--sn', SN, '--check', '--json']])
        self.assertEqual(self.host.acquired[0][0], 'firmware_check')
        self.assertEqual(len(self.host.released), 1)

    def test_upgrade_keeps_official_confirmation_no_yes_force_or_broad_scope(self):
        token = self.ready()
        self.service.start_upgrade(token, confirmed=True)
        state = self.join()
        argv, options = self.host.calls[1]
        self.assertEqual(argv, ['upgrade', '--sn', SN])
        self.assertTrue(options['mutating'])
        self.assertTrue(options['interactive'])
        self.assertNotIn('timeout', options)
        self.assertEqual(self.host.calls[-1][0], ['upgrade', '--sn', SN, '--check', '--json'])
        self.assertFalse(self.host.calls[-1][1]['mutating'])
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual(state['result']['verdict'], 'official_process_exited')
        self.assertNotIn('success', state['result'])
        self.assertFalse(state['can_upgrade'])
        with self.assertRaises(ValueError):
            self.service.start_upgrade(token, confirmed=True)

    def test_user_action_required_before_opening_official_flow(self):
        token = self.ready()
        for confirmed in (False, None, 1, 'yes'):
            with self.subTest(confirmed=confirmed), self.assertRaises(ValueError):
                self.service.start_upgrade(token, confirmed=confirmed)
        self.assertEqual(len(self.host.calls), 1)

    def test_client_metadata_cannot_substitute_for_server_inventory(self):
        for serial in (self.host.row, ['WH2'], '', '--all', 'x y', SN+'\n', 'A'*81):
            with self.subTest(serial=serial), self.assertRaises(ValueError):
                self.service.start_check(serial)
        self.assertEqual(self.host.calls, [])

    def test_stale_future_conflicting_in_use_unknown_devices_are_rejected(self):
        for changes in (dict(stale=True), dict(selectable=False), dict(conflict=True),
                        dict(in_use=True), dict(kind='unknown'), dict(observed_at=900.),
                        dict(observed_at=1001.), dict(observed_at=True), dict(sn='other')):
            host = FakeHost()
            host.row.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                host.service().start_check(SN)
            self.assertEqual(host.calls, [])

    def test_all_workspace_idle_check_blocks_check_and_upgrade(self):
        self.host.idle = False
        with self.assertRaises(ValueError):
            self.service.start_check(SN)
        self.assertEqual(self.host.calls, [])
        self.host.idle = True
        token = self.ready()
        self.host.idle = False
        with self.assertRaises(ValueError):
            self.service.start_upgrade(token, confirmed=True)
        self.assertEqual(len(self.host.calls), 1)
        self.assertFalse(self.service.snapshot()['can_upgrade'])

    def test_token_expires_and_clock_rollback_cannot_revalidate_it(self):
        token = self.ready()
        self.host.now += 121
        self.assertFalse(self.service.snapshot()['can_upgrade'])
        with self.assertRaises(ValueError):
            self.service.start_upgrade(token, confirmed=True)
        host = FakeHost()
        service = host.service()
        service.start_check(SN);service.thread.join(3)
        token = service.snapshot()['preview']['token']
        host.now -= 1
        with self.assertRaises(ValueError):
            service.start_upgrade(token, confirmed=True)

    def test_server_device_fingerprint_and_cli_changes_invalidate_preview(self):
        for field, value in (('side', 'left'), ('hardware_revision', 'changed'),
                             ('generation', 'hand1'), ('address', 'other')):
            host = FakeHost();service = host.service()
            service.start_check(SN);service.thread.join(3)
            token = service.snapshot()['preview']['token']
            host.row[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                service.start_upgrade(token, confirmed=True)
            self.assertEqual(len(host.calls), 1)
        token = self.ready()
        self.host.cli['sha256'] = 'replacement'
        with self.assertRaises(ValueError):
            self.service.start_upgrade(token, confirmed=True)

    def test_failed_check_releases_exact_reservation_and_creates_no_token(self):
        self.host.results = [dict(exited=True, returncode=1, stdout='', stderr='catalog offline')]
        self.service.start_check(SN)
        state = self.join()
        self.assertEqual(state['phase'], 'check_failed')
        self.assertEqual(state['operation'], 'check')
        self.assertIn('catalog offline', state['error'])
        self.assertIsNone(state['preview'])
        self.assertEqual(self.host.released, ['reservation-0'])

    def test_read_only_executor_owns_bounded_query_cleanup(self):
        self.host.results = [dict(exited=False, returncode=None)]
        self.service.start_check(SN)
        state = self.join()
        self.assertEqual(state['phase'], 'check_failed')
        self.assertFalse(state['locked'])
        self.assertEqual(self.host.released, ['reservation-0'])

    def test_duplicate_calls_rejected_while_official_terminal_owns_reservation(self):
        token = self.ready()
        self.host.block = True
        self.service.start_upgrade(token, confirmed=True)
        self.assertTrue(self.host.inside.wait(2))
        self.assertTrue(self.service.snapshot()['busy'])
        with self.assertRaises(ValueError):
            self.service.start_check(SN)
        with self.assertRaises(ValueError):
            self.service.start_upgrade(token, confirmed=True)
        self.assertEqual(len(self.host.released), 1)
        self.host.resume.set()
        self.join()
        self.assertEqual(len(self.host.released), 2)

    def test_unknown_upgrade_retains_lock_no_implicit_retry_or_rollback(self):
        token = self.ready()
        self.host.results = [dict(exited=False, returncode=None)]
        self.service.start_upgrade(token, confirmed=True)
        state = self.join()
        self.assertTrue(state['locked'])
        self.assertTrue(state['recovery_required'])
        self.assertEqual(state['phase'], 'unknown')
        self.assertEqual(len(self.host.released), 1)
        with self.assertRaises(ValueError):
            self.service.start_check(SN)
        self.assertEqual(len(self.host.calls), 2)

    def test_executor_exception_during_write_is_unknown_not_safe_failure(self):
        token = self.ready()
        self.host.results = [RuntimeError('transport lost after spawn')]
        self.service.start_upgrade(token, confirmed=True)
        state = self.join()
        self.assertEqual(state['phase'], 'unknown')
        self.assertEqual(len(self.host.released), 1)

    def test_known_nonzero_exit_is_not_reported_as_success(self):
        token = self.ready()
        self.host.results = [dict(exited=True, returncode=1, stdout='', stderr='device rejected firmware')]
        self.service.start_upgrade(token, confirmed=True)
        state = self.join()
        self.assertEqual(state['phase'], 'failed')
        self.assertEqual(state['result']['returncode'], 1)
        self.assertFalse(state['locked'])

    def test_release_failure_preserves_unknown_state(self):
        self.host.release_error = True
        self.service.start_check(SN)
        state = self.join()
        self.assertEqual(state['phase'], 'unknown')
        self.assertTrue(state['locked'])
        self.assertFalse(state['can_upgrade'])

    def unknown_upgrade(self):
        token = self.ready()
        self.host.results = [dict(exited=False, returncode=None)]
        self.service.start_upgrade(token, confirmed=True)
        self.join()

    def test_recovery_requires_exact_reservation_process_and_actual_device(self):
        self.unknown_upgrade()
        good = dict(process_exited=True, identity_verified=True, sn=SN,
                    reservation_token='reservation-1', firmware_version='2.5.1')
        for field, value in (('process_exited', False), ('identity_verified', False),
                             ('sn', 'another'), ('reservation_token', 'old'),
                             ('firmware_version', '')):
            self.host.recovery = dict(good, **{field:value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.service.verify_recovery()
            self.assertTrue(self.service.snapshot()['locked'])
            self.assertEqual(len(self.host.released), 1)
        self.host.recovery = good
        state = self.service.verify_recovery()
        self.assertEqual(state['phase'], 'verified')
        self.assertFalse(state['locked'])
        self.assertEqual(self.host.released[-1], 'reservation-1')
        self.assertEqual(state['result']['firmware_version'], '2.5.1')
        self.assertEqual(len(self.host.calls), 2)

    def test_snapshot_and_progress_cannot_mutate_service_or_claim_percent(self):
        self.ready()
        state = self.service.snapshot()
        state['preview']['device']['side'] = 'left'
        self.assertEqual(self.service.snapshot()['preview']['device']['side'], 'right')
        self.service._event(dict(type='text', text='Official progress'))
        self.service._event(dict(text='x'*17000))
        self.service._event('bad')
        self.assertEqual(len(self.service.snapshot()['events']), 1)
        self.assertNotIn('percent', self.service.snapshot())

    def test_unknown_check_does_not_retimestamp_an_old_observation(self):
        original = self.host.execute
        def slow(argv, **kwargs):
            result = original(argv, **kwargs)
            self.host.now += 31
            return result
        self.service.executor = slow
        token = self.ready()
        self.assertEqual(self.host.row['observed_at'], 1000.)
        with self.assertRaises(ValueError):
            self.service.start_upgrade(token, confirmed=True)
        self.assertEqual(len(self.host.calls), 1)

    def report(self,current='2.5.0',latest='2.5.1',status='update available'):
        value=dict(devices=[dict(sn=SN,device_type='wuji_hand_2',current_version=current,
                                latest_version=latest,status=status,error=None)])
        return dict(exited=True,returncode=0,stdout=json.dumps(value),stderr='')

    def test_actual_check_json_versions_are_published_and_list_is_unnecessary(self):
        self.host.results=[self.report()]
        self.service.start_check(SN);state=self.join()
        self.assertEqual(state['phase'],'available');self.assertEqual(state['operation'],'check')
        self.assertEqual(state['current_version'],'2.5.0');self.assertEqual(state['target_version'],'2.5.1')
        self.assertTrue(state['notification']['update_available']);self.assertTrue(state['can_upgrade'])
        self.assertIsNone(state['catalog_report'])
        self.assertEqual([call[0] for call in self.host.calls],[['upgrade','--sn',SN,'--check','--json']])

    def test_current_device_never_offers_a_reflash_confirmation(self):
        self.host.results=[self.report('2.5.1','2.5.1','up to date')]
        self.service.start_check(SN);state=self.join()
        self.assertEqual(state['phase'],'up_to_date');self.assertFalse(state['can_upgrade'])
        self.assertIsNone(state['preview']);self.assertEqual(len(self.host.calls),1)

    def test_confirmed_exact_check_survives_discovery_expiry_but_not_preview_expiry(self):
        self.host.results=[self.report()]
        self.service.start_check(SN);state=self.join();token=state['preview']['token']
        self.host.now+=40;self.host.row.update(stale=True,selectable=False)
        self.service.start_upgrade(token,confirmed=True);state=self.join()
        self.assertEqual(state['phase'],'completed');self.assertEqual(state['operation'],'upgrade')
        self.assertEqual(self.host.calls[1][0],['upgrade','--sn',SN])
        self.assertEqual(self.host.calls[-1][0],['upgrade','--sn',SN,'--check','--json'])
        host=FakeHost();service=host.service();host.results=[self.report()]
        service.start_check(SN);service.thread.join(3);token=service.snapshot()['preview']['token'];host.now+=121
        with self.assertRaises(ValueError):service.start_upgrade(token,confirmed=True)
        self.assertEqual(len(host.calls),1)

    def test_check_error_preserves_raw_report_and_is_never_upgrade_failure(self):
        self.host.results=[dict(exited=True,returncode=1,stdout='official query detail',stderr='query unavailable')]
        self.service.start_check(SN);state=self.join()
        self.assertEqual(state['phase'],'check_failed');self.assertEqual(state['operation'],'check')
        self.assertEqual(state['check_report']['stdout'],'official query detail')
        self.assertIsNone(state['result']);self.assertIsNone(state['notification'])

    def test_user_priority_cancels_only_background_read_and_confirms_release(self):
        entered,release=threading.Event(),threading.Event()
        def execute(argv,**options):
            self.assertTrue(options['background']);self.assertFalse(options['mutating']);self.assertFalse(options['interactive'])
            entered.set();self.assertTrue(release.wait(3));raise ValueError('synthetic read cancelled')
        self.service.executor=execute;self.service.cancel_query=release.set
        self.service.start_check(SN,notification_only=True);self.assertTrue(entered.wait(2))
        self.assertTrue(self.service.cancel_notification_check(timeout=2))
        state=self.service.snapshot();self.assertEqual(state['phase'],'deferred');self.assertIsNone(state['error'])
        self.assertFalse(state['busy']);self.assertEqual(self.host.released,['reservation-0'])

    def test_user_priority_cannot_cancel_manual_check_or_firmware_terminal(self):
        token=self.ready();self.host.block=True;self.service.start_upgrade(token,confirmed=True)
        self.assertTrue(self.host.inside.wait(2));cancelled=[];self.service.cancel_query=lambda:cancelled.append(True)
        self.assertTrue(self.service.cancel_notification_check());self.assertEqual(cancelled,[])
        self.assertTrue(self.service.snapshot()['busy']);self.host.resume.set();self.join()

    def test_unconfirmed_background_cancellation_keeps_user_action_waiting(self):
        entered,finish=threading.Event(),threading.Event()
        def execute(argv,**options):
            entered.set();self.assertTrue(finish.wait(3));raise ValueError('synthetic cancelled late')
        self.service.executor=execute;self.service.cancel_query=lambda:None
        self.service.start_check(SN,notification_only=True);self.assertTrue(entered.wait(2))
        self.assertFalse(self.service.cancel_notification_check(timeout=.02));self.assertEqual(self.host.released,[])
        finish.set();self.join();self.assertEqual(self.host.released,['reservation-0'])

    def test_actual_unconfirmed_read_exit_cannot_be_unlocked_by_priority_callback(self):
        from firmware_service import ReadOnlyQueryUnconfirmed
        self.host.results=[ReadOnlyQueryUnconfirmed('read child still alive')]
        self.service.start_check(SN,notification_only=True);state=self.join()
        self.assertEqual(state['phase'],'unknown');self.assertTrue(state['locked'])
        self.assertEqual(self.host.released,[]);self.assertFalse(self.service.cancel_notification_check())
        state=self.service.verify_recovery()
        self.assertEqual(state['phase'],'checked');self.assertFalse(state['locked'])
        self.assertEqual(state['result']['verdict'],'readonly_query_exited')
        self.assertNotIn('firmware_version',state['result']);self.assertEqual(self.host.released,['reservation-0'])

    def test_read_recovery_requires_exact_read_exit_proof_not_firmware_write_verdict(self):
        from firmware_service import ReadOnlyQueryUnconfirmed
        self.host.results=[ReadOnlyQueryUnconfirmed('read child still alive')]
        self.service.start_check(SN,notification_only=True);self.join()
        good=dict(read_only=True,process_exited=True,sn=SN,reservation_token='reservation-0')
        for field,value in (('read_only',False),('process_exited',False),('sn','OTHER'),('reservation_token','other')):
            self.host.recovery=dict(good,**{field:value})
            with self.subTest(field=field),self.assertRaises(ValueError):self.service.verify_recovery()
            self.assertTrue(self.service.snapshot()['locked']);self.assertEqual(self.host.released,[])

    def test_restarted_root_restores_exact_marker_without_unlocking_it(self):
        token = 'a'*32
        marker = dict(kind='firmware', token=token, serial=SN, child_pid=1234, child_start='identity')
        state = self.service.restore_unknown(marker)
        self.assertTrue(state['locked'])
        self.assertEqual(state['device']['sn'], SN)
        self.assertEqual(self.host.released, [])
        with self.assertRaises(ValueError):
            self.service.start_check(SN)
        self.host.recovery = dict(process_exited=True, identity_verified=True,
                                  sn=SN, reservation_token=token, firmware_version='2.5.1')
        self.assertEqual(self.service.verify_recovery()['phase'], 'verified')
        self.assertEqual(self.host.released, [token])

    def test_invalid_restore_markers_cannot_create_a_lease(self):
        for marker in ({}, dict(kind='scan',token='a'*32,serial=SN),
                       dict(kind='firmware',token='bad',serial=SN),
                       dict(kind='firmware',token='a'*32,serial='x --all')):
            with self.subTest(marker=marker), self.assertRaises(ValueError):
                self.service.restore_unknown(marker)
        self.assertFalse(self.service.snapshot()['locked'])


if __name__ == '__main__':
    unittest.main()
