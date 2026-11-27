"""Fresh read-only discovery; never reuse a previous serial or start motion."""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from device_profiles import profile
from zenoh_route import DeviceRoute, endpoint, managed_wsl, strip_udp_prefix

HAND2_SERIAL = re.compile(r'WH2([JK])[A-Z][0-9]{11}')


class SelectionRequired(RuntimeError):
    def __init__(self, devices):
        super().__init__('发现多只灵巧手，请选择设备 / Select one of the discovered hands')
        self.devices = devices


class HandUnavailableBeforeConnect(RuntimeError):
    """The selected hand never reached the SDK open call."""


class HandRouteCleanupUnconfirmed(RuntimeError):
    def __init__(self,bundle,hand=None):
        self.bundle=bundle;self.hand=hand
        super().__init__('灵巧手查找通路退出未确认，请断开原会话后重试 / Hand search route cleanup is unconfirmed; disconnect the owning session')


def candidates(devices):
    found = {}
    for device in devices:
        sn, address = str(device.sn), str(device.address)
        kind = str(device.device_type).lower().replace('_', '')
        match = HAND2_SERIAL.fullmatch(sn)
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
        remaining=[]
        for route in self.routes:
            try:
                if route.close() is False:raise RuntimeError('Route close was not acknowledged')
            except Exception:remaining.append(route)
        self.routes=remaining
        if remaining:raise HandRouteCleanupUnconfirmed(self)
        return True


def scan_only(manager,address='',*,include_gloves=False,cancelled=None):
    """Read discovery advertisements without connecting to or enabling a hand."""
    bundle=Routes()
    def check():
        if cancelled is not None and cancelled():raise RuntimeError('Hand discovery cancelled')
    def open_route(route):
        check()
        try:route.open()
        except RuntimeError:pass  # Its exact partial route still reaches finally cleanup.
    try:
        check()
        target=endpoint(address,'left') if address else ''
        if managed_wsl():
            for value in ([target] if target else ['192.168.1.110:7447','192.168.1.111:7447']):
                check()
                bundle.routes.append(DeviceRoute(value,'left'))
            if len(bundle.routes)==1:
                open_route(bundle.routes[0])
            else:
                # Independent Zenoh route opens each have their own short
                # timeout. Join both before SDK scan or finally closing them.
                with ThreadPoolExecutor(max_workers=len(bundle.routes),thread_name_prefix='Hand discovery route') as pool:
                    list(pool.map(open_route,bundle.routes))
            check()
        found={};deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            check()
            advertisements=list(manager.scan())
            check()
            if include_gloves:
                # This directory is typed metadata only. Legacy hand-only
                # callers retain their existing routed Unknown-SN behavior.
                advertisements=[d for d in advertisements if str(d.device_type).lower().replace('_','')
                    in ('devicetype.wujihand','devicetype.wujihand2','devicetype.wujiglove')]
            for item in candidates(advertisements):found[item['serial']]=item
            if include_gloves:
                import wuji_sdk as sdk
                from glove_discovery import scan_gloves
                for item in scan_gloves(manager,sdk,advertisements,allow_direct=False):
                    found[item['serial']]={**item,'device_type':'wuji_glove'}
            time.sleep(.1)
        check()
        return sorted(found.values(),key=lambda r:r['serial'])
    finally:bundle.close()


def connect_discovered(manager, address='', serial='', use_routes=None):
    """Address restricts endpoints. Ambiguity always needs a fresh serial choice."""
    bundle = Routes();hand = None
    try:
        routed = managed_wsl() if use_routes is None else use_routes
        target = endpoint(address, 'left') if address else ''
        match = HAND2_SERIAL.fullmatch(serial or '')
        usb = None
        if routed and not target and serial and not match:
            # Only an explicit serial with a typed, current USB observation
            # can skip the otherwise necessary Ethernet discovery routes.
            usb = next((d for d in candidates(manager.scan()) if d['serial']==serial
                        and d['generation']=='hand1' and d['address'].startswith('usb://')),None)
        opened_routes=0
        if routed and usb is None:
            side = 'right' if match and match[1]=='K' else 'left'
            # An exact Hand 2 serial gives a factory-route hint only. Actual
            # discovery and identity checks still admit its existing custom route.
            addresses = [target or endpoint('',side)] if target or match else [endpoint('','left'),endpoint('','right')]
            for value in addresses:
                route = DeviceRoute(value, side)
                bundle.routes.append(route)
                try: route.open()
                except RuntimeError:pass
                else:opened_routes+=1
            if target and not opened_routes:
                raise HandUnavailableBeforeConnect('指定设备地址无协议回应 / No response from the specified address')
        if usb is not None:
            devices = [usb]
        else:
            deadline = time.monotonic() + 3
            while True:
                devices = candidates(manager.scan())
                if target:
                    devices = [d for d in devices if strip_udp_prefix(d['address']) == target or
                               (routed and opened_routes and d['address'] == 'zenoh://' + d['serial'])]
                # Another hand may advertise before the explicitly selected one.
                # Keep the original bounded discovery window for that exact serial.
                ready = any(d['serial'] == serial for d in devices) if serial else bool(devices)
                if ready or time.monotonic() >= deadline:break
                time.sleep(.1)
        if not devices:
            raise HandUnavailableBeforeConnect('未发现灵巧手，请检查电源、网线或填写自定义地址 / No hand discovered')
        if serial:
            selected = next((d for d in devices if d['serial'] == serial), None)
            if selected is None:raise HandUnavailableBeforeConnect('所选设备已离线，请重新发现 / Selected device is no longer available')
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
    except Exception as error:
        retained=None
        if hand is not None:
            try:
                if hand.disconnect() is False:raise RuntimeError('Hand disconnect was not acknowledged')
            except Exception:retained=hand
        try:bundle.close()
        except HandRouteCleanupUnconfirmed as cleanup:
            cleanup.hand=retained
            raise cleanup from error
        if retained is not None:raise HandRouteCleanupUnconfirmed(bundle,retained) from error
        raise
