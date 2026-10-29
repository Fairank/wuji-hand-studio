"""Real handover/catalog/host boundaries with isolated synthetic transports.

No device, network, vendor CLI, firmware write or desktop automation is used.
"""
import threading
import unittest
from types import SimpleNamespace as N
from unittest.mock import Mock, patch

from console_server import ConsoleHTTPServer
from test_firmware_direct_route_0255 import DirectHarness, SN, OTHER


class FirmwareTransferTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect', 'subprocess.Popen'):
            mock = patch(target, side_effect=AssertionError('Actual I/O is forbidden'))
            mock.start();self.addCleanup(mock.stop)
        self.h = h = DirectHarness(self)
        self.server = server = object.__new__(ConsoleHTTPServer)
        server.controller = h.controller
        server._firmware = h.service;server._firmware_transport = h.host
        server.firmware_preview_idle = Mock()
        replacements = {
            'official_firmware_catalog.fetch_catalog': h.fetch,
            'official_firmware_package.prepare_package': lambda entry, sn, kind, cache_dir, **kw:h.prepare(entry, sn, kind, **kw),
            'bridge_config.load_config': lambda:dict(mode='wsl', cli='synthetic-never-execute'),
        }
        for name, callback in replacements.items():
            mock = patch(name, side_effect=callback);mock.start();self.addCleanup(mock.stop)
        h.catalog = server.firmware_catalog
        h.catalog.clock = lambda:h.now
        self.tick = 100.
        h.catalog.monotonic = lambda:self.tick
        h.host.package_validator = h.catalog.validate_claim
        h.host.package_releaser = h.catalog.release_owner
        h.service.claim_package = h.catalog.claim_package

    def competing(self, command):
        result = {}
        def request():
            try:result['value'] = self.server.maintenance_action(command)
            except Exception as error:result['error'] = str(error)
        thread = threading.Thread(target=request, daemon=True, name='Synthetic competing maintenance')
        thread.start();thread.join(2)
        self.assertFalse(thread.is_alive(), 'Competing request deadlocked')
        return result

    def install(self, token):
        self.server.start_firmware_package_upgrade(SN, token, True)
        return self.h.join(self.h.service)

    def test_other_catalog_download_sdk_and_firmware_cannot_replace_transfer(self):
        h = self.h;ready = h.prepared();results = []
        sdk = N(snapshot=Mock(return_value=dict(busy=False, recovery_required=False)), start=Mock())
        self.server._sdk_maintenance = sdk
        commands = [dict(name='firmware_catalog_check', serial=OTHER),
            dict(name='firmware_package_prepare', serial=SN, catalog_token=h.catalog.snapshot()['catalog_token']),
            dict(name='sdk_update_prepare'),dict(name='firmware_check', serial=OTHER),
            dict(name='firmware_recovery_check')]
        def stop(*args, **kw):
            self.assertTrue(h.catalog.snapshot()['busy'])
            self.assertIsNone(h.catalog.snapshot()['package']['token'])
            self.assertEqual(h.catalog.snapshot()['serial'], SN)
            for command in commands:results.append(self.competing(command))
            h.controller.official_viz.snapshot.return_value['active'] = False
        h.controller.official_viz.stop_owned.side_effect = stop
        state = self.install(ready['package']['token'])
        self.assertEqual(state['phase'], 'verified')
        self.assertTrue(all('error' in row for row in results), results)
        self.assertEqual(len(h.metadata_calls), 1);self.assertEqual(len(h.download_calls), 1)
        sdk.start.assert_not_called()
        h.controller.official_viz.stop_owned.assert_called_once_with(SN, 'b'*32, require_glove=True)
        self.assertEqual(len(h.spawn_calls), 1);self.assertFalse(h.catalog.snapshot()['busy'])
        self.assertIsNone(h.catalog._transfer);self.assertEqual(h.catalog._claims, {})

    def test_original_catalog_and_package_expiry_during_exact_handover_does_not_disconnect_then_fail(self):
        h = self.h;ready = h.prepared()
        def stop(*args, **kw):
            h.now += 301;h.inventory['observed_at'] = h.now;self.tick += 20
            h.controller.official_viz.snapshot.return_value['active'] = False
        h.controller.official_viz.stop_owned.side_effect = stop
        self.assertEqual(self.install(ready['package']['token'])['phase'], 'verified')
        self.assertEqual(len(h.download_calls), 1);self.assertEqual(len(h.spawn_calls), 1)
        self.assertIsNone(h.catalog.snapshot()['catalog_token'])

    def test_invalid_expired_wrong_sn_and_corrupt_package_preserve_viewer(self):
        h = self.h;ready = h.prepared();token = ready['package']['token']
        for serial, candidate in ((SN, 'invalid'), (OTHER, token)):
            with self.assertRaises(ValueError):self.server.start_firmware_package_upgrade(serial, candidate, True)
        h.now += 300
        with self.assertRaises(ValueError):self.install(token)
        h.now -= 300
        path = h.catalog._prepared['package'].path
        with open(path, 'ab') as stream:stream.write(b'synthetic corruption')
        with self.assertRaises(ValueError):self.install(token)
        h.controller.official_viz.stop_owned.assert_not_called()
        h.controller.calibration_devices.assert_not_called()
        self.assertEqual(h.spawn_calls, []);self.assertFalse(h.catalog.snapshot()['busy'])

    def test_original_expiry_during_validation_rejects_before_pin(self):
        h = self.h;ready = h.prepared();validate = h.catalog.validate
        def slow(*args):
            result = validate(*args);h.now += 300;return result
        h.catalog.validate = slow
        with self.assertRaises(ValueError):self.install(ready['package']['token'])
        h.controller.official_viz.stop_owned.assert_not_called()
        self.assertFalse(h.catalog.snapshot()['busy']);self.assertIsNone(h.catalog._transfer)

    def test_pin_deadline_during_preflight_preserves_viewer_and_never_renews_ui_token(self):
        h = self.h;ready = h.prepared();token = ready['package']['token']
        self.server.firmware_preview_idle.side_effect = lambda *a:setattr(self, 'tick', self.tick+90)
        with self.assertRaises(ValueError):self.install(token)
        h.controller.official_viz.stop_owned.assert_not_called()
        self.assertFalse(h.catalog.snapshot()['busy'])
        self.assertEqual(h.catalog.snapshot()['package']['token'], token)
        self.assertEqual(h.catalog._prepared['expires_at'], ready['package']['expires_at'])
        self.server.firmware_preview_idle.side_effect = None
        self.assertEqual(self.install(token)['phase'], 'verified')

    def test_failed_handover_releases_only_own_pin_and_expired_ui_token_is_not_restored(self):
        h = self.h;ready = h.prepared();token = ready['package']['token']
        def fail(*a, **kw):
            h.now += 301;raise ValueError('Synthetic viewer cleanup unconfirmed')
        h.controller.official_viz.stop_owned.side_effect = fail
        with self.assertRaises(ValueError):self.install(token)
        self.assertFalse(h.catalog.snapshot()['busy']);self.assertIsNone(h.catalog.snapshot()['package'])
        self.assertIsNone(h.catalog._transfer);self.assertEqual(h.spawn_calls, [])
        with self.assertRaises(ValueError):self.install(token)
        h.controller.official_viz.stop_owned.side_effect = None
        new = h.prepared();self.assertNotEqual(new['package']['token'], token)

    def test_old_transfer_finalizer_cannot_release_replacement_and_claim_is_one_use(self):
        h = self.h;ready = h.prepared();token = ready['package']['token']
        old = h.catalog.begin_transfer(token, SN)
        with self.assertRaises(ValueError):h.catalog.begin_transfer(token, SN)
        with self.assertRaises(ValueError):h.catalog.claim_package(token, SN, reservation_token='synthetic-owner')
        with self.assertRaises(ValueError):h.catalog.claim_package(old, OTHER, reservation_token='synthetic-owner')
        h.catalog.finish_transfer(old)
        current = h.catalog.begin_transfer(token, SN)
        h.catalog.finish_transfer(old)
        self.assertTrue(h.catalog.snapshot()['busy'])
        descriptor = h.catalog.claim_package(current, SN, reservation_token='synthetic-owner')
        with self.assertRaises(ValueError):h.catalog.claim_package(current, SN, reservation_token='synthetic-owner')
        h.catalog.finish_transfer(current)
        self.assertEqual(h.catalog.validate_claim(descriptor), descriptor)
        h.catalog.release_owner('another-owner');self.assertEqual(len(h.catalog._claims), 1)
        h.catalog.release_owner('synthetic-owner');self.assertEqual(h.catalog._claims, {})

    def test_archive_mutation_while_stopping_viewer_does_not_start_writer(self):
        h = self.h;ready = h.prepared();path = h.catalog._prepared['package'].path
        def stop(*a, **kw):
            with open(path, 'ab') as stream:stream.write(b'synthetic mutation')
        h.controller.official_viz.stop_owned.side_effect = stop
        with self.assertRaises(ValueError):self.install(ready['package']['token'])
        self.assertEqual(h.spawn_calls, []);self.assertEqual(h.resource_calls, [])
        self.assertFalse(h.catalog.snapshot()['busy']);self.assertIsNone(h.catalog._transfer)

    def test_other_viewer_and_changed_owner_are_never_stopped(self):
        h = self.h;ready = h.prepared()
        h.controller.official_viz.snapshot.return_value['reported_device']['sn'] = OTHER
        h.controller.official_cli.idle_check.side_effect = ValueError('Other viewer is active')
        with self.assertRaises(ValueError):self.install(ready['package']['token'])
        h.controller.official_viz.stop_owned.assert_not_called()
        h.controller.calibration_devices.assert_not_called();self.assertEqual(h.spawn_calls, [])
        h.controller.official_viz.snapshot.return_value['reported_device']['sn'] = SN
        h.controller.official_cli.idle_check.side_effect = None
        h.controller.official_viz.connection_record.side_effect = ValueError('Owner changed')
        with self.assertRaises(ValueError):self.install(ready['package']['token'])
        h.controller.official_viz.stop_owned.assert_not_called()

    def test_close_intent_before_and_during_archive_validation_has_no_viewer_side_effect(self):
        h = self.h;ready = h.prepared();token = ready['package']['token']
        h.controller.desktop_closing = True
        with self.assertRaises(ValueError):self.install(token)
        h.controller.desktop_closing = False;validate = h.catalog.validate
        def closing(*args):
            result = validate(*args);h.controller.desktop_closing = True;return result
        h.catalog.validate = closing
        with self.assertRaises(ValueError):self.install(token)
        self.assertIsNone(h.catalog._transfer);self.assertFalse(h.catalog.snapshot()['busy'])
        h.controller.official_viz.stop_owned.assert_not_called()
        h.controller.calibration_devices.assert_not_called()

    def test_close_intent_published_during_exact_viewer_preflight_preserves_viewer(self):
        h = self.h;ready = h.prepared()
        h.controller.official_viz.connection_record.side_effect = lambda *a, **kw:setattr(h.controller, 'desktop_closing', True)
        with self.assertRaises(ValueError):self.install(ready['package']['token'])
        h.controller.official_viz.stop_owned.assert_not_called()
        h.controller.calibration_devices.assert_not_called()
        self.assertIsNone(h.catalog._transfer);self.assertFalse(h.catalog.snapshot()['busy'])
        self.assertEqual(h.spawn_calls, [])

    def test_catalog_publication_checks_close_intent_after_waiting_for_its_lock(self):
        h = self.h;entered = threading.Event();guarded = threading.Event();errors = []
        original = h.catalog.guard
        def guard():guarded.set();return original()
        h.catalog.guard = guard
        def check():
            entered.set()
            try:h.catalog.start_check(SN)
            except ValueError as error:errors.append(str(error))
        with h.catalog.lock:
            thread = threading.Thread(target=check, daemon=True);thread.start()
            self.assertTrue(entered.wait(1));self.assertFalse(guarded.wait(.05))
            h.controller.desktop_closing = True
        thread.join(2);self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1);self.assertFalse(h.catalog.snapshot()['busy'])
        self.assertEqual(h.metadata_calls, [])

    def test_sdk_update_already_busy_rejects_pin_before_viewer_stop(self):
        h = self.h;ready = h.prepared()
        self.server._sdk_maintenance = N(snapshot=Mock(return_value=dict(busy=True, last_operation='update', recovery_required=False)))
        with self.assertRaises(ValueError):self.install(ready['package']['token'])
        h.controller.official_viz.stop_owned.assert_not_called();self.assertIsNone(h.catalog._transfer)
        self.assertFalse(h.catalog.snapshot()['busy'])

    def test_sdk_dispatch_shares_workflow_lock_and_closing_refuses_before_start(self):
        h = self.h;sdk = N(start=Mock(return_value={}), snapshot=Mock(return_value=dict(busy=False)))
        self.server._sdk_maintenance = sdk
        with h.controller.calibration_handover.workflow_lock:
            result = self.competing(dict(name='sdk_update_prepare'))
            self.assertIn('error', result);sdk.start.assert_not_called()
        h.controller.desktop_closing = True
        with self.assertRaises(ValueError):self.server.maintenance_action(dict(name='sdk_update_prepare'))
        sdk.start.assert_not_called()


if __name__ == '__main__':unittest.main()
