"""Synthetic file/export failures; never construct a controller or HTTP server."""
import ast
import errno
import io
import json
import os
from pathlib import Path
import re
import secrets
import sys
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from urllib.parse import parse_qs,urlsplit
import zipfile


def method(name):
    source=Path(__file__).with_name('console_server.py')
    tree=ast.parse(source.read_text(encoding='utf-8-sig'))
    helper=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='local_file_error')
    handler=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Handler')
    function=next(n for n in handler.body if isinstance(n,ast.FunctionDef) and n.name==name)
    scope=dict(errno=errno,os=os,json=json,secrets=secrets,re=re,parse_qs=parse_qs,urlsplit=urlsplit,
               ORIGIN='http://127.0.0.1:8781',PORT=8781)
    exec(compile(ast.Module(body=[helper,function],type_ignores=[]),str(source),'exec'),scope)
    return scope[name]


class FileActionErrors(unittest.TestCase):
    def post(self,name,error,path='/api/action'):
        values={'name':name} if path=='/api/action' else {'operation':name}
        body=json.dumps(values).encode()
        controller=SimpleNamespace(csrf='synthetic',action=Mock(side_effect=error))
        target=SimpleNamespace(path=path,headers={'Content-Type':'application/json','Content-Length':str(len(body)),'X-Console-Token':'synthetic'},
            rfile=io.BytesIO(body),reply=Mock(),allowed_host=lambda:True,_task_view_headers=lambda:None,
            server=SimpleNamespace(controller=controller,updates=SimpleNamespace(apply=Mock(side_effect=error)),desktop=object()))
        with patch('desktop_tools.dispatch',side_effect=error),patch.dict(os.environ,{'WUJI_FLEET_PARENT':''}):method('do_POST')(target)
        return target.reply.call_args.args

    def test_known_file_actions_keep_category_and_filename_without_arbitrary_exception_text(self):
        for action in ('workbench_update_apply','parameters_save','retarget_tuning_save','retarget_tuning_export'):
            with self.subTest(action=action):
                code,body=self.post(action,PermissionError(13,'private arbitrary exception string','synthetic-data/updates/file'))
                self.assertEqual(code,503);self.assertEqual(body['error_code'],'local_file_error')
                self.assertIn('access denied',body['error']);self.assertIn('synthetic-data/updates/file',body['error'])
                self.assertNotIn('private arbitrary',body['error']);self.assertNotIn('反馈通道',body['error'])

    def test_disk_full_has_specific_category(self):
        _,body=self.post('workbench_update_apply',OSError(errno.ENOSPC,'sensitive','synthetic-data/updates/file'))
        self.assertIn('storage is full',body['error']);self.assertNotIn('sensitive',body['error'])

    def test_device_and_unknown_runtime_failures_keep_existing_guard(self):
        for action,error in [('connect',PermissionError(13,'sensitive','controller-path')),('workbench_update_apply',RuntimeError('sensitive runtime detail')),('workbench_update_apply',TimeoutError('sensitive timeout'))]:
            with self.subTest(action=action,error=type(error).__name__):
                code,body=self.post(action,error)
                self.assertEqual(code,503);self.assertNotIn('error_code',body);self.assertNotIn('sensitive',body['error'])
                self.assertIn('反馈通道',body['error'])

    def test_desktop_model_import_file_error_is_classified(self):
        code,body=self.post('import_model',FileNotFoundError(2,'sensitive','synthetic-model.zip'),'/api/desktop')
        self.assertEqual(code,503);self.assertIn('File or folder is missing',body['error'])

    def test_report_read_failure_returns_json_before_reply_body(self):
        target=SimpleNamespace(path='/api/report?id='+'a'*32,allowed_host=lambda:True,reply=Mock(),server=SimpleNamespace(controller=SimpleNamespace(reports=Path('synthetic-reports'))))
        with patch.object(Path,'read_bytes',side_effect=PermissionError(13,'sensitive','synthetic-reports/report.json')):
            method('do_GET')(target)
        code,body=target.reply.call_args.args
        self.assertEqual(code,503);self.assertEqual(body['error_code'],'local_file_error');self.assertIn('report.json',body['error'])

    def test_support_read_failure_returns_json_before_attachment_headers(self):
        target=SimpleNamespace(path='/api/support?id='+'a'*32,allowed_host=lambda:True,reply=Mock(),send_response=Mock())
        get=method('do_GET')
        with patch('support_bundle.artifact',return_value=Path('synthetic-support.zip')),patch.object(Path,'open',side_effect=PermissionError(13,'sensitive','synthetic-support.zip')):
            get(target)
        code,body=target.reply.call_args.args
        self.assertEqual(code,503);self.assertEqual(body['error_code'],'local_file_error');target.send_response.assert_not_called()

    def test_support_attachment_streams_exact_utf8_bytes_and_length(self):
        get=method('do_GET');headers={}
        with tempfile.TemporaryDirectory(prefix='support-download-0251-') as folder:
            artifact=Path(folder)/'fault-brief.txt';content='合成故障简报\nOfficial original output\n'.encode('utf-8-sig');artifact.write_bytes(content)
            target=SimpleNamespace(path='/api/support?id='+'a'*32+'&kind=brief',allowed_host=lambda:True,reply=Mock(),
                send_response=Mock(),send_header=lambda k,v:headers.update({k:v}),end_headers=Mock(),wfile=io.BytesIO())
            with patch('support_bundle.artifact',return_value=artifact):get(target)
        target.reply.assert_not_called();target.send_response.assert_called_once_with(200)
        self.assertEqual(int(headers['Content-Length']),len(content));self.assertEqual(target.wfile.getvalue(),content)
        self.assertIn('attachment;',headers['Content-Disposition']);self.assertIn('fault-brief.txt',headers['Content-Disposition'])


class MacSupportCollection(unittest.TestCase):
    def collect(self,copy,code=1):
        import support_bundle
        self.folder=tempfile.TemporaryDirectory(prefix='support-0251-');self.addCleanup(self.folder.cleanup)
        root=Path(self.folder.name)
        self.runtime=SimpleNamespace(args=lambda command:['synthetic-limactl','shell',*command],ensure_running=Mock(),
            env=lambda:dict(LIMA_HOME='/synthetic/lima',LIMA_SHELL='/bin/bash',LIMA_WORKDIR='/opt/hand-workbench',SSH_AUTH_SOCK=''),
            run=copy,NAME='hand-workbench')
        self.execute=Mock(return_value=(code,'official original stdout','official original stderr'))
        with patch.object(support_bundle,'DATA',root),patch.dict(sys.modules,{'macos_runtime':self.runtime}),patch('official_cli.cli_path',return_value='/usr/local/bin/wuji'):
            result=support_bundle.export_support(dict(mode='macvm'),1,'synthetic symptom',None,self.execute,None)
        self.path=root/'support'/result['id']
        return result

    def test_missing_remote_zip_retains_brief_and_original_output(self):
        result=self.collect(Mock(side_effect=RuntimeError('copy source is missing')))
        self.assertFalse(result['official_archive_available']);self.assertFalse(result['collection_complete'])
        self.assertEqual(result['exit_code'],1);self.assertIn('source is missing',result['collection_error'])
        text=(self.path/'fault-brief.txt').read_text(encoding='utf-8-sig')
        for expected in ('official original stdout','official original stderr','copy source is missing'):self.assertIn(expected,text)
        with zipfile.ZipFile(self.path/'workbench-support.zip') as package:
            self.assertIn('fault-brief.txt',package.namelist());self.assertNotIn('official-wuji-support.zip',package.namelist())
        for item in ('LIMA_HOME=/synthetic/lima','LIMA_SHELL=/bin/bash','LIMA_WORKDIR=/opt/hand-workbench','SSH_AUTH_SOCK='):
            self.assertIn(item,self.execute.call_args.args[0])

    def test_partial_copy_is_not_mistaken_for_official_archive(self):
        def copy(*args,**kwargs):
            Path(args[2]).write_bytes(b'partial synthetic transfer')
            raise RuntimeError('copy interrupted')
        result=self.collect(copy,code=0)
        self.assertFalse(result['official_archive_available']);self.assertFalse(result['collection_complete'])
        self.assertFalse((self.path/'official-wuji-support.zip.partial').exists())
        self.assertTrue((self.path/'workbench-support.zip').is_file())

    def test_successful_copy_preserves_exact_official_zip(self):
        def copy(*args,**kwargs):
            with zipfile.ZipFile(args[2],'w') as archive:archive.writestr('official.txt','synthetic official logs')
        result=self.collect(copy,code=0)
        self.assertTrue(result['official_archive_available']);self.assertTrue(result['collection_complete'])
        self.assertNotIn('collection_error',result)
        with zipfile.ZipFile(self.path/'workbench-support.zip') as package:
            self.assertEqual(package.read('official-wuji-support.zip'),(self.path/'official-wuji-support.zip').read_bytes())


class MacOpenFolder(unittest.TestCase):
    def open_folder(self,run):
        source=Path(__file__).with_name('macos_desktop.py')
        tree=ast.parse(source.read_text(encoding='utf-8-sig'))
        host=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='MacDesktop')
        function=next(n for n in host.body if isinstance(n,ast.FunctionDef) and n.name=='open_data_folder')
        namespace=dict(DATA=Path('synthetic-application-data'),subprocess=SimpleNamespace(run=run,DEVNULL=subprocess.DEVNULL))
        exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),namespace)
        return namespace['open_data_folder'](None)

    def test_folder_open_waits_for_confirmed_zero_exit(self):
        run=Mock(return_value=SimpleNamespace(returncode=0,stdout='',stderr=''))
        self.assertTrue(self.open_folder(run)['ok']);self.assertEqual(run.call_args.args[0][0],'/usr/bin/open')
        self.assertEqual(run.call_args.kwargs['timeout'],10);self.assertTrue(run.call_args.kwargs['capture_output'])

    def test_folder_open_nonzero_exit_is_visible_and_not_success(self):
        run=Mock(return_value=SimpleNamespace(returncode=3,stdout='',stderr='Synthetic data folder does not exist'))
        with self.assertRaisesRegex(ValueError,'exit 3.*does not exist'):self.open_folder(run)

    def test_folder_open_timeout_is_not_success(self):
        run=Mock(side_effect=subprocess.TimeoutExpired(['/usr/bin/open','synthetic'],10))
        with self.assertRaises(subprocess.TimeoutExpired):self.open_folder(run)


if __name__=='__main__':unittest.main()
