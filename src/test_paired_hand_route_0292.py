"""Preserve a selected hand's concrete locator across the existing SDK switch."""
import unittest
from types import SimpleNamespace as N
from unittest.mock import Mock, patch

import device_discovery as discovery
from device_inventory import build_inventory
from test_connection_switch import HAND
import test_target_mapping_switch as fixture


RIGHT = 'WH2KA00000000042'
LEFT = 'WH2JA00000000041'
FACTORY_RIGHT = '192.168.1.111:7447'
CUSTOM = '192.0.2.45:7448'


def advertisement(serial, address=None):
    return N(sn=serial, device_type='DeviceType.WujiHand2', address=address or 'zenoh://' + serial)


def directory_row(address, *, seen=100, prior=None):
    typed = dict(sn=HAND, device_type='wuji_hand_2', handedness='left', address=address)
    current = dict(serial=HAND, generation='hand2', address='zenoh://' + HAND)
    ads = [current] if prior is None else [current, dict(current, address=prior)]
    return build_inventory(viz=dict(devices=[typed], scanned_at=seen),
        glove=dict(hands=ads, hands_at=100), now=100)['devices'][0]


class PairedHandRouteTests(unittest.TestCase):
    def test_fresh_official_custom_locator_reaches_exact_zenoh_sdk_connection(self):
        with fixture.TargetMappingSwitchTests().environment(False) as e:
            e.rows[-1] = directory_row('udp/' + CUSTOM)
            route = Mock()
            hand = Mock(serial_number=HAND)
            hand.handedness.return_value.get.return_value = 'left'
            manager = Mock(connect=Mock(return_value=hand))
            manager.scan.return_value = [advertisement(LEFT), advertisement(HAND)]
            original = e.c.action.side_effect

            def action(command):
                if command['name'] == 'glove_prepare':
                    # The exact custom endpoint is the only one reachable in
                    # this fixture. SDK still advertises the hand by Zenoh URI.
                    self.assertEqual(command['address'], CUSTOM)
                    connected, routes, profile = discovery.connect_discovered(
                        manager, command['address'], command['serial'], use_routes=True)
                    self.assertIs(connected, hand)
                    self.assertEqual(profile['id'], 'hand2_left')
                    routes.close()
                return original(command)

            e.c.action.side_effect = action
            with patch.object(discovery, 'DeviceRoute', return_value=route) as route_factory:
                result = e.service.start(e.command(address=''))
            route_factory.assert_called_once()
            self.assertEqual(route_factory.call_args.args[0], CUSTOM)
            manager.connect.assert_called_once_with(sn=HAND, device_name='wuji_hand_2')
            hand.enable.assert_not_called()
            self.assertEqual(result['connection_switch_receipt']['target']['hand_serial'], HAND)
            e.c.glove.stop_session_confirmed.assert_not_called()

    def test_fresh_opaque_zenoh_update_does_not_promote_stale_official_ip(self):
        with fixture.TargetMappingSwitchTests().environment(False) as e:
            e.rows[-1] = directory_row(CUSTOM, seen=50)
            self.assertFalse(e.rows[-1]['stale'])
            self.assertIn(CUSTOM, e.rows[-1]['addresses'])
            e.service.start(e.command())
            self.assertEqual(e.calls[-1]['address'], '')

    def test_competing_current_ipv4_observations_do_not_choose_arbitrarily(self):
        with fixture.TargetMappingSwitchTests().environment(False) as e:
            e.rows[-1] = directory_row(CUSTOM, prior='192.0.2.50:7447')
            e.service.start(e.command())
            self.assertEqual(e.calls[-1]['address'], '')

    def test_explicit_address_keeps_precedence_over_directory_locator(self):
        with fixture.TargetMappingSwitchTests().environment(False) as e:
            e.rows[-1] = directory_row(CUSTOM)
            e.service.start(e.command(address='192.0.2.51:7447'))
            self.assertEqual(e.calls[-1]['address'], '192.0.2.51:7447')

    def test_nonmanaged_controller_keeps_sdk_resolution(self):
        with fixture.TargetMappingSwitchTests().environment(False) as e:
            e.rows[-1] = directory_row(CUSTOM)
            with patch('bridge_config.load_config', return_value={'mode': 'local'}):
                e.service.catalog_config = e.gs['controller_config'] = e.service._config()
                e.service.start(e.command())
            self.assertEqual(e.calls[-1]['address'], '')

    def test_exact_managed_zenoh_hand_uses_only_matching_factory_route(self):
        route = Mock()
        hand = Mock(serial_number=RIGHT)
        hand.handedness.return_value.get.return_value = 'right'
        manager = Mock(connect=Mock(return_value=hand))
        manager.scan.return_value = [advertisement(LEFT), advertisement(RIGHT)]
        with patch.object(discovery, 'DeviceRoute', return_value=route) as route_factory:
            _, routes, profile = discovery.connect_discovered(manager, serial=RIGHT, use_routes=True)
        route_factory.assert_called_once()
        self.assertEqual(route_factory.call_args.args[0], FACTORY_RIGHT)
        manager.connect.assert_called_once_with(sn=RIGHT, device_name='wuji_hand_2')
        self.assertEqual(profile['id'], 'hand2_right')
        routes.close()

    def test_missing_exact_hand_never_connects_visible_other_side(self):
        route = Mock()
        route.open.side_effect = RuntimeError('Synthetic factory address unavailable')
        manager = Mock(scan=Mock(return_value=[advertisement(LEFT)]))
        with patch.object(discovery, 'DeviceRoute', return_value=route) as route_factory, \
                patch.object(discovery.time, 'monotonic', side_effect=(0, 4)):
            with self.assertRaisesRegex(RuntimeError, 'no longer'):
                discovery.connect_discovered(manager, serial=RIGHT, use_routes=True)
        route_factory.assert_called_once()
        manager.connect.assert_not_called()

    def test_existing_custom_sdk_advertisement_still_works_after_unavailable_default(self):
        route = Mock()
        route.open.side_effect = RuntimeError('Synthetic factory address unavailable')
        hand = Mock(serial_number=RIGHT)
        hand.handedness.return_value.get.return_value = 'right'
        manager = Mock(scan=Mock(return_value=[advertisement(RIGHT, CUSTOM)]), connect=Mock(return_value=hand))
        with patch.object(discovery, 'DeviceRoute', return_value=route):
            _, routes, _ = discovery.connect_discovered(manager, serial=RIGHT, use_routes=True)
        manager.connect.assert_called_once_with(sn=RIGHT, device_name='wuji_hand_2')
        routes.close()


if __name__ == '__main__':
    unittest.main()
