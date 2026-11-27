"""Address choice and elevated-helper checks with synthetic NICs only."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import device_network as network

GUID = '11111111-2222-4333-8444-555555555555'
POWERSHELL = shutil.which('powershell.exe')


def nic(key='candidate', addresses=('169.254.9.8/16',), up=True, ethernet=True, index=92):
    return dict(id=key, name=key, addresses=list(addresses), up=up,
                ethernet=ethernet, gateway=False, index=index)


class AddressChoiceTests(unittest.TestCase):
    def test_disconnected_nic_still_reserves_its_address(self):
        adapters = [nic(), nic('old-dock', ['192.168.1.50/24'], up=False)]
        original = copy.deepcopy(adapters)
        result = network.analyse(adapters)
        self.assertEqual(result['proposed_address'], '192.168.1.51/24')
        self.assertTrue(result['can_configure'])
        self.assertEqual(adapters, original)

    def test_third_dock_uses_next_free_address(self):
        result = network.analyse([
            nic(), nic('old-dock', ['192.168.1.50/24'], up=False),
            nic('wifi', ['192.168.1.51/24'], ethernet=False)])
        self.assertEqual(result['proposed_address'], '192.168.1.52/24')
        self.assertTrue(result['can_configure'])
        self.assertEqual(result['code'], 'subnet_mismatch')

    def test_target_address_cannot_be_reused_by_the_host(self):
        for target, occupied, expected in [
                ('192.168.1.50', [], '192.168.1.51/24'),
                ('192.168.1.51', [], '192.168.1.50/24'),
                ('192.168.1.50', ['192.168.1.51/24'], '192.168.1.52/24')]:
            with self.subTest(target=target, occupied=occupied):
                result = network.analyse([nic(), nic('other', occupied, up=False)],
                                         address=target)
                self.assertEqual(result['proposed_address'], expected)
                self.assertEqual(result['can_configure'], expected is not None)

    def test_compatible_static_adapter_does_not_offer_reconfiguration(self):
        adapters = [nic(addresses=['192.168.1.51/24']),
                    nic('old-dock', ['192.168.1.50/24'], up=False)]
        result = network.analyse(adapters)
        self.assertEqual(result['code'], 'subnet_ready')
        self.assertFalse(result['can_configure'])
        self.assertFalse(result['hardware_connected'])

    def test_new_dock_is_configurable_while_another_device_adapter_remains_up(self):
        new = nic(GUID)
        existing = nic('existing', ['192.168.1.50/24'], index=14)
        result = network.analyse([new, existing])
        self.assertEqual(result['code'], 'subnet_ready')
        self.assertEqual(result['adapter'], existing)
        self.assertTrue(result['can_configure'])
        self.assertEqual([item['id'] for item in result['candidates']], [GUID])
        self.assertEqual(result['proposed_address'], '192.168.1.51/24')

    def test_subnet_boundaries_do_not_offer_setup(self):
        for target in ('192.168.1.0', '192.168.1.255'):
            with self.subTest(target=target):
                self.assertFalse(network.analyse([nic()], address=target)['can_configure'])

    def test_custom_network_is_never_automatically_renumbered(self):
        result = network.analyse([nic()], address='192.168.6.110')
        self.assertFalse(result['can_configure'])
        self.assertIsNone(result['proposed_address'])

    def test_global_occupancy_read_failure_is_not_an_empty_available_pool(self):
        # The actual reader must fail, never silently turn a failed all-IP read
        # into permission to assign a possibly occupied address.
        with patch.object(network, '_run', side_effect=RuntimeError('read failed')):
            with self.assertRaises(RuntimeError):
                network.read_windows()

    def test_explicit_configure_rechecks_occupancy_and_launches_only_available_address(self):
        calls = []

        class ShellExecute:
            def __call__(self, *args):
                calls.append(args)
                return 33

        with tempfile.TemporaryDirectory(prefix='network-configure-') as directory:
            runtime = SimpleNamespace(DATA=Path(directory),
                                      RESOURCE=Path(network.__file__).parent)
            shell = SimpleNamespace(ShellExecuteW=ShellExecute())
            rows = [nic(GUID, index=93), nic('old-dock', ['192.168.1.50/24'], up=False),
                    nic('other-device-dock', ['192.168.1.51/24'], index=14)]
            with patch.dict(sys.modules, runtime_paths=runtime), \
                    patch.dict(sys.modules, windows_ethernet=SimpleNamespace(TOPOLOGY=SimpleNamespace(read=lambda: 11))), \
                    patch.dict(os.environ, SystemRoot='C:/Windows'), \
                    patch.object(network.sys, 'platform', 'win32'), \
                    patch.object(network, '_setup', None), \
                    patch.object(network, '_cache', None), \
                    patch.object(network, 'read_windows', return_value=rows), \
                    patch('ctypes.WinDLL', return_value=shell, create=True):
                result = network.configure(GUID)
                self.assertEqual(result['state'], 'pending')
                self.assertEqual(len(calls), 1)
                self.assertIn('-HostAddress 192.168.1.52', calls[0][3])
                self.assertIn('-ExpectedInterfaceIndex 93', calls[0][3])
                self.assertIn('-TargetAddress 192.168.1.110', calls[0][3])
                with self.assertRaisesRegex(ValueError, 'previous setup'):
                    network.configure(GUID)
                self.assertEqual(len(calls), 1)

    def test_explicit_configure_refuses_occupied_pool_before_elevation_then_allows_retry(self):
        occupied = [nic(GUID), nic('old-dock', ['192.168.1.50/24'], up=False),
                    nic('virtual', [f'192.168.1.{octet}/24' for octet in range(51, 100)], ethernet=False)]
        with patch.object(network.sys, 'platform', 'win32'), \
                patch.dict(sys.modules, windows_ethernet=SimpleNamespace(TOPOLOGY=SimpleNamespace(read=lambda: 11))), \
                patch.object(network, '_setup', None), \
                patch.object(network, '_cache', None), \
                patch.object(network, 'read_windows', return_value=occupied), \
                patch('ctypes.WinDLL', create=True) as elevated:
            with self.assertRaisesRegex(ValueError, 'selection changed'):
                network.configure(GUID)
            elevated.assert_not_called()
            # A later explicit read may offer the released address, without
            # the rejection mutating either interface or launching a helper.
            available = network.analyse(occupied[:2])
            self.assertTrue(available['can_configure'])
            self.assertEqual(available['proposed_address'], '192.168.1.51/24')

    def test_cache_follows_topology_revision_before_time_limit(self):
        old = [nic('old', ['192.168.1.50/24'], index=8)]
        new = [nic('new', index=10)]
        topology = SimpleNamespace(read=lambda: 12)
        with patch.dict(sys.modules, windows_ethernet=SimpleNamespace(TOPOLOGY=topology)), \
                patch.object(network.sys, 'platform', 'win32'), \
                patch.object(network, '_setup', None), \
                patch.object(network, '_cache', None), \
                patch.object(network, '_cache_revision', None, create=True), \
                patch.object(network, 'read_windows', side_effect=[old, new]) as read:
            self.assertEqual(network.snapshot()['adapter']['id'], 'old')
            self.assertEqual(network.snapshot()['adapter']['id'], 'old')
            self.assertEqual(read.call_count, 1)
            topology.read = lambda: 13
            self.assertEqual(network.snapshot()['adapter']['id'], 'new')
            self.assertEqual(read.call_count, 2)

    def test_topology_probe_failure_uses_snapshot_fallback(self):
        def broken_topology():
            raise OSError('IP Helper unavailable')

        with patch.dict(sys.modules, windows_ethernet=SimpleNamespace(
                TOPOLOGY=SimpleNamespace(read=broken_topology))), \
                patch.object(network.sys, 'platform', 'win32'), \
                patch.object(network, '_setup', None), \
                patch.object(network, '_cache', None), \
                patch.object(network, '_cache_revision', None, create=True), \
                patch.object(network, 'read_windows', return_value=[nic(GUID)]) as read:
            self.assertTrue(network.snapshot()['can_configure'])
            self.assertTrue(network.snapshot()['can_configure'])
            self.assertEqual(read.call_count, 1)


@unittest.skipUnless(os.name == 'nt' and POWERSHELL,
                     'Windows helper is exercised only with mocked PowerShell NIC commands')
class WindowsScriptTests(unittest.TestCase):
    def powershell(self, code, directory):
        wrapper = Path(directory) / 'fixture.ps1'
        wrapper.write_text(code, encoding='utf-8-sig')
        run = subprocess.run([POWERSHELL, '-NoProfile', '-ExecutionPolicy',
                              'Bypass', '-File', str(wrapper)],
                             capture_output=True, timeout=15, check=False)
        self.assertEqual(run.returncode, 0,
                         run.stdout.decode('utf-8', 'replace') +
                         run.stderr.decode('utf-8', 'replace'))
        return run.stdout.decode('utf-8-sig').strip()

    def helper(self, conflicts=(), fail_global=False, changed_selected=False,
               host='192.168.1.51', target='192.168.1.110', move_after=0, expected_index=92):
        # Every network cmdlet is a local function. This runs the real helper
        # parser/control flow; no network-adapter or administrator operation runs.
        with tempfile.TemporaryDirectory(prefix='network-conflict-') as directory:
            result_file = Path(directory) / ('setup-' + 'a' * 32 + '.json')
            script = Path(network.__file__).with_name('network_setup_windows.ps1')
            values = json.dumps(list(conflicts))
            current = '10.2.3.4' if changed_selected else '169.254.9.8'
            code = r"""
[Console]::OutputEncoding=[Text.UTF8Encoding]::new()
$global:changes=@()
$global:conflicts=ConvertFrom-Json '__CONFLICTS__'
$global:adapterReads=0
function Get-NetAdapter {param([switch]$Physical)
 $global:adapterReads++
 $index=if(__MOVE_AFTER__ -gt 0 -and $global:adapterReads -gt __MOVE_AFTER__){14}else{92}
 [pscustomobject]@{InterfaceGuid='__GUID__';InterfaceType=6;Status='Up';ifIndex=$index}}
function Get-NetRoute {param($InterfaceIndex,$AddressFamily,$DestinationPrefix,$PolicyStore,$ErrorAction) @()}
function Get-NetIPInterface {param($InterfaceIndex,$AddressFamily)
 [pscustomobject]@{Dhcp='Enabled'}}
function Get-NetIPAddress {param($InterfaceIndex,$AddressFamily,$IPAddress,$PolicyStore,$ErrorAction)
 if($IPAddress){return [pscustomobject]@{InterfaceIndex=92;IPAddress=$IPAddress;PrefixLength=24;AddressState='Preferred'}}
 $own=[pscustomobject]@{InterfaceIndex=92;IPAddress='__CURRENT__';PrefixLength=16}
 if($null -ne $InterfaceIndex){return $own}
 if('__FAIL_GLOBAL__' -eq 'true'){throw 'Synthetic global IP read failed'}
 @($own)+@($global:conflicts|ForEach-Object {
   [pscustomobject]@{InterfaceIndex=14;IPAddress=$_;PrefixLength=24}})
}
function New-NetIPAddress {param($InterfaceIndex,$IPAddress,$PrefixLength)
 $global:changes+=@{operation='add';index=$InterfaceIndex;address=$IPAddress;prefix=$PrefixLength}}
function Remove-NetIPAddress {param($InterfaceIndex,$IPAddress,$Confirm,$ErrorAction)
 $global:changes+=@{operation='remove';address=$IPAddress}}
function Set-NetIPInterface {param($InterfaceIndex,$AddressFamily,$Dhcp,$ErrorAction)
 $global:changes+=@{operation='dhcp';value=$Dhcp}}
function Start-Sleep {param($Milliseconds)}
& '__SCRIPT__' -AdapterGuid '__GUID__' -ExpectedInterfaceIndex __EXPECTED__ -HostAddress '__HOST__' -TargetAddress '__TARGET__' -ResultFile '__RESULT__'
$body=Get-Content -LiteralPath '__RESULT__' -Raw|ConvertFrom-Json
@{result=$body;changes=@($global:changes)}|ConvertTo-Json -Depth 8 -Compress
"""
            for key, value in {
                    '__GUID__': GUID, '__CONFLICTS__': values,
                    '__CURRENT__': current,
                    '__HOST__': host, '__TARGET__': target,
                    '__MOVE_AFTER__': str(move_after), '__EXPECTED__': str(expected_index),
                    '__FAIL_GLOBAL__': str(fail_global).lower(),
                    '__SCRIPT__': str(script).replace("'", "''"),
                    '__RESULT__': str(result_file).replace("'", "''")}.items():
                code = code.replace(key, value)
            return json.loads(self.powershell(code, directory))

    def test_elevated_helper_rechecks_new_conflict_before_any_change(self):
        result = self.helper(conflicts=['192.168.1.51'])
        self.assertFalse(result['result']['ok'])
        self.assertEqual(result['changes'], [])

    def test_elevated_helper_global_read_error_cannot_write(self):
        result = self.helper(fail_global=True)
        self.assertFalse(result['result']['ok'])
        self.assertEqual(result['changes'], [])

    def test_elevated_helper_fresh_unoccupied_address_is_the_only_change(self):
        result = self.helper(conflicts=['192.168.1.50'])
        self.assertTrue(result['result']['ok'])
        self.assertEqual(result['changes'],
                         [dict(operation='add', index=92,
                               address='192.168.1.51', prefix=24)])
        self.assertFalse(result['result']['gateway_changed'])
        self.assertFalse(result['result']['motors_started'])

    def test_elevated_helper_changed_selected_configuration_is_untouched(self):
        result = self.helper(changed_selected=True)
        self.assertFalse(result['result']['ok'])
        self.assertEqual(result['changes'], [])

    def test_elevated_helper_refuses_index_reassignment_before_write(self):
        for args in (dict(expected_index=14), dict(move_after=1)):
            with self.subTest(args=args):
                result = self.helper(**args)
                self.assertFalse(result['result']['ok'])
                self.assertEqual(result['changes'], [])
                self.assertIn('interface index', result['result']['error'])

    def test_elevated_helper_does_not_rollback_onto_reassigned_index(self):
        result = self.helper(move_after=2)
        self.assertFalse(result['result']['ok'])
        self.assertEqual([change['operation'] for change in result['changes']], ['add'])
        self.assertIn('rollback unconfirmed', result['result']['error'])

    def test_elevated_helper_keeps_host_and_device_addresses_distinct(self):
        result = self.helper(target='192.168.1.51')
        self.assertFalse(result['result']['ok'])
        self.assertEqual(result['changes'], [])

    def test_elevated_helper_accepts_third_dock_address(self):
        result = self.helper(conflicts=['192.168.1.50', '192.168.1.51'],
                             host='192.168.1.52')
        self.assertTrue(result['result']['ok'])
        self.assertEqual(result['changes'],
                         [dict(operation='add', index=92, address='192.168.1.52', prefix=24)])

    def test_windows_reader_includes_nonphysical_ip_occupancy_without_selecting_it(self):
        # Run the real read_windows PowerShell, supplying physical Ethernet and
        # a different virtual/Wi-Fi interface already owning the first address.
        code = r"""
[Console]::OutputEncoding=[Text.UTF8Encoding]::new()
function Get-NetAdapter {param([switch]$Physical)
 [pscustomobject]@{InterfaceGuid=[Guid]'__GUID__';Name='Synthetic USB Ethernet';InterfaceType=6;Status='Up';ifIndex=92}}
function Get-NetRoute {param($InterfaceIndex,$AddressFamily,$DestinationPrefix,$PolicyStore,$ErrorAction) @()}
function Get-NetIPAddress {param($InterfaceIndex,$AddressFamily,$PolicyStore,$ErrorAction)
 $rows=@(
  [pscustomobject]@{InterfaceIndex=92;IPAddress='169.254.9.8';PrefixLength=16},
  [pscustomobject]@{InterfaceIndex=777;IPAddress='192.168.1.50';PrefixLength=24})
 if($null -ne $InterfaceIndex){return @($rows|Where-Object {$_.InterfaceIndex -eq $InterfaceIndex})}
 return $rows
}
""".replace('__GUID__', GUID)
        with tempfile.TemporaryDirectory(prefix='network-reader-') as directory:
            def run(command):
                self.assertEqual(command[:3],
                                 ['powershell.exe', '-NoProfile', '-Command'])
                return self.powershell(code + '\n' + command[3], directory)
            with patch.object(network, '_run', side_effect=run):
                rows = network.read_windows()
        result = network.analyse(rows)
        self.assertEqual(result['proposed_address'], '192.168.1.51/24')
        self.assertEqual(len(result['adapters']), 1)
        self.assertEqual(result['adapter']['id'], GUID)


if __name__ == '__main__':
    unittest.main()
