"""Exact-SN official discovery route, passed only with explicit continuation."""
import ipaddress
import re


def validate_glove_route(value,serial):
    if (not isinstance(value,dict) or set(value)!={'source','serial','address'}
            or value.get('source')!='official_discovery' or value.get('serial')!=serial
            or not isinstance(serial,str) or not re.fullmatch('[A-Za-z0-9_.-]{1,100}',serial)
            or not isinstance(value.get('address'),str)):
        raise ValueError('所选手套的网络身份不可核对 / Selected glove route identity is unverified')
    address=value['address'].removeprefix('udp/')
    match=re.fullmatch(r'([0-9.]+):([1-9][0-9]{0,4})',address)
    if not match:raise ValueError('官方手套地址格式不可核对 / Official glove address is unverified')
    host=ipaddress.IPv4Address(match[1]);port=int(match[2])
    if (host.is_unspecified or host.is_loopback or host.is_multicast or host.is_link_local
            or host==ipaddress.IPv4Address('255.255.255.255') or not 1<=port<=65535):
        raise ValueError('官方手套地址不可用于接入 / Official glove address cannot be used for connection')
    return dict(source='official_discovery',serial=serial,address=str(host)+':'+str(port))


def route_from_discovery(rows,serial):
    from calibration_cli import is_glove_device
    if not isinstance(rows,list):raise ValueError('Official glove directory is unavailable')
    matches=[row for row in rows if isinstance(row,dict) and (row.get('sn') or row.get('serial'))==serial]
    if not matches:return None
    if len(matches)!=1 or not is_glove_device(matches[0]):
        raise ValueError('所选手套的官方目录身份不一致 / Official glove directory identity differs')
    row=matches[0];address=row.get('address')
    if not address or isinstance(address,str) and address.startswith('zenoh://'):return None
    return validate_glove_route(dict(source='official_discovery',serial=serial,address=address),serial)
