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
    def test_local_origin_is_exact(self):
        self.assertTrue(same_origin('http://127.0.0.1:8789/viewer#x','http://127.0.0.1:8789'))
        for url in ['https://127.0.0.1:8789/','http://127.0.0.1:8790/','file:///x','https://example.org','http://127.0.0.1.evil:8789/']:
            self.assertFalse(same_origin(url,'http://127.0.0.1:8789'))
