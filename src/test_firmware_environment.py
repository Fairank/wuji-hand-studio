"""Exact failing Windows environment and terminal-error regressions; no devices."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock,patch

from firmware_host import firmware_environment
from firmware_service import official_failure_detail
import native_terminal_worker

SN='SYNTHETIC-GLOVE'
ERROR='HOME not set; cannot locate firmware cache'
HEADER='│ SN ┆ Status ┆ Before ┆ After ┆ Duration ┆ Detail │\r\n'
BOTTOM='└──────────────┴────────┴────────┴───────┴──────────┴────────────────┘\r\n'
def row(sn=SN,detail=ERROR,status='failed'):
    return '│ '+sn+' ┆ '+status+' ┆ 1.0.0 ┆ - ┆ - ┆ '+detail+' │\r\n'


class FirmwareEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name)

    def test_windows_userprofile_supplies_missing_home_to_child_only(self):
        with patch('firmware_host.sys.platform','win32'),patch.dict(os.environ,{'USERPROFILE':str(self.home),'WUJI_NO_UPDATE_CHECK':'0'}):
            os.environ.pop('HOME',None);before=dict(os.environ)
            value=firmware_environment()
            self.assertEqual(value['HOME'],str(self.home));self.assertEqual(value['USERPROFILE'],str(self.home))
            self.assertEqual(value['WUJI_NO_UPDATE_CHECK'],'1');self.assertEqual(value['WUJI_TELEMETRY_DISABLE'],'1')
            self.assertEqual(dict(os.environ),before)

    def test_explicit_home_is_preserved_without_splitting_sdk_profiles(self):
        custom=self.home/'custom-sdk-home'
        with patch('firmware_host.sys.platform','win32'),patch.dict(os.environ,{'HOME':str(custom),'USERPROFILE':str(self.home)}):
            self.assertEqual(firmware_environment()['HOME'],str(custom))
            self.assertEqual(os.environ['HOME'],str(custom))

    def test_empty_home_falls_back_to_userprofile(self):
        with patch('firmware_host.sys.platform','win32'),patch.dict(os.environ,{'HOME':'','USERPROFILE':str(self.home)}):
            self.assertEqual(firmware_environment()['HOME'],str(self.home));self.assertEqual(os.environ['HOME'],'')

    def test_invalid_windows_fallback_does_not_spawn_or_choose_app_data(self):
        with patch('firmware_host.sys.platform','win32'),patch.dict(os.environ,{'HOME':'','USERPROFILE':'relative-home'}):
            with self.assertRaises(ValueError):firmware_environment()

    def test_conpty_receives_an_explicit_environment_copy(self):
        child={'HOME':str(self.home),'USERPROFILE':str(self.home),'PATH':os.defpath,'WUJI_NO_UPDATE_CHECK':'1'}
        original=dict(child);spawn=Mock(return_value=object())
        module=SimpleNamespace(__version__='3.1.0',Backend=SimpleNamespace(ConPTY=0),PtyProcess=SimpleNamespace(spawn=spawn))
        with patch.dict(sys.modules,winpty=module):
            parent=dict(os.environ);native_terminal_worker._spawn(['synthetic.exe','argument with spaces'],env=child)
            self.assertEqual(dict(os.environ),parent)
        spawn.assert_called_once_with(['synthetic.exe','argument with spaces'],dimensions=(38,100),backend='0',env=original)
        self.assertIsNot(spawn.call_args.kwargs['env'],child);self.assertEqual(child,original)


class OfficialFailureDetailTests(unittest.TestCase):
    def test_original_failed_table_yields_exact_device_detail(self):
        self.assertEqual(official_failure_detail(HEADER+row()+BOTTOM,SN),ERROR)

    def test_split_colours_are_removed_after_terminal_chunks_are_joined(self):
        chunks=[HEADER,'│ '+SN+' ┆ \x1b[38;5;', '9mfailed\x1b[0m ┆ 1.0.0 ┆ - ┆ - ┆ '+ERROR+' │\r\n',BOTTOM]
        self.assertEqual(official_failure_detail(''.join(chunks),SN),ERROR)

    def test_other_serial_or_nonfailed_status_never_supplies_a_detail(self):
        self.assertIsNone(official_failure_detail(HEADER+row('ANOTHER')+BOTTOM,SN))
        self.assertIsNone(official_failure_detail(HEADER+row(status='upgraded')+BOTTOM,SN))

    def test_unknown_or_clipped_table_is_not_guessed(self):
        for output in (row()+BOTTOM,HEADER+row(),row(),HEADER.replace('Detail','Unknown')+row()+BOTTOM):
            with self.subTest(output=output):self.assertIsNone(official_failure_detail(output,SN))

    def test_repeated_or_conflicting_rows_are_not_collapsed_into_one_result(self):
        for output in (HEADER+row()+row(detail='another problem')+BOTTOM,HEADER+row()+BOTTOM+HEADER+row()+BOTTOM):
            with self.subTest(output=output):self.assertIsNone(official_failure_detail(output,SN))

    def test_wrapped_detail_only_consumes_empty_identity_continuation(self):
        output=HEADER+row(detail='HOME not set;')+'│ ┆ ┆ ┆ ┆ ┆ cannot locate firmware cache │\r\n'+row('ANOTHER',detail='unrelated')+BOTTOM
        self.assertEqual(official_failure_detail(output,SN),ERROR)

    def test_conflicting_success_and_failure_for_same_device_has_no_short_reason(self):
        self.assertIsNone(official_failure_detail(HEADER+row()+row(status='succeeded')+BOTTOM,SN))

    def test_incomplete_bottom_border_does_not_close_the_table(self):
        for border in ('└','└──┴──','└──┘'):
            with self.subTest(border=border):self.assertIsNone(official_failure_detail(HEADER+row()+border,SN))


if __name__=='__main__':unittest.main()
