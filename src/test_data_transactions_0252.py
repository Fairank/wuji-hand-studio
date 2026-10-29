"""Synthetic malformed model data and interrupted owned-component replacements.

No SDK, venv, WSL, CLI executable, discovery or real device is started.
"""
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
import model_pack
import runtime_refresh
import sdk_environment_update as sdk_updates
from test_model_pack import pack_bytes
import test_runtime_refresh as runtime_fixtures
import test_sdk_lifecycle as sdk_fixtures
from test_sdk_lifecycle import proof_fixture


def model_fixture(change=None,manifest_change=None):
    with zipfile.ZipFile(pack_bytes()) as archive:files={name:archive.read(name) for name in archive.namelist()}
    if change:
        with np.load(io.BytesIO(files['parity.npz']),allow_pickle=False) as arrays:values={name:arrays[name] for name in arrays.files}
        change(values)
        output=io.BytesIO();np.savez_compressed(output,**values);files['parity.npz']=output.getvalue()
    manifest=json.loads(files['manifest.json']);manifest['sha256']['parity.npz']=hashlib.sha256(files['parity.npz']).hexdigest()
    if manifest_change:manifest=manifest_change(manifest)
    files['manifest.json']=json.dumps(manifest).encode();output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as archive:
        for name,data in files.items():archive.writestr(name,data)
    output.seek(0);return output


class ModelParityValidation(unittest.TestCase):
    def test_every_parity_array_rejects_nonfinite_values_without_installing(self):
        for name in ('input','decoder','recent','force','hidden'):
            for invalid in (np.nan,np.inf):
                with self.subTest(name=name,invalid=invalid),tempfile.TemporaryDirectory() as folder:
                    def change(values):values[name].flat[0]=invalid
                    with self.assertRaises(ValueError):model_pack.install(model_fixture(change),folder)
                    self.assertFalse(model_pack.status(folder)['installed'])
                    self.assertEqual(list((Path(folder)/'models').iterdir()),[])

    def test_broadcasting_and_unchecked_batch_members_are_rejected(self):
        changes=[lambda v:v.update(recent=np.full((1,1,1),.5,np.float32)),
            lambda v:v.update(hidden=np.zeros((1,1,1),np.float32)),
            lambda v:v.update(input=np.zeros((2,10,86),np.float32)),
            lambda v:v.update(force=v['force'].astype(np.int32)),
            lambda v:v.update(extra=np.zeros(1,np.float32)),lambda v:v.pop('recent')]
        for change in changes:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):model_pack.install(model_fixture(change),folder)
                self.assertFalse(model_pack.status(folder)['installed'])

    def test_parity_mismatch_keeps_original_tolerance_and_existing_model(self):
        with tempfile.TemporaryDirectory() as folder:
            model_pack.install(pack_bytes(),folder)
            path=Path(folder)/'models/left-touch-v1/recognizer.npz';before=path.read_bytes()
            def mismatch(values):values['recent']+=.001
            with self.assertRaisesRegex(ValueError,'parity check'):model_pack.install(model_fixture(mismatch),folder)
            self.assertEqual(path.read_bytes(),before);self.assertTrue(model_pack.status(folder)['installed'])

    def test_nonobject_manifest_and_hash_map_are_rejected_as_invalid_data(self):
        for change in (lambda manifest:[],lambda manifest:dict(manifest,sha256=[])):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):model_pack.install(model_fixture(manifest_change=change),folder)
                installed=Path(folder)/'models/left-touch-v1';installed.mkdir()
                with zipfile.ZipFile(model_fixture(manifest_change=change)) as archive:
                    for name in archive.namelist():(installed/name).write_bytes(archive.read(name))
                self.assertTrue(model_pack.status(folder)['invalid'])


class RuntimeInterruptedReplacement(unittest.TestCase):
    def test_interrupt_after_each_displacement_or_switch_restores_both_originals(self):
        fixture=runtime_fixtures.RuntimeRefreshTests();rename=Path.rename
        for component in ('environment','cli'):
            for boundary in ('displace','switch'):
                with self.subTest(component=component,boundary=boundary),tempfile.TemporaryDirectory() as folder:
                    image,digest=fixture.image(folder);root=fixture.runtime(folder)
                    env=root/runtime_refresh.ENV;cli=root/runtime_refresh.CLI;interrupted=False
                    def interrupt(source,destination):
                        nonlocal interrupted
                        result=rename(source,destination)
                        original=env if component=='environment' else cli
                        match=source==original if boundary=='displace' else Path(destination)==original
                        if not interrupted and match:
                            interrupted=True;raise KeyboardInterrupt('synthetic interruption after filesystem move')
                        return result
                    with patch('runtime_refresh.venv.EnvBuilder') as builder,patch('runtime_refresh.subprocess.run',return_value=SimpleNamespace(stdout='wuji '+runtime_refresh.SDK_VERSION)),patch.object(Path,'rename',interrupt):
                        builder.return_value.create.side_effect=fixture.stage
                        with self.assertRaises(KeyboardInterrupt):runtime_refresh.apply(image,digest,root)
                    self.assertTrue(interrupted);self.assertEqual((env/'old-sdk').read_text(),'retained old');self.assertEqual(cli.read_bytes(),b'old CLI')
                    self.assertEqual((root/'home/workbench/.wuji/private').read_bytes(),b'PERSONAL')
                    self.assertEqual((root/'opt/hand-workbench/motion_parameters.py').read_bytes(),b'MY TUNING')
                    self.assertFalse(list(env.parent.glob(env.name+'.pending-*')))

    def test_cli_restore_failure_still_restores_environment_and_preserves_recovery_files(self):
        fixture=runtime_fixtures.RuntimeRefreshTests();rename=Path.rename
        with tempfile.TemporaryDirectory() as folder:
            image,digest=fixture.image(folder);root=fixture.runtime(folder);cli=root/runtime_refresh.CLI;env=root/runtime_refresh.ENV
            def fail_restore(source,destination):
                if source.name.startswith('wuji.backup-'):raise PermissionError('synthetic restore failure')
                return rename(source,destination)
            with patch('runtime_refresh.venv.EnvBuilder') as builder,patch('runtime_refresh.subprocess.run',side_effect=[SimpleNamespace(),SimpleNamespace(stdout='wuji '+runtime_refresh.SDK_VERSION),RuntimeError('synthetic final probe failure')]),patch.object(Path,'rename',fail_restore):
                builder.return_value.create.side_effect=fixture.stage
                with self.assertRaisesRegex(RuntimeError,'restoration is unconfirmed'):runtime_refresh.apply(image,digest,root)
            self.assertEqual((env/'old-sdk').read_text(),'retained old')
            backups=list(cli.parent.glob('wuji.backup-*'));self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_bytes(),b'old CLI')
            self.assertTrue(list(cli.parent.glob('wuji.pending-*')));self.assertTrue(list(env.parent.glob(env.name+'.pending-*')))


class SDKInterruptedReplacement(unittest.TestCase):
    def test_interrupt_after_displacement_or_switch_restores_original_and_records_rollback(self):
        fixture=sdk_fixtures.OwnedUpdateTests();rename=Path.rename
        for boundary in ('displace','switch'):
            with self.subTest(boundary=boundary),tempfile.TemporaryDirectory() as folder:
                wheel,release=fixture.wheel(folder);root,env,site,user,cli=fixture.environment(folder);interrupted=False
                def interrupt(source,destination):
                    nonlocal interrupted
                    result=rename(source,destination);match=source==env if boundary=='displace' else Path(destination)==env
                    if not interrupted and match:
                        interrupted=True;raise KeyboardInterrupt('synthetic SDK interruption')
                    return result
                runner=fixture.runner(proof_fixture('2026.9.22'),proof_fixture(),proof_fixture('2026.9.22'))
                with patch.object(Path,'rename',interrupt),self.assertRaises(sdk_updates.SDKUpdateError) as caught:
                    sdk_updates.apply(wheel,release,'wsl',root,run=runner,operation_token='interrupted-sdk-0252')
                self.assertTrue(caught.exception.rollback_confirmed);self.assertTrue(caught.exception.switched)
                self.assertEqual((site/'wuji_sdk/__init__.py').read_bytes(),b'old SDK')
                receipt=json.loads((root/sdk_updates.RECEIPTS/'interrupted-sdk-0252.json').read_text());self.assertEqual(receipt['phase'],'rolled_back')
                self.assertEqual((user/'model.urdf').read_bytes(),b'PERSONAL MODEL');self.assertEqual(cli.read_bytes(),b'CLI unchanged')


if __name__=='__main__':unittest.main()
