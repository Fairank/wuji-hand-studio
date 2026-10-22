import copy
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

from task_groups import TaskWorkspaces


def hand(ident,sn):
    return dict(task_id=ident*24,revision=1,endpoint_id=ident,kind='hand',devices=dict(hand=sn),
                observed=dict(source='sdk',stale=False),status='connected')


class TaskGroupTests(unittest.TestCase):
    def setUp(self):
        self.base=dict(revision=1,tasks=[hand('a','HAND-A'),hand('b','HAND-B')],pair_candidates=[],pairing={})
        self.device=SimpleNamespace(snapshot=lambda:copy.deepcopy(self.base),action=lambda c:dict(device_action=c))
        self.run=SimpleNamespace(token='session1',snapshot=lambda:dict(active=True,members=[dict(id='a'),dict(id='b')]),stop=lambda:dict(stopping=True))
        self.ws=SimpleNamespace(lock=threading.RLock(),rows=[dict(id='main',label='Two hands',members=['a','b'])],
                                coordinators={'main':self.run},coordinator=lambda ident:self.run)
        self.server=SimpleNamespace(server_address=('127.0.0.1',19876),_workspaces=self.ws)
        with patch('task_server.TaskServer',return_value=self.device):self.service=TaskWorkspaces(self.server)

    def group(self):return next(t for t in self.service.snapshot()['tasks'] if t['kind']=='ensemble')

    def test_only_verified_connected_members_form_group(self):
        group=self.group();self.assertEqual(group['devices']['hands'],['HAND-A','HAND-B'])
        self.assertIn('task_group=main',group['view']['url']);self.assertNotIn('session1',str(group))
        self.assertTrue(self.service.allows_embedded_main(group['task_id']))
        self.base['tasks'][0]['paired_into']='pair-task'
        self.assertFalse(any(t['kind']=='ensemble' for t in self.service.snapshot()['tasks']))

    def test_disconnected_or_single_device_never_forms_group(self):
        self.base['tasks'][0]['status']='disconnected'
        self.assertFalse(any(t['kind']=='ensemble' for t in self.service.snapshot()['tasks']))

    def test_read_layout_and_task_selection_never_start_or_renew_lease(self):
        before=self.run.token;first=self.group();second=self.group()
        self.assertEqual(first,second);self.assertEqual(self.run.token,before)

    def test_exact_group_stop_reuses_original_coordinator(self):
        g=self.group();result=self.service.action(dict(name='task_session_action',task_id=g['task_id'],
            expected_revision=g['revision'],action='pause'))
        self.assertEqual(result,dict(stopping=True))

    def test_group_restart_rejects_stale_stop(self):
        g=self.group();self.run.token='session2'
        with self.assertRaises(ValueError):self.service.action(dict(task_id=g['task_id'],
            expected_revision=g['revision'],action='pause'))

    def test_membership_revision_rejects_stale_stop(self):
        g=self.group();self.base['tasks'][0]['revision']+=1
        with self.assertRaises(ValueError):self.service.action(dict(task_id=g['task_id'],
            expected_revision=g['revision'],action='pause'))

    def test_running_subset_excludes_idle_sibling_and_its_epoch_changes(self):
        self.base['tasks'].append(hand('c','HAND-C'));self.ws.rows[0]['members'].append('c')
        g=self.group()
        self.assertEqual(g['intent']['members'],['a','b'])
        self.assertEqual(g['devices']['hands'],['HAND-A','HAND-B'])
        self.base['tasks'][2]['revision']+=1
        self.assertEqual(self.group()['revision'],g['revision'])
        self.assertEqual(self.service.action(dict(task_id=g['task_id'],expected_revision=g['revision'],action='pause')),dict(stopping=True))
        self.run.snapshot=lambda:dict(active=False)
        self.assertEqual(self.group()['devices']['hands'],['HAND-A','HAND-B','HAND-C'])

    def test_running_members_require_complete_unique_catalog_and_owner_set(self):
        for actual in (None,[],[dict(id='a')],[dict(id='a'),dict(id='a')],
                [dict(id='a'),dict(id='missing')],[dict(id='a'),{}],['a','b']):
            with self.subTest(actual=actual):
                self.run.snapshot=lambda actual=actual:dict(active=True,members=actual)
                self.assertFalse(any(t['kind']=='ensemble' for t in self.service.snapshot()['tasks']))
        self.base['tasks'].append(hand('c','HAND-C'))
        self.run.snapshot=lambda:dict(active=True,members=[dict(id='a'),dict(id='c')])
        self.assertFalse(any(t['kind']=='ensemble' for t in self.service.snapshot()['tasks']))

    def test_arbitrary_group_command_and_follow_never_dispatch(self):
        g=self.group()
        for extra in ({'command':{'name':'hardware_start'}},{'action':'follow'}):
            command=dict(task_id=g['task_id'],expected_revision=g['revision'],action='pause');command.update(extra)
            with self.assertRaises(ValueError):self.service.action(command)

    def test_no_catalog_is_not_created_by_reading_tasks(self):
        del self.server._workspaces
        self.assertEqual(len(self.service.snapshot()['tasks']),2)
        self.assertFalse(hasattr(self.server,'_workspaces'))

    def test_ended_group_cannot_be_stopped_by_stale_running_view(self):
        g=self.group();self.run.snapshot=lambda:dict(active=False)
        with self.assertRaises(ValueError):self.service.action(dict(task_id=g['task_id'],
            expected_revision=g['revision'],action='pause'))

    def test_other_command_name_cannot_use_task_stop(self):
        g=self.group()
        with self.assertRaises(ValueError):self.service.action(dict(name='hardware_start',task_id=g['task_id'],
            expected_revision=g['revision'],action='pause'))


if __name__=='__main__':unittest.main()
