"""Synthetic runtime-cache and repair failures; no SDK, WSL or installer runs."""
import hashlib
import io
import json
import os
from pathlib import Path,PurePosixPath
import subprocess
import tarfile
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import managed_runtime as runtime
import runtime_refresh as refresh


PROJECT=Path(__file__).resolve().parents[3]
QA=Path(os.environ.get('WUJI_RUNTIME_QA_ROOT',str(PROJECT/'outputs/workbench-upgrade-0219/runtime')))


class LocalOwnedFiles:
    """Only the test's temporary directory stands in for the owned UNC tree."""
    def __init__(self,root):self.root=Path(root)
    def path(self,value):return self.root.joinpath(*PurePosixPath(value).relative_to(runtime.AGENT).parts)
    def __enter__(self):return self
    def __exit__(self,*_):pass


class RuntimeCacheTests(unittest.TestCase):
    def setUp(self):
        QA.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=QA);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.files=LocalOwnedFiles(self.root/'controller')
        self.files.root.mkdir()
        self.payload=b'SYNTHETIC RUNTIME IMAGE; NEVER EXECUTED\n'*128
        self.image=self.root/'image.tar';self.image.write_bytes(self.payload)
        self.manifest=dict(file='test-runtime.tar',sha256=hashlib.sha256(self.payload).hexdigest())
        self.destination=runtime.AGENT+'/components/runtime/'+self.manifest['file']
        self.target=self.files.path(self.destination)
        self.user=self.files.root/'motion_parameters.py';self.user.write_bytes(b'PRESERVE TEST TUNING')
        files=patch.object(runtime,'Files',return_value=self.files);files.start();self.addCleanup(files.stop)

    def cache(self,content):
        self.target.parent.mkdir(parents=True,exist_ok=True);self.target.write_bytes(content)

    def assert_clean(self,expected=None):
        if expected is None:self.assertFalse(self.target.exists())
        else:self.assertEqual(self.target.read_bytes(),expected)
        self.assertEqual(list(self.target.parent.glob('*.partial')),[])
        self.assertEqual(self.user.read_bytes(),b'PRESERVE TEST TUNING')

    def test_valid_owned_cache_is_reused_without_opening_source_or_copying(self):
        self.cache(self.payload);self.image.unlink()
        with patch.object(runtime.shutil,'copyfileobj',side_effect=AssertionError('Valid cache must not be copied')):
            self.assertEqual(runtime.stage_image(self.image,self.manifest),self.destination)
        self.assert_clean(self.payload)

    def test_bad_owned_cache_is_replaced_only_after_a_verified_transfer(self):
        self.cache(b'OLD CORRUPT CACHE')
        original_copy=runtime.shutil.copyfileobj
        with patch.object(runtime.shutil,'copyfileobj',wraps=original_copy) as copied:
            self.assertEqual(runtime.stage_image(self.image,self.manifest),self.destination)
        copied.assert_called_once();self.assert_clean(self.payload)

    def interrupted_copy(self,source,destination):
        destination.write(source.read(17));raise OSError('injected copy interruption')

    def test_interrupted_retransfer_preserves_the_previous_cache(self):
        self.cache(b'OLD CORRUPT CACHE')
        with patch.object(runtime.shutil,'copyfileobj',side_effect=self.interrupted_copy),self.assertRaisesRegex(OSError,'interruption'):
            runtime.stage_image(self.image,self.manifest)
        self.assert_clean(b'OLD CORRUPT CACHE')

    def test_interrupted_first_transfer_never_publishes_a_final_cache(self):
        with patch.object(runtime.shutil,'copyfileobj',side_effect=self.interrupted_copy),self.assertRaisesRegex(OSError,'interruption'):
            runtime.stage_image(self.image,self.manifest)
        self.assert_clean()

    def test_failed_hash_never_publishes_or_replaces_the_final_cache(self):
        self.image.write_bytes(b'WRONG SOURCE BYTES')
        for existing in (None,b'OLD CORRUPT CACHE'):
            with self.subTest(existing=existing):
                if existing is not None:self.cache(existing)
                with self.assertRaisesRegex(ValueError,'integrity'):
                    runtime.stage_image(self.image,self.manifest)
                self.assert_clean(existing)


class RuntimeComponentRepairTests(unittest.TestCase):
    def setUp(self):QA.mkdir(parents=True,exist_ok=True)

    def image(self,folder):
        image=folder/'synthetic-runtime.tar'
        files={refresh.ENV+'/lib/python3.12/site-packages/wuji_sdk-'+refresh.SDK_VERSION+'.dist-info/METADATA':b'FAKE NEW METADATA',
               refresh.ENV+'/lib/python3.12/site-packages/wuji_sdk/__init__.py':b'# FAKE NEW SDK; DO NOT IMPORT',
               refresh.CLI:b'FAKE NEW CLI; DO NOT EXECUTE',
               'home/workbench/.wuji/profiles/test.json':b'PAYLOAD MUST NOT OVERWRITE A USER',
               'opt/hand-workbench/motion_parameters.py':b'PAYLOAD MUST NOT OVERWRITE TUNING'}
        with tarfile.open(image,'w') as archive:
            for name,data in files.items():
                member=tarfile.TarInfo(name);member.size=len(data);member.mode=0o755
                archive.addfile(member,io.BytesIO(data))
        return image,hashlib.sha256(image.read_bytes()).hexdigest()

    def root(self,folder,have_env,have_cli):
        root=folder/'root';root.mkdir()
        if have_env:
            current=root/refresh.ENV;current.mkdir(parents=True)
            (current/'old-sdk.dat').write_bytes(b'ORIGINAL SDK BYTES')
        if have_cli:
            cli=root/refresh.CLI;cli.parent.mkdir(parents=True);cli.write_bytes(b'ORIGINAL CLI BYTES')
        for name,data in {
            'home/workbench/.wuji/profiles/test.json':b'ORIGINAL NAMED USER',
            'home/workbench/.wuji/models/left.npz':b'ORIGINAL SYNTHETIC MODEL BYTES',
            'opt/hand-workbench/motion_parameters.py':b'ORIGINAL TUNING',
            'opt/hand-workbench/sessions/saved.json':b'ORIGINAL SYNTHETIC SESSION',
        }.items():
            target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        return root

    def snapshot(self,root):return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}

    def build_fake_venv(self,path):
        (path/'bin').mkdir(parents=True);(path/'bin/python').write_bytes(b'FAKE PYTHON; DO NOT EXECUTE')

    def preserved(self,before,after):
        for name,data in before.items():
            if name.startswith(('home/','opt/hand-workbench/')):self.assertEqual(after[name],data,name)

    def test_missing_cli_or_venv_is_repaired_while_user_data_and_available_backups_survive(self):
        for have_env,have_cli in ((True,False),(False,True),(False,False)):
            with self.subTest(have_env=have_env,have_cli=have_cli),tempfile.TemporaryDirectory(dir=QA) as tmp:
                folder=Path(tmp);image,digest=self.image(folder);root=self.root(folder,have_env,have_cli);before=self.snapshot(root)
                with patch.object(refresh.venv,'EnvBuilder') as builder,patch.object(refresh.subprocess,'run',return_value=SimpleNamespace(stdout='wuji '+refresh.SDK_VERSION)) as run:
                    builder.return_value.create.side_effect=self.build_fake_venv
                    result=refresh.apply(image,digest,root)
                self.assertTrue(result['ok']);self.assertFalse(result['motion_started']);self.assertTrue(result['parameters_preserved'])
                self.assertEqual(run.call_count,3)
                self.assertTrue((root/refresh.ENV/'lib/python3.12/site-packages/wuji_sdk/__init__.py').is_file())
                self.assertEqual((root/refresh.CLI).read_bytes(),b'FAKE NEW CLI; DO NOT EXECUTE')
                self.assertEqual(result['sdk_backup'] is not None,have_env)
                self.assertEqual(result['cli_backup'] is not None,have_cli)
                if have_env:self.assertEqual((Path(result['sdk_backup'])/'old-sdk.dat').read_bytes(),b'ORIGINAL SDK BYTES')
                if have_cli:self.assertEqual(Path(result['cli_backup']).read_bytes(),b'ORIGINAL CLI BYTES')
                self.preserved(before,self.snapshot(root))
                self.assertFalse(list(root.rglob('*.pending-*')))

    def test_final_verification_failure_restores_files_and_original_absence_exactly(self):
        for have_env,have_cli in ((True,True),(True,False),(False,True),(False,False)):
            with self.subTest(have_env=have_env,have_cli=have_cli),tempfile.TemporaryDirectory(dir=QA) as tmp:
                folder=Path(tmp);image,digest=self.image(folder);root=self.root(folder,have_env,have_cli);before=self.snapshot(root)
                responses=[SimpleNamespace(),SimpleNamespace(stdout='wuji '+refresh.SDK_VERSION),subprocess.CalledProcessError(1,['FAKE FINAL VALIDATION'])]
                with patch.object(refresh.venv,'EnvBuilder') as builder,patch.object(refresh.subprocess,'run',side_effect=responses) as run:
                    builder.return_value.create.side_effect=self.build_fake_venv
                    with self.assertRaises(subprocess.CalledProcessError):refresh.apply(image,digest,root)
                self.assertEqual(run.call_count,3)
                self.assertEqual(self.snapshot(root),before)
                self.assertEqual((root/refresh.ENV).exists(),have_env)
                self.assertEqual((root/refresh.CLI).exists(),have_cli)
                self.assertFalse(list(root.rglob('*.pending-*')))
                self.assertFalse(list(root.rglob('*.backup-*')))


class RuntimeInstallDispatchTests(unittest.TestCase):
    def controller(self,status,compatible,busy=False):
        from console_server import Controller
        controller=Controller.__new__(Controller)
        controller.lock=threading.RLock();controller.state=dict(connection='disconnected',hardware=dict(active=False))
        controller.glove=SimpleNamespace(busy=False);controller.doctor=SimpleNamespace(snapshot=lambda:dict(running=False))
        controller.parameters=SimpleNamespace(sync_status=dict(busy=False))
        controller.official_tools=SimpleNamespace(snapshot=lambda:dict(runtime=dict(ready=True),check=dict(status=status,compatible=compatible,busy=busy)))
        return controller

    def dispatch(self,controller):
        from console_server import Controller
        return Controller.action(controller,dict(name='runtime_install'))

    def test_ready_install_record_does_not_prevent_repair_after_mismatch_or_check_failure(self):
        for status in ('ready','failed'):
            with self.subTest(status=status),patch('studio_component.enforce_session_owner'),patch.object(runtime,'status',return_value=dict(ready=True,busy=False)),patch.object(runtime,'start_install',return_value=dict(busy=True)) as install:
                self.assertEqual(self.dispatch(self.controller(status,False)),dict(runtime=dict(busy=True)))
                install.assert_called_once_with(repair=True)

    def test_unchecked_or_compatible_software_does_not_request_a_repair(self):
        for status,compatible in (('unchecked',False),('ready',True)):
            with self.subTest(status=status,compatible=compatible),patch('studio_component.enforce_session_owner'),patch.object(runtime,'start_install',return_value={}) as install:
                self.dispatch(self.controller(status,compatible))
                install.assert_called_once_with(repair=False)

    def test_in_progress_check_cannot_start_an_install(self):
        with patch('studio_component.enforce_session_owner'),patch.object(runtime,'start_install') as install:
            with self.assertRaisesRegex(ValueError,'Wait for the component check'):
                self.dispatch(self.controller('checking',False,busy=True))
        install.assert_not_called()


class RuntimeInstallRepairFlowTests(unittest.TestCase):
    def test_ready_runtime_repair_uses_owned_transfer_and_refresh_without_mounted_windows_drive(self):
        QA.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=QA) as tmp:
            folder=Path(tmp);host=folder/'host';host.mkdir();resource=folder/'source';resource.mkdir()
            (resource/'runtime_refresh.py').write_bytes(b'# SYNTHETIC PUBLIC HELPER; NEVER EXECUTED')
            image=folder/'image.tar';image.write_bytes(b'SYNTHETIC VERIFIED IMAGE')
            manifest=dict(file='test-runtime.tar',sha256=hashlib.sha256(image.read_bytes()).hexdigest(),runtime_version='1.1.0',sdk_version=refresh.SDK_VERSION,cli_version=refresh.SDK_VERSION)
            marker=host/'runtime-info.json';marker.write_text(json.dumps(dict(runtime_version='1.1.0',sdk_version=refresh.SDK_VERSION)),encoding='utf-8')
            private=host/'user-data.json';private.write_bytes(b'ORIGINAL HOST USER DATA')
            files=LocalOwnedFiles(folder/'owned-controller');files.root.mkdir()
            status=dict(ready=True,installed=True,busy=False)
            class InlineThread:
                def __init__(self,target,**_):self.target=target
                def start(self):self.target()
            responses=[SimpleNamespace(returncode=0,stdout=b'',stderr=b''),SimpleNamespace(returncode=0,stdout=b'{}',stderr=b''),SimpleNamespace(returncode=0,stdout=b'',stderr=b''),SimpleNamespace(returncode=0,stdout=('wuji '+refresh.SDK_VERSION).encode(),stderr=b'')]
            with (
                patch.object(runtime.sys,'platform','win32'),
                patch.object(runtime,'ROOT',host),
                patch.object(runtime,'RESOURCE',resource),
                patch.object(runtime,'_state',dict(busy=False,stage='idle',error=None)),
                patch.object(runtime,'_lock',threading.Lock()),
                patch.object(runtime,'_image',return_value=(image,manifest)),
                patch.object(runtime,'registered',return_value=dict(name=runtime.NAME,version=2)),
                patch.object(runtime,'status',return_value=status),
                patch.object(runtime,'Files',return_value=files),
                patch.object(runtime,'select') as select,
                patch.object(runtime.threading,'Thread',InlineThread),
                patch.object(runtime.subprocess,'run',side_effect=responses) as run,
                patch.object(runtime.subprocess,'CREATE_NO_WINDOW',0,create=True),
            ):
                runtime.start_install(repair=True)
                self.assertEqual(runtime._state,dict(busy=False,stage='ready',error=None))
                calls=[call.args[0] for call in run.call_args_list]
                self.assertEqual(len(calls),4)
                refresh_command=calls[1]
                self.assertIn(runtime.AGENT+'/runtime_refresh.py',refresh_command)
                self.assertIn(runtime.AGENT+'/components/runtime/test-runtime.tar',refresh_command)
                self.assertIn(manifest['sha256'],refresh_command)
                self.assertFalse(any('wslpath' in part or '/mnt/' in part or '--import'==part for command in calls for part in command))
                select.assert_called_once_with()
            self.assertEqual(files.path(runtime.AGENT+'/components/runtime/test-runtime.tar').read_bytes(),image.read_bytes())
            self.assertEqual(private.read_bytes(),b'ORIGINAL HOST USER DATA')
            self.assertEqual(json.loads(marker.read_text(encoding='utf-8'))['runtime_version'],'1.1.0')


if __name__=='__main__':unittest.main()
