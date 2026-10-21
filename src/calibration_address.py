"""Verify a freshly discovered glove before official address-based capture."""
import ipaddress
import re


def network_address(value):
    if not isinstance(value,str):raise ValueError('Missing discovered glove address')
    host,separator,port=value.removeprefix('udp/').partition(':')
    ip=ipaddress.IPv4Address(host)
    if (not separator or not port.isascii() or not port.isdecimal() or not 1<=int(port)<=65535
            or ip.is_multicast or ip.is_loopback or ip.is_unspecified or ip.is_link_local or ip.is_reserved):
        raise ValueError('Invalid discovered glove network address')
    return str(ip)+':'+str(int(port))


def verify_glove(address,serial,side,manager=None,sdk=None):
    address=network_address(address)
    if not isinstance(serial,str) or not re.fullmatch(r'WG[A-Za-z0-9_-]{1,77}',serial) or side not in ('left','right'):
        raise ValueError('Invalid expected glove identity')
    if sdk is None:import wuji_sdk as sdk
    if manager is None:manager=sdk.SdkManager.instance()
    glove=manager.connect(address=address,device_name='workbench_calibration_identity')
    try:
        if not isinstance(glove,sdk.WujiGlove) or str(glove.serial_number)!=serial:
            raise ValueError('Glove identity changed; refresh discovery before calibration')
        actual=str(glove.hand_side().get()).lower().removeprefix('handedness.')
        if actual!=side:raise ValueError('Glove side differs from selected calibration side')
    finally:glove.disconnect()
