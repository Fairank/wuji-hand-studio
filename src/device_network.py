"""NIC diagnostics and narrowly scoped Windows device route maintenance.

Device identity and connection remain the responsibility of the CLI and SDK.
"""
from concurrent.futures import ThreadPoolExecutor
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
_cache_revision = None
_lock = threading.Lock()
_setup_lock = threading.Lock()
_setup = None
_dock_recovery = None
GLOVE_ENDPOINTS = {'glove_left': '192.168.1.100:50001', 'glove_right': '192.168.1.101:50001'}
ROUTABLE_HOSTS = {*(endpoint.split(':')[0] for endpoint in GLOVE_ENDPOINTS.values()),
                  '192.168.1.110', '192.168.1.111'}
DEVICE_SUBNET = ipaddress.IPv4Network('192.168.1.0/24')
HOST_POOL = tuple(DEVICE_SUBNET[index] for index in range(50, 100))


def target_address(address='', side='left', *, target='hand'):
    if target in GLOVE_ENDPOINTS:
        if address:
            raise ValueError('Use the selected glove network address')
        return GLOVE_ENDPOINTS[target]
    if target != 'hand':
        raise ValueError('Unknown device network target')
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


def analyse(adapters, address='', side='left', *, target='hand'):
    endpoint = target_address(address, side, target=target)
    host = ipaddress.IPv4Address(endpoint.split(':')[0])
    wired = [a for a in adapters if a.get('ethernet') is True]
    active = [a for a in wired if a.get('up') is True]
    matching = []
    candidates = []
    for adapter in active:
        addresses = [ipaddress.IPv4Interface(x) for x in adapter.get('addresses', [])]
        if any(host in x.network and host != x.ip for x in addresses):
            matching.append(adapter)
        elif not adapter.get('gateway') and not adapter.get('saved_address') and all(x.ip.is_link_local for x in addresses):
            candidates.append(adapter)
    # read_windows includes all active host routes, including those on
    # interfaces (Wi-Fi/virtual) that aren't eligible for configuration.
    routes = next((row['routes'] for row in adapters if 'routes' in row), None)
    prefix = str(host) + '/32'
    exact = [row for row in routes or () if row.get('prefix') == prefix]
    persistent_routes = next((row['persistent_routes'] for row in adapters if 'persistent_routes' in row), ())
    exact_paths = {(row.get('index'), row.get('next_hop')) for row in exact}
    stale_route = any(row.get('prefix') == prefix and (row.get('index'), row.get('next_hop')) not in exact_paths
                      for row in persistent_routes)
    assigned = {ipaddress.IPv4Interface(value).ip
                for adapter in adapters for value in adapter.get('addresses', [])}
    dedicated = [row for row in matching if not row.get('gateway') and type(row.get('index')) is int
                 and any(ipaddress.IPv4Interface(value).network == DEVICE_SUBNET
                         for value in row['addresses'])]
    selected = None
    if exact or stale_route or host in assigned:
        if not stale_route and len(exact) == 1 and host not in assigned and exact[0].get('next_hop') == '0.0.0.0':
            selected = next((row for row in matching if row.get('index') == exact[0]['index']), None)
        code = 'subnet_ready' if selected is not None else 'route_conflict'
    elif len(matching) > 1 and routes is not None and dedicated and str(host) in ROUTABLE_HOSTS:
        code = 'route_required'
    elif len(matching) == 1:
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
    automatic_subnet = DEVICE_SUBNET.network_address < host < DEVICE_SUBNET.broadcast_address
    proposed = next((f'{value}/24' for value in HOST_POOL if value != host and value not in assigned), None) if automatic_subnet else None
    available = [dict(row, setup_kind='route') for row in dedicated] if code == 'route_required' else []
    if proposed is not None and code != 'route_conflict':
        available.extend(dict(row, setup_kind='address') for row in candidates)
    return dict(code=code, target=endpoint, adapters=wired,
                adapter=selected, candidates=available,
                can_configure=bool(available),
                proposed_address=proposed,
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
$allIps=@(Get-NetIPAddress -AddressFamily IPv4 -PolicyStore ActiveStore -ErrorAction Stop)
$allRoutes=@(Get-NetRoute -PolicyStore ActiveStore -ErrorAction Stop)
$savedIps=@(try { Get-NetIPAddress -AddressFamily IPv4 -PolicyStore PersistentStore -ErrorAction Stop }
  catch { if($_.CategoryInfo.Category -ne 'ObjectNotFound'){ throw } })
$savedRoutes=@(try { Get-NetRoute -PolicyStore PersistentStore -ErrorAction Stop }
  catch { if($_.CategoryInfo.Category -ne 'ObjectNotFound'){ throw } })
$interfaces=@(Get-NetAdapter -IncludeHidden)
$interfaceIndices=@(Get-NetIPInterface -IncludeAllCompartments -ErrorAction Stop |
    Select-Object -ExpandProperty InterfaceIndex -Unique)
$rows=@(Get-NetAdapter -Physical -IncludeHidden | Where-Object { $_.InterfaceType -eq 6 } | ForEach-Object {
  $a=$_
  $local=@($allIps | Where-Object { $_.InterfaceIndex -eq $a.ifIndex })
  $ips=@($local | ForEach-Object { "$($_.IPAddress)/$($_.PrefixLength)" })
  $preferred=@($local | Where-Object {$_.AddressState -eq 'Preferred'} | ForEach-Object { "$($_.IPAddress)/$($_.PrefixLength)" })
  $gateway=@(($allRoutes+$savedRoutes) | Where-Object {$_.InterfaceIndex -eq $a.ifIndex -and $_.DestinationPrefix -in '0.0.0.0/0','::/0'}).Count -gt 0
  $saved=@($savedIps | Where-Object {$_.InterfaceIndex -eq $a.ifIndex -and $_.IPAddress -notlike '169.254.*'}).Count -gt 0
  [pscustomobject]@{id=$a.InterfaceGuid.ToString();name=$a.Name;index=$a.ifIndex;ethernet=$true;up=($a.Status -eq 'Up');addresses=$ips;probe_addresses=$preferred;gateway=$gateway;saved_address=$saved}
})
# Occupancy-only row: never a selectable/configurable Ethernet adapter.
function Format-HostRoutes($routes) {
  $routes | Where-Object {$_.DestinationPrefix -like '*.*/*' -and $_.DestinationPrefix -like '*/32'} | ForEach-Object {
    [pscustomobject]@{prefix=$_.DestinationPrefix;index=$_.InterfaceIndex;next_hop=$_.NextHop
      route_metric=$_.RouteMetric;protocol=[string]$_.Protocol;publish=[string]$_.Publish;instance_id=$_.InstanceID}
  }
}
$rows+= [pscustomobject]@{ethernet=$false;addresses=@(($allIps+$savedIps) | ForEach-Object { "$($_.IPAddress)/$($_.PrefixLength)" });
  routes=@(Format-HostRoutes $allRoutes);persistent_routes=@(Format-HostRoutes $savedRoutes)
  interface_ids=@($interfaces | ForEach-Object {[pscustomobject]@{index=$_.ifIndex;id=[string]$_.InterfaceGuid}});
  interface_indices=$interfaceIndices}
ConvertTo-Json -InputObject $rows -Depth 5 -Compress
"""
    return json.loads(_run(['powershell.exe', '-NoProfile', '-Command', script]))


def _adapter_guid(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _route_record(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        return {}
    row = json.loads(path.read_text(encoding='utf-8-sig'))
    return row if isinstance(row, dict) else {}


def _owned_route_records(folder):
    if not folder.is_dir() or folder.is_symlink() or folder.is_junction():
        return
    paths = [p for p in folder.glob('setup-*.json') if re.fullmatch(r'setup-[a-f0-9]{32}\.json', p.name)]
    seen = set()
    for path in sorted(paths, key=lambda p: p.stat().st_mtime_ns, reverse=True):
        try:
            row = _route_record(path)
            prefix = row.get('route')
            guid = _adapter_guid(row.get('adapter'))
            if (not guid or row.get('ok') is not True or row.get('persistent') is not True
                    or prefix not in {host+'/32' for host in ROUTABLE_HOSTS} or prefix in seen
                    or row.get('route_metric', 5) != 5):
                continue
            index = row.get('interface_index')
            if index is None:
                before = _route_record(path.with_name(path.stem+'-before.json'))
                candidates = [a for a in before.get('candidates',[]) if isinstance(a,dict)
                              and _adapter_guid(a.get('id'))==guid and a.get('setup_kind')=='route']
                if (str(before.get('target','')).split(':')[0] != prefix.split('/')[0]
                        or len(candidates)!=1):
                    continue
                index = candidates[0].get('index')
            if type(index) is int and 0 < index <= 2147483647:
                seen.add(prefix)
                yield prefix, guid, index, row.get('route_instance'), path
        except (OSError, ValueError, TypeError):
            continue


def _device_routes(row, prefix):
    return ([r for r in row.get(store,()) if r.get('prefix')==prefix]
            for store in ('routes','persistent_routes'))


def _matches_dock_route(route, index):
    return (route.get('index')==index and route.get('next_hop')=='0.0.0.0'
            and route.get('route_metric')==5 and str(route.get('protocol')).lower() in ('netmgmt','3'))


def _index_owners(table, index):
    return {_adapter_guid(row.get('id')) for row in table['interface_ids'] if row.get('index')==index}


def _disconnected_adapter(adapters, guid, index):
    return any(row.get('ethernet') is True and _adapter_guid(row.get('id'))==guid
               and row.get('index')==index and row.get('up') is False and not row.get('gateway')
               for row in adapters)


def _recorded_dock_route(table, adapters, active, saved, record):
    prefix, guid, index, instance, receipt = record
    owners = _index_owners(table,index)
    present = owners=={guid} and _disconnected_adapter(adapters,guid,index)
    absent = (not owners and not active and index not in table.get('interface_indices',())
              and not any(_adapter_guid(row.get('id'))==guid for row in table['interface_ids'])
              and isinstance(instance,str) and bool(instance))
    if ((present or absent) and len(saved)==1 and len(active)<=1
            and all(_matches_dock_route(row,index) for row in active+saved)
            and (not instance or saved[0].get('instance_id')==instance)):
        return dict(prefix=prefix,old_guid=guid,old_index=index,receipt=receipt)
    return None


def _returning_dock_route(table, active, saved, record):
    prefix,guid,index,instance,receipt=record
    if (len(saved)!=1 or len(active)>1 or _index_owners(table,index)!={guid}
            or not instance or saved[0].get('instance_id')!=instance
            or not all(_matches_dock_route(row,index) for row in active+saved)):
        return None
    prior=_route_record(receipt).get('temporary_address')
    if not isinstance(prior,str) or not re.fullmatch(r'192\.168\.1\.[5-9][0-9]/24',prior):
        return None
    return dict(prefix=prefix,receipt=receipt,restore_address=True)


def _observed_dock_route(table, adapters, prefix, active, saved):
    if len(active)>1 or len(saved)>1 or not (active or saved) or 'interface_indices' not in table:
        return None
    row = (active+saved)[0]
    index,metric = row.get('index'),row.get('route_metric')
    if type(index) is not int or index<=0 or type(metric) is not int or not 0<=metric<=65535:
        return None
    for route in active+saved:
        instance = route.get('instance_id')
        if (route.get('index')!=index or route.get('route_metric')!=metric
                or route.get('next_hop')!='0.0.0.0'
                or str(route.get('protocol')).lower() not in ('netmgmt','3')
                or str(route.get('publish')).lower() not in ('no','0')
                or not isinstance(instance,str) or not 0<len(instance)<=1024):
            return None
    owners = _index_owners(table,index)
    guid = next(iter(owners)) if len(owners)==1 else None
    if owners:
        if not guid or not _disconnected_adapter(adapters,guid,index):
            return None
    elif index in table['interface_indices']:
        return None
    observed = dict(prefix=prefix,old_index=index,old_guid=guid,route_metric=metric,
                    active_instance=active[0]['instance_id'] if active else None,
                    persistent_instance=saved[0]['instance_id'] if saved else None)
    return dict(prefix=prefix,old_index=index,old_guid=guid,receipt=None,observed=observed)


def _ready_docks(adapters):
    prepared, blank = [], []
    for row in adapters:
        guid,index = _adapter_guid(row.get('id')),row.get('index')
        if not guid or type(index) is not int or not row.get('ethernet') or not row.get('up'):
            continue
        addresses = row.get('addresses',())
        local = [ipaddress.IPv4Interface(value) for value in row.get('probe_addresses',addresses)]
        sources = [str(address.ip) for address in local
                   if address.network==DEVICE_SUBNET and str(address.ip) not in ROUTABLE_HOSTS]
        if sources:
            prepared.append((guid,index,sources[0],bool(row.get('gateway'))))
        elif (not row.get('gateway') and not row.get('saved_address')
              and all(ipaddress.IPv4Interface(value).ip.is_link_local for value in addresses)):
            blank.append((guid,index))
    return prepared,blank


def _probe_docks(candidates, host):
    from windows_ethernet import probe_ipv4
    if not candidates:
        return []
    with ThreadPoolExecutor(max_workers=min(8,len(candidates)),thread_name_prefix='Wuji dock probe') as pool:
        macs = list(pool.map(lambda nic: probe_ipv4(nic[1],nic[2],host),candidates))
    return [(adapter,mac) for adapter,mac in zip(candidates,macs) if mac]


def dock_recovery_plans(adapters, folder, *, records=None):
    table = next((row for row in adapters if 'interface_ids' in row), {})
    if not table:
        return []
    prepared,blank = _ready_docks(adapters)
    known = {row[0]:row for row in (_owned_route_records(folder) if records is None else records)}
    assigned = {value.split('/')[0] for value in table.get('addresses',())}
    address = None
    if len(blank)==1:
        address = next((str(value) for value in HOST_POOL if str(value) not in assigned),None)
    plans,returning,cold,unused = [],[],[],[]
    for host in sorted(ROUTABLE_HOSTS-assigned):
        prefix = host+'/32'
        active,saved = _device_routes(table,prefix)
        if not active and not saved:
            unused.append(host)
            continue
        if prefix in known:
            record=known[prefix]
            if address and blank[0]==(record[1],record[2]):
                restore=_returning_dock_route(table,active,saved,record)
                if restore is not None:
                    returning.append(restore)
                    continue
            old = _recorded_dock_route(table,adapters,active,saved,record)
            if old is None and not active and saved and not _index_owners(table,saved[0].get('index')):
                old = _observed_dock_route(table,adapters,prefix,active,saved)
        else:
            old = _observed_dock_route(table,adapters,prefix,active,saved)
        if old is None:
            continue
        try:
            found = _probe_docks(prepared,host)
        except (OSError, ValueError):
            continue
        if len(found)==1 and not found[0][0][3]:
            (guid,index,_,_),mac = found[0]
            plans.append(dict(old,new_guid=guid,new_index=index,mac=mac))
        elif not found:
            cold.append(old)
    if address:
        guid,index=blank[0]
        # For an untouched dock, the helper resolves the factory candidates
        # before writing a route. No order is used to guess a device.
        if returning or cold:
            plans.extend(dict(old,new_guid=guid,new_index=index,host_address=address) for old in returning+cold)
        elif unused:
            plans.append(dict(prefix=None,new_guid=guid,new_index=index,host_address=address,
                              candidate_addresses=unused,receipt=None))
    return plans


def _run_elevated_route_script(script, arguments):
    import ctypes as C
    from ctypes import wintypes as W
    class ExecuteInfo(C.Structure):
        _fields_ = [('size',W.DWORD),('mask',W.ULONG),('window',W.HWND),
                    ('verb',W.LPCWSTR),('file',W.LPCWSTR),('arguments',W.LPCWSTR),
                    ('directory',W.LPCWSTR),('show',C.c_int),('instance',W.HINSTANCE),
                    ('id_list',C.c_void_p),('class_name',W.LPCWSTR),('class_key',W.HKEY),
                    ('hot_key',W.DWORD),('icon_or_monitor',W.HANDLE),('process',W.HANDLE)]
    shell = C.WinDLL('shell32', use_last_error=True)
    execute = shell.ShellExecuteExW
    execute.argtypes = [C.POINTER(ExecuteInfo)]
    execute.restype = W.BOOL
    powershell = str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe')
    command = subprocess.list2cmdline(['-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),*arguments])
    request = ExecuteInfo(size=C.sizeof(ExecuteInfo),mask=0x140,verb='runas',
                          file=powershell,arguments=command,show=0)
    if not execute(C.byref(request)) or not request.process:
        raise OSError(C.get_last_error(),'Windows did not approve device route recovery')
    helper = _ElevatedRouteProcess(request.process)
    if not helper.close(60):
        from official_device_discovery import DiscoveryCleanupUnconfirmed
        raise DiscoveryCleanupUnconfirmed('Windows route helper exit is unconfirmed',owned=helper)


class _ElevatedRouteProcess:
    def __init__(self,handle):
        self.handle=handle

    def close(self,timeout=2):
        import ctypes as C
        from ctypes import wintypes as W
        kernel=C.WinDLL('kernel32',use_last_error=True)
        kernel.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD]
        kernel.WaitForSingleObject.restype=W.DWORD
        kernel.CloseHandle.argtypes=[W.HANDLE]
        if self.handle and kernel.WaitForSingleObject(self.handle,int(timeout*1000))!=0:
            return False
        if self.handle:
            kernel.CloseHandle(self.handle)
            self.handle=None
        return True


def _apply_dock_route(plan, folder, token):
    from runtime_paths import DATA, RESOURCE
    directory=DATA/'network'
    directory.mkdir(parents=True,exist_ok=True)
    if directory.is_symlink() or directory.is_junction():
        raise ValueError('Network record directory must not be a link')
    nonce=uuid.uuid4().hex
    result_file=directory/('setup-'+nonce+'.json')
    args=['-AdapterGuid',plan['new_guid'],'-ExpectedInterfaceIndex',str(plan['new_index']),
          '-ScanMarker',str(folder/'session.json'),'-ScanToken',token,'-ResultFile',str(result_file)]
    if plan['prefix']:
        args+=['-DeviceAddress',plan['prefix'].split('/')[0]]
    else:
        args+=['-CandidateAddresses',','.join(plan['candidate_addresses'])]
    if plan.get('host_address'):
        args+=['-HostAddress',plan['host_address']]
    if plan.get('mac'):
        args+=['-NeighborMac',plan['mac']]
    if plan.get('restore_address'):
        args+=['-RestoreAddressReceipt',str(plan['receipt'])]
    elif plan.get('receipt'):
        args+=['-PreviousReceiptPath',str(plan['receipt']),'-PreviousAdapterGuid',plan['old_guid'],
               '-PreviousInterfaceIndex',str(plan['old_index'])]
    evidence=None
    try:
        if plan.get('observed'):
            evidence=directory/('observed-route-'+nonce+'.json')
            with evidence.open('x',encoding='utf-8') as stream:
                json.dump(plan['observed'],stream)
            args+=['-PreviousRouteState',str(evidence)]
        _run_elevated_route_script(RESOURCE/'network_setup_windows.ps1',args)
        return _route_record(result_file)
    finally:
        if evidence:
            evidence.unlink(missing_ok=True)


def _recover_with_cancellation(plan,folder,token,cancelled):
    from official_device_discovery import DiscoveryCleanupUnconfirmed
    cancel_file=folder/('network-cancel-'+token)
    finished=threading.Event()
    unconfirmed=False
    def watch():
        while not finished.wait(.05):
            if cancelled():
                cancel_file.touch(exist_ok=True)
                return
    watcher=threading.Thread(target=watch,name='Wuji network recovery cancellation',daemon=True)
    watcher.start()
    try:
        if cancelled():
            return {}
        return _apply_dock_route(plan,folder,token)
    except DiscoveryCleanupUnconfirmed:
        unconfirmed=True
        cancel_file.touch(exist_ok=True)
        raise
    finally:
        finished.set()
        watcher.join()
        if not unconfirmed:
            cancel_file.unlink(missing_ok=True)


def recover_dock_routes(folder, token, revision, cancelled, check_idle, *, retry=False, link_scope=None):
    """Move verified stale routes before the existing idle discovery."""
    global _dock_recovery, _cache
    if sys.platform!='win32' or revision is None or cancelled():
        return None
    from runtime_paths import DATA
    records = DATA/'network'
    with _setup_lock:
        now = time.monotonic()
        scope = (records,revision if link_scope is None else link_scope)
        if _dock_recovery is None or _dock_recovery['scope']!=scope:
            _dock_recovery = dict(scope=scope,next_read=0.,attempted=set(),notice=None)
        run = _dock_recovery
        if (now < run['next_read'] and not retry) or setup_status()['state']=='pending':
            return run['notice']
        if retry:
            run['attempted'].clear()
        run['next_read'] = now+8.
        check_idle()
        adapters = read_windows()
        plans = dock_recovery_plans(adapters,records)
        if not plans:
            run['next_read'] = now+30. if any(row.get('up') for row in adapters) else float('inf')
        from official_device_discovery import DiscoveryCleanupUnconfirmed
        for plan in plans:
            key = (plan['prefix'],plan.get('old_guid'),plan['new_guid'])
            if key in run['attempted'] or cancelled():
                continue
            check_idle()
            run['attempted'].add(key)
            try:
                result = _recover_with_cancellation(plan,folder,token,cancelled)
                with _lock:
                    _cache = None
                prefix=result.get('route')
                expected={plan['prefix']} if plan['prefix'] else {host+'/32' for host in plan['candidate_addresses']}
                if (result.get('ok') is not True or prefix not in expected
                        or result.get('adapter')!=plan['new_guid'] or result.get('interface_index')!=plan['new_index']):
                    raise ValueError('Route recovery did not return an exact confirmation')
                current_adapters=read_windows()
                latest = next((row for row in current_adapters if 'interface_ids' in row),{})
                current = list(_device_routes(latest,prefix))
                if not all(len(rows)==1 and _matches_dock_route(rows[0],plan['new_index']) for rows in current):
                    raise ValueError('Windows did not confirm the replacement route')
                if plan.get('host_address'):
                    address=plan['host_address']+'/24'
                    if (result.get('temporary_address')!=address
                            or not any(_adapter_guid(row.get('id'))==plan['new_guid'] and row.get('up') is True
                                       and address in row.get('probe_addresses',row.get('addresses',()))
                                       for row in current_adapters)):
                        raise ValueError('Windows did not confirm the temporary device address')
                run['notice'] = None
                if plan.get('host_address'):
                    # A temporary address changes full topology. Rescan from
                    # the authoritative directory with the new addresses.
                    break
            except DiscoveryCleanupUnconfirmed:
                raise
            except (OSError, ValueError, RuntimeError):
                run['notice'] = ('专用网口准备未完成，可重新查找重试 / '
                                 'The device network setup did not complete. Find devices again to retry.')
        return run['notice']


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


def snapshot(address='', side='left', refresh=False, *, target='hand'):
    global _cache, _cache_at, _cache_revision
    with _lock:
        now = time.monotonic()
        setup = setup_status()
        setup_changed = _setup is not None and _setup.get('observed_state') != setup['state']
        revision = None
        if sys.platform == 'win32':
            from windows_ethernet import TOPOLOGY
            try:
                revision = TOPOLOGY.read()
            except (OSError, ValueError, AttributeError):
                pass  # The timed PowerShell snapshot remains available.
        topology_changed = revision is not None and revision != _cache_revision
        if refresh or setup_changed or topology_changed or _cache is None or now - _cache_at > 8:
            if sys.platform == 'win32':
                _cache = read_windows()
            elif sys.platform == 'darwin':
                _cache = read_macos()
            else:
                return dict(code='external_controller', adapters=[], can_configure=False)
            _cache_at = now
            _cache_revision = revision
            if _setup is not None:
                _setup['observed_state'] = setup['state']
        result = analyse(_cache, address, side, target=target)
        result['can_configure'] = result['can_configure'] and sys.platform == 'win32'
        result['setup'] = (setup if setup['state']=='pending' or not _setup or _setup.get('target')==result['target']
                           else dict(state='idle'))
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


def configure(adapter_id, address='', side='left', *, target='hand'):
    """Only called by the native UI, after the host verifies it is disconnected."""
    with _setup_lock:
        return _configure(adapter_id, address, side, target)


def _configure(adapter_id, address, side, target):
    global _setup, _cache
    if sys.platform != 'win32':
        raise ValueError('Automatic adapter configuration is not yet verified on this platform')
    if setup_status()['state'] == 'pending':
        raise ValueError('Windows is still completing the previous setup')
    status = snapshot(address, side, refresh=True, target=target)
    adapter = next((x for x in status['candidates'] if x['id'] == adapter_id), None)
    if not status['can_configure'] or not adapter:
        raise ValueError('Adapter selection changed; refresh the network check')
    index = adapter.get('index')
    if type(index) is not int or not 1 <= index <= 2147483647:
        raise ValueError('Adapter interface index was not verified; refresh the network check')
    guid = str(uuid.UUID(adapter_id.strip('{}')))
    from runtime_paths import DATA, RESOURCE
    folder = DATA / 'network'; folder.mkdir(parents=True, exist_ok=True)
    # Packaged parent processes may virtualize LocalAppData. Validate the
    # parent and actual reparse points, as the updater does, not a raw path.
    if folder.parent.resolve() != DATA.resolve() or folder.is_symlink() or folder.is_junction():
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
    destination = status['target'].split(':')[0]
    option = (['-DeviceAddress', destination] if adapter['setup_kind'] == 'route'
              else ['-HostAddress', status['proposed_address'].split('/')[0], '-TargetAddress', destination])
    command = subprocess.list2cmdline(['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script),
                                      '-AdapterGuid', guid, '-ExpectedInterfaceIndex', str(index),
                                      *option, '-ResultFile', str(result_file)])
    powershell = str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
    code = shell.ShellExecuteW(None, 'runas', powershell, command, None, 0)
    if not code or int(code) <= 32:
        raise RuntimeError('Windows network setup was cancelled or unavailable')
    _setup = dict(result_file=result_file, started=time.monotonic(), target=status['target'])
    _cache = None
    return dict(state='pending', target=status['target'])
