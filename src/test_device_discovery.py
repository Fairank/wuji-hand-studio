import tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import device_discovery as d

LEFT='WH2JA00000000001';RIGHT='WH2KA00000000002'
def device(sn,kind='DeviceType.Unknown',address=None):
    return SimpleNamespace(sn=sn,device_type=kind,address=address or 'zenoh://'+sn)
def manager(sn=RIGHT):
    m=MagicMock();m.scan.return_value=[device(sn)]
    h=m.connect.return_value;h.serial_number=sn
    h.handedness.return_value.get.return_value='right' if sn==RIGHT else 'left'
    return m

class DiscoveryTests(unittest.TestCase):
    def test_new_right_hand_does_not_need_left_profile_change(self):
        m=manager();h,r,p=d.connect_discovered(m,use_routes=False)
        self.assertEqual(p['id'],'hand2_right');self.assertIs(h,m.connect.return_value)
        h.enable.assert_not_called();h.joint_command.assert_not_called();r.close()
    def test_repeated_fresh_scans_can_change_serial_and_side(self):
        for sn,side in [(RIGHT,'right'),(LEFT,'left'),(RIGHT,'right')]:
            m=manager(sn);h,r,p=d.connect_discovered(m,use_routes=False)
            self.assertEqual(p['side'],side);self.assertEqual(h.serial_number,sn);r.close()
    def test_two_devices_require_selection_without_connecting(self):
        m=manager();m.scan.return_value=[device(LEFT),device(RIGHT)]
        with self.assertRaises(d.SelectionRequired) as c:d.connect_discovered(m,use_routes=False)
        self.assertEqual(len(c.exception.devices),2);m.connect.assert_not_called()
    def test_explicit_serial_selects_only_current_scan_member(self):
        m=manager();m.scan.return_value=[device(LEFT),device(RIGHT)]
        h,r,p=d.connect_discovered(m,serial=RIGHT,use_routes=False)
        m.connect.assert_called_once_with(sn=RIGHT,device_name='wuji_hand_2');r.close()
        m=manager(LEFT)
        with self.assertRaisesRegex(RuntimeError,'no longer'):d.connect_discovered(m,serial=RIGHT,use_routes=False)
        m.connect.assert_not_called()
    def test_unrelated_unknown_device_and_mismatched_uri_rejected(self):
        self.assertEqual(d.candidates([device('WG2JA00000000001'),device(LEFT,address='zenoh://'+RIGHT)]),[])
    def test_real_side_and_serial_must_agree(self):
        for key in ('side','serial'):
            m=manager()
            if key=='side':m.connect.return_value.handedness.return_value.get.return_value='left'
            else:m.connect.return_value.serial_number=LEFT
            with self.assertRaises(RuntimeError):d.connect_discovered(m,use_routes=False)
            m.connect.return_value.disconnect.assert_called_once()
    def test_no_device_cannot_connect_old_serial(self):
        m=manager();m.scan.return_value=[]
        with patch.object(d.time,'monotonic',side_effect=[0,5]),self.assertRaisesRegex(RuntimeError,'No hand'):
            d.connect_discovered(m,use_routes=False)
        m.connect.assert_not_called()
    def test_auto_routes_try_both_factory_addresses_and_close(self):
        m=manager()
        with patch.object(d,'DeviceRoute') as cls:
            a,b=MagicMock(),MagicMock();a.open.side_effect=RuntimeError('offline');cls.side_effect=[a,b]
            h,r,p=d.connect_discovered(m,use_routes=True)
            self.assertEqual([c.args[0] for c in cls.call_args_list],['192.168.1.110:7447','192.168.1.111:7447'])
            a.close.assert_called_once();r.close();b.close.assert_called_once()
    def test_explicit_failed_address_does_not_fall_back(self):
        m=manager()
        with patch.object(d,'DeviceRoute') as cls:
            cls.return_value.open.side_effect=RuntimeError('offline')
            with self.assertRaisesRegex(RuntimeError,'specified'):d.connect_discovered(m,'192.168.1.112',use_routes=True)
            self.assertEqual(cls.call_count,1);m.scan.assert_not_called();m.connect.assert_not_called()
    def test_first_generation_uses_its_own_adapter_identity(self):
        m=manager();m.scan.return_value=[device('first-test','DeviceType.WujiHand','usb://test')]
        h=m.connect.return_value;h.serial_number='first-test';h.handedness_name.return_value='Left'
        h,r,p=d.connect_discovered(m,use_routes=False)
        self.assertEqual(p['id'],'hand1_left');h.handedness.assert_not_called();r.close()
    def test_server_identity_clears_old_mapping_and_updates_profile(self):
        from console_server import Controller
        with tempfile.TemporaryDirectory() as tmp,patch('device_profiles.save_profile',side_effect=d.profile) as save:
            c=Controller(reports=Path(tmp)/'reports');c.state['mapping']=[dict(index=0)];c.state['mapping_device_id']=LEFT
            c.event(dict(type='identity',profile='hand2_right',device_id=RIGHT),c.generation)
            self.assertEqual(c.state['device_profile']['side'],'right');self.assertEqual(c.state['device_id'],RIGHT)
            self.assertEqual(c.state['mapping'],[]);self.assertIsNone(c.state['mapping_device_id'])
            self.assertFalse(c.state['motion_enabled']);save.assert_called_once_with('hand2_right')
    def test_stale_identity_event_cannot_switch_new_session(self):
        from console_server import Controller
        with tempfile.TemporaryDirectory() as tmp,patch('device_profiles.save_profile') as save:
            c=Controller(reports=Path(tmp)/'reports');c.generation=3
            c.event(dict(type='identity',profile='hand2_right',device_id=RIGHT),2)
            save.assert_not_called()

if __name__=='__main__':unittest.main()
