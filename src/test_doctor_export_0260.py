"""Saved diagnostic timestamp corruption must not block original report exports."""
import hashlib,json
from pathlib import Path
import tempfile,unittest,zipfile
from unittest.mock import patch
import doctor,doctor_export,support_bundle

class SavedDiagnosticExportTests(unittest.TestCase):
    def test_restored_invalid_timestamp_exports_original_text_and_complete_support_archive(self):
        for stamp in (1e300,float('nan'),float('inf'),-float('inf'),True,None,'unknown'):
            with self.subTest(stamp=stamp),tempfile.TemporaryDirectory() as folder:
                root=Path(folder)
                original=dict(exit_code=1,stdout='原始 stdout — synthetic',stderr='原始 stderr',finished=stamp,operation='diagnose',controller='local',rows=[])
                (root/'doctor-latest.json').write_text(json.dumps(original,ensure_ascii=False),encoding='utf-8')
                with patch.object(doctor,'DATA',root):restored=doctor.Doctor().snapshot()
                self.assertTrue(restored['restored'])
                text=doctor_export.report_text(restored['report'])
                self.assertIn('时间: unknown',text);self.assertIn(original['stdout'],text);self.assertIn(original['stderr'],text);self.assertIn('退出码: 1',text)
                calls=[]
                def fake_local(args,timeout):
                    calls.append(list(args));path=Path(args[args.index('--output')+1])
                    with zipfile.ZipFile(path,'w') as package:package.writestr('synthetic.log',b'original official log bytes')
                    return 0,'synthetic official stdout','synthetic official stderr'
                with patch.object(support_bundle,'DATA',root),patch.object(doctor,'sdk_command',lambda config,args:args),patch('official_cli.cli_path',lambda config:'/synthetic/never-executed-cli'):
                    result=support_bundle.export_support({'mode':'local'},1,'operator observation',restored['report'],fake_local,None)
                self.assertEqual(len(calls),1);self.assertTrue(result['collection_complete']);bundle=root/'support'/result['id']
                self.assertEqual(hashlib.sha256((bundle/'official-wuji-support.zip').read_bytes()).hexdigest(),result['sha256'])
                with zipfile.ZipFile(bundle/'workbench-support.zip') as package:
                    self.assertEqual(set(package.namelist()),{'official-wuji-support.zip','fault-brief.txt','export-result.json','doctor-report.json'})
                    self.assertIn('时间: unknown',package.read('fault-brief.txt').decode('utf-8-sig'));saved=json.loads(package.read('doctor-report.json'))
                    self.assertEqual(saved['stdout'],original['stdout']);self.assertEqual(saved['stderr'],original['stderr']);self.assertEqual(saved['exit_code'],1)

    def test_restored_public_report_is_strict_json_safe_and_original_file_is_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'doctor-latest.json'
            original=dict(exit_code=1,stdout='original raw stdout',stderr='original raw stderr',finished=float('nan'),counts={'unknown_count':float('inf')},metadata=[{'ratio':-float('inf')}])
            raw=json.dumps(original).encode();path.write_bytes(raw)
            with patch.object(doctor,'DATA',root):restored=doctor.Doctor().snapshot()
            json.dumps(restored,allow_nan=False)
            report=restored['report'];self.assertIsNone(report['finished']);self.assertTrue(report['finished_unknown']);self.assertTrue(report['metadata_unverified'])
            self.assertIsNone(report['counts']['unknown_count']);self.assertIsNone(report['metadata'][0]['ratio'])
            self.assertEqual({x['path'] for x in report['metadata_issues']},{'report.finished','report.counts.unknown_count','report.metadata.0.ratio'})
            self.assertEqual(path.read_bytes(),raw);self.assertEqual(report['stdout'],original['stdout']);self.assertEqual(report['stderr'],original['stderr']);self.assertEqual(report['exit_code'],1)

    def test_valid_numeric_timestamp_and_unknown_bool_do_not_modify_report(self):
        report=dict(finished=0,stdout='original',stderr='',exit_code=0)
        self.assertIn('1970-01-01T00:00:00+00:00',doctor_export.report_text(report));self.assertEqual(report,dict(finished=0,stdout='original',stderr='',exit_code=0))
        report['finished']=False;self.assertIn('时间: unknown',doctor_export.report_text(report))

if __name__=='__main__':unittest.main()
