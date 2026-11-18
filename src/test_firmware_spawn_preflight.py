"""Real firmware workflow with synthetic CLI reports and an unstarted PTY.

No native process, device, network request, or firmware write is performed.
"""
import json
import sys
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock, patch

from test_firmware_direct_route_0255 import DirectHarness, SN
from test_firmware_host import FakeProcess


class FirmwareSpawnPreflightTests(unittest.TestCase):
    def harness(self, mode):
        h = DirectHarness(self)
        if mode == 'direct':
            report = dict(devices=[dict(sn=SN, device_type='wuji_glove',
                current_version='1.0.3', latest_version='1.0.4',
                status='update available', error=None)])
            h.host.runner = Mock(return_value=N(returncode=0, stdout=json.dumps(report), stderr=''))
        h.host.spawn = None  # Exercise the production lazy dependency boundary.
        return h

    def prepare(self, h, mode):
        if mode == 'file':
            return h.prepared()['package']['token']
        h.service.start_check(SN)
        h.service.thread.join(3)
        self.assertFalse(h.service.thread.is_alive())
        return h.service.snapshot()['preview']['token']

    def upgrade(self, h, mode, token):
        if mode == 'file':
            h.service.start_file_upgrade(SN, token, confirmed=True)
        else:
            h.service.start_upgrade(token, confirmed=True)
        h.service.thread.join(3)
        self.assertFalse(h.service.thread.is_alive())
        return h.service.snapshot()

    @staticmethod
    def backend(spawn, *, version='3.1.0', conpty=0):
        return N(__version__=version, Backend=N(ConPTY=conpty), PtyProcess=N(spawn=spawn))

    def test_dependency_version_backend_and_missing_api_fail_without_retaining_maintenance(self):
        for mode in ('direct', 'file'):
            for failure in ('missing', 'version', 'backend', 'api'):
                with self.subTest(mode=mode, failure=failure):
                    h = self.harness(mode)
                    token = self.prepare(h, mode)
                    spawn = Mock(side_effect=AssertionError('A rejected dependency must not spawn'))
                    backend = (None if failure == 'missing' else self.backend(
                        None if failure == 'api' else spawn,
                        version='2.0.15' if failure == 'version' else '3.1.0',
                        conpty=1 if failure == 'backend' else 0))
                    with patch.dict(sys.modules, winpty=backend):
                        state = self.upgrade(h, mode, token)
                    self.assertEqual(state['phase'], 'failed')
                    self.assertFalse(state['locked'])
                    self.assertTrue(state['error'])
                    self.assertEqual(h.marker, {})
                    self.assertIsNone(h.host.process)
                    spawn.assert_not_called()

    def test_failed_release_can_recover_the_exact_unstarted_reservation_then_retry(self):
        for mode in ('direct', 'file'):
            with self.subTest(mode=mode):
                h = self.harness(mode)
                token = self.prepare(h, mode)
                h.release_fail = True
                rejected = Mock(side_effect=AssertionError('No rejected child'))
                with patch.dict(sys.modules, winpty=self.backend(rejected, version='wrong')):
                    state = self.upgrade(h, mode, token)
                self.assertTrue(state['locked'])
                self.assertEqual(h.marker['writer_stage'], 'reserved')
                self.assertNotIn('child_pid', h.marker)
                owner = h.marker['token']
                self.assertEqual(h.service.verify_recovery()['phase'], 'unknown')
                self.assertEqual(h.marker['token'], owner)
                h.release_fail = False
                reads = list(h.resource_calls)
                recovered = h.service.verify_recovery()
                self.assertEqual(recovered['result']['verdict'], 'official_write_not_started')
                self.assertFalse(recovered['locked'])
                self.assertEqual(h.marker, {})
                self.assertEqual(h.resource_calls, reads)
                rejected.assert_not_called()

                retry = self.prepare(h, mode)
                process = FakeProcess()
                spawn = Mock(return_value=process)
                with patch.dict(sys.modules, winpty=self.backend(spawn)):
                    state = self.upgrade(h, mode, retry)
                self.assertIn(state['phase'], ('completed', 'verified'))
                self.assertFalse(state['locked'])
                self.assertEqual(h.marker, {})
                spawn.assert_called_once()
                self.assertEqual(process.writes, [])

    def test_arbitrary_native_spawn_failure_retains_unknown_maintenance(self):
        for mode in ('direct', 'file'):
            with self.subTest(mode=mode):
                h = self.harness(mode)
                token = self.prepare(h, mode)
                releases = list(h.release_calls)
                spawn = Mock(side_effect=OSError('Synthetic spawn outcome is uncertain'))
                with patch.dict(sys.modules, winpty=self.backend(spawn)):
                    state = self.upgrade(h, mode, token)
                self.assertEqual(state['phase'], 'unknown')
                self.assertTrue(state['locked'])
                self.assertEqual(h.marker['writer_stage'], 'starting')
                self.assertEqual(h.release_calls, releases)
                spawn.assert_called_once()
                with self.assertRaises(ValueError):
                    h.service.verify_recovery()
                self.assertTrue(h.service.snapshot()['locked'])

    def test_postspawn_persistence_failure_retains_the_original_process(self):
        for mode in ('direct', 'file'):
            with self.subTest(mode=mode):
                h = self.harness(mode)
                token = self.prepare(h, mode)
                process = FakeProcess(alive=True, status=None)
                h.processes.append(process)
                spawn = Mock(return_value=process)
                releases = list(h.release_calls)
                def write(path, marker):
                    if 'child_pid' in marker:
                        raise OSError('Synthetic evidence write failed after child spawn')
                    h.atomic(path, marker)
                with patch.dict(sys.modules, winpty=self.backend(spawn)), patch('firmware_host.atomic', side_effect=write):
                    state = self.upgrade(h, mode, token)
                self.assertEqual(state['phase'], 'unknown')
                self.assertTrue(state['locked'])
                self.assertIs(h.host.process, process)
                self.assertTrue(process.isalive())
                self.assertFalse(process.closed)
                self.assertEqual(h.release_calls, releases)
                self.assertEqual(process.writes, [])
                with self.assertRaises(ValueError):
                    h.service.verify_recovery()
                self.assertIs(h.host.process, process)


if __name__ == '__main__':
    unittest.main()
