import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import http.client

from doctor import Doctor,sdk_command
from doctor_export import report_text,counts
from doctor_report import flatten_report
from console_server import Controller,Handler


class DoctorUpgradeTests(unittest.TestCase):
    def test_direct_address_selector_is_bounded_and_exclusive(self):
        from doctor import doctor_args
        self.assertEqual(doctor_args({'cli':'wuji'},'diagnose',address='192.168.1.101:50001')[-2:],['--address','192.168.1.101:50001'])
        for address in (None,[],'127.0.0.1:50001','224.0.0.1:50001','192.168.1.101','192.168.1.101:0','192.168.1.101:65536','192.168.1.101:50001;echo x'):
            with self.subTest(address=address),self.assertRaises(ValueError):doctor_args({'cli':'wuji'},'diagnose',address=address)
        with self.assertRaises(ValueError):doctor_args({'cli':'wuji'},'diagnose','test-serial','192.168.1.101:50001')
    def test_official_fix_is_kept_without_mutating_raw(self):
        raw={'env':[{'status':'warn','summary':'old SDK','fix':'update SDK','tip':'check version'}]}
        before=json.dumps(raw)
        self.assertEqual(flatten_report(raw)[0]['tip'],'check version · update SDK')
        self.assertEqual(json.dumps(raw),before)
        raw['env'][0]['fix']='check version'
        self.assertEqual(flatten_report(raw)[0]['tip'],'check version')

    def test_skip_unknown_and_failure_stay_distinct(self):
        rows=flatten_report({'device':[{'status':'skip'},{'status':'fail'},{}]})
        value=counts(rows)
        self.assertEqual(value['pass_count'],0)
        self.assertEqual((value['skip_count'],value['fail_count'],value['unknown_count']),(1,1,1))

    def test_posix_sdk_python_on_windows_host(self):
        cmd=['/usr/local/bin/wuji','doctor','--json']
        self.assertEqual(sdk_command({'mode':'wsl','python':'/opt/sdk/bin/python'},cmd),
                         ['/usr/bin/env','PATH=/opt/sdk/bin:/usr/local/bin:/usr/bin:/bin',*cmd])
        self.assertEqual(cmd,['/usr/local/bin/wuji','doctor','--json'])
        self.assertEqual(sdk_command({'mode':'ssh','python':'python3'},cmd),cmd)

    def test_export_contains_original_streams_and_no_success_claim(self):
        report=dict(stdout='{"device": []}\n',stderr='original warning\n',exit_code=0,controller='wsl',finished=0)
        text=report_text(report)
        self.assertIn(report['stdout'],text);self.assertIn(report['stderr'],text)
        self.assertIn('does not imply all checks passed',text)
        self.assertIn('1970-01-01T00:00:00+00:00',text)

    def test_saved_report_restores_but_never_starts_cli(self):
        with tempfile.TemporaryDirectory() as tmp,patch('doctor.DATA',Path(tmp)),patch('doctor.local_run') as run:
            p=Path(tmp)/'doctor-latest.json'
            p.write_text(json.dumps(dict(exit_code=0,stdout='original',stderr='')),encoding='utf-8')
            d=Doctor();self.assertTrue(d.snapshot()['restored']);self.assertFalse(d.snapshot()['running'])
            run.assert_not_called()
            p.write_text('broken',encoding='utf-8');self.assertEqual(Doctor().snapshot()['status'],'idle')

    def test_work_uses_controller_python_and_records_official_fix(self):
        with tempfile.TemporaryDirectory() as tmp,patch('doctor.DATA',Path(tmp)),patch('doctor.local_run',return_value=(0,'{"env":[{"status":"warn","fix":"retry"}]}','')) as run:
            d=Doctor();d.work(dict(mode='local',python='/opt/sdk/bin/python'),['wuji','doctor','--json'],'diagnose')
            self.assertIn('PATH=/opt/sdk/bin:/usr/local/bin:/usr/bin:/bin',run.call_args.args[0])
            s=d.snapshot();self.assertEqual(s['report']['rows'][0]['tip'],'retry')
            self.assertEqual(s['report']['counts']['warn_count'],1)
            self.assertFalse(s['report']['motor_commands_sent'])

    def test_http_exports_preserve_raw_bytes(self):
        with tempfile.TemporaryDirectory() as tmp,patch('doctor.DATA',Path(tmp)):
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
            server.controller=Controller(reports=Path(tmp)/'reports')
            raw='{"env":[], "device":[]}\n'
            server.controller.doctor.state.update(report=dict(operation='diagnose',stdout=raw,stderr='',exit_code=0))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                for fmt in ('txt','raw','json','bad'):
                    c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                    c.request('GET','/api/doctor/report?format='+fmt,headers={'Host':'127.0.0.1:8781'})
                    r=c.getresponse();body=r.read();c.close()
                    self.assertEqual(r.status,400 if fmt=='bad' else 200)
                    if fmt=='raw':self.assertEqual(body,raw.encode())
                    if fmt=='txt':self.assertIn(raw,body.decode('utf-8-sig'))
                    if fmt=='json':self.assertEqual(json.loads(body)['stdout'],raw)
            finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
