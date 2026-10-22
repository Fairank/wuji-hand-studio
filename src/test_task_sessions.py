import copy
import threading
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

    def test_unrelated_calibration_never_grants_historical_task_disconnect(self):
        for previous in (hand('OLD-HAND'),glove('OLD-GLOVE')):
            for stage in ('capture','handover'):
                with self.subTest(previous=previous,stage=stage):
                    self.setUp();self.add(previous);old=self.service.snapshot()['tasks'][0]
                    state=dict(connection='disconnected',hardware={'active':False},glove={})
                    if stage=='capture':state['calibration']=dict(running=True,id='new-run',serial='OTHER-GLOVE')
                    else:state['calibration_connection']=dict(busy=True,serial='OTHER-GLOVE')
                    self.rows[0]['state']=state
                    task=self.service.snapshot()['tasks'][0]
                    self.assertEqual(task['task_id'],old['task_id']);self.assertEqual(task['status'],'disconnected')
                    self.assertFalse(task['capabilities']['can_disconnect'])
                    with self.assertRaises(ValueError):
                        self.service.action(dict(task_id=task['task_id'],expected_revision=task['revision'],action='disconnect'))
                    self.assertEqual(self.calls,[])

    def test_only_same_glove_calibration_retains_its_explicit_cancel_action(self):
        for stage in ('capture','nested_capture','handover'):
            with self.subTest(stage=stage):
                self.setUp();self.add(glove('OWN-GLOVE'));self.service.snapshot()
                state=dict(connection='disconnected',hardware={'active':False},glove={})
                run=dict(running=True,id='new-run',serial='OWN-GLOVE')
                if stage=='capture':state['calibration']=run
                elif stage=='nested_capture':state['calibration']=dict(run=run)
                else:state['calibration_connection']=dict(busy=True,serial='OWN-GLOVE')
                self.rows[0]['state']=state;task=self.service.snapshot()['tasks'][0]
                self.assertEqual(task['status'],'calibrating')
                self.assertEqual(task['capabilities']['can_disconnect'],stage!='handover')
                if stage!='handover':
                    self.service.action(dict(task_id=task['task_id'],expected_revision=task['revision'],action='disconnect'))
                    self.assertEqual(len(self.calls),1)

    def test_same_glove_loaded_model_change_invalidates_revision_but_frame_stats_do_not(self):
        self.add(glove());g=self.rows[0]['state']['glove']
        g['model_provenance']=dict(source_run_id='run1',model_sha256='a'*64,owner_user_id='u1')
        old=self.service.snapshot()['tasks'][0]
        g['stream'].update(seq=99,age_ms=0,retarget_hz=120)
        self.assertEqual(self.service.snapshot()['tasks'][0]['revision'],old['revision'])
        g['model_provenance']['model_sha256']='b'*64
        new=self.service.snapshot()['tasks'][0]
        self.assertGreater(new['revision'],old['revision'])
        with self.assertRaises(ValueError):
            self.service.action(dict(task_id=old['task_id'],expected_revision=old['revision'],action='disconnect'))
        self.assertEqual(self.calls,[])

    def test_late_observation_cannot_resurrect_replaced_owner(self):
        entered=threading.Event();release=threading.Event();late=[];counter=[0]
        def endpoints():
            counter[0]+=1
            if counter[0]==1:
                entered.set();release.wait(2);state=hand('OLD-HAND')
            else:state=hand('NEW-HAND')
            return [dict(id='main',origin='http://127.0.0.1:19000',state=state)]
        service=TaskSessions(endpoints,lambda *args:self.calls.append(args))
        thread=threading.Thread(target=lambda:late.append(service.snapshot()));thread.start()
        self.assertTrue(entered.wait(1))
        try:fresh=service.snapshot()
        finally:release.set();thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(late,[fresh])
        self.assertEqual([t['devices']['hand'] for t in fresh['tasks']],['NEW-HAND'])
        self.assertEqual(self.calls,[])

    def test_continuation_attempt_is_exact_context_and_its_task_keeps_source_identity(self):
        state=dict(connection='disconnected',device_profile=dict(id='hand2_left',side='left'),hardware={'active':False},
            calibrated_teleoperation=dict(busy=True,attempt_id='a'*32,run_id='b'*32,expected_revision='c'*64,
                serial='OWN-GLOVE',side='left',profile='Wearer',model_sha256='d'*64,sdk_session=None))
        self.add(state);task=self.service.snapshot()['tasks'][0]
        self.assertEqual(task['status'],'connecting');self.assertTrue(task['observed']['stale'])
        self.assertIsNone(task['devices']['hand']);self.assertEqual(task['devices']['glove'],'OWN-GLOVE')
        self.assertEqual(task['observed']['continuation']['attempt_id'],'a'*32)
        self.assertEqual(task['observed']['continuation']['run_id'],'b'*32)
        self.assertTrue(task['capabilities']['can_disconnect']);self.assertFalse(task['capabilities']['can_pair'])
        self.rows[0]['state']['calibrated_teleoperation']['attempt_id']='e'*32
        current=self.service.snapshot()['tasks'][0]
        self.assertGreater(current['revision'],task['revision'])
        with self.assertRaises(ValueError):
            self.service.action(dict(task_id=task['task_id'],expected_revision=task['revision'],action='disconnect'))
        self.assertEqual(self.calls,[])

    def test_each_continuation_identity_change_invalidates_context_but_phase_does_not(self):
        original=glove();original['calibrated_teleoperation']=dict(attempt_id='a'*32,run_id='b'*32,
            expected_revision='c'*64,serial='GLOVE-A',side='left',profile='Wearer',model_sha256='d'*64,sdk_session=4)
        digest=context_digest(original)
        for key in ('attempt_id','run_id','expected_revision','serial','side','profile','model_sha256','sdk_session'):
            changed=copy.deepcopy(original);changed['calibrated_teleoperation'][key]='different'
            self.assertNotEqual(context_digest(changed),digest,key)
        original['calibrated_teleoperation'].update(phase='exporting',busy=True,error='pending')
        self.assertEqual(context_digest(original),digest)

    def test_duplicate_glove_owners_keep_exact_first_owner_stop_but_never_pair(self):
        first=glove('SAME-GLOVE');first['glove']['hardware']['active']=True
        self.add(first,'one',19001);self.add(glove('SAME-GLOVE'),'two',19002)
        task=self.service.snapshot()['tasks'][0]
        self.assertEqual(task['status'],'conflict');self.assertEqual(task['endpoint_id'],'one')
        self.assertTrue(task['capabilities']['can_pause']);self.assertTrue(task['capabilities']['can_disconnect'])
        self.assertFalse(task['capabilities']['can_pair']);self.assertFalse(task['capabilities']['can_unpair'])
        self.service.action(dict(task_id=task['task_id'],expected_revision=task['revision'],action='pause'))
        self.assertEqual(self.calls,[('one',context_digest(first),'pause',{})])


if __name__=='__main__':unittest.main()
