"""Late exact SDK cleanup resolves only its owning failed switch diagnostic."""
import copy
import unittest

from connection_switch import unsettled
from studio_component import require_idle
from test_connection_switch_0264 import PurposeSwitchTests,SN,USER
from test_connection_switch import OTHER


class CleanupOwnerTests(unittest.TestCase):
    environment=PurposeSwitchTests.environment
    command=PurposeSwitchTests.command

    def failed_new_session(self,e):
        original_action=e.c.action.side_effect
        original_stop=e.c.glove.stop_session_confirmed.side_effect
        old_session=e.gs['session_id']
        def action(body):
            if body['name']=='glove_open':
                e.calls.append(copy.deepcopy(body))
                e.gs.update(connection='error',message='Synthetic SDK import failed',glove_serial='',profile=None,hardware={})
            else:original_action(body)
        def stop(session,**expected):
            if session==old_session and e.service.state.get('source'):
                return original_stop(session,**expected)
            raise ValueError('SDK state is unverified while its cleanup ACK is pending')
        e.c.action.side_effect=action;e.c.glove.stop_session_confirmed.side_effect=stop
        target=OTHER if e.gs.get('busy') else SN
        with self.assertRaisesRegex(ValueError,'Synthetic SDK import failed'):
            e.service.start(self.command(e,'teleoperation',target,user_id=USER))
        self.assertTrue(e.service.state['cleanup_error'])
        self.assertTrue(unsettled(e.c.snapshot()))
        return e.gs['session_id']

    def completed_cleanup(self,e,session):
        e.gs.update(busy=False,connection='error',stream={},hardware={},feedback={},follow_pending=False,follow_unconfirmed=False,
            session_cleanup=dict(session_id=session,acknowledged=True,cleanup_confirmed=True,retryable=False,
                                 reader_finished=True,transport_closed=True,error=None,identity=None))

    def test_late_full_cleanup_unblocks_idle_and_retains_original_import_error_without_a_source(self):
        with self.environment('none') as e:
            session=self.failed_new_session(e);self.completed_cleanup(e,session)
            state=e.c.snapshot()
            self.assertIsNone(state['connection_switch']['cleanup_error'])
            self.assertEqual(state['connection_switch']['error'],'Synthetic SDK import failed')
            self.assertFalse(unsettled(state));self.assertTrue(state['connection_switch']['can_switch'])
            require_idle(state)

    def test_new_session_cleanup_is_not_confused_with_the_released_old_sdk_owner(self):
        with self.environment('teleoperation') as e:
            previous=e.gs['session_id'];session=self.failed_new_session(e)
            self.assertNotEqual(session,previous);self.completed_cleanup(e,session)
            state=e.c.snapshot()
            self.assertEqual(state['connection_switch']['source']['session'],previous)
            self.assertIsNone(state['connection_switch']['cleanup_error'])
            self.assertFalse(unsettled(state));require_idle(state)

    def test_incomplete_foreign_or_active_cleanup_never_retires_the_diagnostic(self):
        mutations=[lambda g:g.update(session_id=g['session_id']+1),
            lambda g:g['session_cleanup'].update(session_id=g['session_id']+1),
            lambda g:g['session_cleanup'].update(acknowledged=False),
            lambda g:g['session_cleanup'].update(cleanup_confirmed=False),
            lambda g:g['session_cleanup'].update(reader_finished=False),
            lambda g:g['session_cleanup'].update(transport_closed=False),
            lambda g:g['session_cleanup'].update(error='Transport close unknown'),
            lambda g:g.update(busy=True),lambda g:g.update(follow_pending=True),
            lambda g:g.update(follow_unconfirmed=True),
            lambda g:g.update(hardware={'active':None}),
            lambda g:g.update(hardware={'active':False,'stop_confirmed':None})]
        for index,mutate in enumerate(mutations):
            with self.subTest(index=index),self.environment('none') as e:
                session=self.failed_new_session(e);self.completed_cleanup(e,session);mutate(e.gs)
                state=e.c.snapshot()
                self.assertTrue(state['connection_switch']['cleanup_error'])
                self.assertTrue(unsettled(state));self.assertFalse(state['connection_switch']['can_switch'])
                with self.assertRaises(ValueError):require_idle(state)

    def test_another_attempt_cannot_use_the_old_session_completion_as_its_proof(self):
        with self.environment('none') as e:
            session=self.failed_new_session(e);self.completed_cleanup(e,session)
            e.service.state['attempt_id']='f'*32
            state=e.c.snapshot()
            self.assertTrue(state['connection_switch']['cleanup_error'])
            self.assertTrue(unsettled(state));self.assertFalse(state['connection_switch']['can_switch'])

    def test_an_exact_completion_already_observed_does_not_revive_on_a_later_epoch(self):
        with self.environment('none') as e:
            session=self.failed_new_session(e);self.completed_cleanup(e,session)
            self.assertIsNone(e.c.snapshot()['connection_switch']['cleanup_error'])
            e.gs['session_id']=session+1;e.gs['session_cleanup']['session_id']=session+1
            state=e.c.snapshot()
            self.assertIsNone(state['connection_switch']['cleanup_error'])
            self.assertEqual(state['connection_switch']['error'],'Synthetic SDK import failed')
            self.assertFalse(unsettled(state))

    def test_failure_before_source_release_stays_bound_to_the_original_source(self):
        with self.environment('teleoperation') as e:
            original=e.gs['session_id']
            e.c.glove.stop_session_confirmed.side_effect=ValueError('Original source close unconfirmed')
            with self.assertRaisesRegex(ValueError,'Original source close unconfirmed'):
                e.service.start(self.command(e,'official'))
            self.assertFalse(e.service.state['source_released']);e.c.action.assert_not_called()
            self.completed_cleanup(e,original+1);e.gs['session_id']=original+1
            state=e.c.snapshot()
            self.assertTrue(state['connection_switch']['cleanup_error']);self.assertTrue(unsettled(state))
            self.assertFalse(state['connection_switch']['can_switch'])
            e.gs['session_id']=original;e.gs['session_cleanup']['session_id']=original
            self.assertIsNone(e.c.snapshot()['connection_switch']['cleanup_error'])


del PurposeSwitchTests
if __name__=='__main__':unittest.main()
