import unittest
from unittest.mock import patch
import device_network as n

def adapter(key='one',addresses=(),up=True,gateway=False,ethernet=True):
    return dict(id=key,name=key,addresses=list(addresses),up=up,gateway=gateway,ethernet=ethernet)

class NetworkTests(unittest.TestCase):
    def test_link_up_with_apipa_is_not_device_ready(self):
        s=n.analyse([adapter(addresses=['169.254.12.4/16'])])
        self.assertEqual(s['code'],'subnet_mismatch');self.assertTrue(s['can_configure'])
        self.assertFalse(s['hardware_connected'])
    def test_factory_addresses_follow_selected_hand(self):
        self.assertEqual(n.target_address(side='left'),'192.168.1.110:7447')
        self.assertEqual(n.target_address(side='right'),'192.168.1.111:7447')
        self.assertEqual(n.target_address('192.168.2.20:9000'),'192.168.2.20:9000')
    def test_wifi_and_default_gateway_are_not_reconfigured(self):
        a=[adapter('wifi',['10.0.0.2/24'],gateway=True,ethernet=False),adapter('lan',['169.254.1.2/16'])]
        self.assertEqual(n.analyse(a)['adapter']['id'],'lan')
        self.assertFalse(n.analyse([adapter(gateway=True)])['can_configure'])
    def test_existing_other_lan_configuration_is_not_overwritten(self):
        self.assertEqual(n.analyse([adapter(addresses=['10.1.2.3/24'])])['code'],'network_in_use')
    def test_multiple_adapters_require_choice(self):
        s=n.analyse([adapter('a'),adapter('b')]);self.assertEqual(s['code'],'multiple_adapters')
        self.assertIsNone(s['adapter']);self.assertEqual(len(s['candidates']),2)
    def test_correct_subnet_is_not_proof_of_live_feedback(self):
        s=n.analyse([adapter(addresses=['192.168.1.50/24'])])
        self.assertEqual(s['code'],'subnet_ready');self.assertFalse(s['can_configure']);self.assertFalse(s['hardware_connected'])
    def test_unplugged_and_absent_are_distinct(self):
        self.assertEqual(n.analyse([])['code'],'no_ethernet')
        self.assertEqual(n.analyse([adapter(up=False)])['code'],'cable_unplugged')
    def test_custom_subnet_requires_separate_configuration(self):
        self.assertFalse(n.analyse([adapter()],address='192.168.6.110')['can_configure'])
    def test_host_address_does_not_duplicate_device(self):
        self.assertEqual(n.analyse([adapter()],address='192.168.1.50')['proposed_address'],'192.168.1.51/24')
    def test_two_matching_interfaces_are_ambiguous(self):
        s=n.analyse([adapter('a',['192.168.1.50/24']),adapter('b',['192.168.1.60/24'])])
        self.assertEqual(s['code'],'multiple_adapters');self.assertFalse(s['can_configure'])
    def test_invalid_target_rejected(self):
        for value in ['127.0.0.1','0.0.0.0','224.1.1.1','a;command','192.168.1.110:0','192.168.1.110:65536','192.168.1.110:١']:
            with self.subTest(value=value),self.assertRaises(ValueError):n.target_address(value)
    def test_macos_parser_excludes_wifi_and_remains_read_only(self):
        ports='Hardware Port: Wi-Fi\nDevice: en0\n\nHardware Port: USB Ethernet\nDevice: en5\n'
        details='\tinet 192.168.1.60 netmask 0xffffff00 broadcast 192.168.1.255\n\tstatus: active\n'
        with patch.object(n,'_run',side_effect=[ports,details]):rows=n.read_macos()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['id'],'en5')
        self.assertEqual(n.analyse(rows)['code'],'subnet_ready')
        with patch.object(n.sys,'platform','darwin'),self.assertRaises(ValueError):n.configure('en5')

if __name__=='__main__':unittest.main()
