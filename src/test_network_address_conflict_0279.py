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


def nic(key='candidate', addresses=('169.254.9.8/16',), up=True, ethernet=True):
    return dict(id=key, name=key, addresses=list(addresses), up=up,
                ethernet=ethernet, gateway=False)


class AddressChoiceTests(unittest.TestCase):
    def test_disconnected_nic_still_reserves_its_address(self):
        adapters = [nic(), nic('old-dock', ['192.168.1.50/24'], up=False)]
        original = copy.deepcopy(adapters)
        result = network.analyse(adapters)
        self.assertEqual(result['proposed_address'], '192.168.1.51/24')
        self.assertTrue(result['can_configure'])
        self.assertEqual(adapters, original)

    def test_both_whitelisted_addresses_used_disables_setup(self):
        result = network.analyse([
            nic(), nic('old-dock', ['192.168.1.50/24'], up=False),
            nic('wifi', ['192.168.1.51/24'], ethernet=False)])
        self.assertIsNone(result['proposed_address'])
        self.assertFalse(result['can_configure'])
        self.assertEqual(result['code'], 'subnet_mismatch')

    def test_target_address_cannot_be_reused_by_the_host(self):
        for target, occupied, expected in [
                ('192.168.1.50', [], '192.168.1.51/24'),
                ('192.168.1.51', [], '192.168.1.50/24'),
                ('192.168.1.50', ['192.168.1.51/24'], None)]:
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
            rows = [nic(GUID), nic('old-dock', ['192.168.1.50/24'], up=False)]
            with patch.dict(sys.modules, runtime_paths=runtime), \
                    patch.dict(os.environ, SystemRoot='C:/Windows'), \
                    patch.object(network.sys, 'platform', 'win32'), \
                    patch.object(network, '_setup', None), \
                    patch.object(network, '_cache', None), \
                    patch.object(network, 'read_windows', return_value=rows), \
                    patch('ctypes.WinDLL', return_value=shell, create=True):
                result = network.configure(GUID)
                self.assertEqual(result['state'], 'pending')
                self.assertEqual(len(calls), 1)
                self.assertIn('-HostAddress 192.168.1.51', calls[0][3])
                with self.assertRaisesRegex(ValueError, 'previous setup'):
                    network.configure(GUID)
                self.assertEqual(len(calls), 1)

    def test_explicit_configure_refuses_occupied_pool_before_elevation_then_allows_retry(self):
        occupied = [nic(GUID), nic('old-dock', ['192.168.1.50/24'], up=False),
                    nic('virtual', ['192.168.1.51/24'], ethernet=False)]
        with patch.object(network.sys, 'platform', 'win32'), \
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

    def helper(self, conflicts=(), fail_global=False, changed_selected=False):
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
function Get-NetAdapter {param([switch]$Physical)
 [pscustomobject]@{InterfaceGuid='__GUID__';InterfaceType=6;Status='Up';ifIndex=92}}
function Get-NetRoute {param($InterfaceIndex,$AddressFamily,$DestinationPrefix,$ErrorAction) @()}
function Get-NetIPInterface {param($InterfaceIndex,$AddressFamily)
 [pscustomobject]@{Dhcp='Enabled'}}
function Get-NetIPAddress {param($InterfaceIndex,$AddressFamily,$IPAddress,$ErrorAction)
 if($IPAddress){return [pscustomobject]@{InterfaceIndex=92;IPAddress=$IPAddress;AddressState='Preferred'}}
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
& '__SCRIPT__' -AdapterGuid '__GUID__' -HostAddress '192.168.1.51' -ResultFile '__RESULT__'
$body=Get-Content -LiteralPath '__RESULT__' -Raw|ConvertFrom-Json
@{result=$body;changes=@($global:changes)}|ConvertTo-Json -Depth 8 -Compress
"""
            for key, value in {
                    '__GUID__': GUID, '__CONFLICTS__': values,
                    '__CURRENT__': current,
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

    def test_windows_reader_includes_nonphysical_ip_occupancy_without_selecting_it(self):
        # Run the real read_windows PowerShell, supplying physical Ethernet and
        # a different virtual/Wi-Fi interface already owning the first address.
        code = r"""
[Console]::OutputEncoding=[Text.UTF8Encoding]::new()
function Get-NetAdapter {param([switch]$Physical)
 [pscustomobject]@{InterfaceGuid=[Guid]'__GUID__';Name='Synthetic USB Ethernet';InterfaceType=6;Status='Up';ifIndex=92}}
function Get-NetRoute {param($InterfaceIndex,$AddressFamily,$DestinationPrefix,$ErrorAction) @()}
function Get-NetIPAddress {param($InterfaceIndex,$AddressFamily,$ErrorAction)
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
