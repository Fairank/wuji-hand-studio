"""Synthetic exact-device failed catalog lifecycle; no CLI, HTTP, SDK or PTY."""
import copy
from contextlib import nullcontext
import json
from pathlib import Path
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock, patch

from device_versions import observe_official_check_failure, parse_official_check_json, parse_official_check_report
from firmware_host import FirmwareHost
from test_firmware_service import FakeHost
from update_notifications import UpdateNotifications


SN = 'WG-QA-CATALOG-01'
ERROR = ('failed to query compatible firmware list: firmware API HTTP 404: '
         'hardware version not found for this device type')


def report(*, code=0, failed=False, current='1.0.4', latest='1.0.5', **changes):
    row = dict(sn=SN, device_type='wuji_glove', current_version=current,
               latest_version=None if failed else latest,
               status='failed' if failed else 'up to date' if current == latest else 'update available',
               error=ERROR if failed else None)
    row.update(changes)
    return dict(exited=True, returncode=code, stdout=json.dumps(dict(devices=[row])), stderr='')


class CatalogFailureTests(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost()
        self.host.row.update(sn=SN, serial=SN, kind='glove', generation='glove1', model='Synthetic Glove')
        self.service = self.host.service()

    def join(self):
        self.service.thread.join(3)
        self.assertFalse(self.service.thread.is_alive(), 'Synthetic worker exceeded watchdog')
        return self.service.snapshot()

    def check(self, value, **options):
        self.host.results = [value]
        self.service.start_check(SN, **options)
        return self.join()

    def preflight(self, **options):
        return self.check(report(**options))

    def upgrade(self, checked, *values):
        self.host.results = list(values)
        self.service.start_upgrade(checked['preview']['token'], confirmed=True)
        return self.join()

    def assert_failure_fact(self, state, *, current='1.0.4'):
        self.assertEqual(state['current_version'], current)
        self.assertIsNone(state['target_version'])
        self.assertEqual(state['catalog_error'], ERROR)
        self.assertEqual(state['check_observation'], dict(serial=SN, device_type='wuji_glove',
            current=current, latest=None, status='failed', error=ERROR, catalog_available=False))
        self.assertIsNone(state['notification'])
        self.assertIsNone(state['preview'])
        self.assertFalse(state['can_upgrade'])
        self.assertNotIn(state['phase'], ('verified', 'up_to_date', 'available', 'ready'))

    def test_failed_observation_never_becomes_strict_check_authority(self):
        raw = report(failed=True)['stdout']
        self.assertEqual(observe_official_check_failure(raw, SN, 'wuji_glove')['current'], '1.0.4')
        self.assertIsNone(parse_official_check_json(raw, SN, 'wuji_glove'))
        self.assertIsNone(parse_official_check_report(raw, SN, 'wuji_glove'))
        self.assertIsNone(observe_official_check_failure(report()['stdout'], SN, 'wuji_glove'))

    def test_failure_observation_requires_exact_identity_unique_fields_and_bounded_values(self):
        base = report(failed=True)['stdout']
        row = json.loads(base)['devices'][0]
        invalid = [json.dumps(dict(devices=[row, row])), json.dumps(dict(devices=[row], extra=True)),
            base.replace('"current_version": "1.0.4"', '"current_version":"1.0.4","current_version":"1.0.3"'),
            base.replace('"devices":', '"devices":[],"devices":'),
            report(failed=True, sn='OTHER')['stdout'], report(failed=True, device_type='wuji_hand_2')['stdout'],
            report(failed=True, current=True)['stdout'], report(failed=True, current='01.0.4')['stdout'],
            report(failed=True, latest_version=True)['stdout'], report(failed=True, latest_version='01.0.5')['stdout'],
            report(failed=True, error=None)['stdout'],
            report(failed=True, error='x'*8193)['stdout'], report(failed=True, error='bad\x00error')['stdout'],
            'x'*262145, '['*1100+']'*1100, '{"devices":[NaN]}', None, True]
        for raw in invalid:
            with self.subTest(raw=str(raw)[:80]):
                self.assertIsNone(observe_official_check_failure(raw, SN, 'wuji_glove'))
        for expected in (None, [], {}, True, 'other'):
            with self.subTest(expected=expected):
                self.assertIsNone(observe_official_check_failure(base, SN, expected))

    def test_nonzero_and_zero_failed_checks_manual_and_background_preserve_facts_without_preview(self):
        for code in (0, 1):
            for automatic in (False, True):
                with self.subTest(code=code, automatic=automatic):
                    self.setUp()
                    raw = report(code=code, failed=True)
                    state = self.check(raw, notification_only=automatic)
                    self.assert_failure_fact(state)
                    self.assertEqual(state['phase'], 'check_failed')
                    self.assertEqual(state['error'], ERROR)
                    self.assertEqual(state['check_report']['stdout'], raw['stdout'])
                    self.assertFalse(state['locked'])
                    self.assertEqual(self.host.released, ['reservation-0'])
                    with self.assertRaises(ValueError):self.service.start_upgrade('not-a-preview', confirmed=True)
                    self.assertTrue(all(not call[1]['mutating'] for call in self.host.calls))

    def test_explicit_failure_with_a_latest_version_never_enters_opaque_fallback(self):
        for current in ('1.0.4', None):
            with self.subTest(current=current):
                self.setUp()
                raw = report(failed=True, current=current, latest_version='1.0.5')
                state = self.check(raw)
                self.assert_failure_fact(state, current=current)
                self.assertEqual(state['phase'], 'check_failed')
                self.assertIsNone(parse_official_check_report(raw['stdout'], SN, 'wuji_glove'))
                self.assertEqual(json.loads(state['check_report']['stdout'])['devices'][0]['latest_version'], '1.0.5')
                checked = self.preflight()
                state = self.upgrade(checked, raw)
                self.assert_failure_fact(state, current=current)
                self.assertEqual(state['phase'], 'failed')
                checked = self.preflight()
                state = self.upgrade(checked, dict(exited=True, returncode=0, stdout='', stderr=''), raw)
                self.assert_failure_fact(state, current=current)
                self.assertFalse(state['result']['version_verified'])

    def test_success_failure_repeat_and_retry_clear_only_obsolete_failure_and_preview(self):
        first = self.preflight()
        self.assertTrue(first['can_upgrade'])
        old_token = first['preview']['token']
        self.assert_failure_fact(self.check(report(failed=True, code=1)))
        with self.assertRaises(ValueError):self.service.start_upgrade(old_token, confirmed=True)
        self.assert_failure_fact(self.check(report(failed=True)))
        recovered = self.preflight()
        self.assertEqual(recovered['phase'], 'available')
        self.assertTrue(recovered['can_upgrade'])
        self.assertIsNone(recovered['catalog_error'])
        self.assertIsNone(recovered['check_observation'])
        self.assertNotEqual(recovered['preview']['token'], old_token)
        current = self.check(report(current='1.0.4', latest='1.0.4'))
        self.assertEqual(current['phase'], 'up_to_date')
        self.assertFalse(current['can_upgrade'])
        self.assertIsNone(current['catalog_error'])

    def test_failed_check_without_current_still_blocks_preview_without_inventing_version(self):
        state = self.check(report(failed=True, current=None))
        self.assert_failure_fact(state, current=None)
        self.assertEqual(state['phase'], 'check_failed')

    def test_changed_cli_never_publishes_failed_observation(self):
        before = copy.deepcopy(self.host.cli)
        self.service.cli_identity = Mock(side_effect=[before, dict(before, sha256='changed')])
        state = self.check(report(failed=True, code=1))
        self.assertEqual(state['phase'], 'check_failed')
        self.assertIsNone(state['check_observation'])
        self.assertIsNone(state['current_version'])
        self.assertIsNone(state['catalog_error'])

    def test_interactive_failed_json_is_failure_even_when_process_exits_zero(self):
        for code in (0, 1):
            with self.subTest(code=code):
                self.setUp()
                checked = self.preflight()
                raw = report(code=code, failed=True)
                state = self.upgrade(checked, raw)
                self.assert_failure_fact(state)
                self.assertEqual(state['phase'], 'failed')
                self.assertEqual(state['result']['official_error'], ERROR)
                self.assertEqual(state['result']['stdout'], raw['stdout'])
                self.assertEqual(len(self.host.calls), 2, 'Explicit failure must not start another query or upgrade')
                self.assertEqual(state['error'], ERROR)

    def test_postflow_catalog_failure_keeps_current_but_never_verifies_even_matching_target(self):
        for code in (0, 1):
            for target in ('1.0.4', '1.0.5'):
                with self.subTest(code=code, target=target):
                    self.setUp()
                    checked = self.preflight(current='1.0.3', latest=target)
                    raw = report(code=code, failed=True)
                    state = self.upgrade(checked, dict(exited=True, returncode=0, stdout='Human flow ended', stderr=''), raw)
                    self.assert_failure_fact(state)
                    self.assertEqual(state['phase'], 'completed')
                    self.assertFalse(state['result']['version_verified'])
                    self.assertEqual(state['result']['verdict'], 'catalog_unavailable')
                    self.assertEqual(state['result']['expected_version'], target)
                    self.assertEqual(state['result']['firmware_version'], '1.0.4')
                    self.assertIn(ERROR, state['error'])
                    self.assertEqual(state['verification_report']['stdout'], raw['stdout'])
                    self.assertEqual(self.host.released, ['reservation-0', 'reservation-1'])
                    self.assertEqual(sum(bool(c[1]['mutating']) for c in self.host.calls), 1)

    def test_interactive_failed_observation_requires_unchanged_component_after_exit(self):
        for code in (0, 1):
            for change in ('different', 'invalid', 'raises'):
                with self.subTest(code=code, change=change):
                    self.setUp()
                    checked = self.preflight(current='1.0.3', latest='1.0.5')
                    execute = self.host.execute
                    def replaced(argv, **options):
                        value = execute(argv, **options)
                        if options['interactive']:
                            if change == 'raises':
                                def unavailable():raise ValueError('synthetic component identity unavailable')
                                self.service.cli_identity = unavailable
                            else:self.host.cli = dict(self.host.cli, sha256='different') if change == 'different' else None
                        return value
                    self.service.executor = replaced
                    raw = report(failed=True, code=code, current='1.0.4')
                    state = self.upgrade(checked, raw)
                    self.assertEqual(state['phase'], 'failed')
                    self.assertFalse(state['locked'], 'Confirmed process exit must not become unknown solely for identity loss')
                    self.assertFalse(state['can_upgrade'])
                    self.assertIsNone(state['check_observation'])
                    self.assertIsNone(state['catalog_error'])
                    self.assertEqual(state['current_version'], '1.0.3', 'Keep earlier preflight history, not changed-component facts')
                    self.assertEqual(state['checked_at'], checked['checked_at'])
                    self.assertEqual(state['result']['stdout'], raw['stdout'])
                    self.assertEqual(state['result']['returncode'], code)
                    self.assertEqual(state['result']['verdict'], 'official_process_exited')
                    self.assertNotIn('version_verified', state['result'])
                    self.assertEqual(self.host.released, ['reservation-0', 'reservation-1'])
                    self.assertEqual(len(self.host.calls), 2)

    def test_postflow_wrong_identity_and_cli_cannot_publish_current(self):
        for changes in (dict(sn='OTHER'), dict(device_type='wuji_hand_2'), dict(cli_changed=True)):
            with self.subTest(changes=changes):
                self.setUp()
                checked = self.preflight()
                options = dict(changes)
                if options.pop('cli_changed', False):
                    old = copy.deepcopy(self.host.cli)
                    self.service.cli_identity = Mock(side_effect=[old, old, old, dict(old, sha256='changed')])
                state = self.upgrade(checked, dict(exited=True, returncode=0, stdout='', stderr=''), report(failed=True, **options))
                self.assertIsNone(state['current_version'])
                self.assertIsNone(state['check_observation'])
                self.assertNotEqual(state['phase'], 'verified')

    def test_uncertain_process_and_failed_recovery_keep_exact_write_reservation(self):
        checked = self.preflight()
        state = self.upgrade(checked, dict(exited=False, returncode=None))
        self.assertTrue(state['locked'])
        self.host.recovery = dict(process_exited=True, identity_verified=True, sn=SN,
            reservation_token='reservation-1', firmware_version='1.0.4', verification_report=report(failed=True))
        with self.assertRaises(ValueError):self.service.verify_recovery()
        state = self.service.snapshot()
        self.assertEqual(state['phase'], 'unknown')
        self.assertTrue(state['locked'])
        self.assertEqual(self.host.released, ['reservation-0'])
        self.assertEqual(len(self.host.calls), 2)

    def test_failed_catalog_with_release_failure_retains_protective_lock(self):
        checked = self.preflight()
        self.host.release_error = True
        state = self.upgrade(checked, dict(exited=True, returncode=0, stdout='', stderr=''), report(failed=True))
        self.assert_failure_fact(state)
        self.assertEqual(state['phase'], 'unknown')
        self.assertTrue(state['locked'])
        self.assertEqual(self.host.released, ['reservation-0'])

    def test_display_failure_is_not_ingested_as_update_notification(self):
        state = self.check(report(failed=True))
        others = [Mock() for _ in range(4)]
        for service in others:service.snapshot.return_value = {}
        app, cli, sdk, firmware = others
        firmware.snapshot.side_effect = self.service.snapshot
        connected=dict(connection='connected', device_id=SN, session_generation=1,
            identified_device=dict(serial=SN, source='sdk_identity'),
            device_version=dict(serial=SN, firmware_version='1.0.4', source='sdk_handle_info'))
        monitor = UpdateNotifications(states=lambda: [copy.deepcopy(connected)], app=lambda: app, cli=lambda: cli, sdk=lambda: sdk,
            firmware=lambda: firmware, devices=lambda: [], idle=lambda: None, clock=lambda: self.host.now)
        monitor.observe([connected])
        monitor._ingest_firmware(state, self.host.now)
        self.assertEqual(monitor.snapshot()['items'], [])

    def test_host_transport_keeps_raw_failure_and_recovery_parser_stays_strict(self):
        marker = dict(kind='firmware', token='a'*32, serial=SN, child_pid=123456789, child_start='synthetic')
        for code in (0, 1):
            with self.subTest(code=code):
                raw = report(failed=True, code=code)
                runner = Mock(return_value=N(returncode=code, stdout=raw['stdout'], stderr=''))
                host = FirmwareHost(N(), folder=Path(__file__).resolve().parent/'unused-synthetic', runner=runner)
                with patch('firmware_host.reservation', return_value=marker), \
                     patch('firmware_host.firmware_environment', return_value={}), \
                     patch.object(host, '_component', return_value=dict(binary='synthetic-no-executable')):
                    result = host(['upgrade', '--sn', SN, '--check', '--json'])
                self.assertEqual(result, raw)
                with patch('firmware_host.gate', return_value=nullcontext()), \
                     patch('firmware_host.reservation', return_value=marker), \
                     patch('firmware_host.process_state', return_value='dead'), \
                     patch.object(FirmwareHost, '__call__', return_value=raw):
                    with self.assertRaises(ValueError):host.recovery_check(SN, reservation_token=marker['token'])

    def test_opaque_legacy_confirmation_is_unchanged_by_separate_failed_schema(self):
        state = self.check(dict(exited=True, returncode=0, stdout='opaque official report', stderr=''))
        self.assertEqual(state['phase'], 'ready')
        self.assertTrue(state['can_upgrade'])
        self.assertIsNone(state['current_version'])
        self.assertIsNone(state['check_observation'])


if __name__ == '__main__':unittest.main()
