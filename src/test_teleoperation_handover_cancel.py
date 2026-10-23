"""Scoped Viewer handover cancellation; all resources are synthetic."""
import copy
import io
import threading
import unittest
from unittest.mock import Mock,patch

from glove_protocol import CancellationNotDispatched
import test_teleoperation_handover as fixtures


class ScopedHandoverCancellationTests(unittest.TestCase):
    def expected(self,service):
        state=service.snapshot()
        return {key:state[key] for key in ('viewer_token','attempt_id')}

    def start(self,e):
        e.teleoperation.start(fixtures.TeleoperationTests().command())
        e.glove.stdin=io.StringIO()
        return self.expected(e.teleoperation)

    def test_same_viewer_failed_preflight_retry_has_a_different_attempt(self):
        with fixtures.TeleoperationTests().environment() as e:
            with patch.object(e.teleoperation,'_runtime',side_effect=[ValueError('unprepared'),{'mode':'wsl'}]):
                with self.assertRaisesRegex(ValueError,'unprepared'):
                    e.teleoperation.start(fixtures.TeleoperationTests().command())
                old=self.expected(e.teleoperation)
                new=self.start(e)
            self.assertEqual(old['viewer_token'],new['viewer_token'])
            self.assertNotEqual(old['attempt_id'],new['attempt_id'])
            with self.assertRaisesRegex(ValueError,'attempt changed'):e.teleoperation.cancel(old)
            self.assertFalse(e.teleoperation._cancel);self.assertEqual(e.glove.stdin.getvalue(),'')
            e.teleoperation.cancel(new)
            self.assertEqual(e.glove.stdin.getvalue(),'{"name": "glove_disconnect"}\n')

    def test_invalid_or_stale_scope_does_not_disconnect_the_current_ready_session(self):
        with fixtures.TeleoperationTests().environment() as e:
            current=self.start(e)
            for invalid in ({},{'viewer_token':current['viewer_token']},
                    {**current,'viewer_token':'b'*32},{**current,'attempt_id':'b'*32},
                    {**current,'serial':'OTHER'}):
                with self.subTest(invalid=invalid),self.assertRaisesRegex(ValueError,'attempt changed'):
                    e.teleoperation.cancel(invalid)
            self.assertFalse(e.teleoperation._cancel);self.assertFalse(e.glove.stopping)
            self.assertEqual(e.glove.stdin.getvalue(),'')

    def test_completed_preview_or_follow_is_not_a_pending_handover_cancel(self):
        for active in (False,True):
            with self.subTest(active=active),fixtures.TeleoperationTests().environment() as e:
                expected=self.start(e)
                e.glove.state.update(connection='receiving',hardware={'active':active},
                    feedback={'device_id':'HAND'} if active else {})
                old=copy.deepcopy(e.teleoperation.state)
                with self.assertRaisesRegex(ValueError,'handover finished'):e.teleoperation.cancel(expected)
                self.assertEqual(e.teleoperation.state,old);self.assertFalse(e.teleoperation._cancel)
                self.assertFalse(e.glove.stopping);self.assertEqual(e.glove.stdin.getvalue(),'')

    def test_destination_stage_rejection_does_not_publish_cancel_intent(self):
        with fixtures.TeleoperationTests().environment() as e:
            expected=self.start(e);original=e.glove.disconnect_session
            def after_read(session,**kwargs):
                self.assertFalse(e.teleoperation.lock._is_owned())
                self.assertFalse(e.teleoperation._cancel)
                e.glove.state['connection']='receiving'
                return original(session,**kwargs)
            with patch.object(e.glove,'disconnect_session',side_effect=after_read):
                with self.assertRaises(CancellationNotDispatched):e.teleoperation.cancel(expected)
            self.assertFalse(e.teleoperation._cancel);self.assertFalse(e.glove.stopping)
            self.assertEqual(e.glove.stdin.getvalue(),'');self.assertIsNone(e.teleoperation.state['error'])
            self.assertEqual(e.teleoperation.snapshot()['phase'],'preview')

    def test_real_write_failure_stays_unconfirmed_and_is_not_a_no_send_refusal(self):
        class FlushError(io.StringIO):
            def flush(self):raise OSError('synthetic flush failure')
        with fixtures.TeleoperationTests().environment() as e:
            expected=self.start(e);e.glove.stdin=FlushError()
            with self.assertRaises(OSError):e.teleoperation.cancel(expected)
            self.assertIn('glove_disconnect',e.glove.stdin.getvalue())
            self.assertTrue(e.teleoperation._cancel);self.assertTrue(e.glove.stopping)
            self.assertIn('unconfirmed',e.teleoperation.snapshot()['error'])
            e.glove.state.update(connection='error',message=None)
            self.assertIn('unconfirmed',e.teleoperation.snapshot()['error'])
            self.assertIn('unconfirmed',e.teleoperation.snapshot()['cleanup_error'])

    def test_trusted_cancel_cannot_pick_up_a_replacement_attempt_between_locks(self):
        with fixtures.TeleoperationTests().environment() as e:
            self.start(e)
            original=e.teleoperation.lock
            class InterleavedLock:
                count=0
                def __enter__(lock):
                    original.acquire();lock.count+=1
                    if lock.count==2:
                        e.glove.session+=1
                        e.teleoperation.state.update(viewer_token='b'*32,attempt_id='c'*32,
                            sdk_session=e.glove.session,phase='starting',busy=True)
                        e.teleoperation._cancel=False
                    return lock
                def __exit__(lock,*_):original.release()
            e.teleoperation.lock=InterleavedLock()
            with self.assertRaises(ValueError):e.teleoperation.cancel()
            self.assertEqual(e.teleoperation.state['attempt_id'],'c'*32)
            self.assertEqual(e.teleoperation.state['phase'],'starting')
            self.assertFalse(e.teleoperation._cancel);self.assertFalse(e.glove.stopping)
            self.assertEqual(e.glove.stdin.getvalue(),'')

    def test_late_accepted_stop_does_not_overwrite_a_replacement_attempt(self):
        with fixtures.TeleoperationTests().environment() as e:
            expected=self.start(e)
            def after_accept(session,**kwargs):
                self.assertEqual(kwargs,{'pending_only':True})
                self.assertFalse(e.teleoperation.lock._is_owned())
                e.teleoperation.state.update(attempt_id='b'*32,sdk_session=session+1,
                                             phase='validating',busy=True)
            with patch.object(e.glove,'disconnect_session',side_effect=after_accept):
                result=e.teleoperation.cancel(expected)
            self.assertFalse(e.teleoperation._cancel)
            self.assertEqual(result['teleoperation_connection']['attempt_id'],'b'*32)
            self.assertEqual(result['teleoperation_connection']['phase'],'validating')

    def test_scoped_cancel_during_viewer_stop_is_sticky_without_starting_sdk(self):
        with fixtures.TeleoperationTests().environment() as e:
            original=e.viewer.stop_owned;cancel_errors=[]
            def stop(*args):
                expected=self.expected(e.teleoperation)
                def cancel():
                    try:e.teleoperation.cancel(expected)
                    except Exception as error:cancel_errors.append(str(error))
                thread=threading.Thread(target=cancel);thread.start();thread.join(1)
                self.assertFalse(thread.is_alive());self.assertEqual(cancel_errors,[])
                return original(*args)
            e.viewer.stop_owned=stop
            with self.assertRaisesRegex(ValueError,'cancelled'):
                e.teleoperation.start(fixtures.TeleoperationTests().command())
            e.controller.action.assert_not_called()
            self.assertTrue(e.teleoperation._cancel)


if __name__=='__main__':unittest.main()
