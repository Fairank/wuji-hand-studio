import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import macos_runtime as runtime
import macos_network as network
from bridge_config import DEFAULT, validate


class MacRuntimeTests(unittest.TestCase):
    def test_mode_cannot_be_used_on_windows(self):
        with patch('bridge_config.sys.platform', 'win32'):
            with self.assertRaises(ValueError):
                validate(dict(DEFAULT, mode='macvm'))

    def test_mode_is_valid_on_mac(self):
        with patch('bridge_config.sys.platform', 'darwin'):
            self.assertEqual(validate(dict(DEFAULT, mode='macvm'))['mode'], 'macvm')

    def test_argv_keeps_spaces_and_shell_metacharacters_as_one_argument(self):
        with patch.object(runtime, 'supported', return_value=True), patch.object(runtime, 'payload', return_value=Path('/Applications/Hand Workbench.app/Contents/Resources/mac-runtime')):
            cmd = runtime.args(['/bin/echo', 'one two; $(echo nope)'])
        self.assertEqual(cmd[-1], 'one two; $(echo nope)')
        self.assertEqual(cmd[-3], '--')
        self.assertNotIn('/bin/sh', cmd)
        self.assertIn('hand-workbench', cmd)

    def test_invalid_argv_rejected(self):
        for value in ('rm', [], ['a\0b'], [1]):
            with self.assertRaises(ValueError):
                runtime.args(value)

    def test_file_scope_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(runtime, 'ROOT', Path(folder)):
            files = runtime.Files()
            files.root.mkdir()
            for path in ('/etc/passwd', '/opt/hand-workbench/../secrets', '/opt/hand-workbench-old/x', 'relative', '/opt/hand-workbench/a\\b'):
                with self.assertRaises(ValueError):
                    files.path(path)
            self.assertEqual(files.path('/opt/hand-workbench/a.json'), files.root / 'a.json')

    def test_vm_configuration_does_not_mount_home_or_import_keys(self):
        m = dict(image='ubuntu.raw', files={'ubuntu.raw': '0' * 64})
        with patch.object(runtime, 'ROOT', Path('/owned')):
            c = runtime.configuration(Path('/payload'), m)
        self.assertEqual(len(c['mounts']), 1)
        self.assertEqual(c['mounts'][0]['location'], str(Path('/owned/controller')))
        self.assertFalse(c['ssh']['forwardAgent'])
        self.assertFalse(c['ssh']['loadDotSSHPubKeys'])
        self.assertFalse(c['containerd']['user'])
        self.assertEqual(c['vmType'], 'vz')
        self.assertEqual(c['arch'], 'aarch64')

    def test_status_never_boots_or_calls_device(self):
        with patch.object(runtime, 'payload', side_effect=ValueError('missing')), patch.object(runtime.subprocess, 'run') as run:
            self.assertFalse(runtime.status()['ready'])
            run.assert_not_called()

    def test_running_guest_is_reused(self):
        row = json.dumps(dict(name=runtime.NAME, status='Running'))
        with patch.object(runtime, 'check_ready'), patch.object(runtime, 'run', return_value=row) as run:
            runtime.ensure_running()
            self.assertEqual(run.call_count, 1)

    def test_stopped_guest_starts_only_owned_name(self):
        row = json.dumps(dict(name=runtime.NAME, status='Stopped'))
        with patch.object(runtime, 'check_ready'), patch.object(runtime, 'run', side_effect=[row, '']) as run:
            runtime.ensure_running()
            self.assertEqual(run.call_args.args, ('start', '--tty=false', runtime.NAME))

    def test_transport_does_not_allow_another_command(self):
        with patch.object(runtime, 'ensure_running'), patch.object(runtime, 'args', side_effect=lambda x:x):
            client = runtime.MacController({}, 'hand2_left')
        with patch.object(runtime.subprocess, 'Popen') as popen:
            with self.assertRaises(ValueError):
                client.exec_command(['rm', '-rf', '/'])
            popen.assert_not_called()


class MacNetworkTests(unittest.TestCase):
    def adapter(self, device='en7', addresses=None, active=True):
        return dict(id=device, name='USB Ethernet', addresses=addresses or [], active=active)

    def test_wifi_and_bridge_excluded(self):
        text = 'Hardware Port: Wi-Fi\nDevice: en0\nEthernet Address: aa\n\nHardware Port: USB 10/100/1000 LAN\nDevice: en7\nEthernet Address: bb\n\nHardware Port: Thunderbolt Bridge\nDevice: bridge0\n'
        self.assertEqual([p['id'] for p in network.parse_ports(text)], ['en7'])

    def test_default_route_never_selected(self):
        r = network.classify([self.adapter()], 'en7', '192.168.1.110')
        self.assertEqual(r['candidates'], [])
        self.assertFalse(r['can_configure'])

    def test_other_subnet_not_overwritten(self):
        r = network.classify([self.adapter(addresses=['10.0.0.55'])], '', '192.168.1.110')
        self.assertEqual(r['code'], 'network_in_use')

    def test_link_local_is_eligible_for_alias(self):
        r = network.classify([self.adapter(addresses=['169.254.1.2'])], '', '192.168.1.110')
        self.assertEqual(r['code'], 'subnet_mismatch')
        self.assertTrue(r['can_configure'])

    def test_matching_subnet_is_ready(self):
        r = network.classify([self.adapter(addresses=['192.168.1.50'])], '', '192.168.1.110')
        self.assertEqual(r['code'], 'subnet_ready')

    def test_ambiguity_requires_choice(self):
        r = network.classify([self.adapter('en7'), self.adapter('en8')], '', '192.168.1.110')
        self.assertEqual(r['code'], 'multiple_adapters')
        self.assertIsNone(r['adapter'])

    def test_unplugged_adapter_not_configured(self):
        r = network.classify([self.adapter(active=False)], '', '192.168.1.110')
        self.assertEqual(r['code'], 'cable_unplugged')
        self.assertFalse(r['can_configure'])

    def test_injected_interface_rejected_before_system_call(self):
        with patch.object(network.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                network.configure('en7;touch /tmp/x')
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
