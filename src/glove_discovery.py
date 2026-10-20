"""Fresh glove identity probes for managed guests where broadcast is unavailable.

Only the two documented factory data endpoints are tried. A probe opens and
closes a typed glove handle; it never opens a hand driver or starts following.
"""
import re

FACTORY_ENDPOINTS=('192.168.1.100:50001','192.168.1.101:50001')


def scan_gloves(manager,sdk,discovered,allow_direct=False):
    found={str(d.sn):dict(serial=str(d.sn),address=str(d.address),source='sdk_scan')
           for d in discovered if d.device_type==sdk.DeviceType.WujiGlove}
    if allow_direct:
        known={d['address'].removeprefix('udp/') for d in found.values()}
        for index,address in enumerate(FACTORY_ENDPOINTS):
            if address in known:continue
            glove=None
            try:
                glove=manager.connect(address=address,device_name='workbench_glove_probe_'+str(index))
                if not isinstance(glove,sdk.WujiGlove):continue
                serial=str(glove.serial_number)
                if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',serial):continue
                found.setdefault(serial,dict(serial=serial,address=address,source='direct_address_probe'))
            except Exception:
                pass  # A factory endpoint with no response is not a device-fault diagnosis.
            finally:
                if glove is not None:glove.disconnect()
    return sorted(found.values(),key=lambda d:d['serial'])


def connect_glove(manager,selected):
    # Direct-probe identities cannot rely on a broadcast cache in WSL NAT.
    if selected.get('source')=='direct_address_probe':
        if selected['address'] not in FACTORY_ENDPOINTS:raise ValueError('Unknown direct glove endpoint')
        glove=manager.connect(address=selected['address'],device_name='studio_glove')
    else:glove=manager.connect(sn=selected['serial'],device_name='studio_glove')
    try:
        if str(glove.serial_number)!=selected['serial']:
            raise ValueError('手套身份已改变，请重新扫描 / Glove identity changed; rescan')
    except BaseException:
        glove.disconnect()
        raise
    return glove
