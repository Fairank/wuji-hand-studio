"""Execute the real PowerShell helper with isolated command substitutes.

No installer, desktop application, network endpoint or device is started.
Actual filesystem identity/digest checks and the helper's control flow run.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


WRAPPER = r'''
$ErrorActionPreference='Stop'
$global:fixture=Get-Content -LiteralPath $env:WUJI_UPDATE_HELPER_FIXTURE -Raw|ConvertFrom-Json
$global:fixtureClosed=$false
function RecordEvent($value) {
    [IO.File]::AppendAllText($global:fixture.events,(($value|ConvertTo-Json -Compress)+[Environment]::NewLine))
}
function New-Object {
    param($TypeName,$ArgumentList,$ComObject)
    if ($ComObject -eq 'WScript.Shell') {
        $notice=[pscustomobject]@{}
        $notice|Add-Member -MemberType ScriptMethod -Name Popup -Value {
            param($Text,$Timeout,$Title,$Style)
            RecordEvent @{kind='error-dialog';text=$Text;style=$Style}
            return 0
        }
        return $notice
    }
    return Microsoft.PowerShell.Utility\New-Object -TypeName $TypeName -ArgumentList $ArgumentList
}
function Invoke-RestMethod {
    param($Uri,$TimeoutSec,$Method,$Headers,$ContentType,$Body)
    $operation=if ($Body) { ($Body|ConvertFrom-Json).operation } else { 'read' }
    RecordEvent @{kind='api';uri=$Uri;operation=$operation}
    if ($Uri.EndsWith('/api/state')) { return @{csrf='synthetic-token'} }
    if ($operation -eq 'prepare_update') {
        if ($global:fixture.case -eq 'preflight-failed') { throw 'Synthetic active device session' }
        return @{ok=$true;sha256=$global:fixture.digest}
    }
    if ($operation -eq 'close_idle') {
        if ($global:fixture.case -eq 'close-failed') { throw 'Synthetic app refuses idle close' }
        $global:fixtureClosed=$true
        return @{ok=$true}
    }
    return @{native=$true;edition='workbench';version='0.2.44';pid=12345;data_dir=$global:fixture.data}
}
function Get-Process {
    param($Id,$ErrorAction)
    if ($global:fixtureClosed) { return $null }
    return [pscustomobject]@{Path=$global:fixture.exe;StartTime=[datetime]'2026-01-01T00:00:00Z'}
}
function Start-Process {
    param($FilePath,$ArgumentList,$WindowStyle,[switch]$PassThru,[switch]$Wait)
    $kind=if ($FilePath -eq $global:fixture.installer) { 'installer' } else { 'application' }
    RecordEvent @{kind=$kind;path=$FilePath;arguments=@($ArgumentList);style=$WindowStyle;wait=[bool]$Wait}
    if ($kind -eq 'installer') {
        if ($global:fixture.case -eq 'installer-failed') { return @{ExitCode=2} }
        if ($global:fixture.case -ne 'identity-missing') {
            $edition=@{name='workbench';product_id='hand-workbench';version='0.2.45'}
            if ($global:fixture.case -eq 'wrong-version') { $edition.version='0.2.44' }
            if ($global:fixture.case -eq 'wrong-product') { $edition.product_id='another-product' }
            [IO.File]::WriteAllText($global:fixture.edition,($edition|ConvertTo-Json))
        }
        return @{ExitCode=0}
    }
    if ($global:fixture.case -eq 'restart-failed') { throw 'Synthetic application start failure' }
    if ($global:fixture.case -eq 'restart-no-pid') { return @{Id=0} }
    return @{Id=23456}
}
if ($global:fixture.case -eq 'validate-only') {
    & $global:fixture.helper -PlanFile $global:fixture.plan -ValidateOnly
} else {
    & $global:fixture.helper -PlanFile $global:fixture.plan
}
exit $LASTEXITCODE
'''


@unittest.skipUnless(sys.platform == 'win32', 'Windows PowerShell helper')
class UpdateHelperTests(unittest.TestCase):
    def run_case(self, case):
        with tempfile.TemporaryDirectory(prefix='update-helper-') as folder:
            root=Path(folder);data=root/'data';updates=data/'updates';updates.mkdir(parents=True)
            exe=root/'installed'/'HandWorkbench.exe';exe.parent.mkdir();exe.write_bytes(b'MZ not an application')
            edition=exe.parent/'_internal'/'edition.json';edition.parent.mkdir()
            installer=updates/'HandWorkbench-0.2.45-windows-x64-setup.exe'
            payload=b'MZ SYNTHETIC INSTALLER, NEVER EXECUTED';installer.write_bytes(payload)
            digest=hashlib.sha256(payload).hexdigest();token='a'*32
            result=updates/('apply-'+token+'.result.json');plan=updates/('apply-'+token+'.json')
            plan.write_text(json.dumps(dict(schema=1,app_id='44DF38B2-39A0-491F-B154-E587231C83CE',
                repository='Fairank/wuji-hand-studio',token=token,sha256='0'*64 if case=='bad-hash' else digest,
                version='0.2.45',old_version='0.2.44',data_dir=str(data),result_file=str(result),
                installer=str(installer),executable=str(exe),origin='http://127.0.0.1:8781',expected_app_pid=12345)),encoding='utf-8')
            events=root/'events.jsonl';events.write_text('',encoding='utf-8')
            config=root/'fixture.json';config.write_text(json.dumps(dict(case=case,data=str(data),exe=str(exe),
                digest=digest,installer=str(installer),edition=str(edition),events=str(events),plan=str(plan),
                helper=str(Path(__file__).with_name('workbench_update_apply.ps1')))),encoding='utf-8')
            wrapper=root/'wrapper.ps1';wrapper.write_text(WRAPPER,encoding='utf-8')
            shell=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
            env=dict(os.environ,WUJI_UPDATE_HELPER_FIXTURE=str(config))
            for key in tuple(env):
                if key.upper()=='PSMODULEPATH':env.pop(key)
            completed=subprocess.run([str(shell),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(wrapper)],
                env=env,capture_output=True,text=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertTrue(result.exists(),completed.stderr)
            report=json.loads(result.read_text('utf-8-sig'))
            trace=[json.loads(line) for line in events.read_text('utf-8-sig').splitlines()]
            self.assertEqual(installer.read_bytes(),payload)
            self.assertEqual(report['token'],token)
            return completed.returncode,report,trace

    def test_visible_installer_and_verified_restart_complete_in_order(self):
        code,report,trace=self.run_case('success')
        self.assertEqual(code,0);self.assertEqual(report['phase'],'installed')
        self.assertTrue(report['version_verified']);self.assertTrue(report['launch_started'])
        self.assertEqual([r['kind'] for r in trace],['api','api','api','api','installer','application'])
        installer=trace[-2]
        self.assertIn('/SILENT',installer['arguments']);self.assertNotIn('/VERYSILENT',installer['arguments'])
        self.assertIn('/NOCLOSEAPPLICATIONS',installer['arguments']);self.assertIn('/NORESTARTAPPLICATIONS',installer['arguments'])
        self.assertEqual(installer['style'],'Normal');self.assertTrue(installer['wait'])
        self.assertEqual(trace[-1]['style'],'Normal');self.assertFalse(trace[-1]['wait'])

    def test_validation_only_never_installs_or_restarts(self):
        code,report,trace=self.run_case('validate-only')
        self.assertEqual(code,0);self.assertEqual(report['phase'],'validated')
        self.assertFalse(report['version_verified']);self.assertFalse(report['launch_started'])
        self.assertTrue(all(r['kind']=='api' for r in trace))
        self.assertNotIn('close_idle',[r.get('operation') for r in trace])

    def test_preflight_or_close_failure_never_installs(self):
        for case in ('preflight-failed','close-failed','bad-hash'):
            with self.subTest(case=case):
                code,report,trace=self.run_case(case)
                self.assertEqual(code,1);self.assertEqual(report['phase'],'error')
                self.assertFalse(report['launch_started']);self.assertFalse(report['version_verified'])
                self.assertTrue(all(r['kind']=='api' for r in trace))

    def test_installer_failure_or_wrong_identity_never_launches_application(self):
        for case in ('installer-failed','wrong-version','wrong-product','identity-missing'):
            with self.subTest(case=case):
                code,report,trace=self.run_case(case)
                self.assertEqual(code,1);self.assertEqual(report['phase'],'error')
                self.assertFalse(report['version_verified']);self.assertFalse(report['launch_started'])
                self.assertNotIn('application',[r['kind'] for r in trace])
                self.assertEqual(trace[-1]['kind'],'error-dialog')
                self.assertIn(report['error'],trace[-1]['text'])

    def test_restart_failure_does_not_report_installed(self):
        for case in ('restart-failed','restart-no-pid'):
            with self.subTest(case=case):
                code,report,trace=self.run_case(case)
                self.assertEqual(code,1);self.assertEqual(report['phase'],'error')
                self.assertTrue(report['version_verified']);self.assertFalse(report['launch_started'])
                self.assertEqual(trace[-2]['kind'],'application')
                self.assertEqual(trace[-1]['kind'],'error-dialog')
                self.assertIn(report['error'],trace[-1]['text'])


if __name__=='__main__':unittest.main()
