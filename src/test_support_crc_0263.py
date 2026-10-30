import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import support_bundle


def archive_bytes(damaged=False):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_STORED) as package:
        package.writestr('logs/device.log',b'ORIGINAL-SYNTHETIC-LOG')
    raw=bytearray(buffer.getvalue())
    if damaged:raw[raw.index(b'ORIGINAL-SYNTHETIC-LOG')]^=1
    return bytes(raw)


class SupportCRCFlowTests(unittest.TestCase):
    def test_zero_exit_with_bad_entry_keeps_brief_then_explicit_retry_preserves_exact_good_zip(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);bad=archive_bytes(True);good=archive_bytes()
            self.assertTrue(zipfile.is_zipfile(io.BytesIO(bad)))
            calls=[];payloads=iter((bad,good))
            def run(command,timeout):
                calls.append(list(command))
                Path(command[command.index('--output')+1]).write_bytes(next(payloads))
                return 0,'original official log summary',''
            with patch.object(support_bundle,'DATA',root),patch('doctor.sdk_command',side_effect=lambda config,args:args),patch('official_cli.cli_path',return_value='/synthetic/not-executed-cli'):
                first=support_bundle.export_support({'mode':'local'},1,'first observation',None,run,None)
                self.assertFalse(first['collection_complete'])
                self.assertFalse(first['official_archive_available'])
                self.assertIn('CRC',first['collection_error'])
                self.assertEqual(first['stdout'],'original official log summary')
                with self.assertRaises(FileNotFoundError):support_bundle.artifact(first['id'],'official')
                original_brief=support_bundle.artifact(first['id'],'brief').read_bytes()
                first_all=support_bundle.artifact(first['id'],'all').read_bytes()
                with zipfile.ZipFile(io.BytesIO(first_all)) as package:
                    self.assertIsNone(package.testzip())
                    self.assertNotIn('official-wuji-support.zip',package.namelist())
                    self.assertFalse(json.loads(package.read('export-result.json'))['collection_complete'])
                self.assertEqual(len(calls),1)
                second=support_bundle.export_support({'mode':'local'},1,'explicit retry',None,run,None)
                self.assertTrue(second['collection_complete'])
                self.assertEqual(support_bundle.artifact(second['id'],'official').read_bytes(),good)
                with zipfile.ZipFile(support_bundle.artifact(second['id'],'all')) as package:
                    self.assertIsNone(package.testzip())
                    self.assertEqual(package.read('official-wuji-support.zip'),good)
                self.assertEqual(support_bundle.artifact(first['id'],'brief').read_bytes(),original_brief)
                self.assertEqual(support_bundle.artifact(first['id'],'all').read_bytes(),first_all)
                self.assertEqual(len(calls),2)

    def test_partial_exit_preserves_valid_archive_and_never_marks_collection_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            good=archive_bytes()
            def run(command,timeout):
                Path(command[command.index('--output')+1]).write_bytes(good)
                return 1,'original partial output','failed optional collector'
            with patch.object(support_bundle,'DATA',Path(folder)),patch('doctor.sdk_command',side_effect=lambda config,args:args),patch('official_cli.cli_path',return_value='/synthetic/not-executed-cli'):
                result=support_bundle.export_support({'mode':'local'},7,'',None,run,None)
                self.assertFalse(result['collection_complete'])
                self.assertTrue(result['official_archive_available'])
                self.assertEqual(support_bundle.artifact(result['id'],'official').read_bytes(),good)
                self.assertEqual(result['exit_code'],1)
                self.assertEqual(result['stderr'],'failed optional collector')


if __name__=='__main__':unittest.main()
