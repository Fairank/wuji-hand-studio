"""Exercise the Windows neighbor ABI with synthetic ports and a fake DLL."""
import ctypes as C
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import windows_ethernet as ethernet


INDEX = 17
SOURCE = '192.168.1.55'
TARGET = '192.168.1.111'
MAC = bytes.fromhex('02 A1 B2 C3 D4 E5')
LINKS = (('dock-a', INDEX, 1, ((SOURCE, 24, 4),)),)


class WindowsEthernetProbeTests(unittest.TestCase):
    def setUp(self):
        self.native = Mock(side_effect=self.reply)
        dll = patch.object(ethernet.C, 'WinDLL', create=True,
                           return_value=SimpleNamespace(ResolveIpNetEntry2=self.native))
        link = patch.object(ethernet, 'read_topology', return_value=LINKS)
        self.dll = dll.start()
        self.topology = link.start()
        self.addCleanup(dll.stop)
        self.addCleanup(link.stop)

    def reply(self, row, source):
        # Windows MIB_IPNET_ROW2 embeds a 28-byte SOCKADDR_INET, and
        # aligns the LUID and subsequent ULONGs without packing.
        self.assertEqual(C.sizeof(row._obj), 88)
        self.assertEqual(C.sizeof(source._obj), 28)
        wire = (C.c_ubyte * 88).from_address(C.addressof(row._obj))
        self.assertEqual(bytes(wire[:2]), socket.AF_INET.to_bytes(2, 'little'))
        self.assertEqual(bytes(wire[4:8]), socket.inet_aton(TARGET))
        self.assertEqual(bytes(wire[28:32]), INDEX.to_bytes(4, 'little'))
        self.assertEqual(bytes(wire[32:40]), b'\0' * 8)
        self.assertEqual(bytes(source._obj)[:8],
                         bytes(wire[:4]) + socket.inet_aton(SOURCE))
        wire[40:46] = MAC
        wire[72:76] = (6).to_bytes(4, 'little')
        return 0

    def test_bound_native_probe_normalizes_the_mac(self):
        self.assertEqual(ethernet.probe_ipv4(INDEX, SOURCE, TARGET), '02-A1-B2-C3-D4-E5')
        self.native.assert_called_once()

    def test_unreachable_is_distinct_from_pin_or_provider_errors(self):
        self.native.side_effect = None
        self.native.return_value = 67
        self.assertIsNone(ethernet.probe_ipv4(INDEX, SOURCE, TARGET))
        for code in (5, 50, 87, 1168):
            with self.subTest(code=code), self.assertRaises(OSError) as failure:
                self.native.return_value = code
                ethernet.probe_ipv4(INDEX, SOURCE, TARGET)
            self.assertEqual(failure.exception.errno, code)

    def test_another_interface_in_the_native_result_fails_closed(self):
        def wrong_interface(row, source):
            self.reply(row, source)
            C.c_uint32.from_address(C.addressof(row._obj) + 28).value = INDEX + 1
            return 0
        self.native.side_effect = wrong_interface
        with self.assertRaises(OSError):
            ethernet.probe_ipv4(INDEX, SOURCE, TARGET)

    def test_same_index_reuse_during_probe_fails_closed(self):
        replacement = (('dock-b', INDEX, 1, ((SOURCE, 24, 4),)),)
        self.topology.side_effect = [LINKS, replacement]
        with self.assertRaises(OSError):
            ethernet.probe_ipv4(INDEX, SOURCE, TARGET)

    def test_unprepared_or_ambiguous_source_is_rejected_before_native_probe(self):
        duplicate = ('dock-b', INDEX + 1, 1, ((SOURCE, 24, 4),))
        for links in ((), (('dock-a', INDEX, 1, ((SOURCE, 24, 1),)),), (*LINKS, duplicate)):
            with self.subTest(links=links), self.assertRaises(OSError):
                self.topology.return_value = links
                ethernet.probe_ipv4(INDEX, SOURCE, TARGET)
        self.dll.assert_not_called()

    def test_rejects_invalid_and_non_device_targets_before_native_probe(self):
        for args in ((True, SOURCE, TARGET), (0, SOURCE, TARGET),
                     (INDEX, '169.254.1.2', TARGET), (INDEX, TARGET, TARGET),
                     (INDEX, '192.168.1.100', TARGET), (INDEX, SOURCE, '192.168.1.1')):
            with self.subTest(args=args), self.assertRaises(ValueError):
                ethernet.probe_ipv4(*args)
        self.dll.assert_not_called()

    def test_success_requires_a_full_unicast_mac(self):
        for size, data in ((5, MAC), (0, MAC), (6, bytes(6)), (6, b'\xff' * 6)):
            with self.subTest(size=size, data=data), self.assertRaises(OSError):
                def invalid_mac(row, source):
                    self.reply(row, source)
                    base = C.addressof(row._obj)
                    (C.c_ubyte * 6).from_address(base + 40)[:] = data
                    C.c_uint32.from_address(base + 72).value = size
                    return 0
                self.native.side_effect = invalid_mac
                ethernet.probe_ipv4(INDEX, SOURCE, TARGET)


if __name__ == '__main__':
    unittest.main()
