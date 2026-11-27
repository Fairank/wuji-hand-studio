"""The two device Ethernet links can share a subnet without sharing a route."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import device_network as network


HAND_NIC = 'aaaaaaaa-1111-1111-1111-111111111111'
GLOVE_NIC = 'bbbbbbbb-2222-2222-2222-222222222222'


def interfaces(*routes, upstream=False):
    return [
        dict(id=HAND_NIC, name='Hand adapter', index=9, ethernet=True, up=True,
             addresses=['192.168.1.51/24'], gateway=upstream),
        dict(id=GLOVE_NIC, name='Glove adapter', index=3, ethernet=True, up=True,
             addresses=['192.168.1.50/24'], gateway=False),
        dict(ethernet=False, addresses=[], routes=list(routes)),
    ]


def route(host, index, hop='0.0.0.0'):
    return dict(prefix=host+'/32', index=index, next_hop=hop)


class DeviceHostRouteTests(unittest.TestCase):
    def test_each_endpoint_uses_its_own_already_configured_interface(self):
        rows = interfaces(route('192.168.1.111', 9), route('192.168.1.101', 3))
        hand = network.analyse(rows, side='right')
        glove = network.analyse(rows, target='glove_right')
        self.assertEqual((hand['code'], hand['adapter']['id']), ('subnet_ready', HAND_NIC))
        self.assertEqual((glove['code'], glove['adapter']['id']), ('subnet_ready', GLOVE_NIC))
        self.assertFalse(hand['hardware_connected'])
        self.assertFalse(glove['hardware_connected'])

    def test_one_missing_target_route_offers_only_dedicated_interfaces(self):
        rows = interfaces(route('192.168.1.111', 9), upstream=True)
        glove = network.analyse(rows, target='glove_right')
        self.assertEqual(glove['code'], 'route_required')
        self.assertTrue(glove['can_configure'])
        self.assertEqual([item['id'] for item in glove['candidates']], [GLOVE_NIC])

    def test_existing_conflicting_route_cannot_be_overwritten(self):
        for routes in ((route('192.168.1.111', 99),),
                       (route('192.168.1.111', 3, '192.168.1.1'),),
                       (route('192.168.1.111', 3), route('192.168.1.111', 9))):
            with self.subTest(routes=routes):
                result = network.analyse(interfaces(*routes), side='right')
                self.assertEqual(result['code'], 'route_conflict')
                self.assertFalse(result['can_configure'])

    def test_route_conflict_in_persistent_store_is_not_offered_as_setup(self):
        rows = interfaces()
        rows[-1]['persistent_routes'] = [route('192.168.1.111', 99)]
        result = network.analyse(rows, side='right')
        self.assertEqual(result['code'], 'route_conflict')
        self.assertFalse(result['can_configure'])

    def test_existing_route_and_new_dock_use_distinct_setup_modes(self):
        rows = interfaces()
        rows.insert(1, dict(id='new-dock', name='New dock', index=16, ethernet=True,
                            up=True, addresses=['169.254.12.5/16'], gateway=False))
        result = network.analyse(rows, side='right')
        self.assertEqual(result['code'], 'route_required')
        self.assertEqual({row['id']: row['setup_kind'] for row in result['candidates']},
                         {HAND_NIC: 'route', GLOVE_NIC: 'route', 'new-dock': 'address'})
        self.assertEqual(result['proposed_address'], '192.168.1.52/24')

    def test_setup_uses_selected_endpoint_and_does_not_readdress_adapter(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            resource=Path(network.__file__).parent
            call=Mock(return_value=42)
            with patch.dict('sys.modules', runtime_paths=SimpleNamespace(DATA=root, RESOURCE=resource)), \
                    patch.dict('sys.modules', windows_ethernet=SimpleNamespace(TOPOLOGY=SimpleNamespace(read=lambda: 11))), \
                    patch.dict(os.environ, SystemRoot='C:/Windows'), \
                    patch.object(network.sys, 'platform', 'win32'), \
                    patch.object(network, '_setup', None), patch.object(network, '_cache', None), \
                    patch.object(network, 'read_windows', return_value=interfaces()), \
                    patch('ctypes.WinDLL', return_value=SimpleNamespace(ShellExecuteW=call), create=True):
                result=network.configure(GLOVE_NIC, target='glove_right')
                self.assertEqual(result['state'], 'pending')
                arguments=call.call_args.args[3]
                self.assertIn('-DeviceAddress 192.168.1.101', arguments)
                self.assertIn('-AdapterGuid '+GLOVE_NIC, arguments)
                self.assertIn('-ExpectedInterfaceIndex 3', arguments)
                self.assertNotIn('-HostAddress', arguments)
                with self.assertRaisesRegex(ValueError, 'previous setup'):
                    network.configure(HAND_NIC, side='right')
                self.assertEqual(call.call_count, 1)

    def test_unknown_network_target_is_rejected(self):
        with self.assertRaises(ValueError):
            network.analyse(interfaces(), target='arbitrary')


@unittest.skipUnless(os.name=='nt' and shutil.which('powershell.exe'),
                     'Windows helper runs with substituted network commands')
class WindowsHostRouteTests(unittest.TestCase):
    def helper(self, case):
        with tempfile.TemporaryDirectory(prefix='host-route-') as folder:
            directory=Path(folder)
            result=directory/('setup-'+'a'*32+'.json')
            script=Path(network.__file__).with_name('network_setup_windows.ps1')
            wrapper=directory/'validate.ps1'
            source=r"""
[Console]::OutputEncoding=[Text.UTF8Encoding]::new()
$case='__CASE__'
$global:calls=@()
$global:routeStores=@{}
function Get-NetAdapter {
    param([switch]$Physical)
    [pscustomobject]@{InterfaceGuid='__GUID__';InterfaceType=6;Status='Up';ifIndex=3}
}
function Get-NetIPAddress {
    param($AddressFamily,$InterfaceIndex,$PolicyStore,$ErrorAction)
    [pscustomobject]@{IPAddress='192.168.1.50';InterfaceIndex=3;AddressState='Preferred';PrefixLength=24}
    [pscustomobject]@{IPAddress='192.168.1.51';InterfaceIndex=9;AddressState='Preferred';PrefixLength=24}
}
function Get-NetRoute {
    param($AddressFamily,$InterfaceIndex,$DestinationPrefix,$PolicyStore,$ErrorAction)
    if($DestinationPrefix -eq '0.0.0.0/0') { return }
    if($case -eq 'competing' -and $PolicyStore -eq 'PersistentStore') {
        [pscustomobject]@{DestinationPrefix='192.168.1.101/32';InterfaceIndex=9;NextHop='0.0.0.0';RouteMetric=5}
    }
    if($global:routeStores[$PolicyStore]) {
        [pscustomobject]@{DestinationPrefix='192.168.1.101/32';InterfaceIndex=3;NextHop='0.0.0.0';RouteMetric=5}
    }
}
function New-NetRoute {
    param($DestinationPrefix,$InterfaceIndex,$NextHop,$RouteMetric,$PolicyStore,$ErrorAction)
    $global:calls+=@{operation='add';prefix=$DestinationPrefix;index=$InterfaceIndex;hop=$NextHop;store=$PolicyStore}
    if($case -ne 'not_applied'){ $global:routeStores['ActiveStore']=$true }
    if($case -eq 'partial_write'){ throw 'Synthetic second-store write failure' }
    $global:routeStores['PersistentStore']=$true
}
function Remove-NetRoute {
    param($DestinationPrefix,$InterfaceIndex,$NextHop,$RouteMetric,$PolicyStore,$Confirm,$ErrorAction)
    $global:calls+=@{operation='remove';prefix=$DestinationPrefix;index=$InterfaceIndex;hop=$NextHop;store=$PolicyStore}
    $global:routeStores[$PolicyStore]=$false
}
function New-NetIPAddress { throw 'Unexpected IP write' }
function Set-NetIPInterface { throw 'Unexpected DHCP write' }
function Remove-NetIPAddress { throw 'Unexpected IP removal' }
& '__SCRIPT__' -AdapterGuid '__GUID__' -ExpectedInterfaceIndex 3 -DeviceAddress '192.168.1.101' -ResultFile '__RESULT__'
@{result=(Get-Content -LiteralPath '__RESULT__' -Raw|ConvertFrom-Json);calls=@($global:calls)}|ConvertTo-Json -Depth 6 -Compress
"""
            for key,value in {'__CASE__':case,'__GUID__':GLOVE_NIC,
                              '__SCRIPT__':str(script), '__RESULT__':str(result)}.items():
                source=source.replace(key,value.replace("'", "''"))
            wrapper.write_text(source, encoding='utf-8-sig')
            run=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(wrapper)],
                               capture_output=True, timeout=15)
            self.assertEqual(run.returncode,0,run.stderr.decode('utf-8','replace'))
            return json.loads(run.stdout.decode('utf-8-sig'))

    def test_exact_host_route_is_applied_and_verified_in_both_stores(self):
        run=self.helper('normal')
        self.assertTrue(run['result']['ok'])
        self.assertTrue(run['result']['persistent'])
        self.assertEqual(run['calls'],[dict(operation='add',prefix='192.168.1.101/32',index=3,hop='0.0.0.0',store=None)])

    def test_late_conflict_in_persistent_store_is_not_touched(self):
        run=self.helper('competing')
        self.assertFalse(run['result']['ok'])
        self.assertEqual(run['calls'],[])

    def test_failed_readback_removes_only_the_exact_created_route(self):
        run=self.helper('not_applied')
        self.assertFalse(run['result']['ok'])
        self.assertEqual([item['operation'] for item in run['calls']], ['add','remove'])
        self.assertTrue(all((item['prefix'],item['index'],item['hop'])==('192.168.1.101/32',3,'0.0.0.0')
                            for item in run['calls']))
        self.assertEqual(run['calls'][-1]['store'],'PersistentStore')

    def test_partial_cmdlet_failure_does_not_leave_an_active_host_route(self):
        run=self.helper('partial_write')
        self.assertFalse(run['result']['ok'])
        self.assertEqual([item['operation'] for item in run['calls']], ['add','remove'])
        self.assertEqual(run['calls'][-1]['store'],'ActiveStore')
