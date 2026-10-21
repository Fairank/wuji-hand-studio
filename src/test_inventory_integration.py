"""Directory/controller integration. All observations and discovery are fake."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from console_server import Controller,ConsoleHTTPServer
from official_visualization import OfficialVisualization
from cli_coordination import gate,reserve,release


class InventoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.c=Controller(reports=self.root/'reports')
        self.c.official_viz=OfficialVisualization(self.root/'cli')
        self.row=dict(serial='SYNTHETIC-GLOVE',device_type='WujiGlove',handedness='right',address='192.168.1.101:50001')
        self.c.official_viz.state.update(devices=[self.row],scanned_at=time.time())

    def test_passive_directory_never_runs_discovery_or_connect(self):
        with patch('calibration_cli.cli_json') as cli,patch.object(self.c,'factory') as factory:
            result=self.c.device_inventory()
        cli.assert_not_called();factory.assert_not_called()
        self.assertTrue(result['scan_allowed']);self.assertEqual(result['devices'][0]['sn'],self.row['serial'])
        self.assertEqual(result['devices'][0]['side'],'right')

    def test_alias_dispatches_only_shared_scanner(self):
        with patch.object(self.c.official_viz,'scan',return_value={}) as scan,patch.object(self.c.glove,'action') as glove:
            self.c.action(dict(name='device_inventory_scan'))
        scan.assert_called_once_with();glove.assert_not_called()

    def test_preflight_blocker_does_not_silently_end_preview(self):
        self.c.official_viz.idle_check=Mock(side_effect=ValueError('owning workspace'))
        with patch.object(self.c.glove,'action') as action:
            result=self.c.device_inventory()
        self.assertFalse(result['scan_allowed']);self.assertIn('owning workspace',result['blocked_reason']);action.assert_not_called()

    def test_global_reservation_blocks_advice_but_preserves_readable_rows(self):
        with gate(self.c.official_viz.folder):token=reserve('scan',self.c.official_viz.folder)
        try:
            result=self.c.device_inventory();self.assertFalse(result['scan_allowed']);self.assertEqual(len(result['devices']),1)
        finally:
            with gate(self.c.official_viz.folder):release(token,self.c.official_viz.folder)

    def test_calibration_passive_provider_rejects_stale_failed_pending(self):
        self.assertEqual(self.c.calibration_devices()[0]['sn'],self.row['serial'])
        for changes in (dict(scanned_at=time.time()-31),dict(scanned_at=time.time()+1),dict(error='scan failed'),dict(scanning=True)):
            self.c.official_viz.state.update(scanned_at=time.time(),error=None,scanning=False)
            self.c.official_viz.state.update(changes)
            self.assertEqual(self.c.calibration_devices(),[])

    def test_calibration_fresh_provider_calls_single_inline_scan(self):
        with patch.object(self.c.official_viz,'discover_inline',return_value=[self.row]) as scan:
            self.assertEqual(self.c.calibration_devices(True)[0]['sn'],self.row['serial']);scan.assert_called_once_with()

    def test_viewer_status_does_not_enable_start_during_occupied_workspace(self):
        self.c.official_viz.idle_check=Mock(side_effect=ValueError('glove owns session'))
        result=self.c.visualization_status()
        self.assertFalse(result['can_start']);self.assertIn('glove owns',result['blocked_reason'])

    def test_server_wires_metadata_guard_without_self_blocking_scan(self):
        # configure_official_cli only installs callbacks; no listening socket.
        server=object.__new__(ConsoleHTTPServer);server.controller=self.c
        with patch('managed_runtime.status',return_value=dict(busy=False)):
            server.configure_official_cli(startup=False)
            with gate(self.c.official_viz.folder):token=reserve('viz',self.c.official_viz.folder)
            try:
                with self.assertRaisesRegex(ValueError,'CLI'):
                    self.c.calibration.metadata_check()
                # Actual discovery owns a reservation before idle_check; that
                # callback must not reject its own token.
                self.c.official_viz.idle_check()
            finally:
                with gate(self.c.official_viz.folder):release(token,self.c.official_viz.folder)

    def test_selection_observation_gets_real_timestamp(self):
        self.c.event(dict(type='selection',devices=[dict(serial='SYNTHETIC-HAND',generation='hand1')],message='Choose a hand'),self.c.generation)
        self.assertLess(abs(time.time()-self.c.state['devices_at']),1)

    def test_canonical_duplicate_conflict_cannot_start_viewer(self):
        self.c.official_viz.state['devices']=[self.row,{**self.row,'handedness':'left'}]
        with patch('bridge_config.load_config',return_value=dict(mode='local')),patch('official_visualization.threading.Thread') as worker:
            with self.assertRaisesRegex(ValueError,'conflicting'):
                self.c.official_viz.start(self.row['serial'])
        worker.assert_not_called()

    def test_failed_viewer_thread_start_leaves_retryable_idle_session(self):
        with patch('bridge_config.load_config',return_value=dict(mode='local')),patch('official_visualization.threading.Thread') as worker:
            worker.return_value.start.side_effect=RuntimeError('synthetic start failure')
            with self.assertRaisesRegex(RuntimeError,'synthetic'):
                self.c.official_viz.start(self.row['serial'])
        from cli_coordination import reservation
        self.assertEqual(reservation(self.c.official_viz.folder),{})
        self.assertFalse(self.c.official_viz.state['busy']);self.assertIsNone(self.c.official_viz.token)

    def test_subworkspace_ownership_is_passive_and_does_not_claim_fresh_feedback(self):
        child=dict(glove=dict(busy=True,connection='receiving',glove_serial=self.row['serial'],profile='hand2_right',stream=dict(fresh=True)))
        self.c.inventory_observer=lambda:[dict(id='child',label='Other workspace',state=child)]
        result=self.c.device_inventory()
        self.assertTrue(result['devices'][0]['in_use'])
        self.assertEqual(result['devices'][0]['workspace_owners'][0]['id'],'child')
        self.assertTrue(result['devices'][0]['identity_verified'])

    def test_disconnected_old_hand_id_is_not_owned_by_glove_preview(self):
        self.c.official_viz.state['devices'].append(dict(sn='SYNTHETIC-HAND',type='WujiHand'))
        self.c.inventory_observer=lambda:[dict(id='child',state=dict(connection='disconnected',device_id='SYNTHETIC-HAND',glove=dict(connection='receiving')))]
        result=self.c.device_inventory()
        self.assertFalse(next(row for row in result['devices'] if row['sn']=='SYNTHETIC-HAND')['in_use'])

    def test_calibration_provider_drops_conflicting_serial_in_both_modes(self):
        bad=[self.row,{**self.row,'handedness':'left'}]
        self.c.official_viz.state['devices']=bad
        self.assertEqual(self.c.calibration_devices(),[])
        with patch.object(self.c.official_viz,'discover_inline',return_value=bad):
            self.assertEqual(self.c.calibration_devices(True),[])

    def test_child_only_verified_hand_appears_without_creating_workspace(self):
        child=dict(connection='connected',stale=False,device_id='SYNTHETIC-HAND',device_profile=dict(id='hand1_left',generation='hand1',side='left'),
                   identified_device=dict(serial='SYNTHETIC-HAND',profile='hand1_left',source='sdk_identity'))
        self.c.inventory_observer=Mock(return_value=[dict(id='child',label='Other hand',state=child)])
        result=self.c.device_inventory();row=next(row for row in result['devices'] if row['sn']=='SYNTHETIC-HAND')
        self.assertTrue(row['in_use']);self.assertTrue(row['identity_verified']);self.assertEqual(row['workspace_owners'][0]['id'],'child')

    def test_child_unavailable_cannot_claim_scan_is_allowed(self):
        self.c.inventory_observer=lambda:[dict(id='child',state=None)]
        self.assertFalse(self.c.device_inventory()['scan_allowed'])

    def test_reading_child_inventory_never_creates_a_workspace(self):
        import threading
        from device_fleet import DeviceFleet
        fleet=object.__new__(DeviceFleet);fleet.lock=threading.RLock();fleet.children={'child':dict(id='child',label='Child')}
        fleet.get=Mock(return_value=dict(connection='disconnected'))
        with patch.object(fleet,'create') as create:
            result=fleet.inventory_snapshots()
        create.assert_not_called();self.assertEqual(result[0]['state']['connection'],'disconnected')
        fleet.get.side_effect=OSError('synthetic offline')
        self.assertIsNone(fleet.inventory_snapshots()[0]['state'])

if __name__=='__main__':unittest.main()
