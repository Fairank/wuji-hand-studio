import hashlib,io,pathlib,tarfile,tempfile,unittest
from unittest.mock import patch
from runtime_update import apply,files_from_image

class UpdateTests(unittest.TestCase):
    def image(self,folder,extra=None):
        p=pathlib.Path(folder)/'image.tar'
        rows={'opt/hand-workbench/device_discovery.py':b'# new', 'opt/hand-workbench/motion_parameters.py':b'DO NOT OVERWRITE', 'opt/hand-workbench/sessions/test.json':b'omit'}
        rows.update(extra or {})
        with tarfile.open(p,'w') as t:
            for name,data in rows.items():
                m=tarfile.TarInfo(name);m.size=len(data);t.addfile(m,io.BytesIO(data))
        return p,hashlib.sha256(p.read_bytes()).hexdigest()
    def test_update_preserves_parameters_and_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            p,digest=self.image(tmp);root=pathlib.Path(tmp)/'runtime';target=root/'opt/hand-workbench';target.mkdir(parents=True)
            (target/'motion_parameters.py').write_bytes(b'MY TUNING');(target/'device_discovery.py').write_bytes(b'# old')
            result=apply(p,digest,root)
            self.assertEqual(result['updated_files'],1);self.assertEqual((target/'motion_parameters.py').read_bytes(),b'MY TUNING')
            self.assertEqual((target/'device_discovery.py').read_bytes(),b'# new');self.assertFalse((target/'sessions').exists())
            self.assertEqual((target/'upgrade-backup-1.0.1/opt/hand-workbench/device_discovery.py').read_bytes(),b'# old')
            self.assertEqual(apply(p,digest,root)['updated_files'],0)
    def test_bad_hash_and_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p,h=self.image(tmp)
            with self.assertRaisesRegex(ValueError,'integrity'):files_from_image(p,'0'*64)
            p,h=self.image(tmp,{'opt/hand-workbench/../../escape':b'x'})
            with self.assertRaisesRegex(ValueError,'Unsafe'):files_from_image(p,h)
    def test_unrelated_system_files_are_not_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p,h=self.image(tmp,{'etc/passwd':b'not installed','home/workbench/.ssh/id':b'not installed'})
            self.assertEqual(set(files_from_image(p,h)),{'opt/hand-workbench/device_discovery.py'})

if __name__=='__main__':unittest.main()
