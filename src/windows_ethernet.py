"""Windows IP Helper interface observations and scoped neighbor resolution.

Only the official CLI/SDK produces device identities. Interface changes
invalidate its cached advertisements; the OS adapter names are not devices.
"""
import ctypes as C
from ipaddress import IPv4Address
import socket
import threading
import time


U32 = C.c_uint32
PTR = C.c_void_p


class Adapter(C.Structure):
    # Prefix of IP_ADAPTER_ADDRESSES_LH. Length gates version-dependent fields.
    _fields_ = [('length',U32),('index',U32),('next',PTR),('name',PTR),('unicast',PTR),
                ('anycast',PTR),('multicast',PTR),('dns',PTR),('suffix',PTR),
                ('description',PTR),('friendly_name',PTR),('mac',C.c_ubyte*8),
                ('mac_length',U32),('flags',U32),('mtu',U32),('kind',U32),('status',U32)]


class Unicast(C.Structure):
    # IP_ADAPTER_UNICAST_ADDRESS_LH, including the SOCKET_ADDRESS layout.
    _fields_ = [('length',U32),('flags',U32),('next',PTR),('socket_address',PTR),
                ('socket_length',C.c_int),('prefix_origin',U32),('suffix_origin',U32),
                ('dad_state',U32),('valid_lifetime',U32),('preferred_lifetime',U32),
                ('lease_lifetime',U32),('prefix_length',C.c_ubyte)]


class _SockAddrIn(C.Structure):
    _fields_ = [('family', C.c_uint16), ('port', C.c_uint16),
                ('address', C.c_ubyte * 4), ('padding', C.c_ubyte * 8)]


class _SockAddrInet(C.Union):
    # SOCKADDR_IN6 is 28 bytes and ULONG-aligned.
    _fields_ = [('ipv4', _SockAddrIn), ('ipv6_storage', U32 * 7)]


class _IpNetRow(C.Structure):
    # MIB_IPNET_ROW2 includes the flags and last-reachable time unions.
    _fields_ = [('address', _SockAddrInet), ('index', U32), ('luid', C.c_uint64),
                ('mac', C.c_ubyte * 32), ('mac_length', U32), ('state', C.c_int32),
                ('flags', C.c_ubyte), ('reachability_time', U32)]


def _items(address, kind, start, end):
    visited=set()
    while address:
        if address in visited or len(visited)>=1024 or not start<=address<=end-C.sizeof(kind):
            raise OSError('Invalid Windows network interface list')
        visited.add(address)
        row=kind.from_address(address)
        if row.length<C.sizeof(kind) or address+row.length>end:
            raise OSError('Invalid Windows network interface entry')
        yield row
        address=row.next


def _parse_buffer(buffer):
    start=C.addressof(buffer)
    end=start+C.sizeof(buffer)
    rows=[]
    for link in _items(start,Adapter,start,end):
        if link.kind!=6:  # IF_TYPE_ETHERNET_CSMACD
            continue
        if not link.name or not start<=link.name<end:
            raise OSError('Missing Ethernet interface identifier')
        name=C.string_at(link.name,min(256,end-link.name)).split(b'\0',1)[0].decode('ascii').lower()
        addresses=[]
        for entry in _items(link.unicast,Unicast,start,end):
            address=entry.socket_address
            if not address or entry.socket_length<16 or not start<=address<=end-entry.socket_length:
                raise OSError('Invalid Windows Ethernet address')
            raw=C.string_at(address,16)
            if int.from_bytes(raw[:2],'little')!=socket.AF_INET:
                continue
            addresses.append((socket.inet_ntoa(raw[4:8]),entry.prefix_length,entry.dad_state))
        rows.append((name,link.index,link.status,tuple(sorted(addresses))))
    return tuple(sorted(rows))


def read_topology():
    function=C.WinDLL('iphlpapi').GetAdaptersAddresses
    function.argtypes=[U32,U32,PTR,PTR,C.POINTER(U32)]
    function.restype=U32
    size=U32(16*1024)
    for _ in range(3):
        if not 0<size.value<=1024*1024:
            break
        buffer=C.create_string_buffer(size.value)
        # IPv4, including disconnected NDIS interfaces. No DNS address or
        # multicast memberships are needed to detect a dock changing.
        code=function(socket.AF_INET,0x10e,None,buffer,C.byref(size))
        if code==0:
            return _parse_buffer(buffer)
        if code==232:  # ERROR_NO_DATA
            return ()
        if code!=111:  # ERROR_BUFFER_OVERFLOW
            raise OSError(code,'Could not read the Windows network topology')
    raise OSError('Windows network topology did not fit in the buffer')


def _preferred_source(index, source):
    try:
        links = read_topology()
    except (ValueError, AttributeError) as error:
        raise OSError('Could not verify the current Ethernet source') from error
    matches = [(name, slot, addresses) for name, slot, status, addresses in links
               if status == 1 and any(address == source for address, _, _ in addresses)]
    if len(matches) != 1:
        raise OSError('Ethernet source missing or ambiguous')
    name, slot, addresses = matches[0]
    if slot != index or (source, 24, 4) not in addresses:  # IpDadStatePreferred
        raise OSError('Ethernet source is not preferred on the selected interface')
    return name


def _address_ipv4(value):
    result = _SockAddrInet()
    result.ipv4.family = socket.AF_INET
    result.ipv4.address[:] = value.packed
    return result


def probe_ipv4(index: int, source: str, target: str) -> str | None:
    """Refresh one on-link factory address on an already prepared Ethernet port.

    ResolveIpNetEntry2 refreshes the matching neighbor entry before ARP. Its
    response gives link evidence only. The official discovery supplies identity.
    """
    from device_network import DEVICE_SUBNET, ROUTABLE_HOSTS

    if type(index) is not int or not 1 <= index <= 0xffffff:
        raise ValueError('Invalid Windows interface index')
    if not isinstance(target, str) or target not in ROUTABLE_HOSTS:
        raise ValueError('Expected a factory device address')
    if not isinstance(source, str):
        raise ValueError('Expected a local Ethernet source address')
    source_ip = IPv4Address(source)
    if not DEVICE_SUBNET.network_address < source_ip < DEVICE_SUBNET.broadcast_address or source in ROUTABLE_HOSTS:
        raise ValueError('Expected a local Ethernet source address')

    adapter = _preferred_source(index, source)
    target_ip = IPv4Address(target)
    row = _IpNetRow(address=_address_ipv4(target_ip), index=index)
    source_address = _address_ipv4(source_ip)
    try:
        resolve = C.WinDLL('iphlpapi').ResolveIpNetEntry2
    except AttributeError as error:
        raise OSError('Windows neighbor resolution is unavailable') from error
    resolve.argtypes = [C.POINTER(_IpNetRow), C.POINTER(_SockAddrInet)]
    resolve.restype = U32
    code = resolve(C.byref(row), C.byref(source_address))
    if code not in (0, 67):  # ERROR_BAD_NET_NAME: unreachable on this link.
        raise OSError(code, 'Windows neighbor resolution failed')
    if _preferred_source(index, source) != adapter:
        raise OSError('Ethernet interface was replaced during neighbor resolution')
    if code == 67:
        return None
    if (row.index != index or row.address.ipv4.family != socket.AF_INET
            or bytes(row.address.ipv4.address) != target_ip.packed):
        raise OSError('Windows resolved a different Ethernet path')
    mac = bytes(row.mac[:6])
    if row.mac_length != 6 or not any(mac) or mac[0] & 1 or row.flags & 2:
        raise OSError('Windows returned no usable neighbor hardware address')
    return mac.hex('-').upper()


class TopologyRevision:
    def __init__(self, reader=read_topology):
        self.reader=reader
        self.lock=threading.Lock()
        self.state=None
        self.version=0
        self.read_after=0.

    def read(self,*,refresh=False):
        with self.lock:
            now=time.monotonic()
            if refresh or now>=self.read_after:
                try:
                    state=self.reader()
                except (OSError,ValueError,AttributeError):
                    state=None
                if state!=self.state:
                    self.state=state
                    self.version+=1
                self.read_after=now+.4
            return self.version

    def links(self):
        self.read()
        with self.lock:
            return None if self.state is None else tuple(row[:3] for row in self.state)


TOPOLOGY=TopologyRevision()
