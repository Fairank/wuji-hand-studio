"""Discovery overlap and exact USB selection, with no hardware access."""
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock, patch

import device_discovery as discovery


LEFT = 'WH2JA00000000041'
RIGHT = 'WH2KA00000000042'
USB = 'WH1-SYNTHETIC'


def hand2(serial):
    return N(sn=serial, address='zenoh://' + serial, device_type='DeviceType.WujiHand2')


class Clock:
    def __init__(self):
        self.now = 0.

    def monotonic(self):
        return self.now

    def sleep(self, delay):
        self.now += delay


class DiscoverySpeedTests(unittest.TestCase):
    def test_exact_managed_usb_hand_does_not_wait_for_unrelated_ethernet_routes(self):
        usb = N(sn=USB, address='usb://synthetic', device_type='DeviceType.WujiHand')
        handle = N(serial_number=USB, handedness_name=Mock(return_value='left'),
                   disconnect=Mock(), enable=Mock())
        manager = N(scan=Mock(return_value=[hand2(RIGHT), usb]), connect=Mock(return_value=handle))
        with patch.object(discovery, 'DeviceRoute') as route:
            hand, bundle, profile = discovery.connect_discovered(manager, serial=USB, use_routes=True)
        self.assertIs(hand, handle)
        self.assertEqual(profile['id'], 'hand1_left')
        route.assert_not_called()
        manager.scan.assert_called_once()
        manager.connect.assert_called_once_with(sn=USB, device_name='wuji_hand')
        handle.enable.assert_not_called()
        self.assertTrue(bundle.close())

    def test_default_route_opens_overlap_while_sdk_reads_stay_serial_and_keep_late_peer(self):
        clock = Clock()
        both_opening = threading.Barrier(2)
        owner = threading.get_ident()
        routes = []
        calls = []

        class Route:
            def __init__(self, address, side):
                self.address = address
                self.opened = False
                self.closed = False
                routes.append(self)

            def open(self):
                try:
                    both_opening.wait(.3)
                except threading.BrokenBarrierError as error:
                    raise RuntimeError('Other synthetic route was not started') from error
                self.opened = True

            def close(self):
                self.closed = True

        def scan():
            calls.append(threading.get_ident())
            return [hand2(LEFT)] if clock.now < 2.7 else [hand2(LEFT), hand2(RIGHT)]

        manager = N(scan=scan, connect=Mock())
        with patch.object(discovery, 'DeviceRoute', Route), patch.object(discovery, 'time', clock), \
                patch.object(discovery, 'managed_wsl', return_value=True):
            rows = discovery.scan_only(manager)
        self.assertEqual([row['serial'] for row in rows], [LEFT, RIGHT])
        self.assertEqual(len(routes), 2)
        self.assertTrue(all(route.opened and route.closed for route in routes))
        self.assertTrue(calls)
        self.assertEqual(set(calls), {owner})
        manager.connect.assert_not_called()

    def test_cancelled_parallel_routes_wait_for_both_opens_and_retain_only_unclosed_route(self):
        barrier = threading.Barrier(2)
        cancelled = threading.Event()
        routes = []

        class Route:
            def __init__(self, address, side):
                self.address = address
                self.ended = False
                self.close_calls = 0
                self.close_before_end = False
                routes.append(self)

            def open(self):
                barrier.wait(1)
                if self.address.endswith('.110:7447'):
                    cancelled.set()
                elif not cancelled.wait(1):
                    raise AssertionError('Synthetic cancellation was not observed')
                self.ended = True

            def close(self):
                self.close_calls += 1
                self.close_before_end |= not self.ended
                return self.address.endswith('.111:7447') or self.close_calls > 1

        manager = N(scan=Mock(), connect=Mock())
        with patch.object(discovery, 'DeviceRoute', Route), patch.object(discovery, 'managed_wsl', return_value=True):
            with self.assertRaises(discovery.HandRouteCleanupUnconfirmed) as caught:
                discovery.scan_only(manager, cancelled=cancelled.is_set)
        self.assertTrue(cancelled.is_set())
        self.assertTrue(all(route.ended and not route.close_before_end for route in routes))
        self.assertEqual([route.close_calls for route in routes], [1, 1])
        self.assertEqual(caught.exception.bundle.routes, [routes[0]])
        manager.scan.assert_not_called()
        manager.connect.assert_not_called()
        self.assertTrue(caught.exception.bundle.close())
        self.assertEqual([route.close_calls for route in routes], [2, 1])

    def test_usb_fast_path_still_rejects_mismatched_sdk_returned_serial(self):
        usb = N(sn=USB, address='usb://selected', device_type='DeviceType.WujiHand')
        handle = N(serial_number='WH1-OTHER', handedness_name=Mock(return_value='right'),
                   disconnect=Mock(), enable=Mock())
        manager = N(scan=Mock(return_value=[usb]), connect=Mock(return_value=handle))
        with patch.object(discovery, 'DeviceRoute') as route:
            with self.assertRaisesRegex(RuntimeError, 'serial did not verify'):
                discovery.connect_discovered(manager, serial=USB, use_routes=True)
        route.assert_not_called()
        handle.disconnect.assert_called_once()
        handle.enable.assert_not_called()


if __name__ == '__main__':
    unittest.main()
