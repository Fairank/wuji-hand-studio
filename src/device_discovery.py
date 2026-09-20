"""Fresh read-only discovery; never reuse a previous serial or start motion."""
import re
import time
from device_profiles import profile
from zenoh_route import DeviceRoute, endpoint, managed_wsl


class SelectionRequired(RuntimeError):
    def __init__(self, devices):
        super().__init__('发现多只机械手，请选择设备 / Select one of the discovered hands')
        self.devices = devices


def candidates(devices):
    found = {}
    for device in devices:
        sn, address = str(device.sn), str(device.address)
        kind = str(device.device_type).lower().replace('_', '')
        match = re.fullmatch(r'WH2([JK])[A-Z][0-9]{11}', sn)
        if 'wujihand2' in kind or (kind == 'devicetype.unknown' and match and address == 'zenoh://' + sn):
            generation = 'hand2'
        elif kind == 'devicetype.wujihand':
            generation = 'hand1'
        else:
            continue
        if address.startswith('zenoh://') and address != 'zenoh://' + sn:
            continue
        found[sn] = dict(serial=sn, address=address, generation=generation,
                         side_hint=('left' if match[1] == 'J' else 'right') if match else None)
    return sorted(found.values(), key=lambda x:x['serial'])


class Routes:
    def __init__(self): self.routes = []
    def close(self):
        for route in self.routes:
            route.close()
        self.routes.clear()


def connect_discovered(manager, address='', serial='', use_routes=None):
    """Address restricts endpoints. Ambiguity always needs a fresh serial choice."""
    bundle = Routes();hand = None
    try:
        routed = managed_wsl() if use_routes is None else use_routes
        target = endpoint(address, 'left') if address else ''
        if routed:
            addresses = [target] if target else ['192.168.1.110:7447', '192.168.1.111:7447']
            for value in addresses:
                route = DeviceRoute(value, 'left')  # side is irrelevant for an explicit endpoint
                try: route.open()
                except RuntimeError:
                    route.close()
                else: bundle.routes.append(route)
            if target and not bundle.routes:
                raise RuntimeError('指定设备地址无协议回应 / No response from the specified address')
        deadline = time.monotonic() + 3
        while True:
            devices = candidates(manager.scan())
            if target:
                devices = [d for d in devices if d['address'].removeprefix('udp/') == target or
                           (routed and bundle.routes and d['address'] == 'zenoh://' + d['serial'])]
            if devices or time.monotonic() >= deadline:break
            time.sleep(.1)
        if not devices:
            raise RuntimeError('未发现机械手，请检查电源、网线或填写自定义地址 / No hand discovered')
        if serial:
            selected = next((d for d in devices if d['serial'] == serial), None)
            if selected is None:raise RuntimeError('所选设备已离线，请重新发现 / Selected device is no longer available')
        elif len(devices) == 1:selected = devices[0]
        else:raise SelectionRequired(devices)
        hand = manager.connect(sn=selected['serial'], device_name='wuji_hand_2' if selected['generation']=='hand2' else 'wuji_hand')
        actual = str(hand.handedness().get() if selected['generation']=='hand2' else hand.handedness_name()).lower().removeprefix('handedness.')
        if actual not in ('left', 'right') or (selected['side_hint'] and selected['side_hint'] != actual):
            raise RuntimeError('设备左右手身份不一致 / Device handedness did not verify')
        if str(hand.serial_number) != selected['serial']:
            raise RuntimeError('设备编号与发现结果不一致 / Device serial did not verify')
        verified = profile(selected['generation'] + '_' + actual)
        return hand, bundle, verified
    except Exception:
        if hand is not None:hand.disconnect()
        bundle.close()
        raise
