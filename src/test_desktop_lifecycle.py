import unittest
from unittest.mock import Mock
from desktop_lifecycle import needs_confirmation,close_sessions
from native_desktop import same_origin

def state(active=False,connected=False,confirmed=True,glove=False):
    hw=dict(active=active,stop_confirmed=confirmed)
    return dict(connection='connected' if connected else 'disconnected',hardware=hw,
                glove=dict(busy=glove,hardware=hw),recording={},parameter_sync={})

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
