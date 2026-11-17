"""Pending-open display ends only with the owning worker's exact cleanup ACK."""
from unittest.mock import Mock
import unittest
from glove_bridge import GloveBridge


class ClosedPendingTests(unittest.TestCase):
    def bridge(self):
        bridge=GloveBridge(Mock());bridge.session=119
        bridge._open_pending=True
        bridge._open_intent=dict(session=119,serial='WG-SYNTHETIC',user_id='owner-1',profile='hand2_right')
        bridge._worker_dispatch_attempted=True;bridge._reader_finished=True;bridge._transport_closed=True
        bridge._continuation={'serial':'WG-SYNTHETIC'}
        bridge.state.update(profile='hand2_right')
        bridge.receive(dict(type='glove_error',message='Connection timeout - synthetic'),session=119)
        return bridge

    def ack(self):
        return dict(version=1,glove_serial='',sdk_user_id='',profile='hand2_right',glove_was_open=False,
            subscription_closed=True,glove_disconnected=True,solver_cleanup='not_open',user_restored=True,
            ownership_released=True,profile_lease_released=True,selection_lease_released=True,hand_released=True,retryable=False)

    def close(self,bridge,ack=None,session=119,hardware=None):
        bridge.receive(dict(type='glove_closed',hardware={} if hardware is None else hardware,
            session_cleanup=self.ack() if ack is None else ack),session=session)

    def test_matching_confirmed_closed_ends_open_pending_and_retains_original_error(self):
        bridge=self.bridge();intent=dict(bridge._open_intent)
        self.close(bridge)
        state=bridge.snapshot()
        self.assertTrue(state['session_cleanup']['cleanup_confirmed'])
        self.assertFalse(state['open_pending'])
        self.assertEqual(state['connection'],'error')
        self.assertEqual(state['message'],'Connection timeout - synthetic')
        self.assertEqual(bridge._open_intent,intent)

    def test_unconfirmed_resource_cleanup_remains_pending(self):
        bridge=self.bridge();ack=self.ack();ack.update(glove_disconnected=False,retryable=True)
        self.close(bridge,ack)
        self.assertTrue(bridge.snapshot()['open_pending'])
        self.assertFalse(bridge.snapshot()['session_cleanup']['cleanup_confirmed'])

    def test_late_closed_from_previous_session_cannot_clear_pending(self):
        bridge=self.bridge();self.close(bridge,session=118)
        self.assertTrue(bridge.snapshot()['open_pending'])
        self.assertFalse(bridge.snapshot()['session_cleanup']['acknowledged'])

    def test_wrong_cleanup_identity_cannot_clear_pending(self):
        bridge=self.bridge();ack=self.ack();ack['profile']='hand2_left';self.close(bridge,ack)
        self.assertTrue(bridge.snapshot()['open_pending'])
        self.assertIn('identity differs',bridge.snapshot()['session_cleanup']['error'])

    def test_unconfirmed_hand_disable_cannot_clear_pending(self):
        bridge=self.bridge();self.close(bridge,hardware=dict(active=None,stop_confirmed=False))
        self.assertTrue(bridge.snapshot()['open_pending'])
        self.assertFalse(bridge.snapshot()['session_cleanup']['cleanup_confirmed'])

    def test_missing_cleanup_ack_cannot_clear_pending(self):
        bridge=self.bridge()
        bridge.receive(dict(type='glove_closed',hardware={}),session=119)
        self.assertTrue(bridge.snapshot()['open_pending'])
        self.assertFalse(bridge.snapshot()['session_cleanup']['acknowledged'])


if __name__=='__main__':unittest.main()
