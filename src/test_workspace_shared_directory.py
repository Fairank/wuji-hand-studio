"""Multi-hand entry reuses the actual shared inventory DTO without device IO."""
import copy
from contextlib import contextmanager
import io
import json
from pathlib import Path
import tempfile
import time
from types import MethodType,SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from console_server import Controller
from test_workspace_groups import FakeController,FakeFleet,apply_command,profile_dict
from workspace_groups import WorkspaceGroups


LEFT='SYNTHETIC-SHARED-LEFT';RIGHT='SYNTHETIC-SHARED-RIGHT'


class SharedWorkspaceDirectoryTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        with tempfile.TemporaryDirectory() as folder,patch('cli_coordination.reservation',return_value={}):
            controller=FakeController();fleet=FakeFleet()
            controller.factory=Mock(side_effect=AssertionError('Ordinary discovery must use the shared directory'))
            controller.state['official_viz']=dict(devices=[
                dict(sn=LEFT,device_type='wuji_hand_2',side='left'),
                dict(sn=RIGHT,device_type='wuji_hand_2',side='right')],
                scanned_at=time.time(),supported=True,backend='windows_native')
            controller.official_viz=N(folder=Path(folder)/'cli',check_scan=Mock(),
                scan=Mock(side_effect=lambda **kwargs:copy.deepcopy(controller.state['official_viz'])))
            controller.glove=N(snapshot=lambda:dict(busy=False))
            controller.device_inventory=Mock(wraps=MethodType(Controller.device_inventory,controller))
            def action(command):
                if command['name']=='device_inventory_scan':return Controller._workflow_action(controller,command)
                return apply_command(controller.state,command)
            controller.action=Mock(side_effect=action)
            groups=WorkspaceGroups(fleet,controller,folder)
            yield N(controller=controller,fleet=fleet,groups=groups)

    def test_fresh_native_inventory_is_visible_and_exact_add_needs_no_second_scan(self):
        with self.environment() as e:
            actual=e.controller.device_inventory()
            self.assertEqual({r['serial'] for r in actual['devices']},{LEFT,RIGHT})
            snapshot=e.groups.snapshot()
            self.assertEqual({r['serial'] for r in snapshot['discovery']['devices']},{LEFT,RIGHT})
            e.controller.action.assert_not_called();e.controller.factory.assert_not_called()
            self.assertEqual(e.fleet.created,[])
            result=e.groups.action(dict(name='workspace_add',workspace='main',serial=RIGHT))
            self.assertEqual(result['member'],'main');self.assertTrue(result['connecting'])
            commands=[call.args[0] for call in e.controller.action.call_args_list]
            self.assertEqual(commands,[dict(name='device_profile_select',profile='hand2_right'),
                dict(name='connect',serial=RIGHT,address='',auto_detect=True)])
            e.controller.factory.assert_not_called()

    def test_blank_scan_calls_existing_inventory_scan_and_no_legacy_transport(self):
        with self.environment() as e:
            result=e.groups.action(dict(name='workspace_scan',address=''))
            e.controller.action.assert_called_once_with(dict(name='device_inventory_scan'))
            e.controller.official_viz.scan.assert_called_once_with(supplement=True)
            e.controller.factory.assert_not_called()
            self.assertEqual({r['serial'] for r in result['discovery']['devices']},{LEFT,RIGHT})

    def test_unknown_side_keeps_explicit_profile_intent_and_exact_serial(self):
        with self.environment() as e:
            e.controller.state['device_profile']=profile_dict('hand2_right')
            e.controller.state['official_viz']['devices']=[dict(sn='SYNTHETIC-NO-SIDE',device_type='wuji_hand_2')]
            row=e.groups.snapshot()['discovery']['devices'][0]
            self.assertIsNone(row['side_hint'])
            result=e.groups.action(dict(name='workspace_add',workspace='main',serial=row['serial']))
            self.assertTrue(result['connecting'])
            commands=[call.args[0] for call in e.controller.action.call_args_list]
            self.assertEqual(commands[0],dict(name='device_profile_select',profile='hand2_right'))
            self.assertEqual(commands[-1]['serial'],'SYNTHETIC-NO-SIDE')
            self.assertIsNone(e.groups.discovery.snapshot()['devices'][0]['side_hint'])

    def test_stale_conflicting_glove_and_unknown_generation_do_not_create_hands(self):
        for kind in ('stale','conflict','glove','generation'):
            with self.subTest(kind=kind),self.environment() as e:
                rows=[dict(sn=LEFT,device_type='wuji_hand_2',side='left')]
                if kind=='stale':e.controller.state['official_viz']['scanned_at']=time.time()-40
                elif kind=='conflict':rows.append(dict(sn=LEFT,device_type='wuji_hand_2',side='right'))
                elif kind=='glove':rows=[dict(sn=LEFT,device_type='wuji_glove',side='left')]
                else:rows=[dict(sn=LEFT,device_type='hand',side='left')]
                e.controller.state['official_viz']['devices']=rows
                with self.assertRaises(ValueError):e.groups.action(dict(name='workspace_add',workspace='main',serial=LEFT))
                e.controller.action.assert_not_called();e.controller.factory.assert_not_called()
                self.assertEqual(e.fleet.created,[])

    def test_duplicate_transport_is_one_choice_and_duplicate_add_keeps_existing_owner(self):
        with self.environment() as e:
            e.controller.state['official_viz']['devices']=[
                dict(sn=LEFT,device_type='wuji_hand_2',side='left',address='192.0.2.20:7447'),
                dict(sn=LEFT,device_type='wuji_hand_2',side='left',address='zenoh://'+LEFT)]
            self.assertEqual(len(e.groups.snapshot()['discovery']['devices']),1)
            e.groups.action(dict(name='workspace_add',workspace='main',serial=LEFT))
            with self.assertRaisesRegex(ValueError,'already connecting|unconfirmed session|already assigned'):
                e.groups.action(dict(name='workspace_add',workspace='main',serial=LEFT))
            self.assertEqual(sum(c.args[0]['name']=='connect' for c in e.controller.action.call_args_list),1)
            self.assertEqual(e.fleet.created,[])

    def test_directory_failure_does_not_start_fallback_or_create_a_child(self):
        with self.environment() as e:
            e.controller.device_inventory.side_effect=ValueError('synthetic shared directory unavailable')
            snapshot=e.groups.snapshot()['discovery']
            self.assertIn('shared directory unavailable',snapshot['error']);self.assertEqual(snapshot['devices'],[])
            with self.assertRaises(ValueError):e.groups.action(dict(name='workspace_add',workspace='main',serial=LEFT))
            e.controller.factory.assert_not_called();self.assertEqual(e.fleet.created,[])

    def test_add_rechecks_inventory_after_visible_choice_becomes_stale(self):
        with self.environment() as e:
            self.assertTrue(e.groups.snapshot()['discovery']['devices'])
            e.controller.state['official_viz']['scanned_at']=time.time()-40
            with self.assertRaises(ValueError):e.groups.action(dict(name='workspace_add',workspace='main',serial=LEFT))
            e.controller.action.assert_not_called();self.assertEqual(e.fleet.created,[])

    def test_unknown_sibling_owner_state_blocks_add_without_touching_any_session(self):
        with self.environment() as e:
            inventory=e.controller.device_inventory()
            e.controller.device_inventory=Mock(return_value={**inventory,
                'workspace_errors':[dict(id='unavailable-sibling',error='unavailable')]})
            # The adapter captured the existing provider at construction.
            e.groups.discovery.inventory=e.controller.device_inventory
            with self.assertRaisesRegex(ValueError,'workspace state is unavailable'):
                e.groups.action(dict(name='workspace_add',workspace='main',serial=LEFT))
            e.controller.action.assert_not_called();e.controller.factory.assert_not_called()
            self.assertEqual(e.fleet.created,[])

    def test_actual_main_and_sibling_cli_owners_are_not_offered_as_new_hands(self):
        for owner in ('main','sibling'):
            with self.subTest(owner=owner),self.environment() as e:
                before=e.groups.snapshot()['workspaces']
                owned=copy.deepcopy(e.controller.state)
                owned['official_viz'].update(active=True,busy=False,phase='running',session_id='a'*32,
                    device=dict(sn=LEFT),reported_device=dict(sn=LEFT,device_type='wuji_hand_2',side='left'))
                if owner=='main':e.controller.state=owned
                else:e.controller.inventory_observer=lambda:[dict(id='sibling',label='Existing CLI task',state=owned)]
                actual=next(r for r in e.controller.device_inventory()['devices'] if r['serial']==LEFT)
                self.assertTrue(actual['in_use'])
                if owner=='sibling':self.assertEqual(actual['workspace_owners'][0]['id'],'sibling')
                self.assertNotIn(LEFT,[r['serial'] for r in e.groups.snapshot()['discovery']['devices']])
                with self.assertRaises(ValueError):e.groups.action(dict(name='workspace_add',workspace='main',serial=LEFT))
                self.assertEqual(e.groups.snapshot()['workspaces'],before)
                e.controller.action.assert_not_called();e.controller.factory.assert_not_called()
                self.assertEqual(e.fleet.created,[]);self.assertEqual(e.fleet.commands,[])

    def test_custom_address_cannot_duplicate_exact_cli_or_paired_glove_owner(self):
        for owner in ('cli','glove'):
            with self.subTest(owner=owner),self.environment() as e:
                e.groups.discovery.state.update(address='192.0.2.20:7447',updated=time.time(),
                    devices=[dict(serial=LEFT,generation='hand2',side_hint='left')])
                if owner=='cli':
                    e.controller.state['official_viz'].update(active=True,phase='running',session_id='a'*32,
                        device=dict(sn=LEFT),reported_device=dict(sn=LEFT))
                else:
                    e.controller.state['glove']=dict(busy=True,connection='receiving',
                        feedback=dict(device_id=LEFT,connection='connected'))
                before=copy.deepcopy(e.groups.rows)
                with self.assertRaisesRegex(ValueError,'already|owns|occupied'):
                    e.groups.action(dict(name='workspace_add',workspace='main',serial=LEFT))
                self.assertEqual(e.groups.rows,before)
                e.controller.action.assert_not_called();e.controller.factory.assert_not_called()
                self.assertEqual(e.fleet.created,[]);self.assertEqual(e.fleet.commands,[])

    def test_preview_add_is_passive_and_does_not_require_discovery(self):
        with self.environment() as e:
            e.controller.device_inventory.side_effect=ValueError('directory unavailable')
            result=e.groups.action(dict(name='workspace_add',workspace='main',preview_profile='hand2_right'))
            self.assertIs(result['connecting'],False);self.assertEqual(len(e.fleet.created),1)
            e.controller.device_inventory.assert_not_called();e.controller.factory.assert_not_called()
            self.assertNotIn('connect',[c['name'] for c in e.fleet.commands])

    def test_explicit_custom_address_retains_existing_scan_route_then_blank_restores_shared(self):
        with self.environment() as e:
            custom='192.0.2.20:7447';serial='SYNTHETIC-CUSTOM'
            rows=[dict(serial=serial,generation='hand2',side_hint='left')]
            client=N(agent_command='synthetic-read-only-agent',exec_command=Mock(return_value=(
                io.StringIO(),['WUJI_JSON:'+json.dumps(dict(type='discovery',devices=rows))],io.StringIO())),close=Mock())
            e.controller.factory.side_effect=None;e.controller.factory.return_value=client
            class Inline:
                def __init__(self,*,target,args=(),**kwargs):self.target=target;self.args=args
                def start(self):self.target(*self.args)
            with patch('workspace_groups.threading.Thread',Inline),patch('workspace_groups.threading.Timer'):
                result=e.groups.action(dict(name='workspace_scan',address=custom))
            self.assertEqual(result['discovery']['address'],custom)
            e.groups.action(dict(name='workspace_add',workspace='main',serial=serial))
            self.assertEqual(e.controller.action.call_args.args[0],dict(name='connect',serial=serial,address=custom,auto_detect=True))
            e.controller.action.reset_mock()
            result=e.groups.action(dict(name='workspace_scan',address=''))
            self.assertEqual({r['serial'] for r in result['discovery']['devices']},{LEFT,RIGHT})
            e.controller.action.assert_called_once_with(dict(name='device_inventory_scan'))
            self.assertEqual(e.controller.factory.call_count,1)


if __name__=='__main__':unittest.main()
