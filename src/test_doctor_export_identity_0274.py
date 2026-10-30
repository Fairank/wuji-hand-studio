"""Exact displayed diagnostic exports; no real CLI, network, or device."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace as N
from unittest.mock import patch

import doctor
from console_server import Handler


def completed(label='A', code=0):
    return dict(operation='diagnose', exit_code=code, stdout=json.dumps({'report':label}),
                stderr='original stderr '+label, finished=123., controller='local', rows=[])


def get_report(service, query):
    output, status, headers = io.BytesIO(), {}, {}
    target=N(path='/api/doctor/report'+query, server=N(controller=N(doctor=service)),
             allowed_host=lambda:True, send_response=lambda v:status.update(code=v),
             send_header=lambda k,v:headers.update({k:v}), end_headers=lambda:None, wfile=output)
    target.reply=lambda *a,**k:Handler.reply(target,*a,**k)
    Handler.do_GET(target)
    return status['code'], output.getvalue(), headers


class DiagnosticExportIdentityTests(unittest.TestCase):
    def service(self, folder, report=None):
        with patch.object(doctor,'DATA',Path(folder)):
            service=doctor.Doctor()
        if report is not None:
            with service.lock:service.state.update(status='completed', report=copy.deepcopy(report))
        return service

    def token(self, service):
        token=service.snapshot().get('report_id')
        self.assertIsInstance(token,str)
        self.assertRegex(token,r'^[a-f0-9]{64}$')
        return token

    def test_three_formats_export_exact_displayed_report_without_changing_contents_or_filenames(self):
        with tempfile.TemporaryDirectory() as folder:
            original=completed();service=self.service(folder,original);token=self.token(service)
            from doctor_export import report_text
            for fmt,body,filename in [('txt',report_text(original).encode('utf-8-sig'),'wuji-doctor-report.txt'),
                    ('raw',original['stdout'].encode(),'wuji-doctor-official.json'),
                    ('json',json.dumps(original,ensure_ascii=False,allow_nan=False).encode(),'wuji-doctor-report.json')]:
                with self.subTest(fmt=fmt):
                    code,data,headers=get_report(service,'?format='+fmt+'&report_id='+token)
                    self.assertEqual(code,200);self.assertEqual(data,body)
                    self.assertIn(filename,headers['Content-Disposition'])
            self.assertEqual(service.snapshot()['report'],original)

    def test_old_visible_export_cannot_return_new_report_in_any_format(self):
        with tempfile.TemporaryDirectory() as folder:
            service=self.service(folder,completed());token=self.token(service)
            with service.lock:service.state['report']=completed('B',7)
            self.assertNotEqual(self.token(service),token)
            for fmt in ('txt','raw','json'):
                code,data,_=get_report(service,'?format='+fmt+'&report_id='+token)
                self.assertEqual(code,409);self.assertNotIn(b'original stderr B',data)
                self.assertEqual(json.loads(data)['error_code'],'report_changed')

    def test_malformed_and_duplicate_tokens_refuse_before_export_while_legacy_latest_is_compatible(self):
        with tempfile.TemporaryDirectory() as folder:
            service=self.service(folder,completed())
            for query in ('?report_id=', '?report_id=bad', '?report_id='+'F'*64,
                          '?report_id='+'a'*64+'&report_id='+'b'*64):
                self.assertEqual(get_report(service,query)[0],400)
            self.assertEqual(get_report(service,'?format=raw')[1],completed()['stdout'].encode())

    def test_running_and_failed_without_report_never_return_an_old_or_other_report(self):
        with tempfile.TemporaryDirectory() as folder:
            service=self.service(folder,completed());token=self.token(service)
            for status in ('running','error'):
                with service.lock:service.state.update(status=status,report=None,error='synthetic failure' if status=='error' else None)
                self.assertIsNone(service.snapshot().get('report_id'))
                self.assertEqual(get_report(service,'?report_id='+token)[0],409)
                self.assertEqual(get_report(service,'')[0],404)

    def test_failed_support_or_save_retains_same_report_identity_and_export(self):
        with tempfile.TemporaryDirectory() as folder:
            service=self.service(folder,completed());token=self.token(service)
            with service.lock:service.state.update(status='error',error='synthetic support/storage failure',support=None)
            self.assertEqual(self.token(service),token)
            self.assertEqual(get_report(service,'?format=raw&report_id='+token)[1],completed()['stdout'].encode())

    def test_actual_completed_report_remains_bound_and_exportable_when_atomic_save_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            service=self.service(folder)
            raw=json.dumps({'env':[{'status':'warn','summary':'synthetic stored only in memory'}]})
            with (patch.object(doctor,'DATA',Path(folder)),
                  patch.object(doctor,'local_run',return_value=(7,raw,'original stderr')),
                  patch.object(doctor.os,'replace',side_effect=PermissionError('synthetic report save refused'))):
                service.work({'mode':'local','python':'not-executed'},['not-executed'],'diagnose')
            state=service.snapshot();self.assertEqual(state['status'],'completed');self.assertTrue(state['error'])
            self.assertEqual(state['report']['exit_code'],7);self.assertFalse((Path(folder)/'doctor-latest.json').exists())
            self.assertEqual(get_report(service,'?format=raw&report_id='+self.token(service))[1],raw.encode())

    def test_saved_legacy_report_gets_stable_identity_after_restart_without_writing_source(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'doctor-latest.json';source=json.dumps(completed()).encode();path.write_bytes(source)
            first=self.service(folder);token=self.token(first);second=self.service(folder)
            self.assertTrue(second.snapshot()['restored']);self.assertEqual(self.token(second),token)
            self.assertEqual(get_report(second,'?report_id='+token)[0],200);self.assertEqual(path.read_bytes(),source)
            path.write_text(json.dumps(completed('B')),encoding='utf-8');third=self.service(folder)
            self.assertNotEqual(self.token(third),token);self.assertEqual(get_report(third,'?report_id='+token)[0],409)

    def test_reordered_report_keys_keep_identity_but_same_size_output_mutation_changes_it(self):
        with tempfile.TemporaryDirectory() as folder:
            original=completed();service=self.service(folder,original);token=self.token(service)
            with service.lock:service.state['report']=dict(reversed(list(original.items())))
            self.assertEqual(self.token(service),token)
            with service.lock:service.state['report']['stdout']=completed('Z')['stdout']
            self.assertNotEqual(self.token(service),token)


if __name__=='__main__':unittest.main()
