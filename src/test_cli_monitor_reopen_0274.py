"""A confirmed failed close may resume scheduling only after explicit success."""
from contextlib import contextmanager
import copy,os
from pathlib import Path
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from cli_coordination import atomic
from desktop_lifecycle import close_sessions
import test_cli_lease_recovery_0262 as fixtures


class CLIMonitorReopenTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        root=Path(os.environ.get('WUJI_CONTROL_QA_ROOT',tempfile.gettempdir()))/'cli-monitor-reopen-0274'
        with (patch.object(fixtures,'BASE',root),fixtures.CLILeaseRecoveryTests.environment(self) as values,
                patch.dict(os.environ,{'WUJI_CLI_AUTO_CHECK':'1','WUJI_FLEET_ID':''})):
            owner,store,old,new=values
            atomic(owner.folder/'update-preferences.json',{'native_auto_update':True})
            store.fetch_release.return_value={'tag_name':'v'+fixtures.OLD}
            owner.startup();owner.thread.join(2)
            monitors=[owner.monitor]
            try:yield owner,store,old,new,monitors
            finally:
                monitors.append(owner.monitor);owner.closed.set();owner.monitor_stop.set()
                for monitor in set(monitors):
                    if monitor:monitor.join(2);self.assertFalse(monitor.is_alive())
                if owner.thread:owner.thread.join(2);self.assertFalse(owner.thread.is_alive())

    def failed_close(self,owner):
        clock=[0.];state=dict(connection='disconnected',hardware=dict(active=False,stop_confirmed=True),
            glove={},calibration={},official_cli=owner.snapshot(),unconfirmed_devices=[])
        controller=N(official_cli=owner,doctor=N(snapshot=lambda:dict(running=False)),
            snapshot=lambda:copy.deepcopy(state),player=N(command=Mock()),action=Mock())
        original=owner.cancel_and_wait
        def late(**kw):result=original(**kw);clock[0]=11.;return result
        old_monitor,old_stop=owner.monitor,owner.monitor_stop
        with patch.object(owner,'cancel_and_wait',side_effect=late):
            result=close_sessions(controller,deadline=10.,clock=lambda:clock[0])
        self.assertFalse(result['ok']);old_monitor.join(2)
        self.assertFalse(old_monitor.is_alive());self.assertTrue(old_stop.is_set())
        return old_monitor,old_stop

    def test_whole_close_timeout_then_explicit_check_rearms_the_existing_schedule(self):
        with self.environment() as (owner,store,old,new,monitors):
            prior,stop=self.failed_close(owner)
            owner.start(check_only=True);owner.thread.join(2);monitors.append(owner.monitor)
            self.assertEqual(owner.snapshot()['update']['phase'],'checked')
            self.assertFalse(owner.closed.is_set());self.assertTrue(stop.is_set())
            self.assertIsNot(owner.monitor,prior);self.assertIsNot(owner.monitor_stop,stop)
            self.assertTrue(owner.monitor.is_alive());self.assertFalse(owner.monitor_stop.is_set())
            self.assertTrue(owner.snapshot()['update']['auto_update'])

    def test_successful_close_poll_startup_and_automatic_refusal_do_not_restart_a_monitor(self):
        with self.environment() as (owner,store,old,new,monitors):
            prior=owner.monitor
            self.assertTrue(owner.cancel_and_wait(timeout=.1));prior.join(2)
            owner.snapshot();owner.startup()
            with self.assertRaises(ValueError):owner.start(check_only=True,automatic=True)
            self.assertIs(owner.monitor,prior);self.assertFalse(prior.is_alive());self.assertTrue(owner.monitor_stop.is_set())

    def test_manual_failure_and_later_automatic_check_cannot_rearm_but_explicit_success_can(self):
        with self.environment() as (owner,store,old,new,monitors):
            prior,_=self.failed_close(owner)
            store.fetch_release.side_effect=OSError('synthetic metadata offline')
            owner.start(check_only=True);owner.thread.join(2)
            self.assertEqual(owner.state['phase'],'error');self.assertIs(owner.monitor,prior)
            store.fetch_release.side_effect=None
            owner.start(check_only=True,automatic=True);owner.thread.join(2)
            self.assertIs(owner.monitor,prior);self.assertFalse(prior.is_alive())
            owner.start(check_only=True);owner.thread.join(2);monitors.append(owner.monitor)
            self.assertTrue(owner.monitor.is_alive());self.assertIsNot(owner.monitor,prior)

    def test_unknown_lease_after_manual_update_never_rearms_until_exact_explicit_recovery(self):
        with self.environment() as (owner,store,old,new,monitors):
            prior,_=self.failed_close(owner);store.fetch_release.return_value={'tag_name':'v'+fixtures.NEW}
            with patch('native_cli_maintenance.release',side_effect=PermissionError('synthetic retained lease')):
                owner.start();owner.thread.join(2)
            self.assertTrue(owner.state['recovery_required']);self.assertIs(owner.monitor,prior)
            self.assertFalse(prior.is_alive())
            owner.start(check_only=True);owner.thread.join(2);monitors.append(owner.monitor)
            self.assertFalse(owner.state['recovery_required']);self.assertTrue(owner.monitor.is_alive())

    def test_successful_explicit_rollback_rearms_but_retired_monitor_wake_cannot_dispatch(self):
        with self.environment() as (owner,store,old,new,monitors):
            store.fetch_release.return_value={'tag_name':'v'+fixtures.NEW}
            owner.start();owner.thread.join(2)
            prior,stop=self.failed_close(owner)
            owner.rollback();monitors.append(owner.monitor)
            self.assertTrue(owner.monitor.is_alive());self.assertIsNot(owner.monitor,prior)
            with patch.object(stop,'wait',side_effect=[False,True]),patch.object(owner,'start') as start:
                owner._monitor(stop)
                start.assert_not_called()


if __name__=='__main__':unittest.main()
