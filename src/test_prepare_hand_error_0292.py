"""Report a current paired-hand failure without aborting an explicit retry."""
import copy
from contextlib import contextmanager
import unittest
from unittest.mock import Mock

import test_target_mapping_switch as target_fixture
from test_connection_switch import HAND


class PreparedHandErrorTests(unittest.TestCase):
    @contextmanager
    def fixture(self,prior_error='',replies=None):
        with target_fixture.TargetMappingSwitchTests().environment(False) as e:
            e.gs.update(hand_error=prior_error,hand_cleanup_confirmed=True)
            original_action=e.c.action.side_effect;original_snapshot=e.c.snapshot
            now=[0.];dispatched=[False];pending=list(replies or [])
            e.service.clock=lambda:now[0]
            e.service.sleep=lambda seconds:now.__setitem__(0,now[0]+seconds)
            def action(command):
                if command['name']!='glove_prepare':return original_action(command)
                e.calls.append(copy.deepcopy(command));dispatched[0]=True
                return {}
            def snapshot():
                if dispatched[0] and pending:e.gs.update(copy.deepcopy(pending.pop(0)))
                return original_snapshot()
            e.c.action=Mock(side_effect=action);e.c.snapshot=snapshot
            yield e,now

    @staticmethod
    def failed(message):
        return dict(hand_error=message,hand_cleanup_confirmed=True,
            feedback=dict(connection='error',device_id=None,identified_device={}),hardware={})

    @staticmethod
    def success(error=''):
        return dict(hand_error=error,hand_cleanup_confirmed=None,hardware={'active':False},
            feedback=dict(connection='connected',device_id=HAND,stale=False,
                identified_device=dict(source='sdk_identity',serial=HAND,profile='hand2_left')))

    def assert_glove_kept(self,e,session):
        self.assertEqual(e.gs['session_id'],session)
        self.assertEqual(e.gs['connection'],'receiving');self.assertTrue(e.gs['busy'])
        e.c.glove.stop_session_confirmed.assert_not_called()
        e.c.glove.disconnect_session.assert_not_called()
        self.assertEqual([row['name'] for row in e.calls],['retarget_binding_save','glove_prepare'])

    def test_new_prepare_failure_reports_original_error_without_twelve_second_wait(self):
        with self.fixture(replies=[self.failed('Synthetic new hand discovery failed')]) as (e,now):
            session=e.gs['session_id']
            with self.assertRaisesRegex(ValueError,'Synthetic new hand discovery failed'):
                e.service.start(e.command())
            self.assertLess(now[0],1.);self.assert_glove_kept(e,session)
            self.assertEqual(e.service.state['phase'],'failed');self.assertIsNone(e.service.state['receipt'])

    def test_previous_same_session_error_cannot_abort_successful_explicit_retry(self):
        old='Synthetic old hand discovery failed'
        with self.fixture(old,[self.failed(old),self.success(old)]) as (e,now):
            session=e.gs['session_id'];result=e.service.start(e.command())
            self.assertEqual(result['connection_switch']['phase'],'connected')
            self.assert_glove_kept(e,session)

    def test_clear_then_same_error_is_a_new_failure_and_does_not_reopen_glove(self):
        old='Synthetic repeated discovery failed'
        with self.fixture(old,[self.failed(old),dict(hand_error='',hand_cleanup_confirmed=None),self.failed(old)]) as (e,now):
            session=e.gs['session_id']
            with self.assertRaisesRegex(ValueError,'Synthetic repeated discovery failed'):
                e.service.start(e.command())
            self.assertLess(now[0],1.);self.assert_glove_kept(e,session)

    def test_same_session_changed_error_is_not_hidden_by_the_prior_failure(self):
        with self.fixture('Synthetic previous error',[self.failed('Synthetic current route refused')]) as (e,now):
            with self.assertRaisesRegex(ValueError,'Synthetic current route refused'):
                e.service.start(e.command())
            self.assertLess(now[0],1.)

    def test_nonfatal_notice_does_not_override_verified_hand_feedback(self):
        with self.fixture(replies=[self.success('Synthetic action data notice')]) as (e,now):
            result=e.service.start(e.command())
            self.assertEqual(result['connection_switch']['phase'],'connected')

    def test_replacement_session_is_rejected_before_its_error_and_never_stopped(self):
        with self.fixture(replies=[dict(self.failed('Synthetic foreign failure'),session_id=99)]) as (e,now):
            with self.assertRaisesRegex(ValueError,'Glove identity changed'):
                e.service.start(e.command())
            e.c.glove.stop_session_confirmed.assert_not_called()
            e.c.glove.disconnect_session.assert_not_called()


if __name__=='__main__':unittest.main()
