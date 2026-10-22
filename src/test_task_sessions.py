import copy
import unittest
from task_sessions import TaskSessions,context_digest


def hand(sn='HAND-A', epoch=1):
    return dict(connection='connected',device_id=sn,session_generation=epoch,
        identified_device=dict(source='sdk_identity',serial=sn,profile='hand2_left'),
        device_profile=dict(id='hand2_left',side='left'),stale=False,
        hardware=dict(active=False),glove={},official_viz={},calibration={})


def glove(sn='GLOVE-A',paired=None):
    value=hand();value.update(connection='disconnected',device_id=None,identified_device=None)
    feedback={} if paired is None else dict(connection='connected',device_id=paired,
        identified_device=dict(source='sdk_identity',serial=paired,profile='hand2_left'))
    value['glove']=dict(connection='receiving',glove_serial=sn,session_id=4,
        user=dict(user_id='u1'),stream=dict(fresh=True),hardware=dict(active=False),feedback=feedback)
    return value


class TaskSessionTests(unittest.TestCase):
    def setUp(self):
        self.rows=[];self.calls=[]
        self.service=TaskSessions(lambda:copy.deepcopy(self.rows),lambda *args:self.calls.append(args) or {'requested':True},
            pair=lambda *args:self.calls.append(('pair',*args)) or {'pairing':True},
            unpair=lambda *args:self.calls.append(('unpair',*args)) or {'unpairing':True})

    def add(self,state,ident='main',port=18780):
        self.rows.append(dict(id=ident,origin='http://127.0.0.1:'+str(port),state=state))

    def test_no_task_from_discovery_selection_or_persisted_label(self):
        self.add(dict(connection='disconnected',device_id='advertised',devices=[dict(sn='HAND-A')]))
        self.assertEqual(self.service.snapshot()['tasks'],[]);self.assertEqual(self.calls,[])

    def test_verified_hand_read_only_and_origin_pinned(self):
        self.add(hand());task=self.service.snapshot()['tasks'][0]
        self.assertEqual(task['devices']['hand'],'HAND-A');self.assertEqual(task['view']['origin'],'http://127.0.0.1:18780')
        self.assertIn('task_view=1',task['view']['url']);self.assertNotIn('_context',task);self.assertEqual(self.calls,[])

    def test_exact_sdk_identity_required_for_first_hand_task(self):
        for change in [dict(source='advertisement'),dict(serial='OTHER'),dict(profile='hand2_right')]:
            self.rows=[];s=hand();s['identified_device'].update(change);self.add(s)
            self.assertEqual(self.service.snapshot()['tasks'],[])

    def test_rates_do_not_change_revision_but_replacement_session_does(self):
        self.add(hand());a=self.service.snapshot()['tasks'][0]
        self.rows[0]['state']['metrics']={'host_hz':999};b=self.service.snapshot()['tasks'][0]
        self.assertEqual(a['revision'],b['revision'])
        self.rows[0]['state']['session_generation']=2;c=self.service.snapshot()['tasks'][0]
        self.assertGreater(c['revision'],b['revision'])

    def test_stale_action_cannot_target_replacement_session(self):
        self.add(hand());a=self.service.snapshot()['tasks'][0];self.rows[0]['state']['session_generation']=2
        with self.assertRaisesRegex(ValueError,'Task changed'):
            self.service.action(dict(task_id=a['task_id'],expected_revision=a['revision'],action='disconnect'))
        self.assertEqual(self.calls,[])

    def test_disconnect_captures_exact_endpoint_context(self):
        self.add(hand(),'a',18781);self.add(hand('HAND-B'),'b',18782)
        a=self.service.snapshot()['tasks'][0]
        self.service.action(dict(task_id=a['task_id'],expected_revision=a['revision'],action='disconnect'))
        self.assertEqual(self.calls[0][:3],('a',context_digest(self.rows[0]['state']),'disconnect'))
        self.assertEqual(len(self.calls),1)

    def test_pair_has_one_fixed_owner_and_explicit_candidate_revision(self):
        self.add(glove(),'g',18781);self.add(hand(),'h',18782)
        tasks=self.service.snapshot()['tasks'];source=next(t for t in tasks if t['kind']=='glove');target=next(t for t in tasks if t['kind']=='hand')
        self.assertEqual(self.calls,[])
        self.service.action(dict(task_id=source['task_id'],expected_revision=source['revision'],action='pair',hand_task_id=target['task_id'],hand_revision=target['revision']))
        self.assertEqual(self.calls[0][0],'pair')
        self.assertEqual(self.calls[0][1]['endpoint_id'],'g');self.assertEqual(self.calls[0][2]['endpoint_id'],'h')

    def test_unverified_pair_identity_does_not_create_combined_task(self):
        s=glove(paired='HAND-A');s['glove']['feedback']['identified_device']['serial']='OTHER';self.add(s)
        self.assertEqual(self.service.snapshot()['tasks'][0]['kind'],'glove')

    def test_running_or_unknown_glove_cannot_pair(self):
        for change in ('active','unknown','stale'):
            self.rows=[];s=glove()
            if change=='active':s['glove']['hardware']['active']=True
            if change=='unknown':s['unconfirmed_devices']=[{'id':'unknown'}]
            if change=='stale':s['glove']['stream']['fresh']=False
            self.add(s);a=self.service.snapshot()['tasks'][0]
            self.assertFalse(a['capabilities']['can_pair']);self.assertTrue(a['capabilities']['blocked_reason'])

    def test_arbitrary_url_command_or_port_never_enters_adapter(self):
        for extra in ('url','port','command','serial'):
            with self.assertRaisesRegex(ValueError,'Invalid task'):
                self.service.action(dict(task_id='x',expected_revision=1,action='disconnect',**{extra:'bad'}))
        self.assertEqual(self.calls,[])

    def test_offline_existing_task_remains_without_claiming_live_or_connecting(self):
        self.add(hand());self.service.snapshot();self.rows[0]['state']=None;a=self.service.snapshot()['tasks'][0]
        self.assertEqual(a['status'],'offline');self.assertNotIn('can_disconnect',a['capabilities']);self.assertEqual(self.calls,[])

    def test_bad_endpoint_is_never_returned_as_embed_url(self):
        self.rows=[dict(id='bad',origin='https://evil.test',state=hand())]
        with self.assertRaises(ValueError):self.service.snapshot()


if __name__=='__main__':unittest.main()
