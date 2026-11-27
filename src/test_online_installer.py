"""The canonical update asset stays complete when building an optional online installer."""
from pathlib import Path
import json
import hashlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import build_installer
import build_online_installer as online


class OnlineInstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'dist').mkdir()
        self.version='0.2.100'
        self.payload=self.root/'dist'/f'HandWorkbench-{self.version}-windows-x64-setup.exe'
        self.payload.write_bytes(b'MZ'+b'ISOLATED COMPLETE INSTALLER TEST DATA'*20)
        self.compiler=self.root/'compiler.exe';self.compiler.write_bytes(b'NOT EXECUTED')
        self.language=self.root/'zh.isl';self.language.write_text('ISOLATED LANGUAGE')
        self.calls=[]

    def compile(self,args,**kwargs):
        self.calls.append(args)
        self.assertIs(kwargs['check'],True)
        entry=self.root/'dist'/f'HandWorkbench-{self.version}-windows-x64-online-setup.exe'
        entry.write_bytes(b'MZSMALL ISOLATED ENTRY')

    def build(self):
        with patch.object(subprocess,'run',side_effect=self.compile):
            return online.build(self.root,self.version,self.compiler,self.language)

    def test_optional_online_entry_pins_the_unchanged_canonical_updater_asset(self):
        record=self.build()
        expected=hashlib.sha256(self.payload.read_bytes()).hexdigest()
        command=self.calls[0]
        self.assertIn('/DPayloadSha256='+expected,command)
        self.assertIn('/DPayloadBytes='+str(self.payload.stat().st_size),command)
        self.assertIn('/DPayloadUrl=https://github.com/Fairank/wuji-hand-studio/releases/download/v0.2.100/'+self.payload.name,command)
        self.assertEqual(record['sha256'],expected)
        self.assertEqual(Path(record['installer']),self.payload)
        self.assertEqual(hashlib.sha256(self.payload.read_bytes()).hexdigest(),expected)
        entry=Path(record['online_installer'])
        self.assertEqual(entry.name,'HandWorkbench-0.2.100-windows-x64-online-setup.exe')
        self.assertNotEqual(entry,self.payload)
        self.assertEqual(entry.with_name(entry.name+'.sha256').read_text('ascii'),record['online_sha256']+'  '+entry.name+'\n')
        self.assertFalse(any(x.startswith('/DAudit') for x in command))

    def test_combined_builder_keeps_complete_asset_at_the_public_update_name(self):
        source=self.root/'src'
        source.mkdir()
        (source/'edition.json').write_text(json.dumps({'version':self.version,'name':'workbench'}),encoding='utf-8')
        image=self.root/'runtime_payload'/'controller.tar.gz'
        image.parent.mkdir()
        image.write_bytes(b'test payload')
        (source/'runtime_manifest.json').write_text(
            json.dumps({'file':image.name,'sha256':hashlib.sha256(image.read_bytes()).hexdigest()}),encoding='utf-8')
        full=b'MZ COMPLETE TEST INSTALLER'
        def compiler(args,**kwargs):
            if '-Command' in args:
                return
            self.calls.append(args)
            output=next(arg.removeprefix('/DOutputName=') for arg in args if arg.startswith('/DOutputName='))
            path=self.root/'dist'/(output+'.exe')
            path.write_bytes(full if args[-1].endswith('studio.iss') else b'MZ OPTIONAL ONLINE ENTRY')
        argv=['build_installer.py','--iscc',str(self.compiler),'--zh-language',str(self.language),
              '--webview-bootstrap',str(self.compiler),'--online']
        with patch.object(build_installer,'ROOT',self.root),patch.object(build_installer.sys,'argv',argv),\
             patch.object(build_installer.sys,'platform','win32'),patch.object(subprocess,'run',side_effect=compiler),\
             patch('builtins.print') as output:
            build_installer.main()
        result=json.loads(output.call_args.args[0])
        self.assertEqual(Path(result['installer']),self.payload)
        self.assertEqual(self.payload.read_bytes(),full)
        self.assertEqual(result['edition'],'workbench')
        self.assertEqual(Path(result['online_installer']).name,
                         'HandWorkbench-0.2.100-windows-x64-online-setup.exe')

    def test_environment_cannot_redirect_payload_or_enable_test_mode(self):
        with patch.dict('os.environ',{'PayloadUrl':'http://127.0.0.1:1/other','AuditDownloadOnly':'1','WUJI_UPDATE_URL':'https://example.invalid/'}):
            self.build()
        self.assertTrue(any(x.startswith('/DPayloadUrl=https://github.com/Fairank/wuji-hand-studio/') for x in self.calls[0]))
        self.assertFalse(any('/DAudit' in x or 'example.invalid' in x for x in self.calls[0]))

    def test_noncanonical_or_argument_injection_versions_are_rejected(self):
        for value in (None,False,'','01.2.3','1.2','1.2.3-beta','1.2.3/other',"1.2.3'",'1.2.3\n'):
            with self.subTest(value=value),patch.object(subprocess,'run') as child:
                with self.assertRaises(ValueError):online.build(self.root,value,self.compiler,self.language)
                child.assert_not_called()

    def test_missing_or_truncated_or_non_pe_payload_never_compiles(self):
        for body in (b'',b'MZ',b'NOT A WINDOWS INSTALLER'):
            with self.subTest(body=body):
                self.payload.write_bytes(body)
                with patch.object(subprocess,'run') as child:
                    with self.assertRaises(ValueError):online.build(self.root,self.version,self.compiler,self.language)
                    child.assert_not_called()
        self.payload.unlink()
        with self.assertRaises(ValueError):self.build()

    def test_compile_failure_is_not_reported_as_an_available_installer(self):
        with patch.object(subprocess,'run',side_effect=subprocess.CalledProcessError(1,['SYNTHETIC COMPILER'])):
            with self.assertRaises(subprocess.CalledProcessError):online.build(self.root,self.version,self.compiler,self.language)
        self.assertFalse((self.root/'dist'/'HandWorkbench-0.2.100-windows-x64-online-setup.exe.sha256').exists())

    def test_missing_compiler_output_never_produces_a_success_sidecar(self):
        with patch.object(subprocess,'run'):
            with self.assertRaises(ValueError):online.build(self.root,self.version,self.compiler,self.language)
        self.assertFalse((self.root/'dist'/'HandWorkbench-0.2.100-windows-x64-online-setup.exe.sha256').exists())


if __name__=='__main__':unittest.main()
