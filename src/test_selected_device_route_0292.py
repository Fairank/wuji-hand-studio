"""A selected native device may use its exact current route without a second scan."""
import copy
import io
import json
import queue
import sys
import unittest
from types import SimpleNamespace as N
from unittest.mock import Mock, patch

import numpy  # Keep the extension loaded outside synthetic SDK patches.

from device_inventory import normalize_devices
from glove_agent import worker_glove
from glove_bridge import GloveBridge
from test_connection_switch import HAND, SN, USER
import test_connection_switch as fixture
import test_target_mapping_switch as pair_fixture
from test_sdk_lifecycle import proof_fixture, release_fixture


HAND_ENDPOINT = '192.0.2.10:7447'
GLOVE_ENDPOINT = '192.0.2.11:50001'
ROUTE = dict(source='official_discovery', serial=SN, address=GLOVE_ENDPOINT)


def observed(serial, device_type, address, when=100, prefix='udp/'):
    return normalize_devices([dict(serial=serial, device_type=device_type, side='left', address=prefix+address)],
                             source='official_cli', observed_at=when, now=100)['devices'][0]


class SelectedDeviceRouteTests(unittest.TestCase):
    def test_plain_hand_connection_passes_observed_addr_not_caller_addr(self):
        tests=fixture.SwitchTests()
        for prefix in ('udp/', 'udp://'):
            with self.subTest(prefix=prefix),tests.environment('idle') as e:
                e.rows[-1]=observed(HAND,'wuji_hand_2',HAND_ENDPOINT,prefix=prefix)
                result=e.service.start(tests.command(e,'hand',HAND,address='192.0.2.44:7447'))
                self.assertEqual(e.calls[-1],dict(name='connect',serial=HAND,auto_detect=True,address=HAND_ENDPOINT))
                self.assertEqual(result['connection_switch_receipt']['target']['serial'],HAND)
                self.assertFalse(e.state['hardware']['active'])

    def test_selected_wearer_and_native_locator_do_not_require_repeated_broadcast(self):
        tests=fixture.SwitchTests()
        with tests.environment('idle') as e:
            e.rows[0]=observed(SN,'wuji_glove',GLOVE_ENDPOINT,prefix='udp://')
            e.c.glove.selected_route_ready=Mock(return_value=True)
            original=e.c.action.side_effect
            def action(command):
                result=original(command)
                if command['name']=='glove_scan':e.gs['devices']=[]  # NAT cannot discover the native peer.
                return result
            e.c.action.side_effect=action
            result=e.service.start(tests.command(e,'teleoperation',SN,user_id=USER))
            self.assertEqual(e.calls,[dict(name='glove_scan',_glove_route=ROUTE),
                dict(name='glove_open',serial=SN,user_id=USER,timeout_ms=250)])
            self.assertEqual(e.gs['user']['user_id'],USER)
            self.assertNotIn('_glove_route',result['connection_switch_receipt']['target'])

    def test_old_controller_still_requires_its_finite_discovery(self):
        tests=fixture.SwitchTests()
        for found in (False,True):
            with self.subTest(found=found),tests.environment('idle') as e:
                e.rows[0]=observed(SN,'wuji_glove',GLOVE_ENDPOINT)
                e.c.glove.selected_route_ready=Mock(return_value=False)
                original=e.c.action.side_effect
                def action(command):
                    result=original(command)
                    if command['name']=='glove_scan' and not found:e.gs['devices']=[]
                    return result
                e.c.action.side_effect=action
                command=tests.command(e,'teleoperation',SN,user_id=USER)
                if found:e.service.start(command)
                else:
                    with self.assertRaisesRegex(ValueError,'exact glove'):
                        e.service.start(command)
                self.assertEqual(sum(c['name']=='glove_open' for c in e.calls),int(found))

    def test_untrusted_public_glove_scan_cannot_supply_private_route(self):
        from console_server import Controller
        c=Controller.__new__(Controller);c.connection_switch=N(internal=False)
        with self.assertRaisesRegex(ValueError,'private'):
            c._action(dict(name='glove_scan',_glove_route=ROUTE))

    def test_pairing_private_target_cannot_bypass_current_address_conflict(self):
        from console_server import Controller
        from device_inventory import build_inventory
        payload=dict(hand_serial=HAND,profile='hand2_left',sdk_user=USER)
        batch=[dict(sn=serial,device_type='wuji_hand_2',side='left',address='udp/'+HAND_ENDPOINT)
               for serial in (HAND,'WH2_REPEAT_IP_FIXTURE')]
        directory=build_inventory(viz=dict(devices=batch,scanned_at=100),now=100)
        self.assertEqual(len(directory['devices']),2)
        self.assertTrue(all(row['address_conflicts'] for row in directory['devices']))
        for via_task in (False,True):
            with self.subTest(via_task=via_task),pair_fixture.TargetMappingSwitchTests().environment(False) as e:
                e.rows[-1:]=directory['devices']
                with self.assertRaisesRegex(ValueError,'address conflict'):
                    if via_task:Controller._task_context_dispatch(e.c,'pair_prepare',payload,e.c.snapshot())
                    else:e.service.start(e.command(),pair_target=payload)
                self.assertEqual(e.calls,[])

    def worker(self, fault=None, serial=SN, user=USER):
        former=dict(user_id='',display_name='Default');wearer=dict(user_id=USER,display_name='Chosen')
        current=[former];calls=[];requests=queue.Queue();events=queue.Queue()
        requests.put(dict(name='glove_open',serial=serial,user_id=user,timeout_ms=250));requests.put(dict(name='disconnect'))

        class Glove:
            serial_number='OTHER' if fault=='serial' else SN
            hand_side=Mock(return_value=N(get=lambda:'right' if fault=='side' else 'left'))
            hand_skeleton=Mock(return_value=N(subscribe=lambda:N(recv=lambda:None,close=lambda:True)))
            disconnect=Mock(return_value=True)

        device=Glove() if fault!='type' else N(serial_number=SN,disconnect=Mock(return_value=True),hand_skeleton=Mock())
        def switch(user_id):
            current[0]=wearer if user_id==USER else former
        def connect(**values):
            calls.append((copy.deepcopy(values),current[0]['user_id']))
            return device
        manager=N(current_user=lambda:current[0],list_users=lambda:[former,wearer],switch_user=switch,
                  scan=Mock(side_effect=AssertionError('Native selected route must not scan')),connect=Mock(side_effect=connect))
        sdk=N(SdkManager=N(instance=lambda:manager),DeviceType=N(WujiGlove='glove'),WujiGlove=Glove,
              HandModel=N(WujiHand2='hand2',WujiHand='hand1'),Handedness=N(Left='left',Right='right'),
              RetargetSession=N(for_hand=lambda *a,**kw:N(step=lambda kp:[0.]*20)))
        with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict('os.environ',WUJI_HAND_PROFILE='hand2_left'):
            worker_glove(requests,events,selected_route=ROUTE)
        manager.scan.assert_not_called();self.assertIs(current[0],former)
        return device, calls, list(events.queue)

    def test_worker_uses_existing_sdk_wearer_and_reads_exact_device_before_stream(self):
        device,calls,events=self.worker()
        self.assertEqual(calls,[(dict(address=GLOVE_ENDPOINT,device_name='studio_glove'),USER)])
        device.hand_side.assert_called_once();device.hand_skeleton.assert_called_once();device.disconnect.assert_called_once()
        self.assertTrue(any(e.get('state',{}).get('connection')=='receiving' for e in events))

    def test_wrong_route_identity_side_or_type_never_subscribes_or_falls_back(self):
        for fault in ('serial','side','type'):
            with self.subTest(fault=fault):
                device,calls,events=self.worker(fault=fault)
                self.assertEqual(len(calls),1);device.hand_skeleton.assert_not_called();device.disconnect.assert_called_once()
                self.assertFalse(any(e.get('state',{}).get('connection')=='receiving' for e in events))

    def test_mismatched_open_serial_or_unknown_wearer_never_connects(self):
        for kwargs in (dict(serial='WRONG'),dict(user='unknown')):
            with self.subTest(kwargs=kwargs):
                device,calls,_=self.worker(**kwargs)
                self.assertEqual(calls,[]);device.hand_skeleton.assert_not_called()

    def test_route_is_in_supported_bootstrap_only(self):
        for version in (None,1):
            with self.subTest(version=version):
                sent=io.StringIO()
                ready=dict(type='ready',teleop_version=1,sdk_proof=proof_fixture())
                if version is not None:ready['glove_route_version']=version
                client=N(agent_command='synthetic',exec_command=Mock(return_value=(sent,
                    iter(['WUJI_JSON:'+json.dumps(ready)+'\n']),io.StringIO())),close=Mock(return_value=True))
                bridge=GloveBridge(Mock(return_value=client),release_resolver=release_fixture)
                bridge._selected_route=ROUTE
                bridge.run()
                payloads=[json.loads(line) for line in sent.getvalue().splitlines()]
                self.assertEqual(len(payloads),1)
                self.assertEqual(payloads[0].get('glove_route'),ROUTE if version==1 else None)


if __name__=='__main__':unittest.main()
