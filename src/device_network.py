"""Read-only NIC selection and a narrowly scoped, user-started Windows setup.

No device packets, motor APIs, gateway edits or firewall changes occur here.
"""
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid

_cache = None
_cache_at = 0.0
_lock = threading.Lock()
_setup = None


def target_address(address='', side='left'):
    if side not in ('left', 'right'):
        raise ValueError('Unknown hand side')
    value = address.strip() or ('192.168.1.110:7447' if side == 'left' else '192.168.1.111:7447')
    host, sep, port = value.partition(':')
    ip = ipaddress.IPv4Address(host)
    if ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_link_local:
        raise ValueError('Enter the device Ethernet IPv4 address')
    if sep and (not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535):
        raise ValueError('Invalid device port')
    return f'{ip}:{int(port) if sep else 7447}'


def analyse(adapters, address='', side='left'):
    target = target_address(address, side)
    host = ipaddress.IPv4Address(target.split(':')[0])
    wired = [a for a in adapters if a.get('ethernet') is True]
    active = [a for a in wired if a.get('up') is True]
    matching = []
    candidates = []
    for adapter in active:
        addresses = [ipaddress.IPv4Interface(x) for x in adapter.get('addresses', [])]
        if any(host in x.network and host != x.ip for x in addresses):
            matching.append(adapter)
        elif not adapter.get('gateway') and all(x.ip.is_link_local for x in addresses):
            candidates.append(adapter)
    selected = None
    if len(matching) == 1:
        code, selected = 'subnet_ready', matching[0]
    elif len(matching) > 1 or len(candidates) > 1:
        code = 'multiple_adapters'
    elif len(candidates) == 1:
        code, selected = 'subnet_mismatch', candidates[0]
    elif not active:
        code = 'cable_unplugged' if wired else 'no_ethernet'
    else:
        code = 'network_in_use'
    # A custom device network is detected but never silently re-numbered.
    automatic_subnet = host in ipaddress.IPv4Network('192.168.1.0/24')
    return dict(code=code, target=target, adapters=wired,
                adapter=selected, candidates=candidates,
                can_configure=not matching and bool(candidates) and automatic_subnet,
                proposed_address=('192.168.1.51/24' if str(host)=='192.168.1.50' else '192.168.1.50/24') if automatic_subnet else None,
                hardware_connected=False)


def _run(command):
    r = subprocess.run(command, capture_output=True, timeout=12,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if r.returncode:
        raise RuntimeError('Could not read device network adapters')
    return r.stdout.decode('utf-8-sig', errors='replace')


def read_windows():
    script = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new()
$rows=@(Get-NetAdapter -Physical | Where-Object { $_.ifType -eq 6 } | ForEach-Object {
  $a=$_
  $ips=@(Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object { "$($_.IPAddress)/$($_.PrefixLength)" })
  $gateway=@(Get-NetRoute -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue).Count -gt 0
  [pscustomobject]@{id=$a.InterfaceGuid.ToString();name=$a.Name;ethernet=$true;up=($a.Status -eq 'Up');addresses=$ips;gateway=$gateway}
})
ConvertTo-Json -InputObject $rows -Depth 5 -Compress
"""
    return json.loads(_run(['powershell.exe', '-NoProfile', '-Command', script]))


def read_macos():
    # Detection only until administrator configuration is validated on a Mac.
    ports = _run(['/usr/sbin/networksetup', '-listallhardwareports'])
    rows = []
    for block in ports.split('\n\n'):
        name = re.search(r'^Hardware Port: (.+)$', block, re.M)
        device = re.search(r'^Device: (en\d+)$', block, re.M)
        if not name or not device or any(x in name[1].lower() for x in ('wi-fi', 'airport', 'bluetooth')):
            continue
        details = _run(['/sbin/ifconfig', device[1]])
        addresses = []
        for ip, mask in re.findall(r'\binet (\d+\.\d+\.\d+\.\d+) netmask (0x[0-9a-fA-F]+)', details):
            mask_text = str(ipaddress.IPv4Address(int(mask, 16)))
            addresses.append(str(ipaddress.IPv4Interface(f'{ip}/{mask_text}')))
        rows.append(dict(id=device[1], name=name[1], ethernet=True,
                         up='status: active' in details, addresses=addresses,
                         gateway=True))  # Never auto-configure an unverified Mac adapter.
    return rows


def snapshot(address='', side='left', refresh=False):
    global _cache, _cache_at
    with _lock:
        now = time.monotonic()
        if refresh or _cache is None or now - _cache_at > 8:
            if sys.platform == 'win32':
                _cache = read_windows()
            elif sys.platform == 'darwin':
                _cache = read_macos()
            else:
                return dict(code='external_controller', adapters=[], can_configure=False)
            _cache_at = now
        result = analyse(_cache, address, side)
        result['can_configure'] = result['can_configure'] and sys.platform == 'win32'
        result['setup'] = setup_status()
        return result


def setup_status():
    if _setup is None:
        return dict(state='idle')
    result_file = _setup['result_file']
    if result_file.is_file():
        try:
            result = json.loads(result_file.read_text(encoding='utf-8-sig'))
            return dict(state='completed' if result.get('ok') else 'failed', **result)
        except (OSError, ValueError):
            pass  # The elevated helper may still be writing its result.
    return dict(state='pending' if time.monotonic() - _setup['started'] < 90 else 'unconfirmed')


def configure(adapter_id, address='', side='left'):
    """Only called by the native UI, after the host verifies it is disconnected."""
    global _setup, _cache
    if sys.platform != 'win32':
        raise ValueError('Automatic adapter configuration is not yet verified on this platform')
    if setup_status()['state'] == 'pending':
        raise ValueError('Windows is still completing the previous setup')
    status = snapshot(address, side, refresh=True)
    adapter = next((x for x in status['candidates'] if x['id'] == adapter_id), None)
    if not status['can_configure'] or not adapter:
        raise ValueError('Adapter selection changed; refresh the network check')
    guid = str(uuid.UUID(adapter_id.strip('{}')))
    from runtime_paths import DATA, RESOURCE
    folder = DATA / 'network'; folder.mkdir(parents=True, exist_ok=True)
    if folder.resolve() != DATA.resolve() / 'network':
        raise ValueError('Network record directory must not be a link')
    result_file = folder / ('setup-' + uuid.uuid4().hex + '.json')
    (folder / (result_file.stem + '-before.json')).write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
    script = RESOURCE / 'network_setup_windows.ps1'
    if not script.is_file():
        raise ValueError('Network setup component missing from this build')
    import ctypes
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.ShellExecuteW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                   ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
    shell.ShellExecuteW.restype = ctypes.c_void_p
    command = subprocess.list2cmdline(['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script),
                                      '-AdapterGuid', guid, '-HostAddress', status['proposed_address'].split('/')[0], '-ResultFile', str(result_file)])
    powershell = str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
    code = shell.ShellExecuteW(None, 'runas', powershell, command, None, 0)
    if not code or int(code) <= 32:
        raise RuntimeError('Windows network setup was cancelled or unavailable')
    _setup = dict(result_file=result_file, started=time.monotonic())
    _cache = None
    return dict(state='pending', target=status['target'])
