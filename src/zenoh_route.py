"""Protocol-aware route for the app-owned WSL controller.

The SDK's unknown-address fallback sends a generic Wuji UDP handshake. A
Hand2 Zenoh endpoint first needs a Zenoh session and discovered device identity.
This module never enables joints, changes gains, or emits a motion command.
"""
import ipaddress
import json
import os
import re
import time


def managed_wsl():
    return (os.environ.get('WSL_DISTRO_NAME') == 'HandWorkbenchControl'
            or os.environ.get('WUJI_MANAGED_RUNTIME') == 'macvm')


def endpoint(address, side):
    if side not in ('left', 'right'):
        raise ValueError('Unknown hand side')
    value = address.strip() or ('192.168.1.110:7447' if side == 'left' else '192.168.1.111:7447')
    host, sep, port = value.partition(':')
    ip = ipaddress.IPv4Address(host)
    if ip.is_multicast or ip.is_loopback or ip.is_unspecified or ip.is_link_local:
        raise ValueError('Expected a device Ethernet address')
    if sep and (not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535):
        raise ValueError('Invalid device port')
    return f'{ip}:{int(port) if sep else 7447}'


def configuration(address):
    return dict(mode='router',
                connect=dict(endpoints=['udp/' + address], timeout_ms=1500, exit_on_failure=True),
                listen=dict(endpoints=['tcp/127.0.0.1:0']),
                scouting=dict(multicast=dict(enabled=True, interface='auto', autoconnect=[]),
                              gossip=dict(enabled=False)))


class DeviceRoute:
    def __init__(self, address, side):
        self.address = endpoint(address, side)
        self.side = side
        self.session = None

    def open(self):
        import zenoh
        try:
            self.session = zenoh.open(zenoh.Config.from_json5(json.dumps(configuration(self.address))))
        except Exception as error:
            raise RuntimeError('设备协议连接未完成，请检查设备网络 / Device protocol connection failed') from error
        return self

    def find_serial(self, manager):
        expected = 'J' if self.side == 'left' else 'K'
        deadline = time.monotonic() + 4
        while True:
            devices = manager.scan()
            candidates = []
            for device in devices:
                serial = str(device.sn)
                kind = str(device.device_type).lower().replace('_', '')
                actual = str(device.address).removeprefix('udp/')
                # SDK 2026.8.31 reports Unknown during Zenoh discovery. Its
                # Hand2 serial identifies generation/side; the caller also
                # verifies actual handedness after connecting.
                unknown_zenoh = (kind == 'devicetype.unknown'
                                 and re.fullmatch('WH2' + expected + r'[A-Z][0-9]{11}', serial)
                                 and actual == 'zenoh://' + serial)
                if not serial.startswith('WH2' + expected) or not ('wujihand2' in kind or unknown_zenoh):
                    continue
                # Zenoh discovery reports a serial URI, not its UDP locator.
                # Require the URI identity to agree with the typed scan record.
                if actual == self.address or actual == 'zenoh://' + serial:
                    candidates.append(serial)
            candidates = sorted(set(candidates))
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                raise RuntimeError('发现多个匹配设备，请核对连接 / Multiple matching devices')
            if time.monotonic() >= deadline:
                raise RuntimeError('已建立协议通路，但未发现所选手的身份 / Selected hand identity was not discovered')
            time.sleep(.1)

    def close(self):
        if self.session is not None:
            self.session.close()
            self.session = None


def connect_managed_hand(manager, address, side, device_name='wuji_hand_2'):
    route = DeviceRoute(address, side)
    try:
        route.open()
        serial = route.find_serial(manager)
        hand = manager.connect(sn=serial, device_name=device_name)
        return hand, route
    except Exception:
        route.close()
        raise
