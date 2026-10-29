"""Non-author recovery challenges using synthetic transport and official ZIP bytes."""
import unittest

from test_firmware_direct_route_0255 import DirectHarness
from test_firmware_host import FakeProcess


class IndependentRecoveryChecks(unittest.TestCase):
    def setUp(self):
        self.h = DirectHarness(self)

    def missing_exit_code(self):
        h = self.h
        original = h.host.spawn
        def spawn(*args, **kwargs):
            process = original(*args, **kwargs)
            process.status = None
            return process
        h.host.spawn = spawn
        ready = h.prepared()
        self.assertEqual(h.install(ready['package']['token'])['phase'], 'unknown')
        return h.host.process

    def test_release_failure_after_dead_transport_retirement_can_recover_and_run_next_task(self):
        h = self.h
        old = self.missing_exit_code()
        owner = h.marker['token']
        h.release_fail = True
        first = h.service.verify_recovery()
        self.assertEqual(first['phase'], 'unknown')
        self.assertTrue(first['locked'])
        self.assertEqual(h.marker['token'], owner)
        self.assertIsNone(h.host.process)
        self.assertFalse(old.closed)
        h.release_fail = False
        self.assertEqual(h.service.verify_recovery()['phase'], 'verified')
        h.host.spawn = h.spawn
        ready = h.prepared()
        self.assertEqual(h.install(ready['package']['token'])['phase'], 'verified')
        self.assertEqual(len(h.spawn_calls), 2, 'Recovery must not retry the old firmware writer')
        self.assertEqual(len(h.resource_calls), 6)
        self.assertEqual(h.marker, {})

    def test_transport_replacement_during_readback_is_retained_until_original_owner_is_restored(self):
        h = self.h
        old = self.missing_exit_code()
        owner = h.marker['token']
        replacement = FakeProcess()
        replacement.pid = old.pid + 1
        def resource(command, **kwargs):
            result = h.read_resource(command, **kwargs)
            if command[2] == 'firmware_version':
                h.host.process = replacement
            return result
        h.host.runner = resource
        with self.assertRaises(ValueError):
            h.service.verify_recovery()
        self.assertIs(h.host.process, replacement)
        self.assertEqual(h.marker['token'], owner)
        self.assertTrue(h.service.snapshot()['locked'])
        self.assertFalse(replacement.closed)
        h.host.process = old
        h.host.runner = h.read_resource
        self.assertEqual(h.service.verify_recovery()['phase'], 'verified')
        self.assertIsNone(h.host.process)
        self.assertEqual(len(h.spawn_calls), 1)


if __name__ == '__main__':
    unittest.main()
