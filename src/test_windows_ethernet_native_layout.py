"""Exercise the Windows x64 IP Helper ABI without using an adapter or device."""
import ctypes as C
import socket
import struct
import unittest
from unittest.mock import patch

import windows_ethernet as network


ADAPTER_OFFSET = 0
UNICAST_OFFSET = 256
SOCKADDR_OFFSET = 384
NAME_OFFSET = 416
INDEX = 15
SOURCE = '192.168.1.50'


def native_buffer(*, entry_length=64, socket_length=16, lease=0xE1, state=4):
    data = bytearray(512)
    # The native linked list contains actual pointer addresses into this buffer.
    buffer = (C.c_ubyte * len(data)).from_buffer(data)
    base = C.addressof(buffer)
    struct.pack_into('<IIQQQ', data, ADAPTER_OFFSET, 112, INDEX, 0, base + NAME_OFFSET,
                     base + UNICAST_OFFSET)
    struct.pack_into('<II', data, ADAPTER_OFFSET + 100, 6, 1)
    struct.pack_into('<IIQQi', data, UNICAST_OFFSET, entry_length, 0, 0,
                     base + SOCKADDR_OFFSET, socket_length)
    # The SOCKET_ADDRESS pointer and signed length occupy 16 bytes in the
    # Windows x64 ABI. PrefixOrigin follows their four padding bytes.
    struct.pack_into('<IIIIII', data, UNICAST_OFFSET + 32, 3, 1, state, 7200, 3600, lease)
    data[UNICAST_OFFSET + 56] = 24
    struct.pack_into('<H', data, SOCKADDR_OFFSET, socket.AF_INET)
    data[SOCKADDR_OFFSET + 4:SOCKADDR_OFFSET + 8] = socket.inet_aton(SOURCE)
    data[NAME_OFFSET:NAME_OFFSET + 15] = b'{network-test}\0'
    return buffer


@unittest.skipUnless(C.sizeof(C.c_void_p) == 8, 'x64 native IPv4 buffer')
class WindowsUnicastAbiTests(unittest.TestCase):
    def test_native_preferred_ipv4_is_usable(self):
        rows = network._parse_buffer(native_buffer())
        self.assertEqual(rows, (('{network-test}', INDEX, 1, ((SOURCE, 24, 4),)),))
        with patch.object(network, 'read_topology', return_value=rows):
            self.assertEqual(network._preferred_source(INDEX, SOURCE), '{network-test}')

    def test_lease_counts_do_not_change_topology_revision(self):
        samples = iter([native_buffer(lease=0x20), native_buffer(lease=0x1F),
                        native_buffer(lease=0x1E, state=3)])
        revision = network.TopologyRevision(lambda: network._parse_buffer(next(samples)))
        self.assertEqual(revision.read(refresh=True), 1)
        self.assertEqual(revision.read(refresh=True), 1)
        self.assertEqual(revision.read(refresh=True), 2)

    def test_short_unicast_record_cannot_supply_a_prefix(self):
        with self.assertRaisesRegex(OSError, 'Invalid Windows network interface entry'):
            network._parse_buffer(native_buffer(entry_length=56))

    def test_short_sockaddr_is_rejected(self):
        with self.assertRaisesRegex(OSError, 'Invalid Windows Ethernet address'):
            network._parse_buffer(native_buffer(socket_length=15))


if __name__ == '__main__':
    unittest.main(verbosity=2)
