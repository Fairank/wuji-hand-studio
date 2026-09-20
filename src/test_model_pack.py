"""Synthetic pack-format tests; no private trained weights or recordings."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import numpy as np
from model_pack import SHAPES, CONTRACT, install, status, Recognizer


def pack_bytes(bad_hash=False):
    values={k:np.zeros(shape,dtype=np.float32) for k,shape in SHAPES.items()};values['std'][:]=1
    weights=io.BytesIO();np.savez_compressed(weights,**values)
    parity=io.BytesIO();np.savez_compressed(parity,input=np.zeros((1,10,86),np.float32),hidden=np.zeros((1,1,35),np.float32),
        decoder=np.full((1,10,5),.5,np.float32),recent=np.full((1,10,5),.5,np.float32),force=np.full((1,10,5),np.log(2),np.float32))
    files={'recognizer.npz':weights.getvalue(),'parity.npz':parity.getvalue()}
    manifest=dict(format='hand-workbench-model-v1',contract=CONTRACT,sha256={k:hashlib.sha256(v).hexdigest() for k,v in files.items()})
    if bad_hash:manifest['sha256']['recognizer.npz']='0'*64
    files['manifest.json']=json.dumps(manifest).encode();buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:
        for k,v in files.items():z.writestr(k,v)
    buffer.seek(0);return buffer


class ModelPackTests(unittest.TestCase):
    def test_optional_model_import_is_offline_and_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(status(d)['installed'])
            result=install(pack_bytes(),d)
            self.assertTrue(result['model']['installed']);self.assertFalse(result['model']['hardware_enabled'])
            self.assertFalse(result['motor_commands_sent']);self.assertTrue(install(pack_bytes(),d)['ok'])
            model=Recognizer(Path(d)/'models/left-touch-v1')
            with self.assertRaises(ValueError):model.consume(np.zeros((10,86)),contract='actual_current_A')

    def test_invalid_hash_leaves_no_installed_model(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):install(pack_bytes(True),d)
            self.assertFalse(status(d)['installed'])
            self.assertEqual(list((Path(d)/'models').iterdir()),[])

    def test_zip_cannot_install_code_or_escape_directory(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ('../outside.py','plugin.py','recognizer.npz'):
                buffer=io.BytesIO()
                with zipfile.ZipFile(buffer,'w') as z:z.writestr(name,b'hello')
                buffer.seek(0)
                with self.assertRaises(ValueError):install(buffer,d)
                self.assertFalse(status(d)['installed'])


if __name__=='__main__':unittest.main()
