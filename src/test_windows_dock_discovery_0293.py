"""Windows topology evidence is independent from the official device identity."""
import copy
import tempfile
import time
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from official_visualization import OfficialVisualization


CONFIG = dict(mode='wsl',cli='wuji')
OLD = dict(sn='WH2K_TEST_A',device_type='wuji_hand2',address='192.168.1.111:7447')
NEW = dict(sn='WG_TEST_B',device_type='wuji_glove',address='192.168.1.101:50001')


class DockDiscoveryTests(TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory(prefix='dock-scope-')
        self.addCleanup(directory.cleanup)
        self.viewer=OfficialVisualization(Path(directory.name)/'cli')
        self.revision=1
        for instrument in (
            patch('bridge_config.load_config',return_value=copy.deepcopy(CONFIG)),
            patch.object(self.viewer,'_network_revision',side_effect=lambda *_args,**_kwargs:self.revision,create=True),
        ):
            instrument.start()
            self.addCleanup(instrument.stop)

    def scan(self, rows, supplement=None):
        self.viewer.supplement_scan=supplement
        with patch('calibration_cli.cli_json',return_value={'devices':rows}) as cli:
            token=self.viewer._begin_scan()
            result=self.viewer._collect_scan(token,True,supplement is not None)
            self.assertEqual(cli.call_count,1)
        return result

    def test_new_adapter_invalidates_old_devices_without_expiring_live_session(self):
        self.assertEqual(self.scan([OLD]),[OLD])
        self.viewer.state.update(active=True,phase='running',device=OLD)
        self.revision=2  # The USB dock's Ethernet adapter was re-enumerated.
        state=self.viewer.snapshot()
        self.assertEqual(state['devices'],[])
        self.assertIsNone(state['scanned_at'])
        self.assertIsNone(state['scan_completed_at'])
        self.assertFalse(state['devices_fresh'])
        self.assertTrue(state['active'])
        self.assertEqual(state['device'],OLD)

    def test_late_old_dock_result_cannot_replace_fresh_directory(self):
        def late_supplement(*_):
            self.assertEqual(self.viewer.snapshot()['devices'],[OLD])
            self.revision=2
            return dict(devices=[OLD],observed_at=time.time())
        self.assertIsNone(self.scan([OLD],late_supplement))
        state=self.viewer.snapshot()
        self.assertFalse(state['scanning'])
        self.assertIsNone(state['scan_completed_at'])
        self.assertEqual((state['devices'],state['sdk_devices']),([],[]))
        self.assertEqual(self.scan([NEW]),[NEW])
        self.assertEqual(self.viewer.snapshot()['devices'],[NEW])

    def test_topology_change_cancels_existing_read_and_releases_its_owner(self):
        from cli_coordination import reservation
        from official_device_discovery import DiscoveryDeferred
        def supplement(_,cancelled,_token):
            self.assertFalse(cancelled())
            self.revision=2
            self.assertTrue(cancelled())
            raise DiscoveryDeferred('Controller acknowledged cancellation')
        self.assertIsNone(self.scan([OLD],supplement))
        self.assertFalse(reservation(self.viewer.folder))
        state=self.viewer.snapshot()
        self.assertEqual(state['network_revision'],2)
        self.assertIsNone(state['scan_completed_at'])
        self.assertFalse(state['scanning'])


class TopologyRevisionTests(TestCase):
    def test_same_interface_index_with_new_guid_is_a_new_topology(self):
        from windows_ethernet import TopologyRevision
        diagnostic=[('dock-one',14,1,('192.168.1.50/24',)),('dock-two',55,1,('192.168.1.51/24',))]
        tracker=TopologyRevision(lambda:tuple(sorted(diagnostic)))
        initial=tracker.read(refresh=True)
        diagnostic.reverse()
        self.assertEqual(tracker.read(refresh=True),initial)
        diagnostic[0]=('replacement-dock',55,1,('192.168.1.51/24',))
        self.assertGreater(tracker.read(refresh=True),initial)

    def test_temporary_unreadable_topology_invalidates_at_most_once(self):
        from windows_ethernet import TopologyRevision
        with patch('windows_ethernet.time.monotonic',return_value=5):
            from unittest.mock import Mock
            tracker=TopologyRevision(Mock(side_effect=[(('a',1,1,()),),OSError('adapter moved'),OSError('unreadable'),(('a',1,1,()),)]))
            before=tracker.read(refresh=True)
            degraded=tracker.read(refresh=True)
            self.assertGreater(degraded,before)
            self.assertEqual(tracker.read(refresh=True),degraded)
            self.assertGreater(tracker.read(refresh=True),degraded)
