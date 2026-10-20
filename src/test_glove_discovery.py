from types import SimpleNamespace
import unittest
from glove_discovery import scan_gloves,connect_glove,FACTORY_ENDPOINTS


class Glove:
    def __init__(self,sn):self.serial_number=sn;self.closed=0
    def disconnect(self):self.closed+=1


SDK=SimpleNamespace(WujiGlove=Glove,DeviceType=SimpleNamespace(WujiGlove='glove'))


class Manager:
    def __init__(self,values):self.values=values;self.calls=[]
    def connect(self,**values):
        self.calls.append(values)
        result=self.values.get(values.get('address') or values.get('sn'))
        if result is None:raise TimeoutError('No device')
        return result


class GloveDiscoveryTests(unittest.TestCase):
    def test_native_scan_does_not_probe_addresses(self):
        manager=Manager({})
        rows=scan_gloves(manager,SDK,[SimpleNamespace(sn='test-glove',address='10.4.0.3:50001',device_type='glove')])
        self.assertEqual(rows[0]['source'],'sdk_scan');self.assertEqual(manager.calls,[])

    def test_nat_probe_reads_fresh_glove_identity_and_closes_handle(self):
        glove=Glove('WG-test-right');manager=Manager({FACTORY_ENDPOINTS[1]:glove})
        rows=scan_gloves(manager,SDK,[],allow_direct=True)
        self.assertEqual(rows,[dict(serial='WG-test-right',address=FACTORY_ENDPOINTS[1],source='direct_address_probe')])
        self.assertEqual(glove.closed,1)
        self.assertEqual([c['address'] for c in manager.calls],list(FACTORY_ENDPOINTS))

    def test_other_device_type_is_closed_and_not_claimed_as_glove(self):
        other=SimpleNamespace(disconnect=lambda:closed.append(True));closed=[]
        manager=Manager({FACTORY_ENDPOINTS[0]:other})
        self.assertEqual(scan_gloves(manager,SDK,[],True),[]);self.assertEqual(closed,[True])

    def test_existing_advertised_endpoint_is_not_reopened(self):
        manager=Manager({})
        scan_gloves(manager,SDK,[SimpleNamespace(sn='seen',address=FACTORY_ENDPOINTS[1],device_type='glove')],True)
        self.assertEqual([c['address'] for c in manager.calls],[FACTORY_ENDPOINTS[0]])

    def test_reconnect_uses_verified_address_and_rejects_changed_identity(self):
        selected=dict(serial='original',address=FACTORY_ENDPOINTS[1],source='direct_address_probe')
        changed=Glove('replacement');manager=Manager({FACTORY_ENDPOINTS[1]:changed})
        with self.assertRaisesRegex(ValueError,'identity changed'):connect_glove(manager,selected)
        self.assertEqual(changed.closed,1);self.assertNotIn('sn',manager.calls[0])
        same=Glove('original');manager=Manager({FACTORY_ENDPOINTS[1]:same})
        self.assertIs(connect_glove(manager,selected),same);self.assertEqual(same.closed,0)

    def test_invented_direct_endpoint_is_rejected_before_connecting(self):
        manager=Manager({})
        with self.assertRaises(ValueError):connect_glove(manager,dict(serial='x',address='10.0.0.1:1234',source='direct_address_probe'))
        self.assertEqual(manager.calls,[])

    def test_identity_read_failure_also_releases_the_glove(self):
        closed=[];glove=SimpleNamespace(disconnect=lambda:closed.append(True));manager=Manager({'lost':glove})
        with self.assertRaises(AttributeError):connect_glove(manager,dict(serial='lost',source='sdk_scan'))
        self.assertEqual(closed,[True])


if __name__=='__main__':unittest.main()
