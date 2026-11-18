"""Scoped stops cannot cascade into a replacement hand or group.

Controller dispatch, playlist threads and local preview players are real;
the queued transport supplies synthetic worker feedback/disable ACKs only.
"""
import unittest

from gesture_library import CATALOG
import test_group_abort_owner_0284 as owner_fixture


class ScopedStopTests(unittest.TestCase):
    def setUp(self):
        self.owner=owner_fixture.GroupAbortOwnerTests('test_readonly_prepare_abort_sends_no_hardware_stop')
        self.owner.setUp();self.addCleanup(self.owner.doCleanups)
        self.c=self.owner.c;self.transport=self.owner.transports[-1]
        self.addCleanup(self.finish_program)

    def finish_program(self):
        self.transport.stop_mode='ack';self.c.program.stop()
        if self.c.program.thread:self.c.program.thread.join(3)

    def start_program(self,mode='preview'):
        self.transport.ready();self.owner.wait(lambda:not self.c.snapshot()['stale'])
        action=next(row['id'] for row in CATALOG if row['group']=='dance')
        result=self.c.action(dict(name='program_start',mode=mode,workspace_clear=True,
            amplitude=.5,plan=dict(entries=[dict(action=action,speed=1,cycles=1)],repeats=1)))
        if mode=='preview':self.owner.wait(lambda:self.c.player.snapshot()['active'])
        else:self.owner.wait(lambda:self.c.hardware_lease and self.c.state['hardware'].get('active') is True)
        return result['program_run_id']

    def stop_program(self,run):
        self.c.action(dict(name='program_stop',expected_run=run))
        self.c.program.thread.join(3)
        self.assertFalse(self.c.program.thread.is_alive())
        self.assertFalse(self.c.program.snapshot()['active'])

    def preview(self,action='open'):
        self.c.action(dict(name='demo_start',action=action,speed=1,cycles=0))
        return self.c.player.run_id

    def assert_hardware_unchanged(self,lease,before):
        self.assertEqual(self.transport.sent,before)
        self.assertEqual(self.c.hardware_lease,lease)
        self.assertIs(self.c.state['hardware']['active'],True)

    def test_inactive_preview_program_stop_cannot_stop_new_probe(self):
        old=self.start_program();self.stop_program(old)
        lease=self.owner.probe();before=list(self.transport.sent)
        self.c.action(dict(name='program_stop',expected_run=old))
        self.assert_hardware_unchanged(lease,before)

    def test_inactive_hardware_program_stop_cannot_stop_new_probe(self):
        old=self.start_program('hardware');self.stop_program(old)
        lease=self.owner.probe();before=list(self.transport.sent)
        self.c.action(dict(name='program_stop',expected_run=old))
        self.assert_hardware_unchanged(lease,before)

    def test_inactive_program_stop_cannot_stop_new_hardware_group(self):
        old=self.start_program();self.stop_program(old)
        token=self.owner.prepare();lease=self.owner.arm(token);before=list(self.transport.sent)
        self.c.action(dict(name='program_stop',expected_run=old))
        self.assert_hardware_unchanged(lease,before);self.assertTrue(self.c.group.active)

    def test_inactive_hardware_program_cannot_stop_a_reconnected_hand(self):
        old=self.start_program('hardware');self.stop_program(old)
        self.c.action(dict(name='disconnect'));self.owner.connect('SYNTHETIC-NEW-PROGRAM-HAND')
        self.transport=self.owner.transports[-1]
        lease=self.owner.probe();before=list(self.transport.sent)
        self.c.action(dict(name='program_stop',expected_run=old))
        self.assert_hardware_unchanged(lease,before)

    def test_old_program_stop_cannot_cancel_a_new_program_run(self):
        old=self.start_program();self.stop_program(old)
        current=self.start_program('hardware');lease=self.c.hardware_lease
        before=self.transport.disable_receipts
        with self.assertRaisesRegex(ValueError,'Playlist changed'):
            self.c.action(dict(name='program_stop',expected_run=old))
        self.assertNotEqual(old,current);self.assertFalse(self.c.program.cancel.is_set())
        self.assertEqual(self.c.hardware_lease,lease);self.assertEqual(self.transport.disable_receipts,before)

    def test_mismatched_demo_stop_is_checked_before_hardware_group_cascade(self):
        old=self.preview();self.c.action(dict(name='demo_stop',expected_run=old))
        self.preview('fist');token=self.owner.prepare();lease=self.owner.arm(token)
        before=list(self.transport.sent)
        with self.assertRaisesRegex(ValueError,'Preview changed'):
            self.c.action(dict(name='demo_stop',expected_run=old))
        self.assert_hardware_unchanged(lease,before);self.assertTrue(self.c.group.active)

    def test_matching_retained_demo_id_does_not_own_new_hardware_group(self):
        old=self.preview();self.c.action(dict(name='demo_stop',expected_run=old))
        token=self.owner.prepare();lease=self.owner.arm(token);before=list(self.transport.sent)
        self.assertEqual(self.c.player.run_id,old)
        self.c.action(dict(name='demo_stop',expected_run=old))
        self.assert_hardware_unchanged(lease,before);self.assertTrue(self.c.group.active)

    def test_current_preview_group_scoped_stop_releases_its_own_reservation(self):
        token=self.owner.prepare(mode='preview');run=self.c.player.run_id
        self.c.action(dict(name='demo_stop',expected_run=run))
        self.assertFalse(self.c.group.active);self.assertFalse(self.c.player.snapshot()['active'])

    def test_unscoped_demo_stop_retains_explicit_global_group_stop(self):
        token=self.owner.prepare();self.owner.arm(token)
        before=self.transport.disable_receipts
        self.c.action(dict(name='demo_stop'))
        self.owner.wait(lambda:self.c.hardware_lease is None)
        self.assertFalse(self.c.group.active);self.assertGreater(self.transport.disable_receipts,before)

    def test_unscoped_program_stop_retains_legacy_global_hardware_stop(self):
        run=self.start_program();self.stop_program(run);self.owner.probe()
        before=self.transport.disable_receipts;self.c.action(dict(name='program_stop'))
        self.owner.wait(lambda:self.c.hardware_lease is None)
        self.assertGreater(self.transport.disable_receipts,before)

    def test_inactive_hardware_program_can_retry_its_original_unknown_stop(self):
        run=self.start_program('hardware');lease=self.c.hardware_lease
        self.transport.stop_mode='unknown';self.stop_program(run)
        self.owner.wait(lambda:self.c.state['hardware'].get('active') is None)
        self.assertEqual(self.c.hardware_lease,lease);self.assertTrue(self.c.termination.snapshot())
        before=self.transport.disable_receipts;self.transport.stop_mode='ack'
        self.c.action(dict(name='program_stop',expected_run=run))
        self.owner.wait(lambda:self.c.hardware_lease is None)
        self.assertGreater(self.transport.disable_receipts,before)
        self.assertEqual(self.c.termination.snapshot(),[])

    def test_partial_hardware_program_start_can_retry_its_own_stop(self):
        self.transport.fail_start='flush';self.transport.stop_mode='unknown'
        # A queued start may reach the worker before the caller receives an error.
        action=next(row['id'] for row in CATALOG if row['group']=='dance')
        result=self.c.action(dict(name='program_start',mode='hardware',workspace_clear=True,
            amplitude=.5,plan=dict(entries=[dict(action=action,speed=1,cycles=1)],repeats=1)))
        self.c.program.thread.join(3);self.assertFalse(self.c.program.thread.is_alive())
        self.assertIsNotNone(self.c.hardware_lease);self.assertTrue(self.c.termination.snapshot())
        self.transport.fail_start=None;self.transport.stop_mode='ack'
        self.c.action(dict(name='program_stop',expected_run=result['program_run_id']))
        self.owner.wait(lambda:self.c.hardware_lease is None)
        self.assertEqual(self.c.termination.snapshot(),[])


if __name__=='__main__':unittest.main()
