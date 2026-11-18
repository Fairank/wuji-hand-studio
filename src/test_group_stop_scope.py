"""Automatic cleanup cannot stop a replacement group's exact reservation."""
from pathlib import Path
import tempfile
import unittest

import test_workspace_groups as fixture
from workspace_groups import WorkspaceGroups


class GroupStopScopeTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.groups=WorkspaceGroups(fixture.FakeFleet(),fixture.FakeController(),Path(folder.name))
        self.run=self.groups.coordinator('main');self.addCleanup(self.run.pool.shutdown)
        self.run.token='b'*32;self.run.update(active=True,phase='playing')

    def stop(self,**fields):return self.groups.action(dict(name='ensemble_stop',workspace='main',**fields))

    def test_old_cleanup_lease_is_refused_without_cancelling_replacement(self):
        with self.assertRaisesRegex(ValueError,'lease'):self.stop(lease='a'*32)
        self.assertFalse(self.run.cancel.is_set());self.assertTrue(self.run.snapshot()['active'])

    def test_exact_lease_cleanup_and_explicit_global_stop_remain_available(self):
        self.assertTrue(self.stop(lease='b'*32)['stopping']);self.assertTrue(self.run.cancel.is_set())
        self.run.cancel.clear()
        self.assertTrue(self.stop()['stopping']);self.assertTrue(self.run.cancel.is_set())

    def test_malformed_lease_cannot_fall_back_to_global_stop(self):
        for lease in (None,False,1,{},'', '\u65e7\u4efb\u52a1'):
            with self.subTest(lease=lease),self.assertRaises(ValueError):self.stop(lease=lease)
            self.assertFalse(self.run.cancel.is_set())

    def test_old_lease_after_idle_then_new_start_does_not_authorize_stop(self):
        self.run.update(active=False,phase='completed');self.run.token='a'*32
        self.stop(lease='a'*32);self.run.cancel.clear()
        self.run.token='c'*32;self.run.update(active=True,phase='preparing')
        with self.assertRaises(ValueError):self.stop(lease='a'*32)
        self.assertFalse(self.run.cancel.is_set());self.assertEqual(self.run.snapshot()['phase'],'preparing')


if __name__=='__main__':unittest.main()
