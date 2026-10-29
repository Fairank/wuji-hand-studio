"""CLI release recovery is checked before any unrelated session teardown."""
import copy
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock

from desktop_lifecycle import close_sessions,needs_confirmation
from studio_component import require_idle


def idle_state():
    return dict(connection='disconnected',hardware={'active':False},glove={'busy':False},
        official_cli={'busy':False,'update':{'recovery_required':True}})


class CLICloseRecoveryTests(unittest.TestCase):
    def fixture(self,mode):
        state=idle_state();effects=[]
        def cleanup(**kwargs):
            effects.append('cli_cleanup')
            if mode=='fail':return False
            if mode=='throw':raise OSError('synthetic marker is locked')
            if mode=='success':state['official_cli']['update']['recovery_required']=False
            return True
        c=N(snapshot=lambda:copy.deepcopy(state),doctor=N(snapshot=lambda:{'running':False}),
            official_cli=N(closed=threading.Event(),cancel_and_wait=Mock(side_effect=cleanup)),
            action=Mock(side_effect=lambda cmd:effects.append(cmd['name'])),
            player=N(command=Mock(side_effect=lambda cmd:effects.append(cmd['name']))))
        return c,state,effects

    def test_held_cli_is_not_idle_or_silent_exit_even_with_no_device(self):
        state=idle_state()
        self.assertTrue(needs_confirmation(state))
        with self.assertRaisesRegex(ValueError,'CLI'):require_idle(state)
        state['official_cli']['update']['recovery_required']=False
        require_idle(state);self.assertFalse(needs_confirmation(state))

    def test_failed_or_false_cleanup_keeps_every_other_session_untouched(self):
        for mode in ('fail','throw','still_held'):
            with self.subTest(mode=mode):
                c,state,effects=self.fixture(mode);result=close_sessions(c,timeout=.1)
                self.assertFalse(result['ok']);self.assertEqual(effects,['cli_cleanup'])
                self.assertTrue(state['official_cli']['update']['recovery_required'])
                c.action.assert_not_called();c.player.command.assert_not_called()

    def test_explicit_owned_cleanup_precedes_normal_close_without_false_idle(self):
        c,state,effects=self.fixture('success');result=close_sessions(c,timeout=.1)
        self.assertTrue(result['ok']);self.assertEqual(effects[0],'cli_cleanup')
        self.assertIn('disconnect',effects)
        self.assertFalse(state['official_cli']['update']['recovery_required'])


if __name__=='__main__':unittest.main()
