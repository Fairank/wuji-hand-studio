import queue
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from glove_agent import paired_hand_worker


class PairingTests(unittest.TestCase):
    def test_mismatched_model_releases_device_without_constructing_driver(self):
        calls=[]
        hand=SimpleNamespace(serial_number='hand',disconnect=lambda:calls.append('disconnect'))
        route=SimpleNamespace(close=lambda:calls.append('route-close'))
        sdk=SimpleNamespace(SdkManager=SimpleNamespace(instance=lambda:object()))
        events=queue.Queue()
        with patch.dict(sys.modules,{'wuji_sdk':sdk}),patch('device_discovery.connect_discovered',return_value=(hand,route,{'id':'hand2_right'})),patch('console_agent.worker') as driver:
            paired_hand_worker('','hand',{'id':'hand2_left'},queue.Queue(),events,object())
        driver.assert_not_called();self.assertEqual(calls,['disconnect','route-close'])
        self.assertEqual(events.get()['type'],'error')

    def test_ownership_lives_until_auto_worker_returns(self):
        import console_agent
        calls=[]
        class Ownership:
            def __init__(self,serial):calls.append('lock')
            def close(self):calls.append('unlock')
        sdk=SimpleNamespace(SdkManager=SimpleNamespace(instance=lambda:object()))
        hand=SimpleNamespace(serial_number='hand',disconnect=lambda:None);route=SimpleNamespace(close=lambda:None)
        def worker(*args,**kwargs):self.assertEqual(calls,['lock']);calls.append('driver')
        with patch.dict(sys.modules,{'wuji_sdk':sdk}),patch('sys.platform','linux'),patch('device_ownership.DeviceOwnership',Ownership),patch('device_discovery.connect_discovered',return_value=(hand,route,{'id':'hand2_left','generation':'hand2'})),patch('console_agent.worker',side_effect=worker),patch.dict('os.environ',{}):
            console_agent.worker_auto('','hand',queue.Queue(),queue.Queue())
        self.assertEqual(calls,['lock','driver','unlock'])

    def test_glove_pair_reserves_serial_for_driver_lifetime(self):
        calls=[]
        class Ownership:
            def __init__(self,serial):calls.append(('lock',serial))
            def close(self):calls.append(('unlock',))
        sdk=SimpleNamespace(SdkManager=SimpleNamespace(instance=lambda:object()))
        hand=SimpleNamespace(serial_number='chosen',disconnect=lambda:None);route=SimpleNamespace(close=lambda:None)
        def driver(*args,**kwargs):
            self.assertEqual(calls,[('lock','chosen')]);calls.append(('driver',))
        p={'id':'hand2_left','generation':'hand2'}
        with patch.dict(sys.modules,{'wuji_sdk':sdk}),patch('sys.platform','linux'),patch('device_ownership.DeviceOwnership',Ownership),patch('device_discovery.connect_discovered',return_value=(hand,route,p)) as discover,patch('console_agent.worker',side_effect=driver):
            paired_hand_worker('','chosen',p,queue.Queue(),queue.Queue(),object())
        self.assertEqual(discover.call_args.args[2],'chosen')
        self.assertEqual(calls,[('lock','chosen'),('driver',),('unlock',)])


@unittest.skipUnless(sys.platform.startswith('linux'),'Controller flock test runs on Linux')
class LeaseTests(unittest.TestCase):
    def test_two_gloves_share_but_calibration_and_profile_change_exclude(self):
        from sdk_session import ProfileLease
        with tempfile.TemporaryDirectory() as folder,patch('sdk_session.tempfile.gettempdir',return_value=folder):
            with ProfileLease(),ProfileLease():
                with self.assertRaises(ValueError):ProfileLease(exclusive=True)
            with ProfileLease(exclusive=True):
                with self.assertRaises(ValueError):ProfileLease()
            with ProfileLease():pass


if __name__=='__main__':unittest.main()
