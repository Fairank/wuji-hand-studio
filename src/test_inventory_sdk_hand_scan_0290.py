"""One shared directory reports the retained SDK hand search truthfully."""
import copy
import tempfile
from pathlib import Path
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock

from console_server import Controller


class SDKHandSearchInventoryTests(unittest.TestCase):
    def fixture(self, *, searching=False):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        glove=dict(busy=True,connection='receiving',glove_serial='WG-SYNTHETIC',
            profile='hand2_right',user={'user_id':'Wearer'},stream={'fresh':True},
            hands=[],hands_at=123.,hand_scanning=searching,can_prepare=not searching)
        state=dict(connection='disconnected',hardware={'active':False},glove=glove,
            official_viz=dict(devices=[],supported=True,scanning=False,
                scanned_at=45.,scan_completed_at=46.,backend='windows_native'),
            connection_switch={'can_switch':True})
        c=N(snapshot=lambda:copy.deepcopy(state),official_viz=N(folder=Path(temp.name),
            check_scan=Mock(side_effect=ValueError('SDK preview owns device'))),
            sdk_hand_scan_check=Mock())
        def eligibility():
            if not glove['can_prepare']:raise ValueError('Not an idle SDK preview')
        c.sdk_hand_scan_check.side_effect=eligibility
        return c,state,glove

    def test_running_sdk_hand_search_is_busy_without_requesting_glove_disconnect(self):
        c,state,glove=self.fixture(searching=True)
        row=Controller.device_inventory(c)
        self.assertTrue(row['scanning'])
        self.assertFalse(row['scan_allowed'])
        self.assertEqual(row['backend'],'sdk_session')
        self.assertIn('discovery is running',row['blocked_reason'])
        self.assertNotIn('disconnect',row['blocked_reason'])
        self.assertEqual(row['scanned_at'],123.)
        self.assertEqual(state['glove'],glove)

    def test_completed_sdk_search_uses_its_own_clock_and_not_previous_cli_scan(self):
        c,state,glove=self.fixture()
        row=Controller.device_inventory(c)
        self.assertTrue(row['scan_allowed'])
        self.assertFalse(row['scanning'])
        self.assertEqual(row['backend'],'sdk_session')
        self.assertEqual(row['scanned_at'],123.)
        self.assertEqual(row['scan_completed_at'],123.)
        self.assertIsNone(row['blocked_reason'])
        c.sdk_hand_scan_check.assert_called_once_with()

    def test_sdk_scan_does_not_hide_an_independent_cli_owner_or_scan(self):
        c,state,glove=self.fixture(searching=True)
        state['official_viz']['scanning']=True
        row=Controller.device_inventory(c)
        self.assertTrue(row['scanning']);self.assertFalse(row['scan_allowed'])
        self.assertEqual(row['backend'],'windows_native')
        self.assertEqual(row['scanned_at'],45.)
        c.sdk_hand_scan_check.assert_not_called()

    def test_unavailable_preview_keeps_discovery_blocked(self):
        c,state,glove=self.fixture();glove['can_prepare']=False
        row=Controller.device_inventory(c)
        self.assertFalse(row['scan_allowed']);self.assertFalse(row['scanning'])
        self.assertNotEqual(row['backend'],'sdk_session')

    def test_current_sdk_search_owns_error_instead_of_previous_cli_failure(self):
        for error in ('','Synthetic current hand search failed'):
            with self.subTest(error=error):
                c,state,glove=self.fixture();state['official_viz']['error']='Synthetic previous CLI failure'
                glove['hand_scan_error']=error
                row=Controller.device_inventory(c)
                self.assertEqual(row['error'],error or None)


if __name__=='__main__':unittest.main()
