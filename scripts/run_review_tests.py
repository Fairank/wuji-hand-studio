"""Review runner contracts; only temporary files and synthetic Python children."""
import importlib.util
import io
from contextlib import redirect_stdout
import os
from pathlib import Path
import shutil
import subprocess
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
            self.assertEqual(env['PYTHONIOENCODING'],'utf-8')
            self.assertEqual(env['PYTHONUTF8'],'1')
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

    def test_source_fingerprint_includes_persistent_acceptance_rules_and_template(self):
        for name in ('AGENTS.md','docs/QA_WORKFLOW.md','docs/ci/code-review.yml'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text('original acceptance policy',encoding='utf8');before=self.runner.source_hashes(root)
                self.assertIn(name,before)
                path.write_text('changed acceptance policy',encoding='utf8')
                self.assertNotEqual(before,self.runner.source_hashes(root))

    def test_command_failure_retains_output_and_nonzero_status(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);log=root/'failure.log'
            result=self.runner.run_command([sys.executable,'-c',"print('synthetic failure'); raise SystemExit(3)"],log,root,dict(os.environ))
            self.assertEqual(result['returncode'],3)
            self.assertIn('synthetic failure',log.read_text(encoding='utf8'))

    def test_unicode_failure_keeps_utf8_log_and_status_on_an_ascii_console(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);env,_=self.runner.isolated_environment(root/'isolated')
            output=io.BytesIO();console=io.TextIOWrapper(output,encoding='ascii',write_through=True)
            with redirect_stdout(console):
                result=self.runner.run_command([sys.executable,'-c',
                    "print(chr(0x4e2d)+chr(0x1f9ea)); raise SystemExit(3)"],root/'unicode.log',root,env)
            self.assertEqual(result['returncode'],3)
            self.assertEqual((root/'unicode.log').read_text(encoding='utf8').strip(),'\u4e2d\U0001f9ea')
            self.assertIn(b'\\U0001f9ea',output.getvalue())

    def test_workflow_keeps_two_isolated_passes_and_only_publishes_summary_logs(self):
        import yaml
        root=Path(__file__).resolve().parents[1]
        active=root/'.github/workflows/code-review.yml'
        self.assertEqual(active.read_bytes(),(root/'docs/ci/code-review.yml').read_bytes())
        workflow=yaml.safe_load(active.read_text(encoding='utf8'))
        self.assertEqual(set(workflow['on']),{'push','pull_request','workflow_dispatch'})
        self.assertEqual(workflow['permissions'],{'contents':'read'})
        self.assertNotIn('REVIEW_RECORD',workflow['jobs']['software-checks']['env'])
        steps=workflow['jobs']['software-checks']['steps']
        self.assertEqual(steps[0]['id'],'review-record')
        self.assertEqual(steps[0]['shell'],'pwsh')
        passes=[step['run'] for step in steps if 'run_review.py' in step.get('run','')]
        self.assertEqual(len(passes),2)
        self.assertIn('/pass-1',passes[0]);self.assertIn('/pass-2',passes[1])
        artifact=next(step for step in steps if step.get('name')=='Retain review summaries and logs')
        self.assertEqual(artifact['if'],"always() && steps.review-record.outcome == 'success'")
        self.assertEqual(artifact['with']['path'].splitlines(),[
            '${{ env.REVIEW_RECORD }}/pass-1/*.json','${{ env.REVIEW_RECORD }}/pass-1/*.log',
            '${{ env.REVIEW_RECORD }}/pass-2/*.json','${{ env.REVIEW_RECORD }}/pass-2/*.log'])
        self.assertNotIn('**',artifact['with']['path'])
        comparison=next(step for step in steps if step.get('name')=='Confirm identical frozen sources across both passes')
        self.assertIn('verify_passes',comparison['run'])
        self.assertGreater(steps.index(comparison),max(i for i,step in enumerate(steps) if 'run_review.py' in step.get('run','')))
        self.assertLess(steps.index(comparison),steps.index(artifact))

    def test_repeated_acceptance_requires_identical_successful_complete_reports(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            first,second=Path(folder)/'first',Path(folder)/'second'
            report=dict(successful=True,mode='all',changed_sources=[],sources_before={'src/app.py':'a'*64},sources_after={'src/app.py':'a'*64})
            for directory in (first,second):
                directory.mkdir();(directory/'summary.json').write_text(json.dumps(report),encoding='utf8')
            with redirect_stdout(io.StringIO()):result=self.runner.verify_passes(first,second)
            self.assertTrue(result['successful']);self.assertEqual(result['source_files'],1)
            with self.assertRaisesRegex(ValueError,'different evidence'):self.runner.verify_passes(first,first)

    def test_repeated_acceptance_rejects_between_pass_changes_or_incomplete_passes(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            first,second=Path(folder)/'first',Path(folder)/'second'
            original=dict(successful=True,mode='all',changed_sources=[],sources_before={'src/app.py':'a'*64},sources_after={'src/app.py':'a'*64})
            for directory in (first,second):directory.mkdir()
            (first/'summary.json').write_text(json.dumps(original),encoding='utf8')
            cases=[dict(original,sources_before={'src/app.py':'b'*64},sources_after={'src/app.py':'b'*64}),
                dict(original,successful=False),dict(original,mode='node'),dict(original,changed_sources=['src/app.py']),
                dict(original,sources_after={'src/app.py':'b'*64}),dict(original,sources_before={},sources_after={}),
                dict(original,sources_before=['invalid'],sources_after=['invalid'])]
            for report in cases:
                with self.subTest(report=report):
                    (second/'summary.json').write_text(json.dumps(report),encoding='utf8')
                    with self.assertRaises(ValueError):self.runner.verify_passes(first,second)


class ReviewRecordInitializationTests(unittest.TestCase):
    def initialization(self,folder,values):
        import yaml
        shell=shutil.which('pwsh')
        if not shell:self.skipTest('Workflow initialization requires PowerShell 7')
        workflow=yaml.safe_load((Path(__file__).resolve().parents[1]/'.github/workflows/code-review.yml').read_text(encoding='utf8'))
        step=next(step for step in workflow['jobs']['software-checks']['steps'] if step.get('id')=='review-record')
        script=folder/'initialize.ps1';script.write_text(step['run'],encoding='utf8')
        env=dict(os.environ)
        for key in ('RUNNER_TEMP','GITHUB_ENV','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','REVIEW_RECORD'):env.pop(key,None)
        env.update({key:str(value) for key,value in values.items() if value is not None})
        return subprocess.run([shell,'-NoProfile','-NonInteractive','-File',str(script)],
            cwd=folder,env=env,capture_output=True,text=True,encoding='utf8',timeout=20,check=False)

    def test_step_appends_unique_run_attempt_path_under_runner_temp_before_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);temporary=folder/'runner temp 中文';temporary.mkdir()
            output=folder/'github-env';prefix='EXISTING=preserved\n'
            paths=[]
            for run,attempt in [('41921308929','1'),('41921308929','2'),('41921308930','1')]:
                output.write_text(prefix,encoding='utf8')
                result=self.initialization(folder,dict(RUNNER_TEMP=temporary,GITHUB_ENV=output,GITHUB_RUN_ID=run,GITHUB_RUN_ATTEMPT=attempt))
                self.assertEqual(result.returncode,0,result.stderr)
                expected=temporary/('workbench-review-'+run+'-'+attempt)
                self.assertEqual(output.read_text(encoding='utf8'),prefix+'REVIEW_RECORD='+str(expected)+'\n')
                self.assertFalse(expected.exists(),'The runner must create each fresh pass directory itself')
                paths.append(expected)
            self.assertEqual(len(set(paths)),3)

    def test_missing_or_invalid_temp_and_run_context_fail_without_publishing_a_path(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);temporary=folder/'runner-temp';temporary.mkdir()
            output=folder/'github-env';file=folder/'not-a-directory';file.write_text('keep',encoding='utf8')
            base=dict(RUNNER_TEMP=temporary,GITHUB_ENV=output,GITHUB_RUN_ID='41921308929',GITHUB_RUN_ATTEMPT='1')
            for changes in ({'RUNNER_TEMP':None},{'RUNNER_TEMP':' '},{'RUNNER_TEMP':folder/'absent'},
                    {'RUNNER_TEMP':file},{'RUNNER_TEMP':'runner-temp'},{'GITHUB_RUN_ID':None},
                    {'GITHUB_RUN_ATTEMPT':None},{'GITHUB_ENV':None}):
                with self.subTest(changes=changes):
                    output.write_text('EXISTING=preserved\n',encoding='utf8')
                    result=self.initialization(folder,{**base,**changes})
                    self.assertNotEqual(result.returncode,0)
                    self.assertEqual(output.read_text(encoding='utf8'),'EXISTING=preserved\n')
            self.assertEqual(list(temporary.iterdir()),[])


if __name__=='__main__':unittest.main()
