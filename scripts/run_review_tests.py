"""Review runner contracts; only temporary files and synthetic Python children."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest


class ReviewRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=Path(__file__).with_name('run_review.py')
        spec=importlib.util.spec_from_file_location('review_runner_contract',path)
        cls.runner=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.runner)

    def test_environment_isolated_without_changing_home_or_parent(self):
        original=dict(os.environ)
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder).resolve()
            env,roots=self.runner.isolated_environment(base,{**original,'HOME':'unchanged-home','CODEX_HOME':'unchanged-codex','WUJI_FLEET_PARENT':'old'})
            self.assertEqual(env['HOME'],'unchanged-home')
            self.assertEqual(env['CODEX_HOME'],'unchanged-codex')
            self.assertNotIn('WUJI_FLEET_PARENT',env)
            self.assertTrue(all(path.is_relative_to(base) for path in roots.values()))
            self.assertEqual(env['WUJI_CLI_AUTO_CHECK'],'0')
            self.assertEqual(env['WUJI_APP_AUTO_CHECK'],'0')
        self.assertEqual(dict(os.environ),original)

    def test_guard_refuses_external_network_and_default_sdk_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();events=[]
            guard=self.runner.audit_guard(events,[root/'real-sdk'])
            for host in ['localhost','127.0.0.1','::1']:
                guard('socket.connect',(None,(host,1234)))
            with self.assertRaisesRegex(RuntimeError,'non-loopback'):
                guard('socket.getaddrinfo',('vendor.invalid',80,0,0,0))
            with self.assertRaisesRegex(RuntimeError,'SDK user data'):
                guard('open',(str(root/'real-sdk/users/registry.toml'),'r',0))
            guard('open',(str(root/'synthetic-sdk/users/registry.toml'),'r',0))
            self.assertEqual(len(events),2)

    def test_existing_records_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'record'
            self.runner.fresh_record_directory(target)
            marker=target/'keep';marker.write_bytes(b'original')
            with self.assertRaises(FileExistsError):
                self.runner.fresh_record_directory(target)
            self.assertEqual(marker.read_bytes(),b'original')

    def test_ast_checks_parse_without_importing_and_reject_invalid_python(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'src').mkdir();p=root/'src/fixture.py'
            p.write_text("raise RuntimeError('must not execute')\n",encoding='utf8')
            self.assertEqual(self.runner.check_python_syntax(root),1)
            p.write_text('def broken(\n',encoding='utf8')
            with self.assertRaises(SyntaxError):self.runner.check_python_syntax(root)

    def test_source_fingerprint_detects_a_changed_test_or_product_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'src').mkdir();p=root/'src/fixture.py'
            p.write_text('x=1\n',encoding='utf8');before=self.runner.source_hashes(root)
            p.write_text('x=2\n',encoding='utf8')
            self.assertNotEqual(before,self.runner.source_hashes(root))

    def test_source_fingerprint_includes_ui_markup_styles_identity_and_svg(self):
        for extension in ('.html','.css','.json','.svg'):
            with self.subTest(extension=extension),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);(root/'src').mkdir();path=root/('src/fixture'+extension)
                path.write_text('original',encoding='utf8');before=self.runner.source_hashes(root)
                self.assertIn(path.relative_to(root).as_posix(),before)
                path.write_text('changed',encoding='utf8')
                self.assertNotEqual(before,self.runner.source_hashes(root))

    def test_command_failure_retains_output_and_nonzero_status(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);log=root/'failure.log'
            result=self.runner.run_command([sys.executable,'-c',"print('synthetic failure'); raise SystemExit(3)"],log,root,dict(os.environ))
            self.assertEqual(result['returncode'],3)
            self.assertIn('synthetic failure',log.read_text(encoding='utf8'))


if __name__=='__main__':unittest.main()
