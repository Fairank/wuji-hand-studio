"""A rejected private bootstrap must consume real cleanup, never invent an ACK."""
import copy,io,json
from pathlib import Path
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from glove_bridge import GloveBridge
from test_calibration_portability import bundle_context
from test_sdk_lifecycle import proof_fixture,release_fixture


def cleanup(**changes):
    row=dict(version=1,glove_serial='',sdk_user_id='',profile='hand2_right',glove_was_open=False,
        subscription_closed=True,glove_disconnected=True,solver_cleanup='not_open',user_restored=True,
        ownership_released=True,profile_lease_released=True,selection_lease_released=True,hand_released=True,retryable=False)
    return {**row,**changes}


class BootstrapCleanupTests(unittest.TestCase):
    def transport(self,root,*,mode='confirmed',saved=True):
        context=bundle_context(root/'source')
        if saved:context.update(source_kind='saved_profile',transfer_id='b'*32,source_run_id=None)
        bridge=GloveBridge(Mock());bridge.session=7;bridge._continuation=copy.deepcopy(context);bridge._expected_profile='hand2_right'
        class Stream(io.StringIO):
            failed=False
            def flush(self):
                if mode=='partial_open' and not self.failed and self.getvalue().splitlines()[-1].find('glove_open')>=0:
                    self.failed=True;raise OSError('Synthetic partial open flush failure')
        sent=Stream()
        bridge._stage_calibration=Mock(return_value=copy.deepcopy(context));bridge.release_resolver=release_fixture
        def reject(_):
            if mode=='new_session':bridge.session=8
            if mode=='partial_open':return {'timeout_ms':250}
            if mode=='cancelled':bridge.disconnect_session(7)
            raise ValueError('Exact saved model changed before opening')
        bridge._on_model_ready=Mock(side_effect=reject)
        def line(value):return 'WUJI_JSON:'+json.dumps(value)+'\n'
        receipt={key:context[key] for key in ('source_run_id','source_workspace','source_profile','serial','side','model_sha256')}
        if saved:receipt.update(source_kind='saved_profile',transfer_id='b'*32)
        receipt.update(owner_user_id='owner-1',imported=True)
        explicit=[]
        def lines():
            yield line(dict(type='ready',teleop_version=1,calibration_continuation_version=1,sdk_proof=proof_fixture()))
            yield line(dict(type='sdk_ready',sdk_proof=proof_fixture()))
            yield line(dict(type='calibrated_ready',receipt=receipt))
            self.assertEqual([json.loads(x)['name'] for x in sent.getvalue().splitlines()],
                ['glove_session','glove_open','glove_disconnect'] if mode=='partial_open' else ['glove_session','glove_disconnect'])
            # Late ordinary data must not turn the rejected source into success.
            yield line(dict(type='glove_state',state=dict(connection='receiving',profile='hand2_right',
                glove_serial='WRONG_LATE',user=dict(user_id='OTHER'),stream={},hardware={},feedback={})))
            if mode=='missing':return
            if mode=='retryable':
                yield line(dict(type='glove_closed',hardware={},session_cleanup=cleanup(profile_lease_released=False,retryable=True)))
                self.assertFalse(bridge.snapshot()['session_cleanup']['cleanup_confirmed'])
                self.assertEqual(sent.getvalue().count('glove_disconnect'),1)
                explicit.append('user-disconnect')
                bridge.disconnect_session(7)
            value=cleanup()
            if mode=='partial_open':value.update(glove_was_open=True,glove_serial=context['serial'],sdk_user_id='owner-1')
            if mode=='bad_ack':value['version']=True
            yield line(dict(type='glove_closed',hardware={},session_cleanup=value))
            yield line(dict(type='closed'))
        client=N(agent_command='no actual process',exec_command=lambda *a,**kw:(sent,lines(),io.StringIO()),close=Mock(return_value=True))
        bridge.factory=Mock(return_value=client)
        bridge.run()
        return N(bridge=bridge,sent=sent,client=client,context=context,explicit=explicit)

    def test_rejected_callback_drains_exact_cleanup_and_allows_explicit_new_session(self):
        for saved in (False,True):
            with self.subTest(saved=saved),tempfile.TemporaryDirectory() as tmp:
                e=self.transport(Path(tmp),saved=saved);state=e.bridge.snapshot()
                self.assertTrue(state['session_cleanup']['cleanup_confirmed'])
                self.assertEqual(state['connection'],'error');self.assertIn('saved model changed',state['message'])
                self.assertNotEqual(state.get('glove_serial'),'WRONG_LATE')
                self.assertIsNone(e.bridge._cleanup_error);self.assertEqual(e.sent.getvalue().count('glove_disconnect'),1)
                self.assertNotIn('glove_open',e.sent.getvalue());e.client.close.assert_called_once()
                with patch('glove_bridge.threading.Thread',return_value=N(start=lambda:None,is_alive=lambda:True)):
                    self.assertEqual(e.bridge.start_calibrated(e.context,lambda _:{}),8)

    def test_missing_or_invalid_ack_still_blocks_new_session(self):
        for mode in ('missing','bad_ack'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                e=self.transport(Path(tmp),mode=mode)
                self.assertFalse(e.bridge.snapshot()['session_cleanup']['cleanup_confirmed'])
                with self.assertRaises(ValueError):e.bridge.start_calibrated(e.context,lambda _:{})

    def test_failed_cleanup_needs_a_second_explicit_disconnect_not_an_automatic_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=self.transport(Path(tmp),mode='retryable')
            self.assertEqual(e.explicit,['user-disconnect'])
            self.assertEqual(e.sent.getvalue().count('glove_disconnect'),2)
            self.assertTrue(e.bridge.snapshot()['session_cleanup']['cleanup_confirmed'])

    def test_replaced_epoch_receives_no_cleanup_command_from_old_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=self.transport(Path(tmp),mode='new_session')
            self.assertEqual(e.bridge.session,8)
            self.assertNotIn('glove_disconnect',e.sent.getvalue());e.client.close.assert_called_once()

    def test_partial_open_dispatch_requires_exact_acquired_resource_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=self.transport(Path(tmp),mode='partial_open');state=e.bridge.snapshot()
            self.assertTrue(state['session_cleanup']['cleanup_confirmed'])
            self.assertTrue(e.bridge._cleanup_ack['glove_was_open'])
            self.assertEqual(e.bridge._cleanup_ack['glove_serial'],e.context['serial'])
            self.assertEqual(e.sent.getvalue().count('glove_open'),1)
            self.assertEqual(e.sent.getvalue().count('glove_disconnect'),1)
            self.assertNotIn('glove_follow',e.sent.getvalue())

    def test_existing_explicit_cancel_is_not_duplicated_when_readback_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=self.transport(Path(tmp),mode='cancelled')
            self.assertEqual(e.sent.getvalue().count('glove_disconnect'),1)
            self.assertTrue(e.bridge.snapshot()['session_cleanup']['cleanup_confirmed'])


if __name__=='__main__':unittest.main()
