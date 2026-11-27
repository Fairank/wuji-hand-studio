"""Exercise staged Windows adapter setup with simulated cmdlets and native API."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


HELPER = Path(__file__).with_name('network_auto_address.ps1')
POWERSHELL = shutil.which('powershell.exe')
WRAPPER = r"""
[Console]::OutputEncoding=[Text.UTF8Encoding]::new()
$ErrorActionPreference='Stop'
$case='__CASE__'
$AdapterGuid=[Guid]'aaaaaaaa-1111-1111-1111-111111111111'
$global:calls=@()
$global:temporary=$false
$global:extraAddress=$false
$index=31
. '__HELPER__'
function Confirm-Scan {
    if($case -eq 'cancel_before' -or ($case -eq 'cancel_during' -and $global:temporary)){throw 'Discovery was cancelled'}
}
function Confirm-DedicatedAdapter {}
function Get-NetAdapter {
    param([switch]$Physical,[switch]$IncludeHidden,$ErrorAction)
    $identity=if($case -eq 'adapter_changed' -and $global:temporary){'cccccccc-1111-1111-1111-111111111111'}else{$AdapterGuid}
    [pscustomobject]@{InterfaceGuid=$identity;InterfaceType=6;Status='Up';ifIndex=31}
    [pscustomobject]@{InterfaceGuid='bbbbbbbb-2222-2222-2222-222222222222';InterfaceType=6;Status='Up';ifIndex=22}
    if($global:extraAddress){
        [pscustomobject]@{InterfaceGuid='dddddddd-3333-3333-3333-333333333333';InterfaceType=6;Status='Up';ifIndex=57}
    }
}
function New-MockAddress($Index,$IPAddress,$Prefix=24,$State='Preferred'){
    [pscustomobject]@{InterfaceIndex=$Index;IPAddress=$IPAddress;PrefixLength=$Prefix;AddressState=$State}
}
function Get-NetIPAddress {
    param($AddressFamily,$PolicyStore,$InterfaceIndex,$ErrorAction)
    if($AddressFamily -eq 'IPv6'){
        New-MockAddress 31 'fe80::1234' 64
        if($case -eq 'configured_ipv6'){New-MockAddress 31 '2001:db8::1234' 64}
        return
    }
    if($InterfaceIndex -eq 31 -or !$InterfaceIndex){
        New-MockAddress 31 '169.254.50.20' 16
        if($case -eq 'configured_ipv4'){New-MockAddress 31 '10.0.0.13'}
        if($case -eq 'dhcp_arrived' -and $global:temporary){New-MockAddress 31 '10.0.0.13'}
        if(($global:temporary -or $case -like 'prepared_*') -and $PolicyStore -eq 'ActiveStore'){
            $state=if($case -eq 'duplicate_address'){'Duplicate'}else{'Preferred'}
            New-MockAddress 31 '192.168.1.53' 24 $state
        }
    }
    if($InterfaceIndex -eq 22 -or !$InterfaceIndex){
        if($case -like 'prepared_*'){New-MockAddress 22 '192.168.2.52' 16}
        else{New-MockAddress 22 '192.168.1.52'}
        if($case -eq 'host_taken' -and $PolicyStore -eq 'PersistentStore'){New-MockAddress 22 '192.168.1.53'}
        if($case -eq 'local_device' -and $PolicyStore -eq 'ActiveStore'){New-MockAddress 22 '192.168.1.111'}
    }
    if($global:extraAddress -and ($InterfaceIndex -eq 57 -or !$InterfaceIndex)){New-MockAddress 57 '192.168.1.54'}
}
function Get-NetRoute {
    param($InterfaceIndex,$AddressFamily,$DestinationPrefix,$PolicyStore,$ErrorAction)
    if($case -eq 'ipv6_default' -and $InterfaceIndex -eq 31 -and $AddressFamily -eq 'IPv6'){
        [pscustomobject]@{InterfaceIndex=31;DestinationPrefix='::/0'}
    }
}
function Add-WujiTemporaryAddress {
    param($InterfaceIndex,$Address)
    if($global:temporary -or $InterfaceIndex -ne 31 -or $Address -ne '192.168.1.53'){throw 'Unexpected native address creation'}
    $global:calls+=@{name='add';index=$InterfaceIndex;address=$Address}
    $global:temporary=$true
    [uint32]6711
}
function Remove-WujiTemporaryAddress {
    param($Context)
    if(!$global:temporary -or $Context -ne 6711){throw 'Unexpected native address removal'}
    $global:calls+=@{name='delete';context=$Context}
    if($case -eq 'undo_failure'){throw 'Native removal failed'}
    $global:temporary=$false
}
function Resolve-WujiNeighbor {
    param($InterfaceIndex,$Source,$Target)
    $otherSource=if($case -like 'prepared_*'){'192.168.2.52'}else{'192.168.1.52'}
    if((!$global:temporary -and $case -notlike 'prepared_*') -or $Target -notin '192.168.1.101','192.168.1.111' -or ($InterfaceIndex -eq 31 -and $Source -ne '192.168.1.53') -or ($InterfaceIndex -eq 22 -and $Source -ne $otherSource)){throw 'Unexpected native neighbor request'}
    $global:calls+=@{name='probe';index=$InterfaceIndex;source=$Source;target=$Target}
    if($case -eq 'new_competitor' -and $InterfaceIndex -eq 31 -and $Target -eq '192.168.1.111'){$global:extraAddress=$true}
    if($InterfaceIndex -eq 31 -and $Target -eq '192.168.1.111' -and $case -notin 'absent','undo_failure'){'02-A1-B2-C3-D4-E5'}
    if($InterfaceIndex -eq 31 -and $Target -eq '192.168.1.101' -and $case -eq 'two_devices_new'){'02-A1-B2-C3-D4-E6'}
    if($InterfaceIndex -eq 22 -and ($Target -eq '192.168.1.101' -or $case -in 'duplicate_device','prepared_duplicate')){'02-A1-B2-C3-D4-E7'}
}
function Get-NetNeighbor {throw 'The neighbor cache is not a substitute for a fresh probe'}
function New-NetIPAddress {throw 'Unexpected persistent IP write'}
function Set-NetIPInterface {throw 'Unexpected DHCP write'}
function Remove-NetIPAddress {throw 'Unexpected IP removal'}
function New-NetRoute {throw 'Unexpected route write'}
function Remove-NetRoute {throw 'Unexpected route removal'}
$result=$null
$failure=$null
try {
    $parameters=@{InterfaceIndex=31;HostAddress='192.168.1.53'}
    if($case -in 'candidate','two_devices_new'){$parameters.CandidateAddresses=@('192.168.1.101','192.168.1.111')}
    elseif($case -eq 'foreign_target'){$parameters.DeviceAddress='192.168.1.105'}
    else{$parameters.DeviceAddress='192.168.1.111'}
    if($case -like 'prepared_*'){$result=@(Get-WujiFreshNeighbors -Target $parameters.DeviceAddress)}
    else{$result=Initialize-WujiAutomaticAddress @parameters}
} catch {
    $failure=$_.Exception.Message
}
@{result=$result;failure=$failure;calls=@($global:calls);temporary=$global:temporary}|ConvertTo-Json -Depth 6 -Compress
"""


@unittest.skipUnless(POWERSHELL, 'PowerShell is unavailable')
class WindowsAutomaticAddressTests(unittest.TestCase):
    def run_case(self, case):
        with tempfile.TemporaryDirectory(prefix='wuji-address-probe-') as folder:
            runner = Path(folder)/'test.ps1'
            runner.write_text(WRAPPER.replace('__HELPER__', str(HELPER).replace("'", "''")).replace('__CASE__', case),
                              encoding='utf-8-sig')
            result = subprocess.run(
                [POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(runner)],
                capture_output=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8','replace'))
            return json.loads(result.stdout.decode('utf-8-sig'))

    def test_apipa_dock_keeps_only_exact_staged_temporary_address(self):
        run = self.run_case('exact')
        self.assertIsNone(run['failure'], run['failure'])
        self.assertEqual(run['result'], dict(context=6711, mac='02-A1-B2-C3-D4-E5', device='192.168.1.111'))
        self.assertTrue(run['temporary'])
        self.assertEqual([c['name'] for c in run['calls']], ['add','probe','probe'])
        self.assertEqual({c['index'] for c in run['calls'] if c['name']=='probe'},{22,31})

    def test_multiple_factory_candidates_select_only_the_device_on_this_dock(self):
        run = self.run_case('candidate')
        self.assertIsNone(run['failure'], run['failure'])
        self.assertEqual(run['result']['device'], '192.168.1.111')
        self.assertEqual({(c['index'],c['target']) for c in run['calls'] if c['name']=='probe'},
                         {(index,target) for index in (22,31) for target in ('192.168.1.101','192.168.1.111')})

    def test_dhcp_or_existing_network_blocks_address_staging(self):
        for case in ('ipv6_default','configured_ipv6','configured_ipv4','host_taken','local_device',
                     'cancel_before','foreign_target'):
            with self.subTest(case=case):
                run = self.run_case(case)
                self.assertTrue(run['failure'])
                self.assertEqual(run['calls'], [])
                self.assertFalse(run['temporary'])

    def test_unsuccessful_or_changed_probe_cleans_up_exact_context(self):
        for case in ('absent','duplicate_device','duplicate_address','cancel_during','two_devices_new',
                     'dhcp_arrived','adapter_changed','new_competitor'):
            with self.subTest(case=case):
                run = self.run_case(case)
                self.assertTrue(run['failure'])
                self.assertFalse(run['temporary'])
                self.assertEqual(run['calls'][0]['name'], 'add')
                self.assertEqual(run['calls'][-1],dict(name='delete',context=6711))

    def test_cleanup_failure_is_reported_to_calling_owner(self):
        run = self.run_case('undo_failure')
        self.assertIn('removal failed',run['failure'])
        self.assertEqual(run['calls'][-1],dict(name='delete',context=6711))
        self.assertIsNone(run['result'])

    def test_prepared_dock_fresh_probe_checks_an_alternate_wide_subnet(self):
        run = self.run_case('prepared_unique')
        self.assertIsNone(run['failure'], run['failure'])
        self.assertEqual(run['result'], [dict(index=31, mac='02-A1-B2-C3-D4-E5')])
        self.assertEqual({(call['index'],call['source']) for call in run['calls']},
                         {(31,'192.168.1.53'),(22,'192.168.2.52')})
        self.assertTrue(all(call['name']=='probe' for call in run['calls']))

    def test_prepared_dock_fresh_probe_returns_both_competing_addresses(self):
        run = self.run_case('prepared_duplicate')
        self.assertIsNone(run['failure'], run['failure'])
        self.assertEqual({(response['index'],response['mac']) for response in run['result']},
                         {(31,'02-A1-B2-C3-D4-E5'),(22,'02-A1-B2-C3-D4-E7')})
        self.assertFalse(run['temporary'])
