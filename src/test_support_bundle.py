import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from support_bundle import export_support,artifact,brief


class SupportBundleTests(unittest.TestCase):
    def test_official_archive_is_not_modified_and_partial_exit_is_retained(self):
        with tempfile.TemporaryDirectory() as tmp,patch('support_bundle.DATA',Path(tmp)):
            def run(command,timeout):
                self.assertEqual(timeout,180)
                self.assertIn('--days',command);self.assertIn('7',command)
                self.assertEqual(command[command.index('logs')+1],'export')
                self.assertNotIn('--with-dump',command)
                self.assertNotIn('upgrade',command);self.assertNotIn('enable',command)
                path=Path(command[-1])
                with zipfile.ZipFile(path,'w') as z:z.writestr('manifest.json','{"synthetic":true}')
                return 1,'partial official summary','some collection failed'
            result=export_support({'mode':'local','cli':'wuji','python':'python3'},7,'LED off',None,run,None)
            self.assertEqual(result['exit_code'],1);self.assertFalse(result['automatic_upload'])
            self.assertTrue(result['official_archive_available'])
            original=artifact(result['id'],'official').read_bytes()
            with zipfile.ZipFile(artifact(result['id'],'all')) as z:
                self.assertEqual(z.read('official-wuji-support.zip'),original)
                self.assertIn('LED off',z.read('fault-brief.txt').decode('utf-8-sig'))
                self.assertEqual(json.loads(z.read('export-result.json'))['exit_code'],1)

    def test_missing_official_archive_still_returns_an_honest_brief(self):
        with tempfile.TemporaryDirectory() as tmp,patch('support_bundle.DATA',Path(tmp)):
            result=export_support({'mode':'local','cli':'wuji'},1,'',None,lambda *a,**k:(1,'no devices',''),None)
            self.assertFalse(result['official_archive_available'])
            with self.assertRaises(FileNotFoundError):artifact(result['id'],'official')
            text=artifact(result['id'],'brief').read_text(encoding='utf-8-sig')
            self.assertIn('unconfirmed',text);self.assertNotIn('0x2108',text)

    def test_invalid_scope_is_rejected_before_running_cli(self):
        for days in (0,31,True,'7'):
            with self.assertRaises(ValueError):export_support({'mode':'local'},days,'',None,None,None)
        with self.assertRaises(ValueError):export_support({'mode':'local'},1,'a'*2001,None,None,None)
        for identifier in ('../secret','X'*32,None):
            with self.assertRaises(ValueError):artifact(identifier,'all')

    def test_user_observation_is_labelled_and_previous_report_retains_its_time(self):
        text=brief({'stdout':'old official result','stderr':'','exit_code':0,'finished':0},
                   'operator says index_S4 0x2108',dict(exit_code=1,stdout='new summary',stderr=''))
        self.assertIn('Operator observation',text);self.assertIn('1970-01-01',text)
        self.assertIn('no fault clearing',text);self.assertIn('new summary',text)


if __name__=='__main__':unittest.main()
