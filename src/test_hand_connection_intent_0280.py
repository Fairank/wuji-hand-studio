"""Typed read-only discoveries reach the existing exact-serial switch.

Mapping-side intent is not an assertion of actual SDK handedness. No device,
SDK, network, or process is used here.
"""
import copy
import unittest

from device_inventory import normalize_devices
import test_connection_switch as switch_fixture


class HandConnectionIntentTests(unittest.TestCase):
    def test_unknown_side_discovery_uses_same_generation_intent_without_inventing_identity(self):
        case = switch_fixture.SwitchTests()
        for generation, kind in (('hand1', 'DeviceType.WujiHand'), ('hand2', 'DeviceType.WujiHand2')):
            for side in ('left', 'right'):
                with self.subTest(generation=generation, side=side), case.environment(mode='none') as e:
                    row = normalize_devices([dict(serial='WH_SYNTHETIC_TYPED', device_type=kind)],
                        source='controller_discovery', observed_at=1000, now=1000)['devices'][0]
                    self.assertTrue(row['selectable'])
                    self.assertIsNone(row['side'])
                    self.assertFalse(row['identity_verified'])
                    e.rows[:] = [row]
                    before = copy.deepcopy(row)
                    command = case.command(e, mode='hand', sn=row['sn'], profile=generation+'_'+side)
                    target = e.service._target(command, e.c.snapshot(), None)
                    self.assertEqual(target['serial'], row['sn'])
                    self.assertEqual(target['profile'], generation+'_'+side)
                    self.assertEqual(e.rows[0], before)
                    e.c.action.assert_not_called()


if __name__ == '__main__':
    unittest.main()
