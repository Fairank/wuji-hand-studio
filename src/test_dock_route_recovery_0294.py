"""A replaced dock can reclaim its verified device route before discovery."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import device_network as network

OLD = 'aaaaaaaa-1111-4111-8111-111111111111'
STABLE = 'bbbbbbbb-2222-4222-8222-222222222222'
NEW = 'cccccccc-3333-4333-8333-333333333333'
HAND = '192.168.1.111'
GLOVE = '192.168.1.101'
MAC = '02-AA-BB-CC-DD-EE'


def nic(guid, index, up=True):
    return dict(id=guid,index=index,up=up,ethernet=True,gateway=False,
                addresses=[f'192.168.1.{index+50}/24'])


def entry(host, index, instance):
    return dict(prefix=host+'/32',index=index,next_hop='0.0.0.0',
                protocol='NetMgmt',route_metric=5,instance_id=instance)


class DockRouteTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory=Path(temp.name)
        self.receipt=self.directory/('setup-'+'9'*32+'.json')
        self.rows=[nic(STABLE,2),nic(NEW,3),dict(
            ethernet=False,addresses=[],
            interface_ids=[dict(id=STABLE,index=2),dict(id=NEW,index=3)],
            routes=[entry(GLOVE,2,'active-glove')],
            persistent_routes=[entry(GLOVE,2,'saved-glove'),entry(HAND,1,'saved-hand')])]
        self.record=dict(ok=True,persistent=True,adapter=OLD,interface_index=1,
                         route=HAND+'/32',route_metric=5,route_instance='saved-hand')
        self.write_receipt()
        self.probe=Mock(side_effect=lambda index,*_: MAC if index==3 else None)

    def write_receipt(self):
        self.receipt.write_text(json.dumps(self.record),encoding='utf8')

    def plans(self):
        with patch('windows_ethernet.probe_ipv4',self.probe):
            return network.dock_recovery_plans(self.rows,self.directory)

    def test_removed_nic_persistent_route_moves_to_only_fresh_responder(self):
        before=copy.deepcopy(self.rows)
        self.assertEqual(network.analyse(self.rows,side='right')['code'],'route_conflict')
        plans=self.plans()
        self.assertEqual(len(plans),1)
        self.assertEqual((plans[0]['prefix'],plans[0]['old_index'],plans[0]['new_index']),
                         (HAND+'/32',1,3))
        self.assertEqual(plans[0]['receipt'],self.receipt)
        self.assertEqual(self.rows,before)
        self.assertEqual({call.args[0] for call in self.probe.call_args_list},{2,3})
        self.assertTrue(all(call.args[2]==HAND for call in self.probe.call_args_list))

    def test_reused_index_or_duplicate_responses_cannot_move_route(self):
        self.rows[-1]['interface_ids'].append(dict(id=NEW,index=1))
        self.assertEqual(self.plans(),[])
        self.probe.assert_not_called()
        self.rows[-1]['interface_ids'].pop()
        self.probe.side_effect=None
        self.probe.return_value=MAC
        self.assertEqual(self.plans(),[])

    def test_unowned_or_changed_route_does_not_probe(self):
        self.record['route_instance']='not-the-current-instance'
        self.write_receipt()
        self.assertEqual(self.plans(),[])
        self.receipt.unlink()
        self.assertEqual(self.plans(),[])
        self.probe.assert_not_called()

    def test_hidden_old_nic_can_match_a_legacy_successful_setup(self):
        self.record.pop('route_instance')
        self.record.pop('interface_index')
        self.write_receipt()
        self.receipt.with_name(self.receipt.stem+'-before.json').write_text(json.dumps(
            dict(target=HAND+':7447',candidates=[dict(id=OLD,index=1,setup_kind='route')])),encoding='utf8')
        self.rows.insert(0,nic(OLD,1,up=False))
        self.rows[-1]['interface_ids'].append(dict(id=OLD,index=1))
        self.assertEqual(self.plans()[0]['new_guid'],NEW)
        self.rows[0]['up']=True
        self.assertEqual(self.plans(),[])

    def test_route_repair_receives_exact_original_scan_and_checks_result(self):
        from types import SimpleNamespace
        record={**self.record,'adapter':NEW,'interface_index':3,'route_instance':'saved-replacement'}
        ready=copy.deepcopy(self.rows)
        ready[-1]['routes'].append(entry(HAND,3,'active-hand'))
        ready[-1]['persistent_routes'][-1]=entry(HAND,3,'saved-replacement')
        repair=Mock(return_value=record)
        idle=Mock()
        self.receipt.rename(self.directory/'unused.json')
        data=self.directory/'network'
        data.mkdir()
        (data/self.receipt.name).write_text(json.dumps(self.record),encoding='utf8')
        with patch.dict('sys.modules',runtime_paths=SimpleNamespace(DATA=self.directory)), \
                patch.object(network.sys,'platform','win32'),patch.object(network,'_dock_recovery',None,create=True), \
                patch.object(network,'_setup',None),patch.object(network,'read_windows',side_effect=[self.rows,ready]), \
                patch('windows_ethernet.probe_ipv4',self.probe),patch.object(network,'_apply_dock_route',repair,create=True):
            self.assertIsNone(network.recover_dock_routes(self.directory,'a'*32,8,lambda:False,idle))
        repair.assert_called_once()
        self.assertEqual(repair.call_args.args[1:],(self.directory,'a'*32))
        idle.assert_called()

    def test_cancelling_while_windows_waits_reaches_the_original_helper(self):
        from types import SimpleNamespace
        cancelled=threading.Event()
        token='b'*32
        cancel_file=self.directory/('network-cancel-'+token)
        data=self.directory/'network'
        data.mkdir()
        (data/self.receipt.name).write_text(json.dumps(self.record),encoding='utf8')
        def delayed_helper(*_):
            cancelled.set()
            deadline=time.monotonic()+1.
            while not cancel_file.is_file() and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertTrue(cancel_file.is_file(), 'The helper must receive cancellation during UAC')
            return {'ok':False}
        with patch.dict('sys.modules',runtime_paths=SimpleNamespace(DATA=self.directory)), \
                patch.object(network.sys,'platform','win32'),patch.object(network,'_dock_recovery',None), \
                patch.object(network,'_setup',None),patch.object(network,'read_windows',return_value=self.rows), \
                patch('windows_ethernet.probe_ipv4',self.probe),patch.object(network,'_apply_dock_route',side_effect=delayed_helper):
            network.recover_dock_routes(self.directory,token,9,cancelled.is_set,Mock())
        self.assertFalse(cancel_file.exists())

    def test_declined_automatic_repair_can_be_retried_by_the_find_button(self):
        from types import SimpleNamespace
        data=self.directory/'network'
        data.mkdir()
        (data/self.receipt.name).write_text(json.dumps(self.record),encoding='utf8')
        with patch.dict('sys.modules',runtime_paths=SimpleNamespace(DATA=self.directory)), \
                patch.object(network.sys,'platform','win32'),patch.object(network,'_dock_recovery',None), \
                patch.object(network,'_setup',None),patch.object(network,'read_windows',return_value=self.rows), \
                patch('windows_ethernet.probe_ipv4',self.probe),patch.object(network,'_apply_dock_route',return_value={'ok':False}) as apply:
            self.assertTrue(network.recover_dock_routes(self.directory,'a'*32,9,lambda:False,Mock()))
            self.assertTrue(network.recover_dock_routes(self.directory,'a'*32,9,lambda:False,Mock()))
            self.assertEqual(apply.call_count,1)
            self.assertTrue(network.recover_dock_routes(self.directory,'a'*32,9,lambda:False,Mock(),retry=True))
            self.assertEqual(apply.call_count,2)


if __name__=='__main__':
    unittest.main()
