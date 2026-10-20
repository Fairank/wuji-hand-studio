import hashlib
import io
from pathlib import Path
import subprocess
from types import SimpleNamespace
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from runtime_refresh import apply,image_members,ENV,CLI,SDK_VERSION


class RuntimeRefreshTests(unittest.TestCase):
    def image(self,folder,extra=None):
        image=Path(folder)/'image.tar'
        files={ENV+'/lib/python3.12/site-packages/wuji_sdk-'+SDK_VERSION+'.dist-info/METADATA':b'new metadata',
               ENV+'/lib/python3.12/site-packages/wuji_sdk/__init__.py':b'# new SDK',
               CLI:b'new CLI','home/workbench/.wuji/private':b'must not import'}
        files.update(extra or {})
        with tarfile.open(image,'w') as tar:
            for name,data in files.items():
                member=tarfile.TarInfo(name);member.size=len(data);member.mode=0o755
                tar.addfile(member,io.BytesIO(data))
        return image,hashlib.sha256(image.read_bytes()).hexdigest()

    def runtime(self,folder):
        root=Path(folder)/'root';current=root/ENV;current.mkdir(parents=True)
        (current/'old-sdk').write_text('retained old')
        cli=root/CLI;cli.parent.mkdir(parents=True);cli.write_bytes(b'old CLI')
        user=root/'home/workbench/.wuji';user.mkdir(parents=True);(user/'private').write_bytes(b'PERSONAL')
        parameters=root/'opt/hand-workbench/motion_parameters.py';parameters.parent.mkdir();parameters.write_bytes(b'MY TUNING')
        return root

    def stage(self,path):
        (path/'bin').mkdir(parents=True);(path/'bin/python').write_text('fake Python')

    def test_verified_update_keeps_original_environment_and_user_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            image,digest=self.image(tmp);root=self.runtime(tmp)
            with patch('runtime_refresh.venv.EnvBuilder') as builder,patch('runtime_refresh.subprocess.run',return_value=SimpleNamespace(stdout='wuji '+SDK_VERSION)):
                builder.return_value.create.side_effect=self.stage
                result=apply(image,digest,root)
            self.assertTrue(result['ok'])
            self.assertEqual((Path(result['sdk_backup'])/'old-sdk').read_text(),'retained old')
            self.assertFalse((root/ENV/'old-sdk').exists())
            self.assertEqual(Path(result['cli_backup']).read_bytes(),b'old CLI')
            self.assertEqual((root/CLI).read_bytes(),b'new CLI')
            self.assertEqual((root/'home/workbench/.wuji/private').read_bytes(),b'PERSONAL')
            self.assertEqual((root/'opt/hand-workbench/motion_parameters.py').read_bytes(),b'MY TUNING')

    def test_final_validation_failure_restores_both_runtime_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            image,digest=self.image(tmp);root=self.runtime(tmp)
            responses=[SimpleNamespace(),SimpleNamespace(stdout='wuji '+SDK_VERSION),subprocess.CalledProcessError(1,['python'])]
            with patch('runtime_refresh.venv.EnvBuilder') as builder,patch('runtime_refresh.subprocess.run',side_effect=responses):
                builder.return_value.create.side_effect=self.stage
                with self.assertRaises(subprocess.CalledProcessError):apply(image,digest,root)
            self.assertEqual((root/ENV/'old-sdk').read_text(),'retained old')
            self.assertEqual((root/CLI).read_bytes(),b'old CLI')
            self.assertFalse(list((root/'opt').glob('*.pending-*')))

    def test_pre_swap_cli_version_failure_keeps_old_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            image,digest=self.image(tmp);root=self.runtime(tmp)
            with patch('runtime_refresh.venv.EnvBuilder') as builder,patch('runtime_refresh.subprocess.run',return_value=SimpleNamespace(stdout='wrong version')):
                builder.return_value.create.side_effect=self.stage
                with self.assertRaisesRegex(ValueError,'CLI version'):apply(image,digest,root)
            self.assertTrue((root/ENV/'old-sdk').exists());self.assertEqual((root/CLI).read_bytes(),b'old CLI')

    def test_bad_hash_traversal_and_missing_sdk_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            image,digest=self.image(tmp)
            with self.assertRaisesRegex(ValueError,'integrity'):image_members(image,'0'*64)
            image,digest=self.image(tmp,{'../../escape':b'x'})
            with self.assertRaisesRegex(ValueError,'Unsafe'):image_members(image,digest)
            with tarfile.open(image,'w') as tar:pass
            with self.assertRaisesRegex(ValueError,'missing'):image_members(image,hashlib.sha256(image.read_bytes()).hexdigest())

    def test_payload_cannot_import_user_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            image,digest=self.image(tmp)
            self.assertNotIn('home/workbench/.wuji/private',image_members(image,digest))


if __name__=='__main__':unittest.main()
