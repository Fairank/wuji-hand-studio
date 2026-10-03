import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import macos_runtime as runtime


class MacControllerPortTests(unittest.TestCase):
    def test_controller_deploys_packaged_source_and_uses_versioned_bootstrap(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root
            (source / 'agent_bootstrap.py').write_text('# packaged source\n', encoding='utf-8')
            deployed = '/opt/hand-workbench/code/0123456789abcdef'
            with patch.object(runtime, 'RESOURCE', root), \
                 patch.object(runtime, 'ensure_running') as running, \
                 patch.object(runtime, 'Files') as files_type, \
                 patch('controller_bundle.ensure_version', return_value=deployed) as deploy, \
                 patch('controller_launch.parameter_environment', return_value='WUJI_PARAMETERS_JSON={}'), \
                 patch.dict(os.environ, {'WUJI_FLEET_ID': 'device-session-7'}), \
                 patch.object(runtime, 'args', side_effect=lambda values: values) as make_args:
                client = runtime.MacController({}, 'hand2_left')

            running.assert_called_once_with()
            files_type.assert_called_once_with()
            deploy.assert_called_once_with(source, files_type.return_value.__enter__.return_value)
            self.assertEqual(client.agent_directory, deployed)
            command = make_args.call_args.args[0]
            self.assertIn('WUJI_SESSION_ID=device-session-7', command)
            self.assertEqual(command[-1], deployed + '/agent_bootstrap.py')
            self.assertEqual(command[command.index(runtime.PYTHON) - 1], 'WUJI_MANAGED_RUNTIME=macvm')

    def test_frozen_controller_source_must_include_bootstrap(self):
        with tempfile.TemporaryDirectory() as folder:
            resources = Path(folder)
            (resources / 'controller-source').mkdir()
            with patch.object(runtime, 'RESOURCE', resources), \
                 patch.object(runtime.sys, 'frozen', True, create=True), \
                 patch.object(runtime, 'ensure_running') as running, \
                 patch('controller_bundle.ensure_version') as deploy:
                with self.assertRaisesRegex(ValueError, 'Controller source is missing'):
                    runtime.MacController({}, 'hand2_left')
                deploy.assert_not_called()
                running.assert_not_called()

    def test_stop_owned_refuses_busy_unowned_or_missing_vm(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(runtime, 'ROOT', Path(folder)), \
             patch.object(runtime, 'run') as run:
            root = Path(folder)
            (root / 'lima' / runtime.NAME).mkdir(parents=True)
            (root / 'lima' / runtime.NAME / 'lima.yaml').touch()
            owner = root / 'owner.json'
            owner.write_text('{"product":"other"}', encoding='utf-8')
            with patch.dict(runtime._state, busy=True):
                self.assertFalse(runtime.stop_owned())
            run.assert_not_called()
            self.assertFalse(runtime.stop_owned())
            run.assert_not_called()

            owner.write_text('{"product":"hand-workbench"}', encoding='utf-8')
            (root / 'lima' / runtime.NAME / 'lima.yaml').unlink()
            self.assertFalse(runtime.stop_owned())
            run.assert_not_called()

    def test_stop_owned_lists_exact_vm_and_stops_only_owned_running_instance(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(runtime, 'ROOT', Path(folder)), \
             patch.object(runtime, 'run', side_effect=[
                 '\n'.join((
                     json.dumps(dict(name='other-vm', status='Running')),
                     json.dumps(dict(name=runtime.NAME, status='Running')),
                 )),
                 '',
             ]) as run:
            root = Path(folder)
            owner = root / 'owner.json'
            owner.write_text('{"product":"hand-workbench"}', encoding='utf-8')
            config = root / 'lima' / runtime.NAME / 'lima.yaml'
            config.parent.mkdir(parents=True)
            config.touch()

            self.assertTrue(runtime.stop_owned())

        self.assertEqual(run.call_args_list[0].args, ('list', '--json', runtime.NAME))
        self.assertEqual(run.call_args_list[1].args, ('stop', runtime.NAME))
        self.assertEqual(run.call_args_list[1].kwargs, {'timeout': 60})

    def test_stop_owned_does_not_stop_non_running_vm(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(runtime, 'ROOT', Path(folder)), \
             patch.object(runtime, 'run', return_value=json.dumps(dict(name=runtime.NAME, status='Stopped'))) as run:
            root = Path(folder)
            (root / 'owner.json').write_text('{"product":"hand-workbench"}', encoding='utf-8')
            config = root / 'lima' / runtime.NAME / 'lima.yaml'
            config.parent.mkdir(parents=True)
            config.touch()

            self.assertTrue(runtime.stop_owned())

        run.assert_called_once_with('list', '--json', runtime.NAME)


if __name__ == '__main__':
    unittest.main()
