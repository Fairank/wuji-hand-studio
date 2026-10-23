"""A version reminder must not boot a stopped owned control environment."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from sdk_environment_update import probe_selected,AGENT,PYTHON


class BackgroundProbeTests(unittest.TestCase):
    def test_stopped_wsl_reads_running_list_but_does_not_launch_python(self):
        config=dict(mode='wsl',agent_directory=AGENT,python=PYTHON)
        run=Mock(return_value=SimpleNamespace(stdout='OtherController\r\n'.encode('utf-16-le')))
        with patch('managed_runtime.check_ready'),patch('managed_runtime.args') as arguments:
            with self.assertRaisesRegex(ValueError,'stopped'):probe_selected(config,run=run,background=True)
        arguments.assert_not_called();self.assertEqual(run.call_count,1)
        self.assertEqual(run.call_args.args[0],['wsl.exe','--list','--running','--quiet'])

    def test_stopped_macvm_reads_list_without_ensure_running_or_shell(self):
        import macos_runtime
        config=dict(mode='macvm',agent_directory=AGENT,python=PYTHON)
        report='{"name":"'+macos_runtime.NAME+'","status":"Stopped"}\n'
        with patch('macos_runtime.run',return_value=report) as listing,patch('macos_runtime.ensure_running') as boot,patch('macos_runtime.args') as shell:
            with self.assertRaisesRegex(ValueError,'stopped'):probe_selected(config,background=True)
        listing.assert_called_once_with('list','--json',macos_runtime.NAME,timeout=5)
        boot.assert_not_called();shell.assert_not_called()


if __name__=='__main__':unittest.main()
