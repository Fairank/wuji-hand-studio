import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from studio_component import StudioComponent,enforce_session_owner,lease_state,release_asset,require_idle,validate_version


class StudioComponentTests(unittest.TestCase):
    def idle(self):
        return dict(connection='disconnected',hardware=dict(active=False),glove=dict(busy=False),calibration=dict(running=False),
            recording=dict(active=False),parameter_sync=dict(busy=False),program=dict(active=False),group=dict(active=False))

    def wait(self,component):
        component.worker.join(timeout=4)
        self.assertFalse(component.worker.is_alive(),component.snapshot())

    def test_official_release_identity_is_not_replaceable_by_client_url(self):
        row=dict(tag_name='v2026.9.22',assets=[dict(name='wuji-studio_2026.9.22_amd64.deb',size=123,
            digest='sha256:'+'a'*64,browser_download_url='https://github.com/wuji-technology/wuji-studio/releases/download/v2026.9.22/wuji-studio_2026.9.22_amd64.deb')])
        self.assertEqual(release_asset(row)['version'],'2026.9.22')
        for key,value in [('size',True),('size',300*1024**2),('digest','sha256:unknown'),('browser_download_url','https://example.com/a.deb'),('name','other.deb')]:
            changed=copy.deepcopy(row);changed['assets'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):release_asset(changed)
        for value in ('../current',None,True,'2026.9.22;run','v2026.9.22'):
            with self.subTest(value=value),self.assertRaises(ValueError):validate_version(value)

    def test_every_active_or_unknown_workspace_state_blocks_handover(self):
        require_idle(self.idle(),dict(running=False))
        for section,field,value in [('hardware','active',True),('hardware','active',None),('glove','busy',True),('calibration','running',True),
            ('recording','active',True),('parameter_sync','busy',True),('program','active',True),('group','active',True)]:
            state=self.idle();state[section][field]=value
            with self.subTest(section=section,value=value),self.assertRaises(ValueError):require_idle(state)
        with self.assertRaises(ValueError):require_idle(self.idle(),dict(running=True))

    def test_reservation_blocks_all_workspace_start_paths_but_keeps_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            component=StudioComponent(tmp);component._reserve('starting')
            self.assertTrue(lease_state(tmp)['reserved'])
            for name in ('connect','runtime_install','glove_scan','glove_open','glove_prepare','glove_follow','hardware_trial',
                'calibration_start','doctor_run','doctor_support','group_prepare','program_start','parameters_sync'):
                with self.subTest(name=name),self.assertRaises(ValueError):enforce_session_owner(dict(name=name),tmp)
            for name in ('disconnect','hardware_stop','glove_stop','group_abort','retarget_context','view_camera','session_keepalive'):
                enforce_session_owner(dict(name=name),tmp)
            other=StudioComponent(tmp)
            with self.assertRaises(ValueError):other._reserve('installing')
            other._release();self.assertTrue(lease_state(tmp)['reserved'])
            component._release();self.assertFalse(lease_state(tmp)['reserved'])

    def test_failed_preflight_releases_marker_and_sends_no_linux_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=StudioComponent(tmp,idle_check=lambda:(_ for _ in ()).throw(ValueError('busy hand')));c.state['current']='2026.9.22'
            with patch.object(c,'_linux') as linux,self.assertRaisesRegex(ValueError,'busy hand'):c.open(offline=True)
            linux.assert_not_called();self.assertFalse(c.snapshot()['busy']);self.assertFalse(lease_state(tmp)['reserved'])

    def test_switch_preserves_user_files_and_keeps_previous_version(self):
        with tempfile.TemporaryDirectory() as tmp,patch('studio_component.sys.platform','win32'):
            c=StudioComponent(tmp);c.state.update(current='2026.9.22',previous='2026.8.17')
            private=Path(tmp)/'named-user-calibration';private.write_bytes(b'PRESERVE MY PROFILE')
            info=dict(versions=[dict(version='2026.9.22'),dict(version='2026.8.17')],dependencies={},client_ready=True)
            with patch.object(c,'_linux',side_effect=lambda operation:dict(active=False) if operation=='probe' else info):
                c.select('2026.8.17');self.wait(c)
            self.assertEqual((c.state['current'],c.state['previous']),('2026.8.17','2026.9.22'))
            self.assertEqual(private.read_bytes(),b'PRESERVE MY PROFILE');self.assertFalse(lease_state(tmp)['reserved'])
            self.assertEqual(StudioComponent(tmp).state['current'],'2026.8.17')

    def test_failed_switch_keeps_prior_selection_and_does_not_start_studio(self):
        with tempfile.TemporaryDirectory() as tmp,patch('studio_component.sys.platform','win32'):
            c=StudioComponent(tmp);c.state.update(current='2026.9.22',previous=None)
            with patch.object(c,'_linux',return_value=dict(active=True)) as linux:
                c.select('2026.8.17');self.wait(c)
            self.assertEqual(c.state['current'],'2026.9.22');self.assertEqual(c.state['phase'],'error')
            self.assertEqual(linux.call_args.args,('probe',));self.assertFalse(lease_state(tmp)['reserved'])

    def test_viewer_secret_is_not_in_snapshot_or_install_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=StudioComponent(tmp);c.state.update(active=True,phase='running');c.secret=dict(port=18901,password='private_nonce',session='s')
            self.assertNotIn('private_nonce',json.dumps(c.snapshot()));self.assertIn('#',c.viewer_url())
            c._persist();self.assertNotIn('private_nonce',(Path(tmp)/'installation.json').read_text())
            c.state['active']=False
            with self.assertRaises(ValueError):c.viewer_url()

    def test_corrupted_package_never_replaces_cached_release(self):
        class Response(io.BytesIO):
            def geturl(self):return 'https://release-assets.githubusercontent.com/test'
        with tempfile.TemporaryDirectory() as tmp:
            c=StudioComponent(tmp);item=dict(asset='wuji-studio_2026.9.22_amd64.deb',url='https://github.com/test',bytes=3,sha256=hashlib.sha256(b'abc').hexdigest())
            with patch('studio_component.urlopen',return_value=Response(b'xyz')):
                with self.assertRaisesRegex(ValueError,'SHA-256'):c._package(item)
            self.assertFalse((Path(tmp)/'packages'/item['asset']).exists())
            with patch('studio_component.urlopen',return_value=Response(b'abc')):path=c._package(item)
            with patch('studio_component.urlopen') as network:self.assertEqual(c._package(item),path);network.assert_not_called()

    def test_actual_external_session_marker_cannot_be_cleared_as_stale(self):
        with tempfile.TemporaryDirectory() as tmp,patch('studio_component.sys.platform','win32'):
            c=StudioComponent(tmp);c._reserve('running');c.token=None
            info=dict(versions=[dict(version='2026.9.22')],dependencies={},client_ready=True)
            with patch.object(c,'_linux',side_effect=[info,dict(active=True)]),patch('studio_component.process_identity',return_value=None):
                c.probe();self.wait(c)
            self.assertTrue(lease_state(tmp)['reserved']);self.assertTrue(c.state['active'])

    def test_only_explicit_component_operations_are_exposed(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=StudioComponent(tmp)
            for command in ({'name':'shell','command':'x'},{'name':'official_studio_enable'},{'name':'official_studio_install','version':False}):
                with self.subTest(command=command),self.assertRaises(ValueError):c.handle(command)
            for relative in ('../private','/etc/passwd','core\\file.js','config.toml'):
                with self.subTest(relative=relative),self.assertRaises(ValueError):c.asset(relative)

    def test_version_check_network_failure_uses_pinned_release_without_faking_freshness(self):
        with tempfile.TemporaryDirectory() as tmp,patch('studio_component.sys.platform','win32'):
            c=StudioComponent(tmp);c.state['current']='2026.9.22'
            with patch('studio_component.urlopen',side_effect=OSError('rate limited')):
                c.check_updates();self.wait(c)
            state=c.snapshot();self.assertEqual(state['phase'],'installed')
            self.assertEqual(state['catalog_source'],'bundled');self.assertIsNone(state['checked'])
            self.assertIn('rate limited',state['update_warning']);self.assertEqual(state['catalog'][0]['version'],'2026.9.22')

    def test_timeout_recovery_checks_actual_linux_exit_before_releasing_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=StudioComponent(tmp);c._reserve('running');c.state.update(active=True,phase='needs_check')
            with patch.object(c,'_linux',side_effect=[{},dict(active=True)]),self.assertRaisesRegex(ValueError,'still running'):c.stop()
            self.assertTrue(lease_state(tmp)['reserved'])
            with patch.object(c,'_linux',side_effect=[{},dict(active=False)]):result=c.stop()
            self.assertFalse(result['device_disable_confirmed']);self.assertFalse(lease_state(tmp)['reserved'])
            self.assertFalse(c.snapshot()['active'])

    def test_package_transfer_uses_owned_filesystem_without_windows_mount(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=StudioComponent(tmp);package=Path(tmp)/'download.deb';package.write_bytes(b'verified package')
            target=Path(tmp)/'guest/components/studio/packages/wuji-studio_2026.9.22_amd64.deb'
            with patch('managed_runtime.Files') as files:
                files.return_value.__enter__.return_value.path.return_value=target
                remote=c._stage_package(package,target.name)
                files.return_value.__enter__.return_value.path.assert_called_once_with(remote)
            self.assertTrue(remote.startswith('/opt/hand-workbench/'))
            self.assertEqual(target.read_bytes(),package.read_bytes());self.assertFalse(target.with_suffix('.pending.deb').exists())

    def test_request_waiting_for_controller_lock_rechecks_studio_ownership(self):
        from console_server import Controller
        with tempfile.TemporaryDirectory() as tmp,patch('studio_component.root_folder',return_value=Path(tmp)/'studio'):
            c=Controller(reports=Path(tmp)/'reports');component=StudioComponent();checked=threading.Event();errors=[]
            original=enforce_session_owner
            def gate(command):
                result=original(command);checked.set();return result
            def request():
                try:c.action(dict(name='connect'))
                except ValueError as error:errors.append(str(error))
            with patch('studio_component.enforce_session_owner',side_effect=gate),patch('managed_runtime.status',return_value=dict(busy=False)),patch.object(c,'_action') as execute:
                with c.lock:
                    worker=threading.Thread(target=request);worker.start();self.assertTrue(checked.wait(2));component._reserve('starting')
                worker.join(timeout=3);self.assertFalse(worker.is_alive());execute.assert_not_called()
            self.assertEqual(len(errors),1);self.assertIn('Studio',errors[0]);component._release()


if __name__=='__main__':unittest.main()
