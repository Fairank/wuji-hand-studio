"""Finite, advertisement-only discovery fusion; every vendor boundary is fake."""
import copy
import io
import json
import queue
import sys
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

import official_device_discovery as discovery
from official_visualization import OfficialVisualization
from device_inventory import build_inventory

HAND1=dict(serial='WH1-TEST',generation='hand1',address='usb://synthetic-hand')
HAND2=dict(serial='WH2KA00000000001',generation='hand2',side_hint='right',address='zenoh://WH2KA00000000001')
GLOVE=dict(serial='WG1-TEST',device_type='wuji_glove',address='udp/192.0.2.20:50001')
CONFIG=dict(mode='wsl',cli='wuji')


class ImmediateThread:
    def __init__(self,target,args=(),**kwargs):self.target=target;self.args=args
    def start(self):self.target(*self.args)
    def is_alive(self):return False


class UnifiedDiscoveryTests(unittest.TestCase):
    def root(self):
        from console_server import Controller,ConsoleHTTPServer
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        c=Controller(factory=Mock(side_effect=AssertionError('No device transport without explicit scan')),reports=Path(temp.name)/'reports')
        server=ConsoleHTTPServer.__new__(ConsoleHTTPServer);server.controller=c;server.server_address=('127.0.0.1',1)
        server.configure_official_cli(startup=False)
        return c,server

    def test_actual_controller_scan_empty_native_accepts_finite_sdk_only_hand_without_changing_main_owner(self):
        c,_=self.root();client,stdin=self.client([HAND2]);c.factory=Mock(return_value=client)
        before=(c.generation,c.state['connection'],copy.deepcopy(c.state['device_profile']))
        with patch('bridge_config.load_config',return_value=CONFIG),patch('managed_runtime.status',return_value=dict(ready=True,busy=False)), \
                patch('calibration_cli.cli_json',return_value={'devices':[]}):
            token=c.official_viz._begin_scan()
            c.official_viz._collect_scan(token,True,True)
            inventory=c.device_inventory()
        hand=next(row for row in inventory['devices'] if row['sn']==HAND2['serial'])
        self.assertTrue(hand['selectable']);self.assertFalse(hand['official_available'])
        self.assertEqual(before,(c.generation,c.state['connection'],c.state['device_profile']))
        self.assertEqual(c.official_viz.snapshot()['devices'],[])
        self.assertEqual(json.loads(stdin.getvalue()),{'name':'discover','include_gloves':True})

    def test_configuration_change_during_sdk_read_drops_old_rows_before_publication(self):
        c,_=self.root();client,_=self.client([]);config=[copy.deepcopy(CONFIG)]
        def lines():
            yield 'WUJI_JSON:'+json.dumps(dict(type='ready'))+'\n'
            config[0]['cli']='different-configured-cli'
            yield 'WUJI_JSON:'+json.dumps(dict(type='discovery',devices=[HAND2]))+'\n'
        client.exec_command.return_value=(io.StringIO(),lines(),io.StringIO());c.factory=Mock(return_value=client)
        with patch('bridge_config.load_config',side_effect=lambda:copy.deepcopy(config[0])), \
                patch('managed_runtime.status',return_value=dict(ready=True,busy=False)),patch('calibration_cli.cli_json',return_value={'devices':[GLOVE]}):
            token=c.official_viz._begin_scan();c.official_viz._collect_scan(token,True,True)
        self.assertEqual(c.official_viz.snapshot()['sdk_devices'],[])
        self.assertIsNone(c.official_viz.snapshot()['sdk_scanned_at'])
        self.assertEqual(c.official_viz.snapshot()['devices'],[])
        self.assertIsNone(c.official_viz.snapshot()['scanned_at'])
        self.assertEqual(c.state['connection'],'disconnected');client.close.assert_called()

    def test_sdk_cleanup_unknown_retains_scan_owner_until_explicit_stop_and_retry(self):
        import cli_coordination
        c,_=self.root();first,_=self.client([HAND2],closed=False);second,_=self.client([HAND2])
        c.factory=Mock(side_effect=[first,second])
        with patch('bridge_config.load_config',return_value=CONFIG),patch('managed_runtime.status',return_value=dict(ready=True,busy=False)), \
                patch('calibration_cli.cli_json',return_value={'devices':[]}):
            token=c.official_viz._begin_scan();c.official_viz._collect_scan(token,True,True)
            self.assertEqual(cli_coordination.reservation(c.official_viz.folder)['token'],token)
            self.assertTrue(c.official_viz.snapshot()['scan_cleanup_required'])
            self.assertEqual(c.official_viz.snapshot()['sdk_devices'],[])
            self.assertEqual(c.factory.call_count,1)
            first.close.return_value=True;c.official_viz.stop()
            self.assertEqual(cli_coordination.reservation(c.official_viz.folder),{})
            token=c.official_viz._begin_scan();c.official_viz._collect_scan(token,True,True)
        self.assertEqual(c.official_viz.snapshot()['sdk_devices'],[HAND2])
        self.assertEqual(c.factory.call_count,2);self.assertEqual(c.state['connection'],'disconnected')

    def test_runtime_not_ready_keeps_native_devices_and_never_installs_or_launches_sdk(self):
        c,_=self.root()
        with patch('bridge_config.load_config',return_value=CONFIG),patch('managed_runtime.status',return_value=dict(ready=False,busy=False)), \
                patch('calibration_cli.cli_json',return_value={'devices':[GLOVE]}),patch('managed_runtime.start_install') as install:
            token=c.official_viz._begin_scan();c.official_viz._collect_scan(token,True,True)
        self.assertEqual(c.official_viz.snapshot()['devices'],[GLOVE])
        self.assertEqual(c.official_viz.snapshot()['sdk_devices'],[])
        self.assertTrue(c.official_viz.snapshot()['sdk_error']);c.factory.assert_not_called();install.assert_not_called()

    def test_existing_exact_sdk_preview_uses_only_its_original_hand_scan(self):
        from test_glove_hand_scan import GloveHandScanTests
        from device_profiles import profile
        c,_=self.root();c.glove=GloveHandScanTests().bridge();c.state['device_profile']=profile('hand2_left')
        with patch('managed_runtime.status',return_value=dict(ready=True,busy=False)):
            self.assertTrue(c.device_inventory()['scan_allowed'])
            c.action(dict(name='device_inventory_scan'))
        self.assertEqual(json.loads(c.glove.stdin.getvalue()),dict(name='glove_hand_scan'))
        self.assertEqual(c.glove.session,7);c.factory.assert_not_called()

    def test_sdk_hand_with_unknown_side_uses_only_same_generation_mapping_intent(self):
        from connection_switch import ConnectionSwitch
        from task_sessions import context_digest
        from device_profiles import profile
        state=dict(connection='disconnected',hardware={'active':False},device_profile=profile('hand1_right'),glove={},official_viz={})
        result=build_inventory(viz=dict(sdk_devices=[HAND1],sdk_scanned_at=123.),now=124.)
        c=N(calibration_handover=N(workflow_lock=threading.RLock()),teleoperation_handover=N(_runtime=Mock()),
            device_inventory=Mock(return_value=dict(result,workspace_errors=[])))
        switch=ConnectionSwitch(c)
        command=dict(name='connection_switch',request_id='a'*32,expected_owner=context_digest(state),
                     target=dict(mode='hand',serial=HAND1['serial'],profile='hand1_left'))
        selected=switch._target(command,state,None)
        self.assertEqual(selected['profile'],'hand1_left')
        self.assertIsNone(result['devices'][0]['side']);self.assertFalse(result['devices'][0]['identity_verified'])
        command['target']['profile']='hand2_left'
        with self.assertRaises(ValueError):switch._target(command,state,None)

    def test_agent_discover_uses_same_finite_operation_without_spawning_a_device_worker(self):
        import console_agent
        events=[];manager=Mock();process=Mock(side_effect=AssertionError('No device worker'))
        sdk=N(SdkManager=N(instance=lambda:manager))
        with patch.dict(sys.modules,wuji_sdk=sdk), \
                patch('console_agent.mp.get_context',return_value=N(Queue=queue.Queue,Process=process)), \
                patch('console_agent.select.select',return_value=([object()],[],[])), \
                patch('console_agent.sys.stdin',io.StringIO('{"name":"discover","include_gloves":true}\n')), \
                patch('sdk_runtime.probe_runtime',return_value={}),patch('console_agent.emit',side_effect=events.append), \
                patch('device_discovery.scan_only',return_value=[HAND2,GLOVE]) as scan:
            console_agent.main()
        scan.assert_called_once_with(manager,'',include_gloves=True)
        self.assertEqual(events[-1],dict(type='discovery',devices=[HAND2,GLOVE]))
        process.assert_not_called();manager.connect.assert_not_called()

    def test_include_gloves_is_an_explicit_boolean_not_a_hidden_probe_option(self):
        from console_agent import validate_command
        for value in ('true',1,None):
            with self.subTest(value=value),self.assertRaises(ValueError):
                validate_command(dict(name='discover',include_gloves=value))

    def test_original_sdk_scan_can_include_typed_usb_hands_and_gloves_without_probing(self):
        import device_discovery
        rows=[N(sn='FIRST',device_type='DeviceType.WujiHand',address='usb://first'),
              N(sn=HAND2['serial'],device_type='DeviceType.WujiHand2',address=HAND2['address']),
              N(sn=GLOVE['serial'],device_type='DeviceType.WujiGlove',address=GLOVE['address']),
              N(sn='WH2JA00000000002',device_type='DeviceType.Unknown',address='zenoh://WH2JA00000000002')]
        manager=Mock();manager.scan.return_value=rows
        sdk=N(DeviceType=N(WujiGlove='DeviceType.WujiGlove'))
        with patch.dict(sys.modules,wuji_sdk=sdk),patch('device_discovery.managed_wsl',return_value=False), \
                patch('device_discovery.time.monotonic',side_effect=[0.,.1,3.1]),patch('device_discovery.time.sleep'):
            found=device_discovery.scan_only(manager,include_gloves=True)
        self.assertEqual({r['serial'] for r in found},{'FIRST',HAND2['serial'],GLOVE['serial']})
        self.assertEqual(next(r for r in found if r['serial']==GLOVE['serial'])['device_type'],'wuji_glove')
        manager.scan.assert_called_once();manager.connect.assert_not_called()

    def client(self,rows,*,closed=True):
        stdin=io.StringIO()
        stdout=iter(['WUJI_JSON:'+json.dumps(dict(type='ready'))+'\n',
                     'WUJI_JSON:'+json.dumps(dict(type='discovery',devices=rows))+'\n'])
        client=N(agent_command='SYNTHETIC-READ-ONLY',close=Mock(return_value=closed))
        client.exec_command=Mock(return_value=(stdin,stdout,io.StringIO()))
        return client,stdin

    def test_finite_sdk_reader_emits_only_original_discover_and_accepts_typed_devices(self):
        client,stdin=self.client([HAND1,HAND2,GLOVE])
        result=discovery.sdk_devices(lambda:client,cancelled=lambda:False)
        self.assertEqual(json.loads(stdin.getvalue()),{'name':'discover','include_gloves':True})
        self.assertEqual({r['serial'] for r in result['devices']},{r['serial'] for r in (HAND1,HAND2,GLOVE)})
        self.assertIsInstance(result['observed_at'],float);client.close.assert_called()

    def test_reader_rejects_untyped_or_mixed_identity_without_publishing(self):
        for rows in ([dict(serial='SYNTHETIC-UNKNOWN',address='usb://unknown')],
                     [dict(HAND2,device_type='wuji_glove')],
                     [dict(HAND2),dict(HAND2,generation='hand1')]):
            client,_=self.client(rows)
            with self.subTest(rows=rows),self.assertRaises(ValueError):
                discovery.sdk_devices(lambda:client,cancelled=lambda:False)
            client.close.assert_called()

    def test_unconfirmed_transport_retains_exact_private_owner_for_explicit_cleanup(self):
        client,_=self.client([HAND2],closed=False)
        with self.assertRaises(discovery.DiscoveryCleanupUnconfirmed) as caught:
            discovery.sdk_devices(lambda:client,cancelled=lambda:False)
        owned=caught.exception.owned;self.assertIsNotNone(owned)
        self.assertFalse(owned.close(timeout=.1))
        client.close.return_value=True;self.assertTrue(owned.close(timeout=.1))

    def test_cancel_before_factory_never_launches_agent(self):
        factory=Mock(side_effect=AssertionError('No transport after cancellation'))
        with self.assertRaises(discovery.DiscoveryDeferred):discovery.sdk_devices(factory,cancelled=lambda:True)
        factory.assert_not_called()

    def viewer(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        viewer=OfficialVisualization(Path(temporary.name))
        viewer.supplement_scan=Mock(return_value=dict(devices=[copy.deepcopy(HAND2)],observed_at=123.))
        return viewer

    def test_directory_scan_fuses_sdk_with_native_glove_without_lending_viewer_authority(self):
        viewer=self.viewer()
        with patch('bridge_config.load_config',return_value=CONFIG), \
                patch('calibration_cli.cli_json',return_value={'devices':[copy.deepcopy(GLOVE)]}), \
                patch('official_visualization.time.time',return_value=123.), \
                patch('official_visualization.threading.Thread',ImmediateThread):
            state=viewer.scan(supplement=True)
        self.assertEqual(state['devices'],[GLOVE]);self.assertEqual(state['sdk_devices'],[HAND2])
        viewer.supplement_scan.assert_called_once()
        inventory=build_inventory(viz=state,now=124.)
        by_sn={r['sn']:r for r in inventory['devices']}
        self.assertTrue(by_sn[GLOVE['serial']]['official_available'])
        self.assertFalse(by_sn[HAND2['serial']]['official_available'])
        self.assertTrue(by_sn[HAND2['serial']]['selectable'])

    def test_inline_authoritative_native_read_never_invokes_supplement(self):
        viewer=self.viewer()
        with patch('bridge_config.load_config',return_value=CONFIG),patch('calibration_cli.cli_json',return_value={'devices':[]}):
            self.assertEqual(viewer.discover_inline(),[])
        viewer.supplement_scan.assert_not_called()

    def test_supplement_cancellation_does_not_publish_late_rows_or_discard_native_success(self):
        viewer=self.viewer()
        def supplement(*args):
            viewer.scan_cancel.set()
            return dict(devices=[copy.deepcopy(HAND2)],observed_at=123.)
        viewer.supplement_scan.side_effect=supplement
        with patch('bridge_config.load_config',return_value=CONFIG), \
                patch('calibration_cli.cli_json',return_value={'devices':[]}), \
                patch('official_visualization.threading.Thread',ImmediateThread):
            state=viewer.scan(supplement=True)
        self.assertEqual(state.get('sdk_devices'),[])
        self.assertIsNone(state.get('sdk_scanned_at'))
        self.assertFalse(state['scanning'])


if __name__=='__main__':unittest.main()
