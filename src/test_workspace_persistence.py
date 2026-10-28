"""No false workspace changes or lost child ownership on disk/cleanup failure."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
from workspace_groups import WorkspaceGroups
from test_workspace_groups import FakeFleet,FakeController


class WorkspacePersistenceTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name);self.fleet=FakeFleet();self.controller=FakeController()
        self.groups=WorkspaceGroups(self.fleet,self.controller,self.path)
        self.a=self.act('workspace_create',label='A')['workspace']['id']
        self.b=self.act('workspace_create',label='B')['workspace']['id']

    def act(self,name,**payload):return self.groups.action(dict(name=name,**payload))
    def saved(self):return self.groups.path.read_bytes()
    def member(self,workspace=None):return self.act('workspace_add',workspace=workspace or self.a,preview_profile='hand2_left')['member']
    def row(self,ident):return next(r for r in self.groups.rows if r['id']==ident)
    def discovered(self):
        self.groups.discovery.state.update(updated=time.time(),devices=[dict(serial='QA-HAND-ONLY',generation='hand2',side_hint='left')])
        return 'QA-HAND-ONLY'

    def test_failed_creation_keeps_memory_and_disk_then_one_retry_succeeds(self):
        before=copy.deepcopy(self.groups.rows);saved=self.saved()
        with patch.object(self.groups,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.act('workspace_create',label='Retry')
        self.assertEqual(self.groups.rows,before);self.assertEqual(self.saved(),saved)
        self.act('workspace_create',label='Retry')
        self.assertEqual(sum(r['label']=='Retry' for r in self.groups.rows),1)

    def test_move_and_remove_failures_keep_existing_membership(self):
        member=self.member();before=copy.deepcopy(self.groups.rows);saved=self.saved()
        for command in (dict(name='workspace_move',workspace=self.b,member=member),dict(name='workspace_remove',workspace=self.b)):
            with self.subTest(command=command),patch.object(self.groups,'save',side_effect=OSError('replace denied')):
                with self.assertRaises(OSError):self.groups.action(command)
                self.assertEqual(self.groups.rows,before);self.assertEqual(self.saved(),saved)

    def test_unwritable_add_is_rejected_before_any_child_is_created(self):
        before=copy.deepcopy(self.groups.rows)
        with patch.object(self.groups,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.member()
        self.assertEqual(self.fleet.created,[]);self.assertEqual(self.groups.rows,before)

    def test_late_add_commit_failure_reclaims_only_its_new_child_and_can_retry(self):
        existing=self.member();before=copy.deepcopy(self.groups.rows);saved=self.saved();original=self.groups.save;calls=0
        def save():
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('late write failed')
            original()
        with patch.object(self.groups,'save',side_effect=save):
            with self.assertRaises(OSError):self.member()
        self.assertEqual(set(self.fleet.children),{existing});self.assertEqual(self.groups.rows,before)
        self.assertEqual(self.saved(),saved);self.assertEqual(len(self.fleet.removed),1)
        added=self.member();self.assertNotEqual(existing,added);self.assertIn(added,self.row(self.a)['members'])

    def test_unconfirmed_add_cleanup_stays_visible_with_error_and_can_be_forgotten(self):
        saved=self.saved();original=self.groups.save;calls=0
        def save():
            nonlocal calls
            calls+=1
            if calls>=2:raise OSError('storage remains unavailable')
            original()
        self.fleet.refuse_remove=True
        with patch.object(self.groups,'save',side_effect=save):
            with self.assertRaisesRegex(ValueError,'cleanup is unconfirmed'):self.member()
        member=next(iter(self.fleet.children))
        self.assertIn(member,self.row(self.a)['members']);self.assertEqual(self.saved(),saved)
        self.assertTrue(self.groups.snapshot()['persistence_error'])
        # A restart recovers the real unassigned child from the fleet catalog.
        reloaded=WorkspaceGroups(self.fleet,self.controller,self.path)
        self.assertTrue(any(member in r['members'] for r in reloaded.rows))
        self.fleet.refuse_remove=False
        self.act('workspace_forget',workspace=self.a,member=member)
        self.assertNotIn(member,self.fleet.children);self.assertIsNone(self.groups.persistence_error)

    def test_forget_disk_failure_does_not_touch_the_child(self):
        member=self.member();saved=self.saved()
        with patch.object(self.groups,'save',side_effect=OSError('replace denied')):
            with self.assertRaises(OSError):self.act('workspace_forget',workspace=self.a,member=member)
        self.assertEqual(self.fleet.removed,[]);self.assertIn(member,self.fleet.children)
        self.assertIn(member,self.row(self.a)['members']);self.assertEqual(self.saved(),saved)

    def test_forget_refusal_restores_catalog_and_never_returns_success(self):
        member=self.member();before=copy.deepcopy(self.groups.rows);saved=self.saved();self.fleet.refuse_remove=True
        with self.assertRaisesRegex(ValueError,'removal is unconfirmed'):self.act('workspace_forget',workspace=self.a,member=member)
        self.assertEqual(self.groups.rows,before);self.assertEqual(self.saved(),saved)
        self.assertIn(member,self.fleet.children)
        self.fleet.refuse_remove=False;self.act('workspace_forget',workspace=self.a,member=member)
        self.assertNotIn(member,self.fleet.children)

    def test_refused_forget_and_failed_rollback_keep_actual_member_recoverable(self):
        member=self.member();original=self.groups.save;calls=0;self.fleet.refuse_remove=True
        def save():
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('rollback write failed')
            original()
        with patch.object(self.groups,'save',side_effect=save):
            with self.assertRaises(ValueError):self.act('workspace_forget',workspace=self.a,member=member)
        self.assertIn(member,self.row(self.a)['members']);self.assertIn(member,self.fleet.children)
        self.assertTrue(self.groups.snapshot()['persistence_error'])
        self.fleet.refuse_remove=False;self.act('workspace_forget',workspace=self.a,member=member)
        self.assertIsNone(self.groups.snapshot()['persistence_error']);self.assertNotIn(member,self.fleet.children)

    def test_successful_forget_commits_before_child_removal_and_never_writes_after(self):
        member=self.member();original=self.fleet.remove
        def remove(ident):
            disk=json.loads(self.saved())
            self.assertFalse(any(ident in r['members'] for r in disk))
            return original(ident)
        with patch.object(self.fleet,'remove',side_effect=remove),patch.object(self.groups,'save',wraps=self.groups.save) as save:
            self.assertTrue(self.act('workspace_forget',workspace=self.a,member=member)['removed'])
            self.assertEqual(save.call_count,1)

    def test_pending_catalog_error_blocks_start_but_never_blocks_stop_or_beat(self):
        self.groups.persistence_error='Catalog recovery pending'
        coordinator=Mock();coordinator.stop.return_value={'stopped':True}
        with patch.object(self.groups,'coordinator',return_value=coordinator) as read:
            with self.assertRaisesRegex(ValueError,'Catalog recovery pending'):
                self.act('ensemble_start',workspace=self.a,members=[])
            read.assert_not_called();coordinator.start.assert_not_called()
            self.assertTrue(self.act('ensemble_stop',workspace=self.a)['stopped'])
            self.act('ensemble_beat',workspace=self.a,lease='retained-lease')
            coordinator.stop.assert_called_once();coordinator.beat.assert_called_once_with('retained-lease')

    def test_preview_camera_failure_reports_durable_addition_without_false_failure(self):
        original=self.fleet.post
        def post(record,command):
            if command['name']=='view_camera':raise OSError('camera ACK unavailable')
            return original(record,command)
        with patch.object(self.fleet,'post',side_effect=post):
            result=self.act('workspace_add',workspace=self.a,preview_profile='hand2_left')
        self.assertEqual(result['warning'],'preview_camera_unconfirmed')
        member=result['member'];self.assertIn(member,self.fleet.children)
        self.assertEqual(self.row(self.a)['members'],[member])
        self.assertEqual(len(self.fleet.created),1);self.assertEqual(self.fleet.removed,[])
        self.assertFalse(result['connecting'])
        self.assertEqual(WorkspaceGroups(self.fleet,self.controller,self.path).get(self.a)['members'],[member])

    def test_failed_connect_reclaims_only_new_child_then_explicit_retry_adds_once(self):
        keep=self.member(self.b);serial=self.discovered();original=self.fleet.post
        def post(record,command):
            response=original(record,command)
            if command['name']=='connect':raise OSError('dispatch ACK lost')
            return response
        with patch.object(self.fleet,'post',side_effect=post):
            with self.assertRaisesRegex(OSError,'ACK lost'):
                self.act('workspace_add',workspace=self.a,serial=serial)
        self.assertEqual(set(self.fleet.children),{keep});self.assertEqual(self.row(self.a)['members'],[])
        self.assertEqual(self.groups.pending_serials,{})
        added=self.act('workspace_add',workspace=self.a,serial=serial)
        self.assertTrue(added['connecting']);self.assertEqual(self.row(self.a)['members'],[added['member']])
        self.assertEqual(set(self.fleet.children),{keep,added['member']})

    def test_unconfirmed_connect_cleanup_keeps_visible_child_and_blocks_duplicate_after_error_poll(self):
        serial=self.discovered();self.fleet.refuse_remove=True;original=self.fleet.post
        def post(record,command):
            response=original(record,command)
            if command['name']=='connect':raise OSError('dispatch ACK lost')
            return response
        with patch.object(self.fleet,'post',side_effect=post):
            result=self.act('workspace_add',workspace=self.a,serial=serial)
        member=result['member']
        self.assertEqual(result['warning'],'workspace_connection_unconfirmed');self.assertIsNone(result['connecting'])
        self.assertIn(member,self.row(self.a)['members']);self.assertIn(member,self.fleet.children)
        self.fleet._states[member]['connection']='error'
        for _ in range(2):
            with self.assertRaisesRegex(ValueError,'unconfirmed session'):
                self.act('workspace_add',workspace=self.a,serial=serial)
        self.assertEqual(len(self.fleet.created),1);self.assertEqual(self.groups.pending_serials[member],serial)
        self.fleet.refuse_remove=False;self.act('workspace_forget',workspace=self.a,member=member)
        self.assertNotIn(member,self.groups.pending_serials)

    def test_connection_refusal_response_cannot_be_reported_as_connecting(self):
        serial=self.discovered();original=self.fleet.post
        def post(record,command):
            if command['name']=='connect':return dict(ok=False,error='Connection was refused')
            return original(record,command)
        with patch.object(self.fleet,'post',side_effect=post):
            with self.assertRaisesRegex(ValueError,'was refused'):
                self.act('workspace_add',workspace=self.a,serial=serial)
        self.assertFalse(self.fleet.children);self.assertEqual(self.row(self.a)['members'],[])

    def test_connect_failure_with_unwritable_cleanup_keeps_existing_card_and_serial_guard(self):
        serial=self.discovered();original=self.fleet.post
        def post(record,command):
            if command['name']=='connect':
                self.groups.save=Mock(side_effect=OSError('storage unavailable during cleanup'))
                raise OSError('ACK lost')
            return original(record,command)
        with patch.object(self.fleet,'post',side_effect=post):
            result=self.act('workspace_add',workspace=self.a,serial=serial)
        self.assertIsNone(result['connecting']);self.assertEqual(result['warning'],'workspace_connection_unconfirmed')
        self.assertEqual(self.row(self.a)['members'],[result['member']]);self.assertEqual(self.fleet.removed,[])
        self.assertEqual(self.groups.pending_serials[result['member']],serial)

    def test_primary_connect_unknown_never_deletes_main_and_requires_confirmed_disconnect(self):
        serial=self.discovered();original=self.controller.action
        def post(command):
            if command['name']=='connect':raise OSError('main ACK lost')
            return original(command)
        with patch.object(self.controller,'action',side_effect=post):
            result=self.act('workspace_add',workspace='main',serial=serial)
        self.assertEqual(result['member'],'main');self.assertIsNone(result['connecting'])
        self.assertEqual(self.row('main')['members'],['main']);self.assertEqual(self.fleet.created,[])
        with patch.object(self.controller,'action',return_value=dict(ok=False,error='Cleanup unconfirmed')):
            with self.assertRaisesRegex(ValueError,'Cleanup unconfirmed'):
                self.act('workspace_disconnect',workspace='main',member='main')
        self.assertEqual(self.groups.pending_serials['main'],serial)
        with self.assertRaisesRegex(ValueError,'unconfirmed session'):
            self.act('workspace_add',workspace='main',serial=serial)
        with patch.object(self.controller,'action',return_value=dict(ok=True)):
            self.assertTrue(self.act('workspace_disconnect',workspace='main',member='main')['ok'])
        self.assertNotIn('main',self.groups.pending_serials)
        self.assertTrue(self.act('workspace_add',workspace='main',serial=serial)['connecting'])

    def test_unknown_disconnect_reply_keeps_serial_guard(self):
        member=self.member();self.groups.pending_serials[member]='QA-HAND-ONLY'
        with patch.object(self.fleet,'post',return_value={}):
            with self.assertRaisesRegex(ValueError,'disconnect is unconfirmed'):
                self.act('workspace_disconnect',workspace=self.a,member=member)
        self.assertEqual(self.groups.pending_serials[member],'QA-HAND-ONLY')

    def test_authoritative_connected_identity_replaces_pending_guard_with_live_owner_check(self):
        serial=self.discovered();result=self.act('workspace_add',workspace=self.a,serial=serial)
        self.fleet._states[result['member']].update(connection='connected',device_id=serial)
        with self.assertRaisesRegex(ValueError,'already assigned'):
            self.act('workspace_add',workspace=self.b,serial=serial)
        self.assertNotIn(result['member'],self.groups.pending_serials)
        self.assertEqual(len(self.fleet.created),1)


if __name__=='__main__':unittest.main()
