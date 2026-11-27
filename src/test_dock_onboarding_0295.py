"""Choose an automatic setup using live Windows evidence without opening hardware."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import device_network as network
from test_dock_route_recovery_0294 import HAND, GLOVE, MAC, NEW, OLD, STABLE, entry, nic


class DockOnboardingTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.records=Path(directory.name)/'not-created'
        self.old=nic(OLD,1,up=False)
        self.other=nic(STABLE,2)
        self.new=nic(NEW,3)
        self.table=dict(ethernet=False,addresses=['192.168.1.50/24'],
                        interface_indices=[1,2,3],
                        interface_ids=[dict(index=i,id=g) for i,g in [(1,OLD),(2,STABLE),(3,NEW)]],
                        routes=[dict(entry(GLOVE,2,'live-glove'),publish='No')],
                        persistent_routes=[dict(entry(GLOVE,2,'saved-glove'),publish='No')])

    def plans(self, responder=None):
        with patch('windows_ethernet.probe_ipv4',side_effect=lambda index,source,target:
                   MAC if index==responder and target==HAND else None):
            return network.dock_recovery_plans([self.old,self.other,self.new,self.table],self.records)

    def test_unknown_active_only_route_on_a_down_adapter_uses_live_instance(self):
        self.table['routes'].append(dict(entry(HAND,1,'active-hand'),publish='No',route_metric=37))
        plan,=self.plans(3)
        self.assertEqual(plan['new_index'],3)
        self.assertEqual(plan['observed'],dict(prefix=HAND+'/32',old_index=1,old_guid=OLD,
                         route_metric=37,active_instance='active-hand',persistent_instance=None))
        self.assertIsNone(plan.get('receipt'))

    def test_unknown_persistent_only_route_uses_the_instance_without_the_old_adapter(self):
        self.old={}
        self.table['interface_ids']=self.table['interface_ids'][1:]
        self.table['interface_indices']=[2,3]
        self.table['persistent_routes'].append(dict(entry(HAND,1,'old-instance'),publish='No'))
        plan,=self.plans(3)
        self.assertEqual(plan['observed']['persistent_instance'],'old-instance')
        self.assertIsNone(plan['observed']['old_guid'])
        self.table['interface_indices']=[1,2,3]
        self.assertEqual(self.plans(3),[])

    def test_unknown_orphan_active_route_requires_an_unclaimed_index(self):
        self.old={}
        self.table['interface_ids']=self.table['interface_ids'][1:]
        self.table['interface_indices']=[2,3]
        self.table['routes'].append(dict(entry(HAND,1,'orphan-active'),publish='No'))
        plan,=self.plans(3)
        self.assertEqual(plan['observed']['active_instance'],'orphan-active')
        self.assertIsNone(plan['observed']['old_guid'])
        self.table['interface_indices']=[1,2,3]
        self.assertEqual(self.plans(3),[])

    def test_keep_a_working_or_published_route(self):
        row=dict(entry(HAND,1,'active-hand'),publish='Yes')
        self.table['routes'].append(row)
        self.assertEqual(self.plans(3),[])
        row['publish']='No'
        self.old['up']=True
        self.assertEqual(self.plans(3),[])

    def test_unconfigured_dock_starts_one_setup_with_or_without_apipa(self):
        for addresses in (['169.254.5.20/16'],[]):
            with self.subTest(addresses=addresses):
                self.new.update(addresses=addresses,saved_address=False)
                plan,=self.plans()
                self.assertEqual(plan['new_index'],3)
                self.assertEqual(plan['host_address'],'192.168.1.51')
                self.assertIsNone(plan['prefix'])
                self.assertIn(HAND,plan['candidate_addresses'])
                self.assertNotIn(GLOVE,plan['candidate_addresses'])

    def test_unconfigured_replacement_can_inherit_the_exact_stale_route(self):
        self.new.update(addresses=['169.254.6.12/16'],saved_address=False)
        self.table['routes'].append(dict(entry(HAND,1,'active-hand'),publish='No'))
        plan,=self.plans()
        self.assertEqual(plan['prefix'],HAND+'/32')
        self.assertEqual(plan['host_address'],'192.168.1.51')
        self.assertEqual(plan['observed']['active_instance'],'active-hand')

    def test_unconfigured_adapter_with_an_uplink_or_saved_setting_is_not_prepared(self):
        self.new.update(addresses=['169.254.6.12/16'],gateway=True,saved_address=False)
        self.assertEqual(self.plans(),[])
        self.new.update(gateway=False,saved_address=True)
        self.assertEqual(self.plans(),[])

    def test_returning_dock_prepares_its_address_without_replacing_its_route(self):
        self.new.update(addresses=[],saved_address=False)
        self.table['persistent_routes'].append(dict(entry(HAND,3,'returning-hand'),publish='No'))
        self.records.mkdir()
        receipt=self.records/('setup-'+'9'*32+'.json')
        receipt.write_text(json.dumps(dict(ok=True,persistent=True,adapter=NEW,interface_index=3,
                                          route=HAND+'/32',route_metric=5,route_instance='returning-hand',
                                          temporary_address='192.168.1.53/24')),encoding='utf8')
        plan,=self.plans()
        self.assertTrue(plan['restore_address'])
        self.assertEqual((plan['prefix'],plan['new_index'],plan['receipt']),(HAND+'/32',3,receipt))


if __name__=='__main__':
    unittest.main()
