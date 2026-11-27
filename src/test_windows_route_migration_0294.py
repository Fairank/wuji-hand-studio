"""Exercise the actual PowerShell migration with disposable simulated cmdlets."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


OLD_GUID = 'aaaaaaaa-1111-1111-1111-111111111111'
NEW_GUID = 'bbbbbbbb-2222-2222-2222-222222222222'
GLOVE_GUID = 'cccccccc-3333-3333-3333-333333333333'
HAND = '192.168.1.111/32'
GLOVE = '192.168.1.101/32'
TOKEN = '3' * 32
SCAN_START = '130945000000000000'

WRAPPER = r"""
[Console]::OutputEncoding=[Text.UTF8Encoding]::new()
$ErrorActionPreference='Stop'
$case='__CASE__'
$marker='__MARKER__'
$cancel=Join-Path ([IO.Path]::GetDirectoryName($marker)) 'network-cancel-__TOKEN__'
$global:calls=@()
$global:routes=@{
    ActiveStore=@()
    PersistentStore=@()
}
function Get-Process {
    param($Id,$ErrorAction)
    if($Id -ne 5678 -or $case -eq 'scan_process_exited'){throw 'Discovery process does not exist'}
    $creation=if($case -eq 'scan_pid_reused'){[long]__SCAN_START__+1}else{[long]__SCAN_START__}
    [pscustomobject]@{StartTime=[DateTime]::FromFileTimeUtc($creation)}
}
function New-MockRoute($Prefix,$Index,$Metric=5,$Protocol='NetMgmt'){
    [pscustomobject]@{DestinationPrefix=$Prefix;InterfaceIndex=$Index;NextHop='0.0.0.0';RouteMetric=$Metric;Protocol=$Protocol;InstanceID=('synthetic-'+$Index)}
}
foreach($store in 'ActiveStore','PersistentStore'){
    $global:routes[$store]+=(New-MockRoute '__GLOVE__' 22)
    if($case -notin 'persistent_only','moved_absent','absent_instance_changed' -or $store -eq 'PersistentStore'){
        $protocol=if($case -eq 'foreign_protocol'){'Dhcp'}else{'NetMgmt'}
        $global:routes[$store]+=(New-MockRoute '__HAND__' 17 5 $protocol)
    }
}
if($case -eq 'duplicate_old'){$global:routes['ActiveStore']+=(New-MockRoute '__HAND__' 22)}
function Get-NetAdapter {
    param([switch]$Physical,[switch]$IncludeHidden)
    [pscustomobject]@{InterfaceGuid='__NEW_GUID__';InterfaceType=6;Status='Up';ifIndex=31}
    [pscustomobject]@{InterfaceGuid='__GLOVE_GUID__';InterfaceType=6;Status='Up';ifIndex=22}
    if($IncludeHidden){
        if($case -eq 'recycled_index'){
            [pscustomobject]@{InterfaceGuid='eeeeeeee-5555-5555-5555-555555555555';InterfaceType=6;Status='Down';ifIndex=17}
        } elseif($case -notin 'missing_old_adapter','moved_absent','absent_instance_changed') {
            $state=if($case -eq 'old_up'){'Up'}else{'Not Present'}
            [pscustomobject]@{InterfaceGuid='__OLD_GUID__';InterfaceType=6;Status=$state;ifIndex=17}
        }
    }
}
function Get-NetIPAddress {
    param($AddressFamily,$InterfaceIndex,$PolicyStore,$ErrorAction)
    $state=if($case -eq 'tentative_new'){'Tentative'}else{'Preferred'}
    [pscustomobject]@{IPAddress='192.168.1.53';InterfaceIndex=31;AddressState=$state;PrefixLength=24}
    [pscustomobject]@{IPAddress='192.168.1.52';InterfaceIndex=22;AddressState='Preferred';PrefixLength=24}
}
function Get-NetNeighbor {
    param($IPAddress,$InterfaceIndex,$AddressFamily,$PolicyStore,$ErrorAction)
    if($IPAddress -ne '192.168.1.111' -or ($InterfaceIndex -and $InterfaceIndex -notin 22,31)){throw 'Unexpected neighbor request'}
    if(!$InterfaceIndex -or $InterfaceIndex -eq 31){
        $mac=if($case -eq 'wrong_mac'){'AA-BB-CC-DD-EE-02'}else{'AA-BB-CC-DD-EE-01'}
        $state=if($case -eq 'stale_neighbor'){'Stale'}else{'Reachable'}
        [pscustomobject]@{IPAddress='192.168.1.111';InterfaceIndex=31;LinkLayerAddress=$mac;State=$state}
    }
    if($case -eq 'ambiguous_neighbor' -and (!$InterfaceIndex -or $InterfaceIndex -eq 22)){
        [pscustomobject]@{IPAddress='192.168.1.111';InterfaceIndex=22;LinkLayerAddress='AA-BB-CC-DD-EE-03';State='Reachable'}
    }
}
function Get-NetRoute {
    param($AddressFamily,$InterfaceIndex,$DestinationPrefix,$PolicyStore,$ErrorAction)
    if($DestinationPrefix -eq '0.0.0.0/0'){
        if($case -eq 'new_gateway' -and $InterfaceIndex -eq 31){New-MockRoute '0.0.0.0/0' 31}
        return
    }
    $global:routes[$PolicyStore] | Where-Object {
        (!$DestinationPrefix -or $_.DestinationPrefix -eq $DestinationPrefix) -and (!$InterfaceIndex -or $_.InterfaceIndex -eq $InterfaceIndex)
    } | ForEach-Object {
        $_ | Add-Member -NotePropertyName Store -NotePropertyValue $PolicyStore -Force -PassThru
    }
}
function New-NetRoute {
    param($DestinationPrefix,$InterfaceIndex,$NextHop,$RouteMetric,$Protocol,$PolicyStore,$ErrorAction)
    if($DestinationPrefix -ne '__HAND__' -or $InterfaceIndex -ne 31 -or $NextHop -ne '0.0.0.0'){throw 'Unexpected route creation'}
    $global:calls+=@{operation='add';prefix=$DestinationPrefix;index=$InterfaceIndex;store=$PolicyStore}
    $global:routes['ActiveStore']+=(New-MockRoute $DestinationPrefix $InterfaceIndex $RouteMetric)
    if($case -eq 'partial_add'){throw 'Injected second store creation failure'}
    $global:routes['PersistentStore']+=(New-MockRoute $DestinationPrefix $InterfaceIndex $RouteMetric)
    if($case -eq 'marker_revoked'){Remove-Item -LiteralPath $marker}
    if($case -eq 'cancel_after_add'){[IO.File]::WriteAllText($cancel,'cancelled')}
}
function Remove-NetRoute {
    param($DestinationPrefix,$InterfaceIndex,$NextHop,$RouteMetric,$Protocol,$PolicyStore,$InputObject,$Confirm,$ErrorAction)
    if($InputObject){
        if($DestinationPrefix -or $InterfaceIndex -or $PolicyStore){throw 'The removed CIM row selects its own store'}
        $DestinationPrefix=$InputObject.DestinationPrefix
        $InterfaceIndex=$InputObject.InterfaceIndex
        $NextHop=$InputObject.NextHop
        $PolicyStore=$InputObject.Store
    }
    if($InterfaceIndex -eq 17 -and !$InputObject){throw 'The old route requires its exact CIM instance'}
    if($DestinationPrefix -ne '__HAND__' -or $InterfaceIndex -notin 17,31 -or $NextHop -ne '0.0.0.0'){throw 'Unexpected route removal'}
    $global:calls+=@{operation='remove';prefix=$DestinationPrefix;index=$InterfaceIndex;store=$PolicyStore}
    if($case -eq 'partial_remove' -and $InterfaceIndex -eq 17 -and $PolicyStore -eq 'PersistentStore'){throw 'Injected old persistent removal failure'}
    $global:routes[$PolicyStore]=@($global:routes[$PolicyStore] | Where-Object {
        $_.DestinationPrefix -ne $DestinationPrefix -or $_.InterfaceIndex -ne $InterfaceIndex
    })
}
function New-NetIPAddress { throw 'Unexpected IP write' }
function Set-NetIPInterface { throw 'Unexpected DHCP write' }
function Remove-NetIPAddress { throw 'Unexpected IP removal' }
& '__SCRIPT__' -AdapterGuid '__NEW_GUID__' -ExpectedInterfaceIndex 31 -DeviceAddress '192.168.1.111' -ResultFile '__RESULT__' -PreviousAdapterGuid '__OLD_GUID__' -PreviousInterfaceIndex 17 -NeighborMac 'aa:bb:cc:dd:ee:01' -ScanMarker $marker -ScanToken '__TOKEN__' -PreviousReceiptPath '__RECEIPT__'
@{result=(Get-Content -LiteralPath '__RESULT__' -Raw|ConvertFrom-Json);calls=@($global:calls);routes=$global:routes}|ConvertTo-Json -Depth 7 -Compress
"""


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'),
                     'Windows PowerShell with substituted network cmdlets required')
class WindowsRouteMigrationTests(unittest.TestCase):
    def helper(self, case):
        manifest = dict(kind='scan', token=TOKEN, pid=5678, start=SCAN_START)
        if case == 'stale_marker':
            manifest['token'] = '4' * 32
        with tempfile.TemporaryDirectory(prefix='dock-route-migration-') as folder:
            root = Path(folder)
            marker, result, runner = (root/'session.json', root/('setup-'+'a'*32+'.json'),
                                      root/'simulate.ps1')
            receipt = root/('setup-'+'b'*32+'.json')
            record = dict(ok=True, persistent=True, adapter=OLD_GUID, route=HAND)
            if case not in ('missing_old_adapter', 'persistent_only'):
                record.update(interface_index=17, route_metric=5,
                              route_instance='different' if case == 'absent_instance_changed' else 'synthetic-17')
            receipt.write_text(json.dumps(record), encoding='utf-8')
            marker.write_text(json.dumps(manifest), encoding='utf-8')
            if case == 'cancel_before_add':
                (root/('network-cancel-'+TOKEN)).touch()
            replacements = {
                'CASE': case, 'MARKER': str(marker), 'RESULT': str(result), 'TOKEN': TOKEN,
                'SCAN_START': SCAN_START,
                'HAND': HAND, 'GLOVE': GLOVE, 'OLD_GUID': OLD_GUID, 'NEW_GUID': NEW_GUID,
                'GLOVE_GUID': GLOVE_GUID, 'RECEIPT': str(receipt),
                'SCRIPT': str(Path(__file__).with_name('network_setup_windows.ps1')),
            }
            code = WRAPPER
            for key, value in replacements.items():
                code = code.replace(f'__{key}__', value.replace("'", "''"))
            runner.write_text(code, encoding='utf-8-sig')
            completed = subprocess.run(
                ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(runner)],
                capture_output=True, timeout=20,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr.decode('utf-8', 'replace'))
            return json.loads(completed.stdout.decode('utf-8-sig'))

    def assert_routes(self, outcome, active, persistent):
        for store, indexes in [('ActiveStore', active), ('PersistentStore', persistent)]:
            rows = outcome['routes'][store]
            self.assertEqual([row['InterfaceIndex'] for row in rows
                              if row['DestinationPrefix'] == GLOVE], [22])
            self.assertEqual(sorted(row['InterfaceIndex'] for row in rows
                                    if row['DestinationPrefix'] == HAND), indexes)

    def test_switches_only_hand_route_and_reads_back_both_stores(self):
        for case in ('normal', 'persistent_only', 'moved_absent'):
            with self.subTest(case=case):
                outcome = self.helper(case)
                self.assertTrue(outcome['result']['ok'], outcome['result'])
                self.assertEqual(outcome['result']['interface_index'], 31)
                self.assertEqual(outcome['result']['route_metric'], 5)
                self.assertEqual(outcome['result']['previous_adapter'], OLD_GUID)
                self.assertEqual(outcome['result']['route_instance'], 'synthetic-31')
                self.assertEqual([(call['operation'], call['index']) for call in outcome['calls']],
                                 [('add', 31)] + [('remove', 17)] * (2 if case == 'normal' else 1))
                self.assert_routes(outcome, [31], [31])

    def test_rejects_stale_ambiguous_or_changed_inputs_without_route_writes(self):
        for case in ('stale_marker', 'scan_process_exited', 'scan_pid_reused',
                     'recycled_index', 'missing_old_adapter', 'old_up', 'absent_instance_changed',
                     'foreign_protocol', 'duplicate_old', 'wrong_mac', 'stale_neighbor',
                     'ambiguous_neighbor', 'new_gateway', 'tentative_new'):
            with self.subTest(case=case):
                outcome = self.helper(case)
                self.assertFalse(outcome['result']['ok'])
                self.assertEqual(outcome['calls'], [])

    def test_partial_add_cleans_only_the_new_route(self):
        outcome = self.helper('partial_add')
        self.assertFalse(outcome['result']['ok'])
        self.assertEqual([(call['operation'], call['index']) for call in outcome['calls']],
                         [('add', 31), ('remove', 31)])
        self.assert_routes(outcome, [17], [17])

    def test_partial_old_removal_rolls_back_new_while_verified_old_persistent_route_exists(self):
        outcome = self.helper('partial_remove')
        self.assertFalse(outcome['result']['ok'])
        self.assertEqual([(call['operation'], call['index']) for call in outcome['calls']],
                         [('add', 31), ('remove', 17), ('remove', 17),
                          ('remove', 31), ('remove', 31)])
        self.assert_routes(outcome, [], [17])

    def test_revoked_scan_scope_does_not_remove_either_interface_after_add(self):
        outcome = self.helper('marker_revoked')
        self.assertFalse(outcome['result']['ok'])
        self.assertEqual([(call['operation'], call['index']) for call in outcome['calls']], [('add', 31)])
        self.assert_routes(outcome, [17, 31], [17, 31])

    def test_cancelled_scan_cannot_add_and_rolls_back_its_own_completed_add(self):
        before = self.helper('cancel_before_add')
        self.assertFalse(before['result']['ok'])
        self.assertEqual(before['calls'], [])
        self.assert_routes(before, [17], [17])

        after = self.helper('cancel_after_add')
        self.assertFalse(after['result']['ok'])
        self.assertEqual([(call['operation'], call['index']) for call in after['calls']],
                         [('add', 31), ('remove', 31), ('remove', 31)])
        self.assert_routes(after, [17], [17])


if __name__ == '__main__':
    unittest.main()
