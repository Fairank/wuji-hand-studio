"""Official metadata, actual interpreter attestation and rollback; synthetic only."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path,PurePosixPath
import queue
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch
import zipfile

import sdk_release as releases
import sdk_runtime as runtime
import sdk_environment_update as updates
from glove_bridge import GloveBridge

VERSION='2026.10.12'


def proof_fixture(version=VERSION):
    return dict(proof_version=1,platform='linux',implementation='cpython',python_tag='cp312',
                machine='x86_64',libc=['glibc','2.39'],executable='/opt/hand-workbench-venv/bin/python',
                import_path='/opt/hand-workbench-venv/lib/python3.12/site-packages/wuji_sdk/__init__.py',
                available=True,api_compatible=True,version=version,module_sha256='a'*64,
                native_sha256={'wuji_sdk/native.so':'b'*64})


def release_fixture(now=None):
    filename=f'wuji_sdk-{VERSION}-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl'
    return dict(source=releases.INDEX,version=VERSION,checked_at=now or time.time(),catalog_sha256='c'*64,
                requires_python='>=3.8',requires_dist=['pytest; extra == "dev"'],
                files=[dict(filename=filename,url='https://files.pythonhosted.org/packages/synthetic/'+filename,
                            sha256='d'*64,size=10,yanked=False)])


def catalog_fixture():
    release=release_fixture()
    return json.dumps(dict(info=dict(version=VERSION,requires_python='>=3.8',requires_dist=[]),
        urls=[dict(release['files'][0],packagetype='bdist_wheel',digests={'sha256':'d'*64})])).encode()


class CatalogTests(unittest.TestCase):
    def test_prerelease_at_index_selects_latest_non_yanked_stable_metadata(self):
        index=json.loads(catalog_fixture());index['info']['version']='2026.10.23rc1'
        index['releases']={VERSION:[dict(packagetype='bdist_wheel',yanked=False)],
            '2026.10.20':[dict(packagetype='bdist_wheel',yanked=True)],
            '2026.10.23rc1':[dict(packagetype='bdist_wheel',yanked=False)]}
        def response(url,data):
            value=io.BytesIO(data);value.url=url;return value
        with patch('sdk_release.urllib.request.urlopen',side_effect=[response(releases.INDEX,json.dumps(index).encode()),
                response('https://pypi.org/pypi/wuji-sdk/'+VERSION+'/json',catalog_fixture())]) as opened:
            self.assertEqual(releases.parse_catalog(releases._read_official())['version'],VERSION)
            self.assertEqual(opened.call_count,2)

    def test_live_catalog_cache_and_expiry_never_extend_when_offline(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'stable.json';fetch=Mock(return_value=catalog_fixture());now=10000
            first=releases.resolve_release(path,fetch=fetch,now=now)
            self.assertEqual(first['version'],VERSION);self.assertEqual(fetch.call_count,1)
            self.assertEqual(releases.resolve_release(path,fetch=fetch,now=now+100)['checked_at'],now)
            self.assertEqual(fetch.call_count,1)
            offline=releases.resolve_release(path,force=True,fetch=Mock(side_effect=OSError()),now=now+101)
            self.assertTrue(offline['offline']);self.assertEqual(offline['checked_at'],now)
            with self.assertRaisesRegex(ValueError,'official SDK'):
                releases.resolve_release(path,fetch=Mock(side_effect=OSError()),now=now+901)
            self.assertEqual(json.loads(path.read_text())['checked_at'],now)

    def test_prerelease_yanked_wrong_source_and_future_cache_fail(self):
        raw=json.loads(catalog_fixture())
        for version in ('2026.10.12rc1','2026.10.12.dev1','main','2026.13.12'):
            raw['info']['version']=version
            with self.subTest(version=version),self.assertRaises(ValueError):releases.parse_catalog(json.dumps(raw).encode())
        raw=json.loads(catalog_fixture());raw['urls'][0]['yanked']=True
        with self.assertRaises(ValueError):releases.parse_catalog(json.dumps(raw).encode())
        for change in ({'source':'https://example.invalid/'},{'checked_at':time.time()+3600},{'checked_at':float('nan')}):
            with self.subTest(change=change),self.assertRaises(ValueError):releases.validate_release({**release_fixture(),**change})
        value=release_fixture();value['files'][0]['url']='https://example.invalid/'+value['files'][0]['filename']
        with self.assertRaises(ValueError):releases.validate_release(value)

    def test_platform_matching_uses_actual_worker_python_abi_and_arch(self):
        self.assertEqual(releases.select_wheel(release_fixture(),proof_fixture())['filename'],release_fixture()['files'][0]['filename'])
        for change in ({'platform':'win32'},{'machine':'aarch64'},{'python_tag':'cp313'},{'libc':['musl','1.2']}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                releases.select_wheel(release_fixture(),{**proof_fixture(),**change})


class RuntimeProofTests(unittest.TestCase):
    def test_import_and_native_proof_never_initializes_manager_or_devices(self):
        with tempfile.TemporaryDirectory() as folder:
            site=Path(folder);sdk=site/'wuji_sdk';sdk.mkdir();(sdk/'__init__.py').write_bytes(b'# synthetic')
            (sdk/'native.so').write_bytes(b'synthetic native file')
            manager=Mock();classes={name:N(**{member:Mock() for member in members}) for name,members in runtime._APIS.items()}
            classes['WujiException']=type('SyntheticSDKError',(Exception,),{})
            classes['SdkManager'].instance=manager
            module=N(__file__=str(sdk/'__init__.py'),**classes)
            dist=N(version=VERSION,files=[PurePosixPath('wuji_sdk/native.so')],locate_file=lambda p:site/str(p))
            with patch('sdk_runtime.importlib.metadata.distribution',return_value=dist), \
                    patch('sdk_runtime.importlib.import_module',side_effect=lambda name:module if name=='wuji_sdk' else N()):
                proof=runtime.probe_runtime()
            self.assertTrue(proof['available']);self.assertTrue(proof['api_compatible']);manager.assert_not_called()
            self.assertEqual(proof['native_sha256']['wuji_sdk/native.so'],hashlib.sha256(b'synthetic native file').hexdigest())
            module.RetargetSession.for_hand.assert_not_called()

    def test_missing_or_wrong_actual_sdk_is_not_a_successful_label(self):
        expected=releases.requirement(release_fixture())
        self.assertEqual(runtime.verify_proof(proof_fixture(),expected)['version'],VERSION)
        for proof in (None,{},dict(proof_fixture(),version='2026.9.22'),dict(proof_fixture(),available=False),
                      dict(proof_fixture(),api_compatible=False),dict(proof_fixture(),native_sha256={})):
            with self.subTest(proof=proof),self.assertRaises(ValueError):runtime.verify_proof(proof,expected)

    def test_ssh_probe_uses_configured_interpreter_only_and_never_starts_agent(self):
        run=Mock(return_value=(0,json.dumps(proof_fixture()),''))
        config=dict(mode='ssh',python='/remote/venv/bin/python',agent_directory='/remote/agent')
        self.assertEqual(updates.probe_selected(config,run=run)['version'],VERSION)
        self.assertEqual(run.call_args.args[1][0],config['python'])
        self.assertEqual(run.call_args.args[1][1],'-c')
        self.assertNotIn('agent_bootstrap.py',run.call_args.args[1])

    def test_real_spawned_worker_rechecks_before_sdk_manager_scan(self):
        from glove_agent import worker_glove
        events=queue.Queue();sdk=N(SdkManager=N(instance=Mock()))
        with patch.dict(sys.modules,wuji_sdk=sdk),patch('sdk_runtime.probe_runtime',return_value=dict(proof_fixture(),version='2026.9.22')):
            worker_glove(queue.Queue(),events,sdk_requirement=releases.requirement(release_fixture()))
        sdk.SdkManager.instance.assert_not_called()
        self.assertTrue(any(e['type']=='glove_error' for e in list(events.queue)))
        self.assertFalse(any(e['type']=='sdk_ready' for e in list(events.queue)))


class SDKHandshakeTests(unittest.TestCase):
    def bridge(self,events):
        sent=io.StringIO();client=N(agent_command='synthetic',exec_command=lambda *a,**kw:(sent,
            iter('WUJI_JSON:'+json.dumps(e) for e in events),io.StringIO()),close=Mock())
        bridge=GloveBridge(lambda:client,release_resolver=release_fixture)
        bridge.run();return bridge,sent

    def test_old_controller_or_actual_old_version_cannot_receive_session(self):
        for proof in (None,dict(proof_fixture(),version='2026.9.22')):
            bridge,sent=self.bridge([dict(type='ready',teleop_version=1,sdk_proof=proof)])
            self.assertEqual(sent.getvalue(),'');self.assertEqual(bridge.snapshot()['connection'],'error')

    def test_spawned_worker_proof_required_before_accepting_state(self):
        ready=dict(type='ready',teleop_version=1,sdk_proof=proof_fixture())
        state=dict(type='glove_state',state=dict(connection='ready',devices=[],users=[],hardware={},feedback={},stream={}))
        bridge,sent=self.bridge([ready,state])
        self.assertIn('sdk_requirement',json.loads(sent.getvalue()))
        self.assertIsNone(bridge.snapshot()['sdk_proof']);self.assertIn('Actual worker',bridge.snapshot()['message'])
        bridge,sent=self.bridge([ready,dict(type='sdk_ready',sdk_proof=proof_fixture()),state])
        self.assertEqual(bridge.snapshot()['sdk_proof']['version'],VERSION)


class OwnedUpdateTests(unittest.TestCase):
    def wheel(self,folder,extra=None,bad_record=False):
        release=release_fixture();prefix=f'wuji_sdk-{VERSION}.dist-info'
        members={'wuji_sdk/__init__.py':b'# new SDK','wuji_sdk/native.so':b'new native',
                 prefix+'/METADATA':f'Name: wuji-sdk\nVersion: {VERSION}\n'.encode(),
                 prefix+'/WHEEL':b'Wheel-Version: 1.0\n'}
        members.update(extra or {})
        rows=[]
        for name,data in members.items():
            digest=base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
            rows.append(f'{name},sha256={digest},{len(data)}')
        rows.append(prefix+'/RECORD,,')
        members[prefix+'/RECORD']=('\n'.join(rows)+'\n').encode()
        if bad_record:members['wuji_sdk/native.so']=b'TAMPERED'
        path=Path(folder)/release['files'][0]['filename']
        with zipfile.ZipFile(path,'w') as archive:
            for name,data in members.items():archive.writestr(name,data)
        release['files'][0].update(size=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        return path,release

    def environment(self,folder):
        root=Path(folder)/'guest';env=root/updates.ENV;site=env/'lib/python3.12/site-packages';site.mkdir(parents=True)
        (env/'bin').mkdir();(env/'bin/python').write_text('synthetic interpreter')
        (site/'wuji_sdk').mkdir();(site/'wuji_sdk/__init__.py').write_bytes(b'old SDK')
        (site/'numpy-retained.txt').write_bytes(b'old dependency')
        user=root/'home/workbench/.wuji/sdk/alice';user.mkdir(parents=True);(user/'model.urdf').write_bytes(b'PERSONAL MODEL')
        cli=root/'usr/local/bin/wuji';cli.parent.mkdir(parents=True);cli.write_bytes(b'CLI unchanged')
        return root,env,site,user,cli

    def runner(self,*proofs):
        return Mock(side_effect=[x if isinstance(x,Exception) else N(stdout=json.dumps(x)) for x in proofs])

    def test_verified_side_by_side_update_preserves_dependencies_profiles_cli_and_old_venv(self):
        with tempfile.TemporaryDirectory() as folder:
            path,release=self.wheel(folder);root,env,site,user,cli=self.environment(folder)
            result=updates.apply(path,release,'wsl',root,run=self.runner(proof_fixture('2026.9.22'),proof_fixture(),proof_fixture()))
            self.assertTrue(result['changed']);self.assertEqual((site/'wuji_sdk/__init__.py').read_bytes(),b'# new SDK')
            self.assertEqual((Path(result['sdk_backup'])/'lib/python3.12/site-packages/wuji_sdk/__init__.py').read_bytes(),b'old SDK')
            self.assertEqual((site/'numpy-retained.txt').read_bytes(),b'old dependency')
            self.assertEqual((user/'model.urdf').read_bytes(),b'PERSONAL MODEL');self.assertEqual(cli.read_bytes(),b'CLI unchanged')
            self.assertFalse(result['motion_started'])

    def test_final_probe_failure_rolls_back_and_proves_original_interpreter(self):
        with tempfile.TemporaryDirectory() as folder:
            path,release=self.wheel(folder);root,env,site,user,cli=self.environment(folder)
            run=self.runner(proof_fixture('2026.9.22'),proof_fixture(),RuntimeError('synthetic ABI failure'),proof_fixture('2026.9.22'))
            with self.assertRaises(updates.SDKUpdateError) as caught:updates.apply(path,release,'wsl',root,run=run)
            self.assertTrue(caught.exception.rollback_confirmed);self.assertTrue(caught.exception.switched)
            self.assertEqual((site/'wuji_sdk/__init__.py').read_bytes(),b'old SDK');self.assertEqual((user/'model.urdf').read_bytes(),b'PERSONAL MODEL')

    def test_rollback_proof_failure_stays_unknown_for_host_interlock(self):
        with tempfile.TemporaryDirectory() as folder:
            path,release=self.wheel(folder);root,*_=self.environment(folder)
            run=self.runner(proof_fixture('2026.9.22'),proof_fixture(),RuntimeError('new probe failed'),RuntimeError('restore probe failed'))
            with self.assertRaises(updates.SDKUpdateError) as caught:updates.apply(path,release,'wsl',root,run=run)
            self.assertFalse(caught.exception.rollback_confirmed);self.assertTrue(caught.exception.switched)

    def test_wrong_hash_record_traversal_and_new_unknown_dependencies_never_install(self):
        with tempfile.TemporaryDirectory() as folder:
            path,release=self.wheel(folder)
            with self.assertRaises(ValueError):updates.wheel_members(path,{**release['files'][0],'sha256':'0'*64},VERSION)
            for extra,bad_record in (({'../../home/user':b'bad'},False),(None,True)):
                path,release=self.wheel(folder,extra,bad_record)
                with self.assertRaises(ValueError):updates.wheel_members(path,release['files'][0],VERSION)
            release=release_fixture();release['requires_dist']=['new-unreviewed-dependency>=9']
            with self.assertRaises(ValueError):updates.dependency_contract(release)

    def test_same_verified_stable_does_not_reinstall_and_external_env_never_mutates(self):
        with tempfile.TemporaryDirectory() as folder:
            path,release=self.wheel(folder);root,env,site,user,cli=self.environment(folder)
            result=updates.apply(path,release,'wsl',root,run=self.runner(proof_fixture()))
            self.assertFalse(result['changed']);self.assertEqual((site/'wuji_sdk/__init__.py').read_bytes(),b'old SDK')
        idle=Mock()
        with self.assertRaisesRegex(ValueError,'external environments'):
            updates.update_owned(dict(mode='ssh'),release_fixture(),idle=idle)
        idle.assert_not_called()

    def test_failed_dispatch_callback_is_pre_dispatch_and_never_launches_guest_apply(self):
        with tempfile.TemporaryDirectory() as folder:
            path,release=self.wheel(folder);mapped=Path(folder)/'transport';mapped.mkdir()
            class Files:
                def path(self,value):return mapped/PurePosixPath(value).relative_to('/opt/hand-workbench')
                def __enter__(self):return self
                def __exit__(self,*_):pass
            runner=Mock(return_value=N(stdout=json.dumps(proof_fixture())))
            callback=Mock(side_effect=OSError('synthetic persistence failure'))
            config=dict(mode='wsl',agent_directory=updates.AGENT,python=updates.PYTHON)
            with patch('managed_runtime.check_ready'),patch('managed_runtime.args',side_effect=lambda argv,**kw:argv), \
                    patch('managed_runtime.Files',Files),patch('sdk_environment_update.download_wheel',return_value=(path,release['files'][0])):
                with self.assertRaises(updates.SDKUpdateError) as caught:
                    updates.update_owned(config,release,idle=Mock(),run=runner,on_dispatch=callback)
            self.assertTrue(caught.exception.rollback_confirmed);self.assertFalse(caught.exception.switched)
            callback.assert_called_once_with();self.assertEqual(runner.call_count,1)


class RecoveryTests(unittest.TestCase):
    environment=OwnedUpdateTests.environment
    wheel=OwnedUpdateTests.wheel
    runner=OwnedUpdateTests.runner

    def receipt(self,root,release,token,phase,**extra):
        data=dict(schema=1,operation_token=token,catalog_sha256=release['catalog_sha256'],version=VERSION,
                  pid=123456,process_start='synthetic',phase=phase,original_proof=proof_fixture('2026.9.22'),
                  staged_proof=proof_fixture(),**extra)
        path=root/updates.RECEIPTS/(token+'.json');updates._atomic_json(path,data);return path

    def test_completed_matching_receipt_requires_ended_process_and_actual_native_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root,*_=self.environment(folder);release=release_fixture();token='synthetic-token-0001'
            self.receipt(root,release,token,'completed',sdk_proof=proof_fixture())
            with patch('sdk_environment_update._operation_ended',return_value=False),self.assertRaisesRegex(ValueError,'still be running'):
                updates.recover_guest(release,'wsl',token,root,run=self.runner(proof_fixture()))
            result=updates.recover_guest(release,'wsl',token,root,run=self.runner(proof_fixture()))
            self.assertTrue(result['rollback_confirmed']);self.assertEqual(result['sdk_proof']['version'],VERSION)

    def test_stopped_crash_between_directory_renames_restores_only_recorded_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root,env,site,user,cli=self.environment(folder);release=release_fixture();token='synthetic-token-0002'
            self.receipt(root,release,token,'switching');backup=env.with_name(env.name+'.sdk-backup-'+token);env.rename(backup)
            result=updates.recover_guest(release,'wsl',token,root,run=self.runner(proof_fixture('2026.9.22'),proof_fixture('2026.9.22')))
            self.assertTrue(result['rollback_confirmed']);self.assertEqual((site/'wuji_sdk/__init__.py').read_bytes(),b'old SDK')
            self.assertEqual((user/'model.urdf').read_bytes(),b'PERSONAL MODEL')

    def test_missing_wrong_operation_or_changed_native_backup_never_releases_unknown_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root,env,*_=self.environment(folder);release=release_fixture();token='synthetic-token-0003'
            with self.assertRaises(ValueError):updates.recover_guest(release,'wsl',token,root)
            path=self.receipt(root,release,token,'switching')
            with self.assertRaises(ValueError):updates.recover_guest({**release,'catalog_sha256':'f'*64},'wsl',token,root)
            env.rename(env.with_name(env.name+'.sdk-backup-'+token))
            with self.assertRaises(ValueError):updates.recover_guest(release,'wsl',token,root,run=self.runner(proof_fixture('unexpected')))

    def test_pre_dispatch_failure_is_explicitly_not_a_switched_runtime(self):
        with self.assertRaises(updates.SDKUpdateError) as caught:
            updates.update_owned(dict(mode='ssh'),release_fixture(),idle=Mock())
        self.assertTrue(caught.exception.rollback_confirmed);self.assertFalse(caught.exception.switched)


if __name__=='__main__':unittest.main()
