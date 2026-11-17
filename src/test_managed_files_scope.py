"""Pinned runtime scope without caching a requested target's resolution.

All paths are isolated local fixtures; WSL, the SDK and devices are not used.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from controller_bundle import ensure_version
from managed_runtime import Files


class ManagedFilesScopeTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base=Path(temporary.name)
        self.root=self.base/'runtime';self.root.mkdir()
        self.outside=self.base/'outside';self.outside.mkdir()
        self.files=Files();self.files.root=self.root

    def test_invalid_paths_never_resolve_or_touch_the_runtime(self):
        with patch.object(Path,'resolve',side_effect=AssertionError('Unexpected filesystem access')):
            for value in ('/etc/passwd','relative','/opt/hand-workbench/../../escape',
                          '/opt/hand-workbench/a\\b'):
                with self.subTest(value=value),self.assertRaises(ValueError):self.files.path(value)

    def test_repeated_file_access_pins_root_once_but_resolves_each_target(self):
        actual=Path.resolve;calls=[]
        def resolve(path,*args,**kwargs):
            calls.append(path)
            return actual(path,*args,**kwargs)
        with patch.object(Path,'resolve',resolve):
            for name in ('a.py','b.py','a.py'):
                self.assertEqual(self.files.path('/opt/hand-workbench/'+name),self.root/name)
        self.assertEqual(calls.count(self.root),1,'Each file must not repeat slow runtime-root normalization')
        self.assertEqual(calls.count(self.root/'a.py'),2,'Requested targets must never use cached resolution')
        self.assertEqual(calls.count(self.root/'b.py'),1)

    def test_a_redirected_target_is_rechecked_after_an_earlier_success(self):
        actual=Path.resolve;redirected=False;target=self.root/'code.py'
        def resolve(path,*args,**kwargs):
            return self.outside/'code.py' if path==target and redirected else actual(path,*args,**kwargs)
        with patch.object(Path,'resolve',resolve):
            self.assertEqual(self.files.path('/opt/hand-workbench/code.py'),target)
            redirected=True
            with self.assertRaisesRegex(ValueError,'outside'):self.files.path('/opt/hand-workbench/code.py')

    def test_retargeting_runtime_root_cannot_move_its_already_pinned_scope(self):
        # Resolve is the external UNC/reparse-point boundary. Replace only its
        # OS answer so this case does not require Windows link privileges.
        actual=Path.resolve;redirected=False;target=self.root/'code.py'
        def resolve(path,*args,**kwargs):
            if redirected and path==self.root:return self.outside
            if redirected and path==target:return self.outside/'code.py'
            return actual(path,*args,**kwargs)
        with patch.object(Path,'resolve',resolve):
            self.files.path('/opt/hand-workbench/code.py')
            redirected=True
            with self.assertRaisesRegex(ValueError,'outside'):self.files.path('/opt/hand-workbench/code.py')

    def test_a_failed_initial_root_resolution_can_be_explicitly_retried(self):
        actual=Path.resolve;roots=[]
        def resolve(path,*args,**kwargs):
            if path==self.root:
                roots.append(path)
                if len(roots)==1:raise OSError('Synthetic unavailable runtime path')
            return actual(path,*args,**kwargs)
        with patch.object(Path,'resolve',resolve):
            with self.assertRaisesRegex(OSError,'unavailable'):self.files.path('/opt/hand-workbench/a.py')
            self.assertEqual(self.files.path('/opt/hand-workbench/a.py'),self.root/'a.py')
            self.assertEqual(self.files.path('/opt/hand-workbench/b.py'),self.root/'b.py')
        self.assertEqual(len(roots),2,'Only successful root resolution may be retained')

    def test_concurrent_access_pins_one_root_without_reusing_target_answers(self):
        actual=Path.resolve;roots=[];targets=[];counts=threading.Lock();start=threading.Barrier(5)
        def resolve(path,*args,**kwargs):
            with counts:
                (roots if path==self.root else targets).append(path)
            if path==self.root:time.sleep(.02)
            return actual(path,*args,**kwargs)
        def access(index):
            start.wait(2)
            return self.files.path('/opt/hand-workbench/'+str(index)+'.py')
        with patch.object(Path,'resolve',resolve),ThreadPoolExecutor(max_workers=5) as pool:
            futures=[pool.submit(access,index) for index in range(5)]
            self.assertEqual([future.result(3) for future in futures],[self.root/(str(i)+'.py') for i in range(5)])
        self.assertEqual(len(roots),1)
        self.assertEqual(set(targets),{self.root/(str(i)+'.py') for i in range(5)})

    def test_existing_bundle_is_still_hashed_and_tampering_is_rejected(self):
        source=self.base/'source';source.mkdir()
        (source/'agent.py').write_bytes(b'# exact synthetic controller\n')
        (source/'nested').mkdir();(source/'nested'/'data.json').write_bytes(b'{"synthetic":true}\n')
        target=ensure_version(source,self.files)
        self.assertEqual(ensure_version(source,self.files),target)
        installed=self.files.path(target+'/agent.py')
        installed.write_bytes(b'# tampered controller\n')
        with self.assertRaisesRegex(ValueError,'file does not match'):ensure_version(source,self.files)


if __name__=='__main__':unittest.main()
