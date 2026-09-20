import unittest
from unittest.mock import MagicMock,patch
from types import SimpleNamespace
import zenoh_route as r

class RouteTests(unittest.TestCase):
    def test_route_uses_zenoh_endpoint_and_loopback_listener(self):
        cfg=r.configuration(r.endpoint('','left'))
        self.assertEqual(cfg['connect']['endpoints'],['udp/192.168.1.110:7447'])
        self.assertEqual(cfg['listen']['endpoints'],['tcp/127.0.0.1:0'])
        self.assertTrue(cfg['connect']['exit_on_failure']);self.assertEqual(cfg['connect']['timeout_ms'],1500)
    def test_side_changes_default_endpoint(self):
        self.assertEqual(r.endpoint('','right'),'192.168.1.111:7447')
    def test_invalid_targets_do_not_reach_library(self):
        for target in ['127.0.0.1:7447','224.1.1.1:7447','host;command','192.168.1.110:0','192.168.1.110:70000']:
            with self.subTest(target=target),self.assertRaises(ValueError):r.DeviceRoute(target,'left')
    def test_only_exact_identity_is_selected(self):
        route=r.DeviceRoute('','left');m=MagicMock()
        m.scan.return_value=[SimpleNamespace(sn='WH2Jexample',device_type='DeviceType.WujiHand2',address='192.168.1.110:7447'),SimpleNamespace(sn='WH2Kexample',device_type='DeviceType.WujiHand2',address='192.168.1.111:7447')]
        self.assertEqual(route.find_serial(m),'WH2Jexample')
    def test_wrong_device_or_endpoint_never_falls_back_to_generic_udp(self):
        route=r.DeviceRoute('','left');m=MagicMock()
        m.scan.return_value=[SimpleNamespace(sn='WH2Jexample',device_type='DeviceType.WujiHand2',address='192.168.3.110:7447')]
        with patch.object(r.time,'monotonic',side_effect=[0,5]),self.assertRaises(RuntimeError):route.find_serial(m)
        m.connect.assert_not_called()
    def test_connect_uses_verified_serial_and_never_enable(self):
        m=MagicMock();route=MagicMock();route.find_serial.return_value='WH2Jexample'
        with patch.object(r,'DeviceRoute',return_value=route):hand,saved=r.connect_managed_hand(m,'','left')
        m.connect.assert_called_once_with(sn='WH2Jexample',device_name='wuji_hand_2')
        hand.enable.assert_not_called();self.assertIs(saved,route)
    def test_sdk_zenoh_serial_uri(self):
        m=MagicMock();m.scan.return_value=[SimpleNamespace(sn='WH2Jexample',device_type='DeviceType.WujiHand2',address='zenoh://WH2Jexample')]
        self.assertEqual(r.DeviceRoute('','left').find_serial(m),'WH2Jexample')
    def test_official_unknown_type_with_valid_hand2_serial(self):
        sn='WH2JA00000000001';m=MagicMock()
        m.scan.return_value=[SimpleNamespace(sn=sn,device_type='DeviceType.Unknown',address='zenoh://'+sn)]
        self.assertEqual(r.DeviceRoute('','left').find_serial(m),sn)
    def test_unknown_type_wrong_side_or_nonhand_serial_rejected(self):
        for sn in ('WH2KA00000000001','WG2JA00000000001','WH2Jbad'):
            with self.subTest(sn=sn):
                m=MagicMock();m.scan.return_value=[SimpleNamespace(sn=sn,device_type='DeviceType.Unknown',address='zenoh://'+sn)]
                with patch.object(r.time,'monotonic',side_effect=[0,5]),self.assertRaises(RuntimeError):r.DeviceRoute('','left').find_serial(m)
    def test_mismatched_uri_is_rejected(self):
        m=MagicMock();m.scan.return_value=[SimpleNamespace(sn='WH2Jexample',device_type='DeviceType.WujiHand2',address='zenoh://WH2Jother')]
        with patch.object(r.time,'monotonic',side_effect=[0,5]),self.assertRaises(RuntimeError):r.DeviceRoute('','left').find_serial(m)
    def test_multiple_same_side_devices_are_rejected(self):
        m=MagicMock();m.scan.return_value=[SimpleNamespace(sn=sn,device_type='DeviceType.WujiHand2',address='zenoh://'+sn) for sn in ['WH2Jone','WH2Jtwo']]
        with self.assertRaisesRegex(RuntimeError,'Multiple matching'):r.DeviceRoute('','left').find_serial(m)
    def test_failed_identity_or_connect_closes_route(self):
        m=MagicMock();m.connect.side_effect=RuntimeError('failed');route=MagicMock()
        with patch.object(r,'DeviceRoute',return_value=route),self.assertRaises(RuntimeError):r.connect_managed_hand(m,'','left')
        route.close.assert_called_once()
    def test_only_our_managed_environment_uses_route(self):
        with patch.dict(r.os.environ,{'WSL_DISTRO_NAME':'UnrelatedUserDistro'}):self.assertFalse(r.managed_wsl())

if __name__=='__main__':unittest.main()
