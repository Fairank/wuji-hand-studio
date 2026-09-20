"""Read macOS physical Ethernet; add only a dedicated adapter alias after OS approval."""
import ipaddress
import re
import shlex
import subprocess
import threading
import sys

_lock = threading.Lock()
_setup = dict(state='idle')


def read(argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    return p.stdout if p.returncode == 0 else ''


def parse_ports(text):
    result = []
    for block in text.split('\n\n'):
        fields = dict(line.split(': ', 1) for line in block.splitlines() if ': ' in line)
        name, device = fields.get('Hardware Port', ''), fields.get('Device', '')
        if re.fullmatch(r'en[0-9]+', device) and re.search(r'ethernet|lan|usb.*10/100|thunderbolt.*bridge', name, re.I) and 'bridge' not in name.lower():
            result.append(dict(id=device, name=name, mac=fields.get('Ethernet Address', '')))
    return result


def classify(adapters, default_interface, address):
    network = ipaddress.IPv4Network(address + '/24', strict=False)
    candidates = []
    for a in adapters:
        if a['id'] == default_interface:
            continue
        values = [ipaddress.IPv4Address(v) for v in a['addresses']]
        a = dict(a, compatible=any(ip in network for ip in values),
                 dedicated=not any(not ip.is_link_local and ip not in network for ip in values))
        if a['dedicated']:
            candidates.append(a)
    live = [a for a in candidates if a['active']]
    if len(live) > 1:
        code, chosen = 'multiple_adapters', None
    elif len(live) == 1:
        chosen = live[0]
        code = 'subnet_ready' if chosen['compatible'] else 'subnet_mismatch'
    else:
        chosen = None
        code = 'cable_unplugged' if candidates else 'network_in_use' if adapters else 'no_ethernet'
    return dict(code=code, candidates=live, adapter=chosen,
                can_configure=bool(live) and code != 'subnet_ready')


def snapshot(address='', side='left'):
    if sys.platform != 'darwin':
        return dict(code='unsupported', can_configure=False)
    from zenoh_route import endpoint
    target = endpoint(address, side).split(':')[0]
    ports = parse_ports(read(['/usr/sbin/networksetup', '-listallhardwareports']))
    for a in ports:
        text = read(['/sbin/ifconfig', a['id']])
        a.update(active=bool(re.search(r'\bstatus: active\b', text)),
                 addresses=re.findall(r'\binet (\d+\.\d+\.\d+\.\d+)\b', text))
    default = re.search(r'interface:\s*(\S+)', read(['/sbin/route', '-n', 'get', 'default']))
    result = classify(ports, default.group(1) if default else '', target)
    return dict(result, platform='macos', setup=dict(_setup), device_address=target)


def configure(adapter_id, address='', side='left'):
    if not re.fullmatch(r'en[0-9]+', adapter_id):
        raise ValueError('Invalid Ethernet adapter')
    if not _lock.acquire(blocking=False):
        raise ValueError('Network setup is already running')
    try:
        state = snapshot(address, side)
        selected = next((a for a in state.get('candidates', []) if a['id'] == adapter_id), None)
        if not selected or not selected['dedicated']:
            raise ValueError('Select a connected dedicated Ethernet adapter')
        if selected['compatible']:
            return state
        net = ipaddress.IPv4Network(state['device_address'] + '/24', strict=False)
        host = str(net.network_address + (51 if state['device_address'].endswith('.50') else 50))
        shell_command = shlex.join(['/sbin/ifconfig', adapter_id, 'inet', host, 'netmask', '255.255.255.0', 'alias'])
        # Fixed AppleScript, data passed as argv. macOS owns the password dialog.
        script = 'on run argv\ndo shell script (item 1 of argv) with administrator privileges\nend run'
        _setup.update(state='pending', error=None)
        p = subprocess.run(['/usr/bin/osascript', '-e', script, shell_command],
                           capture_output=True, text=True, timeout=120)
        if p.returncode:
            raise RuntimeError('Network setup cancelled or failed: ' + p.stderr[-250:])
        _setup['state'] = 'completed'
        result = snapshot(address, side)
        if not any(a['id'] == adapter_id and a['compatible'] for a in result['candidates']):
            raise RuntimeError('macOS did not confirm the device subnet')
        return result
    except Exception as error:
        _setup.update(state='failed', error=str(error))
        raise
    finally:
        _lock.release()
