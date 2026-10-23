import unittest
from unittest.mock import Mock
from desktop_lifecycle import needs_confirmation,close_sessions
from native_desktop import same_origin

def state(active=False,connected=False,confirmed=True,glove=False):
    hw=dict(active=active,stop_confirmed=confirmed)
    return dict(connection='connected' if connected else 'disconnected',hardware=hw,
                glove=dict(busy=glove,hardware=hw if glove else {}),recording={},parameter_sync={})

class ExitTests(unittest.TestCase):
    def controller(self,s):
        c=Mock();c.snapshot.return_value=s;c.doctor.snapshot.return_value={};c.glove.busy=False;return c
    def test_idle_exit(self):
        c=self.controller(state());self.assertFalse(needs_confirmation(state()))
        self.assertTrue(close_sessions(c,sleep=lambda _:None)['ok'])
        self.assertEqual(c.action.call_args.args[0],{'name':'disconnect'})
    def test_active_waits_for_confirmed_disable_before_disconnect(self):
        c=self.controller(state(True,True,False));c.snapshot.side_effect=[state(True,True,False),state(False,True,True)]
        self.assertTrue(close_sessions(c,sleep=lambda _:None)['ok'])
        self.assertEqual([x.args[0]['name'] for x in c.action.call_args_list],['hardware_stop','disconnect'])
    def test_failed_disable_does_not_disconnect_or_exit(self):
        c=self.controller(state(None,True,False));ticks=iter([0,1])
        self.assertFalse(close_sessions(c,timeout=.5,clock=lambda:next(ticks),sleep=lambda _:None)['ok'])
        self.assertEqual([x.args[0]['name'] for x in c.action.call_args_list],['hardware_stop'])
    def test_glove_shutdown(self):
        c=self.controller(state(glove=True))
        self.assertTrue(close_sessions(c,sleep=lambda _:None)['ok'])
        self.assertEqual([x.args[0]['name'] for x in c.action.call_args_list],['glove_disconnect','disconnect'])
    def test_background_cli_cancel_waits_only_after_device_stop(self):
        s=state(True,True,False);s['official_cli']={'update':{'preparing':True}}
        c=self.controller(s);c.snapshot.side_effect=[s,state(False,True,True)]
        events=[]
        c.official_cli.closed.set.side_effect=lambda:events.append('cancel')
        c.action.side_effect=lambda command:events.append(command['name'])
        c.official_cli.cancel_and_wait.side_effect=lambda:events.append('wait') or True
        self.assertTrue(close_sessions(c,sleep=lambda _:None)['ok'])
        self.assertEqual(events,['cancel','hardware_stop','disconnect','wait'])
    def test_background_download_can_cancel_on_an_idle_exit(self):
        from desktop_tools import idle
        s=state();s['official_cli']={'update':{'preparing':True}}
        self.assertFalse(needs_confirmation(s));self.assertTrue(idle(s,{}))
        c=self.controller(s);c.official_cli.cancel_and_wait.return_value=True
        self.assertTrue(close_sessions(c,sleep=lambda _:None)['ok'])
        c.official_cli.closed.set.assert_called_once();c.official_cli.cancel_and_wait.assert_called_once()
    def test_parameter_transfer_is_not_cut_off(self):
        s=state();s['parameter_sync']['busy']=True;c=self.controller(s)
        self.assertFalse(close_sessions(c)['ok']);c.action.assert_not_called()
    def test_studio_installation_is_not_interrupted_by_window_exit(self):
        s=state();s['official_studio']=dict(busy=True,active=False,reserved=True);c=self.controller(s)
        self.assertTrue(needs_confirmation(s));self.assertFalse(close_sessions(c)['ok'])
        c.studio.stop.assert_not_called();c.action.assert_not_called()
    def test_unconfirmed_studio_exit_keeps_workbench_open(self):
        s=state();s['official_studio']=dict(active=True,reserved=True);c=self.controller(s)
        c.studio.stop.side_effect=ValueError('Studio exit not confirmed')
        self.assertFalse(close_sessions(c)['ok']);c.action.assert_not_called()
    def test_studio_close_does_not_become_a_motor_disable_claim(self):
        s=state();s['official_studio']=dict(active=True,reserved=True);c=self.controller(s)
        c.studio.stop.return_value=dict(component_closed=True,device_disable_confirmed=False)
        self.assertTrue(close_sessions(c,sleep=lambda _:None)['ok']);c.studio.stop.assert_called_once()
        self.assertEqual([x.args[0]['name'] for x in c.action.call_args_list],['disconnect'])
    def test_active_calibration_must_release_before_exit(self):
        s=state();s['calibration']={'running':True};c=self.controller(s);c.calibration.run={'running':True}
        self.assertTrue(needs_confirmation(s))
        ticks=iter([0,1])
        self.assertFalse(close_sessions(c,timeout=.5,clock=lambda:next(ticks),sleep=lambda _:None)['ok'])
        self.assertEqual([x.args[0]['name'] for x in c.action.call_args_list],['calibration_cancel'])
    def test_ended_but_unconfirmed_calibration_requires_confirmation_and_keeps_window_open(self):
        for nested in (False,True):
            with self.subTest(nested=nested):
                s=state();run=dict(running=False,id='a'*32,source='official_jsonl_native',cleanup_confirmed=False)
                s['calibration']={'run':run,'cleanup_required':True} if nested else dict(run,cleanup_required=True)
                c=self.controller(s);c.calibration.run=run
                self.assertTrue(needs_confirmation(s))
                self.assertFalse(close_sessions(c,sleep=lambda _:None)['ok'])
                self.assertEqual([x.args[0]['name'] for x in c.action.call_args_list],['calibration_cancel'])
    def test_ended_calibration_can_close_only_after_explicit_cleanup_recovery(self):
        s=state();s['calibration']=dict(running=False,id='a'*32,source='official_tty',cleanup_confirmed=False,cleanup_required=True)
        c=self.controller(s);c.calibration.run=s['calibration']
        def action(command):
            if command['name']=='calibration_cancel':s['calibration'].update(cleanup_confirmed=True,cleanup_required=False)
        c.action.side_effect=action
        self.assertTrue(close_sessions(c,sleep=lambda _:None)['ok'])
        self.assertEqual([x.args[0]['name'] for x in c.action.call_args_list],['calibration_cancel','disconnect'])
    def test_failed_cleanup_recovery_never_disconnects_other_sessions(self):
        for error in (ValueError('owned by another workspace'),OSError('cleanup report unavailable')):
            with self.subTest(error=error):
                s=state();s['calibration']=dict(running=False,cleanup_required=True)
                c=self.controller(s);c.calibration.run={'running':False};c.action.side_effect=error
                self.assertFalse(close_sessions(c,sleep=lambda _:None)['ok'])
                self.assertEqual([x.args[0]['name'] for x in c.action.call_args_list],['calibration_cancel'])
    def test_calibration_owner_changed_during_recovery_keeps_window_open(self):
        s=state();s['calibration']=dict(running=False,id='a'*32,cleanup_required=True)
        c=self.controller(s);c.calibration.run={'running':False}
        def action(command):
            if command['name']=='calibration_cancel':s['calibration']=dict(running=False,id='b'*32,cleanup_confirmed=True)
        c.action.side_effect=action
        self.assertFalse(close_sessions(c,sleep=lambda _:None)['ok'])
        self.assertEqual([x.args[0]['name'] for x in c.action.call_args_list],['calibration_cancel'])
    def test_pending_handover_is_not_an_idle_closed_session_and_can_retry(self):
        for key in ('calibration_connection','teleoperation_connection'):
            with self.subTest(key=key):
                s=state();s[key]=dict(busy=True,phase='starting')
                c=self.controller(s)
                self.assertTrue(needs_confirmation(s))
                result=close_sessions(c,sleep=lambda _:None)
                self.assertFalse(result['ok']);self.assertIn('handover',result['error'])
                c.action.assert_not_called();c.official_cli.closed.set.assert_not_called()
                s[key]['busy']=False
                self.assertTrue(close_sessions(c,sleep=lambda _:None)['ok'])
    def test_local_origin_is_exact(self):
        self.assertTrue(same_origin('http://127.0.0.1:8789/viewer#x','http://127.0.0.1:8789'))
        for url in ['https://127.0.0.1:8789/','http://127.0.0.1:8790/','file:///x','https://example.org','http://127.0.0.1.evil:8789/']:
            self.assertFalse(same_origin(url,'http://127.0.0.1:8789'))
