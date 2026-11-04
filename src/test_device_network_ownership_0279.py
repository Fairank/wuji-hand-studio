"""Device adapter setup respects USB transport and unresolved hand ownership."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from macos_desktop import MacDesktop
from native_desktop import NativeDesktop


class NetworkOwnershipTests(unittest.TestCase):
    def host(self, cls, **state):
        host=object.__new__(cls)
        value=dict(connection='disconnected',hardware=dict(active=False),
            device_profile=dict(generation='hand2',side='left'))
        value.update(state)
        host.server=SimpleNamespace(controller=SimpleNamespace(snapshot=lambda:value,glove=SimpleNamespace(busy=False)))
        return host

    def test_mac_hand1_status_does_not_probe_ethernet(self):
        host=self.host(MacDesktop,device_profile=dict(generation='hand1',side='right'))
        with patch('macos_network.snapshot') as probe:
            self.assertEqual(host.network_status(),dict(code='usb_device',can_configure=False))
        probe.assert_not_called()

    def test_hand1_never_configures_an_ethernet_adapter(self):
        for cls,module in ((NativeDesktop,'device_network'),(MacDesktop,'macos_network')):
            with self.subTest(platform=cls.__name__):
                host=self.host(cls,device_profile=dict(generation='hand1',side='right'))
                with patch(module+'.configure') as configure,self.assertRaisesRegex(ValueError,'USB'):
                    host.configure_device_network('synthetic-adapter')
                configure.assert_not_called()

    def test_unknown_stop_pending_start_and_retained_uncertainty_block_configuration(self):
        for cls,module in ((NativeDesktop,'device_network'),(MacDesktop,'macos_network')):
            for state in (dict(hardware=dict(active=None)),
                          dict(hardware_control_id='a'*64),
                          dict(unconfirmed_devices=[dict(source='hand',device_id='SYNTHETIC')])):
                with self.subTest(platform=cls.__name__,state=state):
                    host=self.host(cls,connection='error',**state)
                    with patch(module+'.configure') as configure,self.assertRaises(ValueError):
                        host.configure_device_network('synthetic-adapter')
                    configure.assert_not_called()

    def test_explicit_idle_hand2_configuration_still_uses_existing_adapter(self):
        for cls,module in ((NativeDesktop,'device_network'),(MacDesktop,'macos_network')):
            with self.subTest(platform=cls.__name__):
                host=self.host(cls)
                with patch(module+'.configure',return_value={'synthetic':True}) as configure:
                    self.assertEqual(host.configure_device_network('adapter-1','192.0.2.1'),{'synthetic':True})
                configure.assert_called_once_with('adapter-1','192.0.2.1','left')

    def test_official_view_calibration_and_switch_owners_block_configuration(self):
        states=[dict(official_viz={key:True}) for key in ('active','busy','reserved','session_id')]
        states.extend(dict(official_studio={key:True}) for key in ('active','busy','reserved'))
        states.extend((dict(calibration=dict(running=True)),dict(calibration=dict(cleanup_required=True)),
            dict(connection_switch=dict(busy=True)),dict(connection_switch=dict(cleanup_error='synthetic'))))
        for cls,module in ((NativeDesktop,'device_network'),(MacDesktop,'macos_network')):
            for state in states:
                with self.subTest(platform=cls.__name__,state=state):
                    host=self.host(cls,**state)
                    with patch(module+'.configure') as configure,self.assertRaises(ValueError):
                        host.configure_device_network('synthetic-adapter')
                    configure.assert_not_called()


if __name__=='__main__':unittest.main()
