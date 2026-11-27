"""Exercise current Windows route evidence and cold setup with fake NetTCPIP."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_windows_route_migration_0294 import (
    GLOVE, GLOVE_GUID, HAND, NEW_GUID, OLD_GUID, SCAN_START, TOKEN, WRAPPER,
)


SETUP = r"""
$cold=__COLD__
$legacy='__OLD_MODE__'
$restore='__RESTORE__'
$global:hasTemporaryAddress=$false
foreach($store in 'ActiveStore','PersistentStore'){
    $global:routes[$store]=@($global:routes[$store] | Where-Object {
        $_.DestinationPrefix -eq '__GLOVE__' -or
        ($legacy -ne 'none' -and ($legacy -eq 'both' -or $legacy -eq $store))
    })
    foreach($row in $global:routes[$store]){
        $arg=if($case -eq 'published_old' -and $row.DestinationPrefix -eq '__HAND__'){'Yes'}else{'No'}
        $row | Add-Member -NotePropertyName Publish -NotePropertyValue $arg -Force
        if($row.DestinationPrefix -eq '__HAND__'){$row.RouteMetric=19}
    }
}
if($restore){
    $global:routes['PersistentStore']+=(New-MockRoute '__HAND__' 31)
    if($restore -eq 'both'){$global:routes['ActiveStore']+=(New-MockRoute '__HAND__' 31)}
}
$netRoute=${function:Get-NetRoute}
function Get-NetRoute {
    param($AddressFamily,$InterfaceIndex,$DestinationPrefix,$PolicyStore,$ErrorAction)
    if(!$DestinationPrefix -and !$InterfaceIndex -and $case -eq 'old_gateway'){
        New-MockRoute '0.0.0.0/0' 17
    }
    & $netRoute @PSBoundParameters
}
function Get-NetIPInterface {
    param($InterfaceIndex,[switch]$IncludeAllCompartments,$ErrorAction)
    if($case -eq 'hidden_ip_index' -and $IncludeAllCompartments -and (!$InterfaceIndex -or $InterfaceIndex -eq 17)){
        [pscustomobject]@{InterfaceIndex=17;ConnectionState='Connected'}
    }
}
function Get-NetIPAddress {
    param($AddressFamily,$InterfaceIndex,$PolicyStore,$ErrorAction)
    if(!$cold -or $global:hasTemporaryAddress){
        [pscustomobject]@{IPAddress='192.168.1.53';InterfaceIndex=31;AddressState='Preferred';PrefixLength=24}
    }
    [pscustomobject]@{IPAddress='192.168.1.52';InterfaceIndex=22;AddressState='Preferred';PrefixLength=24}
}
"""

ADDRESS_HELPER = r"""
function Initialize-WujiAutomaticAddress {
    param($InterfaceIndex,$HostAddress,$DeviceAddress,$CandidateAddresses)
    if($InterfaceIndex -ne 31 -or $HostAddress -ne '192.168.1.53' -or
       (($DeviceAddress -ne '192.168.1.111') -and
        (($CandidateAddresses -join ',') -ne '192.168.1.100,192.168.1.111'))){
        throw 'Unexpected temporary address plan'
    }
    $global:calls+=@{operation='add_address';index=$InterfaceIndex}
    $global:hasTemporaryAddress=$true
    if($case -eq 'cancel_after_address'){[IO.File]::WriteAllText($cancel,'cancelled')}
    @{context=[uint32]41;mac='AA-BB-CC-DD-EE-01';device='192.168.1.111'}
}
function Remove-WujiAutomaticAddress {
    param($Context)
    if($Context -ne 41){throw 'Wrong address context'}
    $global:calls+=@{operation='remove_address';index=31}
    $global:hasTemporaryAddress=$false
}
function Get-WujiFreshNeighbors {
    param($Target)
    if($Target -ne '192.168.1.111'){throw 'Unexpected refreshed address'}
    @{index=31;mac='AA-BB-CC-DD-EE-01'}
    if($case -eq 'overlapping_ethernet_reply'){@{index=77;mac='AA-BB-CC-DD-EE-09'}}
}
"""


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'),
                     'Windows PowerShell with fake NetTCPIP cmdlets required')
class AutomaticNetworkTests(unittest.TestCase):
    def helper(self, case='normal', old='none', *, absent=False, cold=False,
               candidates=False, wrong_instance=False, restore=''):
        with tempfile.TemporaryDirectory(prefix='automatic-route-') as folder:
            root = Path(folder)
            marker = root/'session.json'
            result = root/('setup-'+'a'*32+'.json')
            observed = root/('observed-route-'+'b'*32+'.json')
            receipt = root/('setup-'+'c'*32+'.json')
            marker.write_text(json.dumps(dict(kind='scan',token=TOKEN,pid=5678,start=SCAN_START)),
                              encoding='utf-8')
            if old != 'none':
                evidence = dict(prefix=HAND,old_index=17,old_guid=None if absent else OLD_GUID,
                                route_metric=19,
                                active_instance='synthetic-17' if old in ('both','ActiveStore') else None,
                                persistent_instance='synthetic-17' if old in ('both','PersistentStore') else None)
                if wrong_instance:
                    evidence['persistent_instance'] = 'previous-instance'
                observed.write_text(json.dumps(evidence), encoding='utf-8')
            if restore:
                receipt.write_text(json.dumps(dict(ok=True,persistent=True,adapter=NEW_GUID,
                                                   route=HAND,interface_index=31,route_metric=5,
                                                   route_instance='stale-31' if wrong_instance else 'synthetic-31',
                                                   temporary_address='192.168.1.59/24')),encoding='utf-8')

            installed = root/'network_setup_windows.ps1'
            installed.write_bytes(Path(__file__).with_name(installed.name).read_bytes())
            (root/'network_auto_address.ps1').write_text(ADDRESS_HELPER, encoding='utf-8')
            base, command = WRAPPER.split("& '__SCRIPT__'", 1)
            output = command.split('@{result=', 1)[1]
            base = base.replace("$global:routes['PersistentStore']+=(New-MockRoute $DestinationPrefix $InterfaceIndex $RouteMetric)",
                                "if($PolicyStore -ne 'ActiveStore'){$global:routes['PersistentStore']+=(New-MockRoute $DestinationPrefix $InterfaceIndex $RouteMetric)}")
            if absent:
                base = base.replace("$case -notin 'missing_old_adapter','moved_absent','absent_instance_changed'",
                                    '$false')
            args = "-CandidateAddresses '192.168.1.100,192.168.1.111'" if candidates else "-DeviceAddress '192.168.1.111'"
            if cold:
                args += " -HostAddress '192.168.1.53'"
            else:
                args += " -NeighborMac 'aa:bb:cc:dd:ee:01'"
            if old != 'none':
                args += " -PreviousRouteState '__OBSERVED__'"
            if restore:
                args += " -RestoreAddressReceipt '__RESTORE_RECEIPT__'"
            invoke = f"& '__SCRIPT__' -AdapterGuid '__NEW_GUID__' -ExpectedInterfaceIndex 31 -ResultFile '__RESULT__' -ScanMarker $marker -ScanToken '__TOKEN__' {args}"
            code = base + SETUP + invoke + '\n@{result=' + output
            changes = dict(CASE=case,MARKER=str(marker),RESULT=str(result),TOKEN=TOKEN,
                           SCAN_START=SCAN_START,HAND=HAND,GLOVE=GLOVE,OLD_GUID=OLD_GUID,
                           NEW_GUID=NEW_GUID,GLOVE_GUID=GLOVE_GUID,SCRIPT=str(installed),
                           OBSERVED=str(observed),OLD_MODE=old,COLD='$true' if cold else '$false',
                           RESTORE=restore,RESTORE_RECEIPT=str(receipt))
            for name, value in changes.items():
                code = code.replace(f'__{name}__',value.replace("'","''"))
            launch = root/'simulate.ps1'
            launch.write_text(code, encoding='utf-8-sig')
            completed = subprocess.run(
                ['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(launch)],
                capture_output=True,timeout=20,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr.decode('utf-8','replace'))
            return json.loads(completed.stdout.decode('utf-8-sig'))

    def test_recovers_present_and_orphaned_routes_with_no_receipt(self):
        for old, absent in [('both',False),('ActiveStore',True),('PersistentStore',True)]:
            with self.subTest(old=old,absent=absent):
                result = self.helper(old=old,absent=absent)
                self.assertTrue(result['result']['ok'],result['result'])
                self.assertEqual(result['result']['route_instance'],'synthetic-31')
                self.assertEqual([(c['operation'],c['index']) for c in result['calls']],
                                 [('add',31)]+[('remove',17)]*(2 if old=='both' else 1))
                for routes in result['routes'].values():
                    self.assertEqual({(r['DestinationPrefix'],r['InterfaceIndex']) for r in routes},
                                     {(GLOVE,22),(HAND,31)})

    def test_preserves_ambiguous_changed_published_and_live_routes(self):
        for case, absent, wrong in [('old_gateway',False,False),('old_up',False,False),
                                    ('published_old',False,False),('normal',True,True),
                                    ('hidden_ip_index',True,False),('recycled_index',True,False)]:
            with self.subTest(case=case):
                result = self.helper(case,old='PersistentStore',absent=absent,wrong_instance=wrong)
                self.assertFalse(result['result']['ok'])
                self.assertEqual(result['calls'],[])

    def test_fresh_overlapping_ethernet_reply_blocks_prepared_untracked_move(self):
        outcome = self.helper('overlapping_ethernet_reply',old='PersistentStore')
        self.assertFalse(outcome['result']['ok'])
        self.assertEqual(outcome['calls'],[])

    def test_new_route_with_prepared_or_cold_adapter_needs_no_old_route(self):
        for cold in (False,True):
            with self.subTest(cold=cold):
                result = self.helper(cold=cold,candidates=cold)
                self.assertTrue(result['result']['ok'],result['result'])
                self.assertEqual(result['result'].get('temporary_address'),'192.168.1.53/24' if cold else None)
                self.assertEqual([c['operation'] for c in result['calls']],
                                 (['add_address'] if cold else [])+['add'])
                for routes in result['routes'].values():
                    self.assertEqual({(r['DestinationPrefix'],r['InterfaceIndex']) for r in routes},
                                     {(GLOVE,22),(HAND,31)})

    def test_cold_adapter_address_is_cleaned_on_cancel_and_failed_route_write(self):
        for case in ('cancel_after_address','partial_add'):
            with self.subTest(case=case):
                result = self.helper(case,cold=True,candidates=True)
                self.assertFalse(result['result']['ok'])
                self.assertEqual(result['calls'][0]['operation'],'add_address')
                self.assertEqual(result['calls'][-1]['operation'],'remove_address')
                for routes in result['routes'].values():
                    self.assertEqual({(r['DestinationPrefix'],r['InterfaceIndex']) for r in routes},
                                     {(GLOVE,22)})

    def test_failed_saved_route_removal_allows_a_new_observation_to_retry(self):
        failed = self.helper('partial_remove',old='both')
        self.assertFalse(failed['result']['ok'])
        self.assertEqual({(r['DestinationPrefix'],r['InterfaceIndex']) for r in failed['routes']['ActiveStore']},
                         {(GLOVE,22)})
        self.assertEqual({(r['DestinationPrefix'],r['InterfaceIndex']) for r in failed['routes']['PersistentStore']},
                         {(GLOVE,22),(HAND,17)})
        retried = self.helper(old='PersistentStore')
        self.assertTrue(retried['result']['ok'],retried['result'])

    def test_returning_dock_restores_address_and_only_fills_missing_active_route(self):
        for restore in ('both','persistent_only'):
            with self.subTest(restore=restore):
                result = self.helper(cold=True,restore=restore)
                self.assertTrue(result['result']['ok'],result['result'])
                self.assertEqual(result['result']['temporary_address'],'192.168.1.53/24')
                self.assertEqual([c['operation'] for c in result['calls']],
                                 ['add_address'] if restore=='both' else ['add_address','add'])
                if restore=='persistent_only':
                    self.assertEqual(result['calls'][1]['store'],'ActiveStore')
                for routes in result['routes'].values():
                    self.assertEqual({(r['DestinationPrefix'],r['InterfaceIndex']) for r in routes},
                                     {(GLOVE,22),(HAND,31)})

    def test_returning_dock_checks_receipt_and_cleans_only_its_new_active_route(self):
        stale = self.helper(cold=True,restore='both',wrong_instance=True)
        self.assertFalse(stale['result']['ok'])
        self.assertEqual(stale['calls'],[])
        failed = self.helper('partial_add',cold=True,restore='persistent_only')
        self.assertFalse(failed['result']['ok'])
        self.assertEqual([c['operation'] for c in failed['calls']],
                         ['add_address','add','remove','remove_address'])
        self.assertEqual(failed['calls'][2]['store'],'ActiveStore')
        self.assertEqual({(r['DestinationPrefix'],r['InterfaceIndex']) for r in failed['routes']['PersistentStore']},
                         {(GLOVE,22),(HAND,31)})


if __name__ == '__main__':
    unittest.main()
