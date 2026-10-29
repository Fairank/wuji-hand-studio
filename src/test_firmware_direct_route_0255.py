"""Real direct-route coordinator/service/host, with fake network and processes.

No SDK, CLI, device discovery, firmware write or real HTTP request is performed.
"""
from contextlib import nullcontext
import copy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from firmware_catalog_service import FirmwareCatalogService
from firmware_host import FirmwareHost
from firmware_service import FirmwareNotStarted,FirmwareService,FirmwareWriterUnconfirmed,ReadOnlyQueryUnconfirmed
from official_firmware_catalog import fetch_catalog
from official_firmware_package import prepare_package,validate_package
from test_firmware_host import FakeProcess
from test_official_firmware_catalog_0255 import SN,Response as CatalogResponse,document,encode
from test_official_firmware_package_0255 import Response as PackageResponse,bundle


OTHER='WG1KA00000000001'


class DirectHarness:
    def __init__(self,test):
        self.test=test
        self.temp=TemporaryDirectory(dir=Path(__file__).resolve().parent);test.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.now=1000.;self.marker={};self.lease_number=0
        self.release_fail=False;self.spawn_code=0;self.spawn_block=False;self.processes=[]
        self.entered=threading.Event();self.resume=threading.Event()
        self.metadata_calls=[];self.download_calls=[];self.spawn_calls=[];self.resource_calls=[];self.release_calls=[]
        self.observed_sn=SN;self.reported_sn=SN;self.reported_version='1.0.4';self.resource_override={}
        self.catalog_error=None;self.download_error=None;self.prepare_count=0
        self.row,self.raw=bundle()
        self.inventory=dict(sn=SN,serial=SN,kind='glove',generation='glove1',side='right',device_type='wuji_glove',
            observed_at=self.now,stale=False,selectable=True,conflict=False,in_use=False)
        self.cli=dict(binary='synthetic-official-no-execute.exe',version='2026.10.26',binary_sha256='a'*64)
        self.controller=N(lock=threading.RLock(),desktop_closing=False,csrf='synthetic-csrf',
            official_cli=N(idle_check=Mock()),calibration_devices=Mock(side_effect=lambda **kw:[copy.deepcopy(self.inventory)]),
            calibration_handover=N(workflow_lock=threading.RLock()),
            official_viz=N(snapshot=Mock(return_value=dict(active=True,phase='running',session_id='b'*32,reported_device=dict(sn=SN))),
                folder=self.root/'viewer',connection_record=Mock(),stop_owned=Mock(),discover_inline=Mock(),discovery_priority=Mock(side_effect=lambda:nullcontext())))
        self.host=FirmwareHost(self.controller,folder=self.root/'coordination',runner=self.read_resource,spawn=self.spawn)
        self.host._component=Mock(side_effect=lambda:copy.deepcopy(self.cli))
        self.catalog=FirmwareCatalogService(self.fetch,self.prepare,validate_package,self.observed,self.guard,clock=lambda:self.now)
        self.host.package_validator=self.catalog.validate_claim
        self.host.package_releaser=self.catalog.release_owner
        self.service=FirmwareService(self.host,self.host.cli_identity,self.controller.official_cli.idle_check,
            self.host.acquire,self.host.release,self.host.device,clock=lambda:self.now,recovery_check=self.host.recovery_check,
            claim_package=self.catalog.claim_package,validate_package=self.host.validate_package_claim,file_readback=self.host.file_readback)
        replacements={'gate':lambda *a:nullcontext(),'reservation':lambda *a:copy.deepcopy(self.marker),
            'reserve':self.reserve,'release':self.release,'atomic':self.atomic,'identity':lambda pid:'synthetic-child-start',
            'process_state':lambda *a:'dead','firmware_environment':lambda:{}}
        for name,value in replacements.items():
            patcher=patch('firmware_host.'+name,side_effect=value);patcher.start();test.addCleanup(patcher.stop)
        patcher=patch('cli_coordination.gate',side_effect=lambda *a:nullcontext());patcher.start();test.addCleanup(patcher.stop)
        test.addCleanup(self.finish_workers)

    def finish_workers(self):
        self.resume.set()
        for process in self.processes:process.alive=False
        for service in (self.catalog,self.service):
            thread=service.thread
            if thread and thread.ident is not None:thread.join(3)
            self.test.assertFalse(thread and thread.is_alive(),'Synthetic firmware worker remained alive')

    def reserve(self,kind,folder,metadata=None):
        if self.marker:raise ValueError('Synthetic lease already held')
        self.lease_number+=1;token='synthetic-lease-'+str(self.lease_number)
        self.marker=dict(kind=kind,token=token,**(metadata or {}));return token

    def release(self,token,folder):
        self.release_calls.append(token)
        if self.release_fail:raise ValueError('Synthetic release unconfirmed')
        if self.marker and self.marker.get('token')!=token:raise ValueError('Synthetic lease changed')
        self.marker={}

    def atomic(self,path,marker):self.marker=copy.deepcopy(marker)

    def guard(self):
        state=getattr(self,'service',None)
        if self.controller.desktop_closing or state and (state.snapshot()['busy'] or state.snapshot()['locked']):
            raise ValueError('Synthetic firmware busy')
        return state.snapshot()['operation_generation'] if state else 0

    def observed(self,sn):
        return dict(serial=self.observed_sn,device_type='wuji_glove',version='1.0.3',observed_at=self.now,source='synthetic-known-observation')

    def fetch(self,sn,kind,current):
        self.metadata_calls.append((sn,kind,current))
        if self.catalog_error:raise self.catalog_error
        row=copy.deepcopy(self.row)
        for key in ('download_url','elf_download_url'):row[key]=row[key].replace(SN,sn)
        return fetch_catalog(sn,kind,current,opener=lambda request,**kw:CatalogResponse(encode(document([row])),request.full_url))

    def prepare(self,entry,sn,kind,**options):
        self.prepare_count+=1
        def opener(request,**kw):
            self.download_calls.append(request.full_url)
            if self.download_error:raise self.download_error
            return PackageResponse(self.raw,request.full_url)
        return prepare_package(entry,sn,kind,self.root/('attempt-'+str(self.prepare_count)),opener=opener,**options)

    def spawn(self,command,**options):
        self.spawn_calls.append(command)
        if self.spawn_block:
            self.entered.set()
            if not self.resume.wait(3):raise RuntimeError('Synthetic spawn watchdog expired')
        process=FakeProcess(status=self.spawn_code);self.processes.append(process);return process

    def read_resource(self,command,**options):
        self.resource_calls.append(command)
        path=command[2]
        if path in self.resource_override:
            value=self.resource_override[path]
            if isinstance(value,BaseException):raise value
            return value
        value=self.reported_sn if path=='serial_number' else self.reported_version
        return N(returncode=0,stdout=json.dumps(dict(path=path,value=value)),stderr='')

    def join(self,service):
        thread=service.thread
        if thread and thread.ident is not None:thread.join(3)
        self.test.assertFalse(thread and thread.is_alive(),'Synthetic worker watchdog exceeded')
        return service.snapshot()

    def check(self,sn=SN):
        self.catalog.start_check(sn);return self.join(self.catalog)

    def prepared(self,sn=SN):
        checked=self.check(sn)
        self.catalog.start_prepare(sn,checked['catalog_token']);return self.join(self.catalog)

    def install(self,token,sn=SN):
        self.service.start_file_upgrade(sn,token,confirmed=True);return self.join(self.service)


class DirectFirmwareRouteTests(unittest.TestCase):
    def setUp(self):self.h=DirectHarness(self)

    def server(self):
        from console_server import ConsoleHTTPServer
        server=object.__new__(ConsoleHTTPServer);server.controller=self.h.controller
        server._firmware_catalog=self.h.catalog;server._firmware=self.h.service;server._firmware_transport=self.h.host
        server.firmware_preview_idle=Mock()
        return server

    def test_metadata_and_package_preparation_never_touch_device_or_end_viewer(self):
        state=self.h.prepared()
        self.assertEqual(state['phase'],'package_ready')
        self.assertEqual(len(self.h.metadata_calls),1);self.assertEqual(len(self.h.download_calls),1)
        self.h.controller.calibration_devices.assert_not_called();self.h.controller.official_cli.idle_check.assert_not_called()
        self.h.controller.official_viz.stop_owned.assert_not_called();self.h.controller.official_viz.discover_inline.assert_not_called()
        self.assertEqual(self.h.spawn_calls,[]);self.assertEqual(self.h.resource_calls,[]);self.assertEqual(self.h.marker,{})
        public=json.dumps(state)
        self.assertNotIn('download_url',public);self.assertNotIn(str(self.h.root),public);self.assertNotIn('https://',public)

    def test_complete_catalog_package_claim_official_file_process_two_resource_readback(self):
        ready=self.h.prepared();token=ready['package']['token']
        state=self.h.install(token)
        self.assertEqual(state['phase'],'verified',state)
        self.assertEqual(state['result']['source'],'official_resource_readback')
        self.assertEqual(state['result']['firmware_version'],'1.0.4');self.assertTrue(state['result']['version_verified'])
        self.assertFalse(state['can_upgrade']);self.assertIsNone(state['notification'])
        self.assertEqual(len(self.h.spawn_calls),1)
        command=self.h.spawn_calls[0]
        self.assertEqual(command[:4],[self.h.cli['binary'],'upgrade','--sn',SN]);self.assertEqual(command[-2],'--file')
        self.assertTrue(command[-1].endswith('.zip'));self.assertNotIn('--yes',command);self.assertNotIn('--force',command)
        self.assertEqual([cmd[1:3] for cmd in self.h.resource_calls],[['get','serial_number'],['get','firmware_version']])
        self.assertTrue(all(cmd[-3:]==['--sn',SN,'--json'] for cmd in self.h.resource_calls))
        self.assertEqual(self.h.marker,{});self.assertEqual(self.h.catalog._claims,{})
        self.assertIsNone(self.h.host._package_claim)
        with self.assertRaises(ValueError):self.h.install(token)
        self.assertEqual(len(self.h.spawn_calls),1)

    def test_repeated_completed_operations_release_claims_without_32_operation_exhaustion(self):
        checked=self.h.check()
        for index in range(33):
            self.h.catalog.start_prepare(SN,checked['catalog_token']);ready=self.h.join(self.h.catalog)
            state=self.h.install(ready['package']['token'])
            self.assertEqual(state['phase'],'verified',index)
            self.assertEqual(self.h.catalog._claims,{})
        self.assertEqual(len(self.h.spawn_calls),33)
        self.assertEqual(len(self.h.download_calls),1,'Validated immutable cache should avoid duplicate downloads')

    def test_metadata_offline_failure_and_explicit_retry_never_leave_busy_or_invent_current(self):
        self.h.catalog_error=OSError('synthetic offline')
        state=self.h.check();self.assertEqual(state['phase'],'failed');self.assertFalse(state['busy'])
        self.assertIsNone(state['catalog_token']);self.assertEqual(self.h.spawn_calls,[])
        self.h.catalog_error=None;self.h.observed_sn=OTHER
        state=self.h.check();self.assertEqual(state['phase'],'listed');self.assertIsNone(state['current_version'])
        self.assertIsNone(state['catalog_metadata']['higher_listed'])

    def test_busy_duplicate_old_response_switch_sn_and_stale_tokens_are_bound(self):
        entered=threading.Event();resume=threading.Event();original=self.h.catalog.fetch
        def held(*args):
            entered.set()
            if not resume.wait(2):raise RuntimeError('Synthetic catalog watchdog')
            return original(*args)
        self.h.catalog.fetch=held
        self.h.catalog.start_check(SN);self.assertTrue(entered.wait(1))
        try:
            with self.assertRaises(ValueError):self.h.catalog.start_check(OTHER)
            self.assertEqual(self.h.catalog.snapshot()['serial'],SN)
        finally:resume.set()
        first=self.h.join(self.h.catalog);self.h.catalog.fetch=original
        second=self.h.check(OTHER)
        self.assertEqual(second['serial'],OTHER)
        with self.assertRaises(ValueError):self.h.catalog.start_prepare(SN,first['catalog_token'])
        with self.assertRaises(ValueError):self.h.catalog.start_prepare(OTHER,first['catalog_token'])
        self.assertEqual(self.h.spawn_calls,[])

    def test_expired_and_backward_clock_catalog_or_package_tokens_never_start_writer(self):
        checked=self.h.check();self.h.now-=1
        with self.assertRaises(ValueError):self.h.catalog.start_prepare(SN,checked['catalog_token'])
        self.h.now+=1;self.h.catalog.start_prepare(SN,checked['catalog_token']);ready=self.h.join(self.h.catalog)
        self.h.now+=301
        with self.assertRaises(ValueError):self.h.install(ready['package']['token'])
        self.assertEqual(self.h.spawn_calls,[]);self.assertEqual(self.h.marker,{})

    def test_corrupt_package_and_different_serial_or_descriptor_are_rejected_before_spawn(self):
        ready=self.h.prepared();token=ready['package']['token']
        with self.assertRaises(ValueError):self.h.catalog.claim_package(token,OTHER,reservation_token='lease-other')
        self.assertIsNotNone(self.h.catalog.snapshot()['package'])
        package=self.h.catalog._prepared['package'];Path(package.path).write_bytes(b'corrupt retained evidence')
        with self.assertRaises(ValueError):self.h.install(token)
        self.assertEqual(self.h.spawn_calls,[]);self.assertEqual(self.h.marker,{})
        self.assertEqual(Path(package.path).read_bytes(),b'corrupt retained evidence')
        # Explicit preparation uses a fresh attempt folder when old cache fails.
        checked=self.h.catalog.snapshot();self.h.catalog.start_prepare(SN,checked['catalog_token'])
        renewed=self.h.join(self.h.catalog);self.assertEqual(renewed['phase'],'package_ready')
        self.assertEqual(self.h.install(renewed['package']['token'])['phase'],'verified')

    def test_file_changed_after_claim_is_rechecked_immediately_before_spawn(self):
        ready=self.h.prepared();original=self.h.host.package_validator;calls=[]
        def mutate_after_validation(descriptor):
            result=original(descriptor);calls.append(1)
            if len(calls)==1:Path(descriptor['path']).write_bytes(b'changed after claim validation')
            return result
        self.h.host.package_validator=mutate_after_validation
        state=self.h.install(ready['package']['token'])
        self.assertEqual(state['phase'],'failed');self.assertFalse(state['locked'])
        self.assertEqual(self.h.spawn_calls,[]);self.assertEqual(self.h.catalog._claims,{})

    def test_changed_cli_before_mutating_launch_is_refused_without_unknown_write(self):
        ready=self.h.prepared();original=self.h.catalog.claim_package
        def changed(*args,**kw):
            archive=original(*args,**kw);self.h.cli['binary_sha256']='b'*64;return archive
        self.h.service.claim_package=changed
        state=self.h.install(ready['package']['token'])
        self.assertEqual(state['phase'],'failed');self.assertFalse(state['locked']);self.assertEqual(self.h.spawn_calls,[])

    def test_unknown_process_keeps_exact_lease_claim_and_blocks_other_work_until_recovery(self):
        ready=self.h.prepared();self.h.spawn_code=None
        state=self.h.install(ready['package']['token'])
        self.assertEqual(state['phase'],'unknown');self.assertTrue(state['locked'])
        self.assertEqual(state['device']['sn'],SN);self.assertEqual(len(self.h.catalog._claims),1)
        with self.assertRaises(ValueError):self.h.check(OTHER)
        recovered=self.h.service.verify_recovery()
        self.assertEqual(recovered['phase'],'verified');self.assertEqual(recovered['result']['verdict'],'recovery_verified')
        self.assertEqual(self.h.marker,{});self.assertEqual(self.h.catalog._claims,{})
        self.assertEqual(len(self.h.spawn_calls),1,'Recovery cannot retry the writer')

    def test_confirmed_writer_failure_and_bad_resource_shape_never_report_verified(self):
        ready=self.h.prepared();self.h.spawn_code=1
        state=self.h.install(ready['package']['token'])
        self.assertEqual(state['phase'],'failed');self.assertEqual(self.h.resource_calls,[]);self.assertFalse(state['locked'])
        self.h.spawn_code=0;ready=self.h.prepared()
        self.h.resource_override['firmware_version']=N(returncode=0,stdout='{"path":"firmware_version","value":"1.0.4","extra":true}',stderr='')
        state=self.h.install(ready['package']['token'])
        self.assertEqual(state['phase'],'completed');self.assertNotEqual(state['result'].get('verdict'),'target_version_verified')
        self.assertFalse(state['locked']);self.assertEqual(self.h.catalog._claims,{})

    def test_wrong_serial_or_unmatched_version_readback_never_verifies_target(self):
        ready=self.h.prepared();self.h.reported_sn=OTHER
        state=self.h.install(ready['package']['token'])
        self.assertEqual(state['phase'],'completed');self.assertEqual(len(self.h.resource_calls),1)
        self.h.reported_sn=SN;self.h.reported_version='1.0.3';ready=self.h.prepared()
        state=self.h.install(ready['package']['token'])
        self.assertEqual(state['phase'],'completed');self.assertEqual(state['current_version'],'1.0.3')
        self.assertFalse(state['result']['version_verified'])

    def test_read_only_query_unknown_exit_retains_writer_lease_for_explicit_recovery(self):
        ready=self.h.prepared()
        self.h.service.file_readback=Mock(side_effect=ReadOnlyQueryUnconfirmed('synthetic read process unknown'))
        state=self.h.install(ready['package']['token'])
        self.assertEqual(state['phase'],'unknown');self.assertTrue(state['locked'])
        self.assertTrue(self.h.marker);self.assertEqual(len(self.h.catalog._claims),1)
        self.assertEqual(len(self.h.spawn_calls),1)

    def test_release_failure_retains_claim_until_exact_owner_release_is_confirmed(self):
        ready=self.h.prepared();self.h.release_fail=True
        state=self.h.install(ready['package']['token'])
        self.assertTrue(state['locked']);self.assertEqual(len(self.h.catalog._claims),1)
        self.assertIsNotNone(self.h.host._package_claim)
        self.h.release_fail=False
        self.h.service.verify_recovery()
        self.assertEqual(self.h.marker,{});self.assertEqual(self.h.catalog._claims,{})

    def test_claim_failure_with_failed_release_keeps_selected_device_and_reserved_recovery(self):
        self.h.release_fail=True
        with self.assertRaises(ValueError):self.h.service.start_file_upgrade(SN,'missing-package',confirmed=True)
        state=self.h.service.snapshot()
        self.assertTrue(state['locked']);self.assertEqual(state['device']['sn'],SN)
        self.assertEqual(self.h.spawn_calls,[]);self.assertEqual(self.h.marker.get('writer_stage'),'reserved')
        self.h.release_fail=False
        recovered=self.h.service.verify_recovery()
        self.assertEqual(recovered['phase'],'completed');self.assertEqual(recovered['result']['verdict'],'official_write_not_started')
        self.assertIsNone(recovered['current_version']);self.assertEqual(self.h.resource_calls,[])

    def test_starting_marker_without_child_identity_cannot_recover_as_no_write(self):
        self.h.release_fail=True
        with self.assertRaises(ValueError):self.h.service.start_file_upgrade(SN,'missing',confirmed=True)
        self.h.marker['writer_stage']='starting';self.h.release_fail=False
        with self.assertRaises(ValueError):self.h.service.verify_recovery()
        self.assertTrue(self.h.service.snapshot()['locked']);self.assertEqual(self.h.spawn_calls,[])

    def test_reserved_marker_with_unconfirmed_query_owner_cannot_clear_the_lease(self):
        self.h.release_fail=True
        with self.assertRaises(ValueError):self.h.service.start_file_upgrade(SN,'missing',confirmed=True)
        self.h.release_fail=False;self.h.host.query_cancel=threading.Event()
        with self.assertRaises(ValueError):self.h.service.verify_recovery()
        self.assertTrue(self.h.service.snapshot()['locked']);self.assertTrue(self.h.marker)
        self.assertEqual(self.h.resource_calls,[])

    def test_acquire_failure_retains_its_exact_reserved_token_when_cleanup_fails(self):
        self.h.release_fail=True
        self.h.controller.official_cli.idle_check.side_effect=[None,ValueError('synthetic second idle check refused')]
        with self.assertRaises(ValueError):self.h.service.start_file_upgrade(SN,'unused-token',confirmed=True)
        self.assertTrue(self.h.service.snapshot()['locked'])
        self.assertEqual(self.h.service._reservation,self.h.marker['token'])
        self.assertEqual(self.h.service.snapshot()['device']['sn'],SN)
        self.h.controller.official_cli.idle_check.side_effect=None;self.h.release_fail=False
        self.assertEqual(self.h.service.verify_recovery()['result']['verdict'],'official_write_not_started')
        self.assertEqual(self.h.spawn_calls,[]);self.assertEqual(self.h.marker,{})

    def test_component_preparation_failure_before_spawn_releases_claim_without_unknown_writer(self):
        ready=self.h.prepared();component=copy.deepcopy(self.h.cli)
        self.h.host._component.side_effect=[component,component,ValueError('synthetic binary unavailable')]
        state=self.h.install(ready['package']['token'])
        self.assertEqual(state['phase'],'failed');self.assertFalse(state['locked'])
        self.assertEqual(self.h.spawn_calls,[]);self.assertEqual(self.h.marker,{});self.assertEqual(self.h.catalog._claims,{})

    def test_thread_start_failure_cleans_busy_and_leases_and_allows_explicit_retry(self):
        with patch('firmware_catalog_service.threading.Thread.start',side_effect=RuntimeError('synthetic thread start failed')):
            with self.assertRaises(RuntimeError):self.h.catalog.start_check(SN)
        self.assertFalse(self.h.catalog.snapshot()['busy'])
        ready=self.h.prepared()
        with patch('firmware_service.threading.Thread.start',side_effect=RuntimeError('synthetic writer thread unavailable')):
            with self.assertRaises(RuntimeError):self.h.service.start_file_upgrade(SN,ready['package']['token'],confirmed=True)
        self.assertFalse(self.h.service.snapshot()['busy']);self.assertFalse(self.h.service.snapshot()['locked'])
        self.assertEqual(self.h.catalog._claims,{});self.assertEqual(self.h.marker,{})
        ready=self.h.prepared();self.assertEqual(self.h.install(ready['package']['token'])['phase'],'verified')

    def test_host_file_claim_cannot_be_replayed_under_a_new_reservation_or_path(self):
        ready=self.h.prepared();self.h.host.device(SN);token=self.h.host.acquire('firmware')
        descriptor=self.h.catalog.claim_package(ready['package']['token'],SN,reservation_token=token)
        self.h.host.validate_package_claim(descriptor)
        with self.assertRaises(FirmwareNotStarted):self.h.host(['upgrade','--sn',SN,'--file',str(self.h.root/'other.zip')],interactive=True,mutating=True)
        self.h.marker['token']='new-owner'
        with self.assertRaises(FirmwareNotStarted):self.h.host.validate_package_claim(descriptor)
        self.assertEqual(self.h.spawn_calls,[])

    def test_server_action_dispatch_uses_unified_snapshot_without_device_side_effects(self):
        server=self.server()
        with patch('bridge_config.load_config',return_value=dict(mode='wsl',cli='wuji')):
            server.maintenance_action(dict(name='firmware_catalog_check',serial=SN));checked=self.h.join(self.h.catalog)
            response=server.maintenance_action(dict(name='firmware_package_prepare',serial=SN,catalog_token=checked['catalog_token']))
            ready=self.h.join(self.h.catalog)
            self.assertIn('official_catalog',response['device_maintenance'])
            self.h.controller.calibration_devices.assert_not_called();self.h.controller.official_viz.stop_owned.assert_not_called()
            with self.assertRaises(ValueError):server.maintenance_action(dict(name='firmware_package_prepare',serial=SN,catalog_token='bad',path='untrusted.zip'))
            server.maintenance_action(dict(name='firmware_package_upgrade',serial=SN,package_token=ready['package']['token'],confirmed=True))
            self.assertEqual(self.h.join(self.h.service)['phase'],'verified')

    def test_http_action_handler_bytes_reach_only_root_maintenance_and_reject_child_context(self):
        from console_server import Handler,ORIGIN
        server=self.server()
        def post(payload,*,csrf='synthetic-csrf',scope=None):
            raw=json.dumps(payload).encode()
            handler=N(path='/api/action',server=server,rfile=io.BytesIO(raw),
                headers={'Content-Length':str(len(raw)),'Content-Type':'application/json','Origin':ORIGIN,'X-Console-Token':csrf},
                allowed_host=lambda:True,_task_view_headers=lambda:scope,reply=Mock())
            Handler.do_POST(handler)
            return handler.reply.call_args.args
        with patch('bridge_config.load_config',return_value=dict(mode='wsl',cli='wuji')):
            self.assertEqual(post(dict(name='firmware_catalog_check',serial=SN),csrf='wrong')[0],403)
            self.assertEqual(post(dict(name='firmware_catalog_check',serial=SN),scope={'task':'synthetic'})[0],400)
            self.assertEqual(self.h.metadata_calls,[])
            status,body=post(dict(name='firmware_catalog_check',serial=SN));self.assertEqual(status,200);self.assertTrue(body['ok'])
            checked=self.h.join(self.h.catalog)
            self.assertEqual(post(dict(name='firmware_package_prepare',serial=SN,catalog_token=checked['catalog_token']))[0],200)
            ready=self.h.join(self.h.catalog)
            self.h.controller.calibration_devices.assert_not_called();self.assertEqual(self.h.spawn_calls,[])
            status,body=post(dict(name='firmware_package_upgrade',serial=SN,package_token=ready['package']['token'],confirmed=True))
            self.assertEqual(status,200);self.assertTrue(body['ok'])
            self.assertEqual(self.h.join(self.h.service)['phase'],'verified')

    def test_invalid_rest_install_never_discovers_and_success_snapshot_hides_private_owner(self):
        from console_server import Handler,ORIGIN
        server=self.server()
        ready=self.h.prepared();token=ready['package']['token']
        def post(sn,capability,confirmed):
            raw=json.dumps(dict(name='firmware_package_upgrade',serial=sn,package_token=capability,confirmed=confirmed)).encode()
            handler=N(path='/api/action',server=server,rfile=io.BytesIO(raw),
                headers={'Content-Length':str(len(raw)),'Content-Type':'application/json','Origin':ORIGIN,'X-Console-Token':'synthetic-csrf'},
                allowed_host=lambda:True,_task_view_headers=lambda:None,reply=Mock())
            Handler.do_POST(handler);return handler.reply.call_args.args
        with patch('bridge_config.load_config',return_value=dict(mode='wsl',cli='wuji')):
            for sn,capability,confirmed in [(SN,token,False),(SN,'invalid-token',True),(OTHER,token,True)]:
                with self.subTest(sn=sn,confirmed=confirmed):self.assertEqual(post(sn,capability,confirmed)[0],400)
            self.h.now+=301;self.assertEqual(post(SN,token,True)[0],400);self.h.now-=301
            self.h.controller.calibration_devices.assert_not_called();self.h.controller.official_cli.idle_check.assert_not_called()
            self.h.controller.official_viz.discover_inline.assert_not_called();self.assertEqual(self.h.spawn_calls,[])
            self.assertEqual(post(SN,token,True)[0],200)
            self.assertEqual(self.h.join(self.h.service)['phase'],'verified')
            public=json.dumps(server.maintenance_snapshot())
            for private in ('reservation_token','claim_id','synthetic-lease-',str(self.h.root)):
                self.assertNotIn(private,public)

    def test_claim_with_uncertain_writer_stage_stays_owned_instead_of_not_started_release(self):
        ready=self.h.prepared();claim=self.h.catalog.claim_package
        def uncertain(*args,**kw):
            descriptor=claim(*args,**kw)
            self.h.marker['writer_stage']='starting'
            return descriptor
        self.h.service.claim_package=uncertain
        with self.assertRaises(FirmwareWriterUnconfirmed):
            self.h.service.start_file_upgrade(SN,ready['package']['token'],confirmed=True)
        state=self.h.service.snapshot()
        self.assertEqual(state['phase'],'unknown');self.assertTrue(state['locked'])
        self.assertEqual(self.h.marker['token'],self.h.service._reservation)
        self.assertEqual(len(self.h.catalog._claims),1);self.assertEqual(self.h.release_calls,[])
        self.assertEqual(self.h.spawn_calls,[]);self.assertEqual(self.h.resource_calls,[])
        with self.assertRaises(ValueError):self.h.service.verify_recovery()
        self.assertTrue(self.h.service.snapshot()['locked']);self.assertTrue(self.h.marker)

    def test_unbounded_cached_current_version_is_not_forwarded_as_an_observed_fact(self):
        self.h.catalog.observed=lambda sn:dict(serial=SN,device_type='wuji_glove',version='1234567890.0.0',observed_at=self.h.now,source='synthetic')
        checked=self.h.check()
        self.assertEqual(checked['phase'],'listed');self.assertIsNone(checked['current_version'])
        self.assertIsNone(checked['catalog_metadata']['higher_listed'])
        self.assertEqual(self.h.metadata_calls[-1],(SN,'wuji_glove',None))

    def test_exact_viewer_handover_checks_package_and_all_owners_before_one_stop_then_install(self):
        server=self.server();ready=self.h.prepared();token=ready['package']['token'];events=[]
        validate=self.h.catalog.validate_ready_token
        def validated(*args):
            result=validate(*args);events.append('package_valid');return result
        self.h.catalog.validate_ready_token=validated
        viewer=self.h.controller.official_viz
        viewer.connection_record.side_effect=lambda *a,**kw:events.append('viewer_identity')
        server.firmware_preview_idle.side_effect=lambda owner:events.append('all_owners_idle')
        def stopped(*args,**kw):
            events.append('stop_exact_viewer');viewer.snapshot.return_value['active']=False
        viewer.stop_owned.side_effect=stopped
        self.h.controller.official_cli.idle_check.side_effect=lambda:events.append('idle')
        self.h.controller.calibration_devices.side_effect=lambda **kw:(events.append('selected_inventory') or [copy.deepcopy(self.h.inventory)])
        spawn=self.h.host.spawn
        def spawning(*args,**kw):events.append('spawn_official_file');return spawn(*args,**kw)
        self.h.host.spawn=spawning
        server.start_firmware_package_upgrade(SN,token,True)
        self.assertEqual(self.h.join(self.h.service)['phase'],'verified')
        self.assertEqual(events[:4],['package_valid','viewer_identity','all_owners_idle','stop_exact_viewer'])
        self.assertLess(events.index('stop_exact_viewer'),events.index('idle'))
        self.assertLess(events.index('selected_inventory'),events.index('spawn_official_file'))
        viewer.connection_record.assert_called_once_with(SN,'b'*32,require_glove=True)
        viewer.stop_owned.assert_called_once_with(SN,'b'*32,require_glove=True)
        server.firmware_preview_idle.assert_called_once_with('b'*32)
        self.assertEqual(len(self.h.spawn_calls),1)

    def test_invalid_package_foreign_viewer_and_other_active_owners_preserve_viewer(self):
        server=self.server();ready=self.h.prepared();token=ready['package']['token']
        viewer=self.h.controller.official_viz
        with self.assertRaises(ValueError):server.start_firmware_package_upgrade(SN,'invalid-token',True)
        path=Path(self.h.catalog._prepared['package'].path);path.write_bytes(b'corrupt archive')
        with self.assertRaises(ValueError):server.start_firmware_package_upgrade(SN,token,True)
        viewer.connection_record.assert_not_called();viewer.stop_owned.assert_not_called()
        self.h.controller.official_cli.idle_check.assert_not_called();self.h.controller.calibration_devices.assert_not_called()
        path.write_bytes(self.h.raw)
        viewer.snapshot.return_value['reported_device']['sn']=OTHER
        self.h.controller.official_cli.idle_check.side_effect=ValueError('synthetic other viewer active')
        with self.assertRaises(ValueError):server.start_firmware_package_upgrade(SN,token,True)
        viewer.connection_record.assert_not_called();viewer.stop_owned.assert_not_called()
        self.h.controller.calibration_devices.assert_not_called()
        self.h.controller.official_cli.idle_check.side_effect=None
        viewer.snapshot.return_value['reported_device']['sn']=SN
        server.firmware_preview_idle.side_effect=ValueError('synthetic other task owns a device')
        with self.assertRaises(ValueError):server.start_firmware_package_upgrade(SN,token,True)
        viewer.connection_record.assert_called_once_with(SN,'b'*32,require_glove=True)
        viewer.stop_owned.assert_not_called();self.assertEqual(self.h.spawn_calls,[])
        self.assertIsNotNone(self.h.catalog.snapshot()['package'])

    def test_fast_finished_owned_writer_needs_no_creation_time_but_missing_proof_never_clears_live_pid(self):
        ready=self.h.prepared()
        with patch('firmware_host.identity',return_value=None), \
                patch('firmware_host.process_state',side_effect=AssertionError('Retained exact exit proof needs no OS guess')) as probe:
            state=self.h.install(ready['package']['token'])
            self.assertEqual(state['phase'],'verified');probe.assert_not_called()
        self.assertEqual(len(self.h.resource_calls),2)
        owner='synthetic-retained-fast-owner'
        self.h.marker=dict(kind='firmware',token=owner,serial=SN,command='official-interactive-file-upgrade',
            writer_stage='running',child_pid=123456789,child_start=None)
        session=copy.deepcopy(self.h.host.session)
        bad_proofs=[dict(session_token='different-owner',session=session),
            dict(session_token=owner,session=dict(session,serial=OTHER)),
            dict(session_token=owner,session=dict(session,exitstatus=None)),
            dict(session_token=owner,session=dict(session,exitstatus=False)),
            dict(session_token=owner,session=dict(session,status='unknown'))]
        for os_state in ('live','unknown'):
            for proof in bad_proofs:
                with self.subTest(os_state=os_state,proof=proof):
                    self.h.host.session_token=proof['session_token'];self.h.host.session=proof['session']
                    with patch('firmware_host.process_state',return_value=os_state):
                        with self.assertRaises(ValueError):self.h.host.file_readback(SN,reservation_token=owner)
                        with self.assertRaises(ValueError):self.h.host.recovery_check(SN,reservation_token=owner)
                    self.assertEqual(self.h.marker['token'],owner)
                    self.assertEqual(len(self.h.resource_calls),2,'Unconfirmed writer must not open a version query')

    def test_failed_catalog_after_verified_writer_is_causally_new_even_with_fixed_clock(self):
        ready=self.h.prepared();first=self.h.install(ready['package']['token'])
        self.assertEqual(first['phase'],'verified')
        first_generation=first['operation_generation'];self.assertGreater(first_generation,0)
        self.h.catalog_error=OSError('synthetic later catalog request failed')
        failed=self.h.check()
        self.assertEqual(self.h.now,1000.,'This regression must not depend on advancing timestamps')
        self.assertEqual(failed['phase'],'failed');self.assertIsNone(failed['checked_at'])
        self.assertEqual(failed['follows_operation_generation'],first_generation)
        self.assertEqual(self.h.service.snapshot()['operation_generation'],failed['follows_operation_generation'])
        self.h.catalog_error=None;ready=self.h.prepared()
        preceding=self.h.catalog.snapshot()['follows_operation_generation']
        second=self.h.install(ready['package']['token'])
        self.assertEqual(second['phase'],'verified');self.assertGreater(second['operation_generation'],first_generation)
        self.assertNotEqual(second['operation_generation'],failed['follows_operation_generation'])
        self.assertNotEqual(second['operation_generation'],preceding,'An earlier catalog result must not mask a later writer')

    def test_catalog_busy_is_not_an_sdk_lease_but_defers_desktop_close_before_disconnect(self):
        from console_server import Controller
        from connection_switch import ConnectionSwitch
        from desktop_lifecycle import close_sessions,needs_confirmation
        from desktop_tools import idle
        from firmware_host import enforce_maintenance
        from studio_component import require_idle
        controller=Controller.__new__(Controller)
        controller.lock=threading.RLock();controller.hardware_lease=None;controller.last_update=0;controller.csrf='synthetic'
        controller.state=dict(connection='disconnected',hardware=dict(active=False),device_profile=dict(id='hand2_left',generation='hand2'),
            device_id=None,joint_rates=[],mapping=[],mapping_device_id=None,metrics=dict(age_ms=None),recording=dict(active=False))
        controller.retarget=N(selection=lambda profile:None)
        controller.solver_runtime=N(snapshot=lambda:{})
        controller.studio=N(snapshot=lambda:{},stop=Mock())
        controller.official_viz=N(snapshot=lambda:{},stop=Mock())
        controller.official_cli=N(snapshot=lambda:{},closed=threading.Event(),cancel_and_wait=Mock(return_value=True))
        controller.player=N(snapshot=lambda:{},command=Mock())
        controller.parameters=N(sync_status={})
        controller.glove=N(snapshot=lambda:dict(busy=False,connection='disconnected'),busy=False)
        controller.termination=N(snapshot=lambda:[])
        controller.program=N(snapshot=lambda:dict(active=False));controller.group=N(snapshot=lambda:dict(active=False))
        controller.calibration=N(lock=threading.RLock(),run=dict(running=False))
        controller.calibration_handover=N(snapshot=lambda:dict(busy=False))
        controller.teleoperation_handover=N(snapshot=lambda:dict(busy=False))
        controller.calibrated_teleoperation=None;controller.connection_switch=None
        controller.doctor=N(snapshot=lambda:dict(running=False));controller.action=Mock()
        catalog_busy=[True];controller.firmware_catalog_observer=lambda:dict(busy=catalog_busy[0])
        switch=ConnectionSwitch.__new__(ConnectionSwitch)
        switch.state=dict(busy=False);switch.catalog=dict(busy=False);switch.controller=controller
        switch._cleanup_error=lambda state:None
        with patch('cli_coordination.reservation',side_effect=lambda *a:copy.deepcopy(self.h.marker)):
            state=controller.snapshot()
            self.assertFalse(state['maintenance']['busy']);self.assertTrue(state['maintenance']['catalog_busy'])
            self.assertIsNone(require_idle(state,{}));self.assertIsNone(switch._blocked(state),'HTTP preparation must not own an SDK device lease')
            self.assertFalse(idle(state,{}));self.assertTrue(needs_confirmation(state))
            self.assertFalse(close_sessions(controller,sleep=lambda _:None)['ok'])
            controller.state['connection']='connected'
            self.assertFalse(close_sessions(controller,sleep=lambda _:None)['ok'])
            controller.action.assert_not_called();controller.player.command.assert_not_called();controller.official_viz.stop.assert_not_called()
            self.assertFalse(controller.official_cli.closed.is_set(),'Catalog wait must precede shutdown/disconnect side effects')
            controller.state['connection']='disconnected';catalog_busy[0]=False
            self.h.marker=dict(kind='firmware',token='actual-device-owner',serial=SN)
            real_maintenance=controller.snapshot()
            self.assertTrue(real_maintenance['maintenance']['busy']);self.assertFalse(real_maintenance['maintenance']['catalog_busy'])
            self.assertIsNotNone(switch._blocked(real_maintenance),'A true firmware lease must still block SDK entry')
            with self.assertRaises(ValueError):enforce_maintenance(dict(name='connect'))
            self.h.marker={}
            self.assertTrue(idle(controller.snapshot(),{}))
            self.assertTrue(close_sessions(controller,sleep=lambda _:None)['ok'])
            controller.action.assert_called_once_with(dict(name='disconnect'))


if __name__=='__main__':unittest.main()
