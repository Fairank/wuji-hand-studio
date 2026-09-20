"""Transport and diagnostics checks use synthetic children only, never SDK I/O."""
import json,os,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from bridge_config import DEFAULT,validate,load_config
from local_controller import LocalController,LocalFiles
from doctor import Doctor,doctor_args,local_run
from console_agent import validate_command

class TransportTests(unittest.TestCase):
    def test_old_ssh_config_gets_new_defaults_without_writing(self):
        with tempfile.TemporaryDirectory() as d,patch('bridge_config.DATA',Path(d)):
            p=Path(d)/'connection.json';p.write_text('{"host":"controller.invalid","username":"user"}')
            before=p.read_bytes();c=load_config()
            self.assertEqual(c['cli'],'wuji');self.assertEqual(c['host'],'controller.invalid')
            self.assertEqual(c['mode'],'ssh')
            self.assertEqual(p.read_bytes(),before)

    def test_local_rejected_on_non_linux(self):
        with patch('bridge_config.sys.platform','win32'):
            with self.assertRaises(ValueError):validate({**DEFAULT,'mode':'local'})

    def test_files_cannot_escape_controller(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'controller';root.mkdir();files=LocalFiles(root)
            with self.assertRaises(ValueError):files.open(root/'../outside','w')
            with files.open(root/'parameters.upload','wb') as f:f.write(b'synthetic')
            files.posix_rename(root/'parameters.upload',root/'parameters.py')
            self.assertEqual((root/'parameters.py').read_bytes(),b'synthetic')

    def test_local_roundtrip_and_eof_no_shell(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'console_agent.py').write_text('import sys,os\nfor line in sys.stdin:\n print(os.environ["WUJI_HAND_PROFILE"]+":"+line.strip(),flush=True)\n')
            with patch('local_controller.sys.platform','linux'):
                c=LocalController(dict(agent_directory=d,python=sys.executable),'hand1_right')
            with self.assertRaises(ValueError):c.exec_command('arbitrary command')
            try:
                i,o,e=c.exec_command(c.agent_command);p=c.process
                i.write('test\n');i.flush();self.assertEqual(next(iter(o)).strip(),'hand1_right:test')
                with self.assertRaises(ValueError):c.exec_command(c.agent_command)
                c.close();self.assertIsNotNone(p.poll())
            finally:c.close()

    def test_device_endpoint_port_validation(self):
        for address in ('','192.0.2.1','192.0.2.1:7447'):
            validate_command(dict(name='connect',address=address))
        for address in ('host;echo bad','192.0.2.1:0','192.0.2.1:99999','192.0.2.1:','192.0.2.1:74:47'):
            with self.assertRaises(ValueError):validate_command(dict(name='connect',address=address))

class DoctorTests(unittest.TestCase):
    def test_only_fixed_official_readonly_operations(self):
        c=dict(cli='wuji')
        self.assertEqual(doctor_args(c,'version'),['wuji','--version'])
        self.assertEqual(doctor_args(c,'diagnose','S_01'),['wuji','doctor','--json','--sn','S_01'])
        for operation,serial in [('upgrade',''),('diagnose',';reboot'),('diagnose','--yes')]:
            with self.assertRaises(ValueError):doctor_args(c,operation,serial)

    def test_local_capture_preserves_nonzero_and_both_streams(self):
        code,out,err=local_run([sys.executable,'-c','import sys; print("synthetic"); print("warning",file=sys.stderr); sys.exit(1)'])
        self.assertEqual((code,out.strip(),err.strip()),(1,'synthetic','warning'))

    def test_capture_limit_is_an_error(self):
        with patch('doctor.MAX_OUTPUT',100):
            with self.assertRaises(ValueError):local_run([sys.executable,'-c','print("x"*500)'])

    def test_skip_is_preserved_and_failure_code_not_changed(self):
        doc=Doctor();raw=json.dumps({'env':[{'label':'synthetic','status':'pass'}],'device':[{'label':'no recipe','status':'skip'}]})
        with tempfile.TemporaryDirectory() as d,patch('doctor.DATA',Path(d)),patch('doctor.local_run',return_value=(1,raw,'warning')):
            doc.work(dict(mode='local'),['wuji','doctor','--json'],'diagnose')
            report=doc.snapshot()['report']
            self.assertEqual(report['exit_code'],1);self.assertEqual(report['rows'][-1]['status'],'skip')
            self.assertFalse(report['motor_commands_sent']);self.assertEqual(report['stdout'],raw)

    def test_malformed_report_keeps_raw_output(self):
        doc=Doctor()
        with tempfile.TemporaryDirectory() as d,patch('doctor.DATA',Path(d)),patch('doctor.local_run',return_value=(0,'not json','')):
            doc.work(dict(mode='local'),['wuji','doctor','--json'],'diagnose')
            report=doc.snapshot()['report'];self.assertIn('parse_error',report);self.assertNotIn('rows',report)

    def test_active_diagnostic_blocks_conflicting_actions(self):
        from console_server import Controller
        with tempfile.TemporaryDirectory() as d:
            c=Controller(reports=Path(d)/'reports');c.doctor.state['running']=True
            for name in ('connect','parameters_sync','bridge_config_save','device_profile_select'):
                with self.subTest(name=name),self.assertRaisesRegex(ValueError,'diagnostic'):c.action(dict(name=name))

if __name__=='__main__':unittest.main()
