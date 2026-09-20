import unittest
from unittest.mock import patch,MagicMock
import managed_runtime as runtime
from bridge_config import DEFAULT,validate

class ManagedRuntimeTests(unittest.TestCase):
    def test_explicit_distro_without_shell_ssh_or_password(self):
        with patch.object(runtime.sys,'platform','win32'):
            command=runtime.args([runtime.PYTHON,'-u',runtime.AGENT+'/console_agent.py'])
        self.assertIn(runtime.NAME,command);self.assertIn('--exec',command)
        self.assertNotIn('sh',command);self.assertNotIn('ssh',command)
        self.assertEqual(command[command.index('--user')+1],'workbench')
    def test_path_traversal_rejected_before_filesystem_access(self):
        files=runtime.Files()
        for path in ['/etc/passwd','/opt/hand-workbench/../../etc/shadow','relative','/opt/hand-workbench/x\\y']:
            with self.assertRaises(ValueError):files.path(path)
    def test_other_platform_cannot_select_wsl(self):
        with patch('bridge_config.sys.platform','darwin'):
            with self.assertRaises(ValueError):validate(dict(DEFAULT,mode='wsl'))
    def test_wrong_controller_command_cannot_execute(self):
        with patch.object(runtime,'check_ready'),patch.object(runtime.sys,'platform','win32'):
            client=runtime.WslController({},'hand2_left')
            with patch.object(runtime.subprocess,'Popen') as launch:
                with self.assertRaises(ValueError):client.exec_command(['wsl.exe','--unregister',runtime.NAME])
                launch.assert_not_called()
    def test_status_does_not_start_linux(self):
        with patch.object(runtime,'registered',return_value=None),patch.object(runtime.subprocess,'run') as run:
            runtime.status();run.assert_not_called()
    def test_connection_and_reconfiguration_wait_for_setup(self):
        from console_server import Controller
        with patch.object(runtime,'status',return_value={'busy':True}):
            for command in ['connect','runtime_select','parameters_sync','bridge_config_save','doctor_run']:
                with self.assertRaisesRegex(ValueError,'Wait for controller setup'):
                    Controller.action(None,{'name':command})

if __name__=='__main__':unittest.main()
