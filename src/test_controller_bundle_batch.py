"""Exact bundle verification without UNC-per-file reads. No WSL/SDK/device use."""
import hashlib
import json
from pathlib import Path,PureWindowsPath
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import controller_bundle as bundle
import managed_runtime as runtime


class BundleBatchTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.source=self.root/'source';self.source.mkdir()
        (self.source/'agent.py').write_bytes(b'# synthetic exact source\n')
        self.expected={'agent.py':hashlib.sha256((self.source/'agent.py').read_bytes()).hexdigest()}
        digest=hashlib.sha256(b'agent.py\0'+self.expected['agent.py'].encode()+b'\n').hexdigest()[:16]
        self.target=runtime.AGENT+'/code/'+digest

    def test_verified_batch_reuses_only_exact_expected_bundle_without_unc_access(self):
        calls=[]
        class Remote:
            def verify_existing_bundle(_,target,expected):calls.append((target,expected));return True
            def path(*_):raise AssertionError('Per-file UNC access must not follow exact batch success')
        self.assertEqual(bundle.ensure_version(self.source,Remote()),self.target)
        self.assertEqual(calls,[(self.target,self.expected)])

    def test_batch_failure_never_falls_back_or_writes(self):
        class Remote:
            def verify_existing_bundle(*_):raise ValueError('Synthetic bundle digest mismatch')
            def path(*_):raise AssertionError('A failed verification must not fall back')
        with self.assertRaisesRegex(ValueError,'digest mismatch'):bundle.ensure_version(self.source,Remote())

    def test_invalid_batch_result_never_counts_as_success_or_fallback(self):
        class Remote:
            def verify_existing_bundle(*_):return {'verified':True}
            def path(*_):raise AssertionError('Invalid batch response must not fall back')
        with self.assertRaisesRegex(ValueError,'verification result'):bundle.ensure_version(self.source,Remote())

    def test_absent_marker_keeps_exact_existing_install_path(self):
        backing=self.root/'remote';backing.mkdir();calls=[]
        class Remote:
            def verify_existing_bundle(_,target,expected):calls.append(target);return False
            def path(_,value):return backing.joinpath(*Path(value).parts[-3:])
        remote=Remote();self.assertEqual(bundle.ensure_version(self.source,remote),self.target)
        marker=remote.path(self.target+'/bundle.json')
        self.assertEqual(json.loads(marker.read_text('utf8')),self.expected)
        self.assertEqual(calls,[self.target])

    def files(self):
        files=runtime.Files()
        files._resolved_root=PureWindowsPath(r'\\wsl.localhost\HandWorkbenchControl\opt\hand-workbench')
        return files

    def test_wsl_runs_one_isolated_stdlib_verifier_with_exact_manifest(self):
        files=self.files();reply={'version':1,'status':'verified','target':self.target}
        with patch.object(files,'path',return_value=Path('unused')) as scoped,patch.object(runtime,'args',side_effect=lambda x:['owned-wsl',*x]),patch.object(runtime.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(reply).encode(),stderr=b'')) as run:
            self.assertIs(files.verify_existing_bundle(self.target,self.expected),True)
        scoped.assert_called_once_with(self.target+'/bundle.json')
        argv=run.call_args.args[0];self.assertEqual(argv[1:4],['/usr/bin/python3','-I','-c'])
        payload=json.loads(run.call_args.kwargs['input'])
        self.assertEqual(payload,{'target':self.target,'scope':runtime.AGENT,'expected':self.expected})
        self.assertNotIn('wuji_sdk',argv[-1]);self.assertEqual(run.call_count,1)

    def test_local_files_fixture_retains_old_path_without_wsl(self):
        files=runtime.Files();files.root=self.root
        with patch.object(runtime.subprocess,'run') as run:
            self.assertIsNone(files.verify_existing_bundle(self.target,self.expected))
        run.assert_not_called()

    def test_invalid_paths_and_digests_fail_before_runtime_or_unc(self):
        files=self.files()
        with patch.object(files,'path') as scoped,patch.object(runtime.subprocess,'run') as run:
            for target,expected in [('/etc/passwd',self.expected),(self.target,{'../outside':'0'*64}),(self.target,{'/absolute':'0'*64}),(self.target,{'a\\b':'0'*64}),(self.target,{'agent.py':'bad'})]:
                with self.subTest(target=target,expected=expected),self.assertRaises(ValueError):files.verify_existing_bundle(target,expected)
        scoped.assert_not_called();run.assert_not_called()

    def test_malformed_nonzero_or_wrong_target_reply_fails_closed(self):
        for reply in [SimpleNamespace(returncode=2,stdout=b'',stderr=b'mismatch'),SimpleNamespace(returncode=0,stdout=b'bad json',stderr=b''),SimpleNamespace(returncode=0,stdout=json.dumps({'version':1,'status':'verified','target':self.target+'x'}).encode(),stderr=b'')]:
            files=self.files()
            with self.subTest(reply=reply),patch.object(files,'path',return_value=Path('unused')),patch.object(runtime,'args',side_effect=lambda x:x),patch.object(runtime.subprocess,'run',return_value=reply),self.assertRaises((ValueError,RuntimeError)):
                files.verify_existing_bundle(self.target,self.expected)


class BatchVerifierTreeTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/'owned';self.root.mkdir()
        self.scope=self.root.resolve();self.target=self.root/'code'/'bundle'
        self.target.mkdir(parents=True);(self.target/'agent.py').write_bytes(b'# synthetic\n')
        self.expected={'agent.py':hashlib.sha256((self.target/'agent.py').read_bytes()).hexdigest()}
        self.marker=self.target/'bundle.json';self.marker.write_text(json.dumps(self.expected),'utf8')
        namespace={'__name__':'isolated_test'};exec(bundle.VERIFY_BUNDLE_SCRIPT,namespace)
        self.verify=namespace['verify_tree']

    def check(self):return self.verify(self.root,self.scope,self.target,self.expected)

    def test_every_file_is_rehashed_on_each_existing_bundle_verification(self):
        self.assertIs(self.check(),True)
        (self.target/'agent.py').write_bytes(b'# changed after first verification\n')
        with self.assertRaisesRegex(ValueError,'file does not match'):self.check()

    def test_only_an_absent_marker_returns_missing(self):
        self.marker.unlink();self.assertIs(self.check(),False)

    def test_marker_mismatch_or_corrupt_json_fails_instead_of_missing(self):
        for raw in ('{}','bad json','[]'):
            self.marker.write_text(raw,'utf8')
            with self.subTest(raw=raw),self.assertRaises(ValueError):self.check()

    def test_missing_member_fails_instead_of_reinstalling(self):
        (self.target/'agent.py').unlink()
        with self.assertRaisesRegex(ValueError,'file does not match'):self.check()

    def test_each_requested_target_and_marker_rejects_outside_resolution(self):
        actual=Path.resolve;outside=self.root.parent/'outside';outside.mkdir()
        for target in (self.target,self.marker,self.target/'agent.py'):
            def resolve(path,*args,**kwargs):return outside if path==target else actual(path,*args,**kwargs)
            with self.subTest(target=target),patch.object(Path,'resolve',resolve),self.assertRaisesRegex(ValueError,'outside'):self.check()

    def test_changed_root_is_rejected_before_reading_marker(self):
        actual=Path.resolve
        def resolve(path,*args,**kwargs):return self.root.parent/'replacement' if path==self.root else actual(path,*args,**kwargs)
        with patch.object(Path,'resolve',resolve),self.assertRaisesRegex(ValueError,'root changed'):self.check()

    def test_root_move_during_file_hash_is_rejected(self):
        actual=Path.resolve;roots=[]
        def resolve(path,*args,**kwargs):
            if path==self.root:
                roots.append(path)
                if len(roots)>1:return self.root.parent/'replacement'
            return actual(path,*args,**kwargs)
        with patch.object(Path,'resolve',resolve),self.assertRaisesRegex(ValueError,'root changed'):self.check()


if __name__=='__main__':unittest.main()
