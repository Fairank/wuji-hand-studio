"""Synthetic asynchronous owner handoff and cancellation, with zero devices."""
import copy
from pathlib import Path
import tempfile
import threading
import unittest

from device_termination import TerminationJournal
from task_pairing import TaskPairing
from task_sessions import context_digest
from test_task_server import command,glove,hand,server


class TaskPairingTests(unittest.TestCase):
    def fixture(self):
        host,service=server(source=glove(),target=hand())
        rows=service.registry.snapshot()['tasks']
        source=next(t for t in rows if t['kind']=='glove');target=next(t for t in rows if t['kind']=='hand')
        return host,service,copy.deepcopy(service.registry.records[source['task_id']]),copy.deepcopy(service.registry.records[target['task_id']])

    def join(self,pairing):
        pairing.thread.join(2);self.assertFalse(pairing.thread.is_alive());self.assertFalse(pairing.snapshot()['busy'])

    def test_normal_termination_snapshot_is_empty_and_unknown_rows_block_before_release(self):
        with tempfile.TemporaryDirectory() as folder:
            journal=TerminationJournal(Path(folder)/'termination.json')
            self.assertEqual(journal.snapshot(),[])
            host,service,source,target=self.fixture()
            host._fleet.controllers['source'].state['unconfirmed_devices']=journal.snapshot()
            service.pairing.start(source,target);self.join(service.pairing)
            self.assertEqual(service.pairing.snapshot()['phase'],'paired')
            host,service,source,target=self.fixture()
            host._fleet.controllers['source'].state['unconfirmed_devices']=[dict(source='journal',reason='Unknown cleanup')]
            with self.assertRaisesRegex(ValueError,'confirm disable'):service.pairing.start(source,target)
            self.assertEqual(host._fleet.controllers['target'].calls,[])

    def test_changed_source_or_target_is_rejected_before_any_release(self):
        for mutation in ('glove_sn','session','user','profile','source_profile','stream','target_stale','target_identity','unknown'):
            with self.subTest(mutation=mutation):
                host,service,source,target=self.fixture();g=host._fleet.controllers['source'].state['glove'];h=host._fleet.controllers['target'].state
                if mutation=='glove_sn':g['glove_serial']='OTHER'
                elif mutation=='session':g['session_id']+=1
                elif mutation=='user':g['user']['user_id']='other'
                elif mutation=='profile':host._fleet.controllers['source'].state['device_profile']['id']='hand1_left'
                elif mutation=='source_profile':g['profile']='hand1_left'
                elif mutation=='stream':g['stream']['fresh']=False
                elif mutation=='target_stale':h['stale']=True
                elif mutation=='target_identity':h['identified_device']['source']='advertisement'
                else:g['hardware']={'active':None}
                with self.assertRaises(ValueError):service.pairing.start(source,target)
                self.assertEqual(host._fleet.controllers['target'].calls,[])

    def test_release_timeout_never_prepares_and_records_only_attempted_release(self):
        host,service,source,target=self.fixture();clock=[0.];calls=[]
        pairing=TaskPairing(service.read,lambda *args:calls.append(args) or {'ok':True},timeout=.2,
            clock=lambda:clock[0],sleep=lambda amount:clock.__setitem__(0,clock[0]+amount))
        pairing.start(source,target);self.join(pairing);state=pairing.snapshot()
        self.assertEqual(state['phase'],'error');self.assertTrue(state['release_requested']);self.assertFalse(state['released_hand'])
        self.assertEqual([call[2] for call in calls],['disconnect'])

    def test_different_or_unconfirmed_released_target_never_prepares(self):
        for unknown in (False,True):
            with self.subTest(unknown=unknown):
                host,service,source,target=self.fixture();calls=[]
                def dispatch(endpoint,context,action,payload):
                    calls.append(action);state=host._fleet.controllers['target'].state
                    state.update(connection='disconnected' if unknown else 'connected',device_id=None if unknown else 'OTHER')
                    if unknown:state['hardware']['active']=None
                    return {'ok':True}
                pairing=TaskPairing(service.read,dispatch)
                pairing.start(source,target);self.join(pairing)
                self.assertEqual(pairing.snapshot()['phase'],'error');self.assertEqual(calls,['disconnect'])

    def test_source_failure_after_confirmed_release_does_not_reconnect_old_hand(self):
        host,service,source,target=self.fixture();controller=host._fleet.controllers['target'];original=controller._task_context_dispatch
        def release_then_change(action,payload,state):
            result=original(action,payload,state);host._fleet.controllers['source'].state['glove']['session_id']+=1;return result
        controller._task_context_dispatch=release_then_change
        service.pairing.start(source,target);self.join(service.pairing)
        self.assertEqual(service.pairing.snapshot()['phase'],'error');self.assertTrue(service.pairing.snapshot()['released_hand'])
        self.assertEqual(controller.calls,[('disconnect',{})]);self.assertEqual(host._fleet.controllers['source'].calls,[])

    def test_prepared_identity_stream_or_hardware_failure_is_not_paired(self):
        for mutation in ('serial','profile','stale','stream','active','source_disconnect','user'):
            with self.subTest(mutation=mutation):
                host,service,source,target=self.fixture();controller=host._fleet.controllers['source'];original=controller._task_context_dispatch
                def prepare(action,payload,state):
                    result=original(action,payload,state);g=controller.state['glove'];f=g['feedback']
                    if mutation=='serial':f['identified_device']['serial']='OTHER'
                    elif mutation=='profile':f['identified_device']['profile']='hand1_left'
                    elif mutation=='stale':f['stale']=True
                    elif mutation=='stream':g['stream']['fresh']=False
                    elif mutation=='active':g['hardware']['active']=None
                    elif mutation=='source_disconnect':g['connection']='error'
                    else:g['user']['user_id']='OTHER'
                    return result
                controller._task_context_dispatch=prepare
                service.pairing.start(source,target);self.join(service.pairing)
                self.assertEqual(service.pairing.snapshot()['phase'],'error')
                self.assertEqual([a for a,_ in controller.calls],['pair_prepare'])

    def test_stop_of_source_is_sticky_and_does_not_prepare_after_late_release(self):
        host,service,source,target=self.fixture();entered=threading.Event();release=threading.Event()
        controller=host._fleet.controllers['target'];original=controller._task_context_dispatch
        def delayed(action,payload,state):
            result=original(action,payload,state);entered.set();release.wait(2);return result
        controller._task_context_dispatch=delayed
        service.pairing.start(source,target);self.assertTrue(entered.wait(1))
        visible=service.snapshot();row=next(t for t in visible['tasks'] if t['task_id']==source['task_id'])
        self.assertEqual(row['status'],'pairing');self.assertFalse(row['capabilities']['can_pair'])
        service.action(command(row,'disconnect'));self.assertTrue(service.pairing.snapshot()['cancel_requested'])
        self.assertTrue(service.pairing.snapshot()['busy'])
        with self.assertRaisesRegex(ValueError,'in progress'):service.action(command(row,'pair',hand_task_id=target['task_id'],hand_revision=target['revision']))
        release.set();self.join(service.pairing)
        self.assertEqual(service.pairing.snapshot()['phase'],'cancelled')
        self.assertEqual(host._fleet.controllers['source'].calls,[('disconnect',{})])
        self.assertEqual(controller.calls,[('disconnect',{})])

    def test_cancel_during_inflight_prepare_is_not_retried_or_claimed_as_device_stop(self):
        host,service,source,target=self.fixture();entered=threading.Event();release=threading.Event()
        controller=host._fleet.controllers['source'];original=controller._task_context_dispatch
        def delayed(action,payload,state):
            entered.set();release.wait(2);return original(action,payload,state)
        controller._task_context_dispatch=delayed
        service.pairing.start(source,target);self.assertTrue(entered.wait(1))
        row=next(t for t in service.snapshot()['tasks'] if t['task_id']==source['task_id'])
        with self.assertRaisesRegex(ValueError,'switching'):service.action(command(row,'disconnect'))
        self.assertTrue(service.pairing.snapshot()['cancel_requested'])
        release.set();self.join(service.pairing)
        self.assertEqual(service.pairing.snapshot()['phase'],'cancelled')
        self.assertEqual([action for action,_ in controller.calls],['pair_prepare'])
        self.assertFalse(controller.state['glove']['hardware']['active'])

    def test_cancel_during_final_feedback_read_cannot_publish_paired_success(self):
        host,service,source,target=self.fixture();entered=threading.Event();release=threading.Event();held=[False]
        def read(endpoint):
            state=service.read(endpoint)
            if endpoint=='source' and state['glove'].get('feedback',{}).get('device_id') and not held[0]:
                held[0]=True;entered.set();release.wait(2)
            return state
        pairing=TaskPairing(read,service.dispatch)
        pairing.start(source,target);self.assertTrue(entered.wait(1))
        try:
            current=service.read('source')
            self.assertTrue(pairing.cancel('source',context_digest(current),current))
        finally:release.set();self.join(pairing)
        self.assertEqual(pairing.snapshot()['phase'],'cancelled')
        self.assertTrue(pairing.snapshot()['cancel_requested'])
        self.assertEqual([a for a,_ in host._fleet.controllers['source'].calls],['pair_prepare'])

    def test_cancel_after_terminal_commit_does_not_rewrite_completed_job(self):
        host,service,source,target=self.fixture();current=service.read('source')
        for phase in ('paired','error','cancelled'):
            with self.subTest(phase=phase):
                pairing=TaskPairing(service.read,service.dispatch)
                pairing.state.update(busy=True,phase=phase,source_endpoint='source',
                    source_serial=source['devices']['glove'],source_session=source['observed']['session'],
                    source_user=source['intent']['sdk_user'],source_profile=source['intent']['profile_id'])
                self.assertFalse(pairing.cancel('source',context_digest(current),current))
                self.assertEqual(pairing.snapshot()['phase'],phase);self.assertFalse(pairing.cancel_event.is_set())

    def test_cancel_other_endpoint_does_not_cancel_and_completed_workflow_is_not_cancelled(self):
        host,service,source,target=self.fixture()
        current=service.read('source')
        self.assertFalse(service.pairing.cancel('other',context_digest(current),current))
        service.pairing.start(source,target);self.join(service.pairing)
        self.assertEqual(service.pairing.snapshot()['phase'],'paired')
        current=service.read('source')
        self.assertFalse(service.pairing.cancel('source',context_digest(current),current))

    def test_rejected_stale_dispatch_cannot_cancel_new_source_job(self):
        host,service,source,target=self.fixture()
        old_scope=source['_context']
        host._fleet.controllers['source'].state['glove']['session_id']+=1
        rows=service.registry.snapshot()['tasks'];current=next(t for t in rows if t['kind']=='glove')
        source=copy.deepcopy(service.registry.records[current['task_id']])
        entered=threading.Event();release=threading.Event();controller=host._fleet.controllers['target']
        original=controller._task_context_dispatch
        def delayed(action,payload,state):
            result=original(action,payload,state);entered.set();release.wait(2);return result
        controller._task_context_dispatch=delayed
        service.pairing.start(source,target);self.assertTrue(entered.wait(1))
        try:
            with self.assertRaisesRegex(ValueError,'session changed'):service.dispatch('source',old_scope,'disconnect',{})
            self.assertFalse(service.pairing.snapshot()['cancel_requested'])
        finally:release.set();self.join(service.pairing)
        self.assertEqual(service.pairing.snapshot()['phase'],'paired')
        self.assertEqual([a for a,_ in host._fleet.controllers['source'].calls],['pair_prepare'])

    def test_cancel_snapshot_of_another_source_owner_is_rejected_under_job_lock(self):
        host,service,source,target=self.fixture();entered=threading.Event();release=threading.Event()
        controller=host._fleet.controllers['target'];original=controller._task_context_dispatch
        def delayed(action,payload,state):
            result=original(action,payload,state);entered.set();release.wait(2);return result
        controller._task_context_dispatch=delayed
        service.pairing.start(source,target);self.assertTrue(entered.wait(1))
        try:
            for mutation in ('serial','session','user','profile'):
                current=service.read('source')
                if mutation=='serial':current['glove']['glove_serial']='OTHER'
                elif mutation=='session':current['glove']['session_id']+=1
                elif mutation=='user':current['glove']['user']['user_id']='OTHER'
                else:current['device_profile']['id']='hand1_left'
                self.assertFalse(service.pairing.cancel('source',context_digest(current),current))
            self.assertFalse(service.pairing.snapshot()['cancel_requested'])
        finally:release.set();self.join(service.pairing)
        self.assertEqual(service.pairing.snapshot()['phase'],'paired')

    def test_unpair_unknown_target_requires_strict_false_and_never_dispatches(self):
        host,service,source,target=self.fixture();service.pairing.start(source,target);self.join(service.pairing)
        row=next(t for t in service.registry.snapshot()['tasks'] if t['kind']=='glove_pair')
        paired=copy.deepcopy(service.registry.records[row['task_id']]);controller=host._fleet.controllers['source']
        controller.calls.clear();controller.state['glove']['hardware']['active']=None
        with self.assertRaisesRegex(ValueError,'confirm disable'):service.pairing.unpair(paired)
        self.assertEqual(controller.calls,[])

    def test_successful_unpair_is_empty_typed_action_and_retains_glove_session(self):
        host,service,source,target=self.fixture();service.pairing.start(source,target);self.join(service.pairing)
        row=next(t for t in service.snapshot()['tasks'] if t['kind']=='glove_pair')
        controller=host._fleet.controllers['source'];epoch=controller.state['glove']['session_id']
        service.action(command(row,'unpair'))
        self.assertEqual(controller.calls[-1],('unpair',{}));self.assertEqual(controller.state['glove']['session_id'],epoch)
        self.assertEqual(controller.state['glove']['connection'],'receiving')


if __name__=='__main__':unittest.main()
