"""SDK metadata failures do not remove valid uncalibrated user identities."""
import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from sdk_user_catalog import read_users,validate_report

ROOT=Path(__file__).resolve().parents[3]/'outputs/customer-flow-0243/catalog-test-temp'
ROOT.mkdir(parents=True,exist_ok=True)


class WujiException(Exception):pass


def user(ident='',name='Default',default=True):
    return dict(user_id=ident,display_name=name,is_default=default)


def empty(label):return WujiException('Internal error: no calibration data found for user '+label)


class UserCatalogTests(unittest.TestCase):
    def run_read(self,manager):
        original=tempfile.TemporaryDirectory
        with patch.dict(sys.modules,wuji_sdk=N(WujiException=WujiException)), \
                patch('sdk_user_catalog.tempfile.TemporaryDirectory',side_effect=lambda **_:original(dir=ROOT)):
            result=read_users(manager)
        validate_report(dict(version=1,users=result))
        return result

    def test_default_empty_user_retains_actual_empty_id_and_no_models(self):
        manager=N(list_users=Mock(return_value=[user()]),export_user_data=Mock(side_effect=empty('Default')))
        self.assertEqual(self.run_read(manager),[dict(user_id='',name='Default',is_default=True,models={'left':False,'right':False})])
        manager.list_users.assert_called_once_with()
        self.assertEqual(manager.export_user_data.call_args.args[0],'')

    def test_named_empty_user_matches_exact_sdk_id_never_display_name(self):
        manager=N(list_users=Mock(return_value=[user('actual-sdk-uuid','Alex',False)]),
                  export_user_data=Mock(side_effect=empty('actual-sdk-uuid')))
        self.assertEqual(self.run_read(manager),[dict(user_id='actual-sdk-uuid',name='Alex',is_default=False,models={'left':False,'right':False})])
        self.assertEqual(manager.export_user_data.call_args.args[0],'actual-sdk-uuid')

    def test_empty_and_calibrated_users_are_all_returned_without_switch_scan_or_connect(self):
        users=[user(),user('empty-uuid','Empty',False),user('left-uuid','Alex',False),user('right-uuid','Alex',False)]
        original=copy.deepcopy(users)
        def export(ident,path):
            self.assertTrue(str(path).endswith('.zip'))
            if ident in ('','empty-uuid'):raise empty('Default' if ident=='' else ident)
            return dict(owner_user_id=ident,components=[dict(name='hand_model.left',state='present' if ident=='left-uuid' else 'missing'),
                                                      dict(name='hand_model.right',state='present' if ident=='right-uuid' else 'missing')])
        manager=N(list_users=Mock(return_value=users),export_user_data=Mock(side_effect=export))
        result=self.run_read(manager)
        self.assertEqual([u['user_id'] for u in result],['','empty-uuid','left-uuid','right-uuid'])
        self.assertEqual([u['models'] for u in result],[{'left':False,'right':False}]*2+[{'left':True,'right':False},{'left':False,'right':True}])
        self.assertEqual(users,original)
        self.assertEqual([call.args[0] for call in manager.export_user_data.call_args_list],['','empty-uuid','left-uuid','right-uuid'])
        # The strict adapter exposes only these two SDK APIs, so extra calls fail.
        self.assertEqual(set(vars(manager)),{'list_users','export_user_data'})

    def test_other_sdk_export_failures_remain_the_same_failure(self):
        for message in ('Internal error: permission denied','No data available',
                        'Internal error: invalid calibration archive','Internal error: no calibration data found for user Default; unreadable files'):
            with self.subTest(message=message):
                error=WujiException(message)
                manager=N(list_users=Mock(return_value=[user()]),export_user_data=Mock(side_effect=error))
                with self.assertRaises(WujiException) as caught:self.run_read(manager)
                self.assertIs(caught.exception,error)

    def test_wrong_identity_or_lookalike_python_error_is_never_treated_as_empty(self):
        for row,error in ((user(),empty('other-uuid')),(user('actual-sdk-uuid','Alex',False),empty('Alex')),
                          (user('actual-sdk-uuid','Default',False),empty('Default')),
                          (user(),RuntimeError('Internal error: no calibration data found for user Default'))):
            with self.subTest(row=row,error=type(error).__name__):
                manager=N(list_users=Mock(return_value=[row]),export_user_data=Mock(side_effect=error))
                with self.assertRaises(type(error)) as caught:self.run_read(manager)
                self.assertIs(caught.exception,error)

    def test_successful_export_still_requires_exact_owner_and_valid_manifest(self):
        for manifest in (dict(owner_user_id='different',components=[]),dict(owner_user_id='',components=None),None):
            with self.subTest(manifest=manifest):
                manager=N(list_users=Mock(return_value=[user()]),export_user_data=Mock(return_value=manifest))
                with self.assertRaises(ValueError):self.run_read(manager)

    def test_inconsistent_default_identity_does_not_export_or_hide_failure(self):
        for row in (user('',default=False),user('named-uuid','Default',True)):
            with self.subTest(row=row):
                manager=N(list_users=Mock(return_value=[row]),export_user_data=Mock(side_effect=empty('Default')))
                with self.assertRaisesRegex(ValueError,'Invalid SDK wearer identity'):self.run_read(manager)
                manager.export_user_data.assert_not_called()


if __name__=='__main__':unittest.main()
