"""Independent synthetic guided-calibration contracts; no CLI/SDK/device calls."""
from contextlib import contextmanager, ExitStack
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import calibration_cli
from calibration_guided import OfficialEvents, validate_profile
import cli_coordination
import native_calibration as native
import native_cli

SN = 'WG_SYNTHETIC_GUIDED'
USER = 'Synthetic'
SDK_USER = dict(user_id='owner-guided-0257',display_name=USER)
COMPONENT = dict(version='2026.10.12', binary='C:/synthetic/wuji.exe')


def profile(name=USER, calibrated=False):
    return dict(name=name, is_default=False, left_hand=dict(calibrated=calibrated),
                right_hand=dict(calibrated=False))


def record(kind='progress', **fields):
    value=dict(schema_version=2, calibration='hand_model', operation='calibrate', event=kind,user=copy.deepcopy(SDK_USER))
    value.update(fields)
    if kind == 'result' and 'result' not in fields:
        value.update(status='ok', result=dict(sdk_user=copy.deepcopy(SDK_USER), handedness='left',
                    calibrated_urdf='/synthetic/left_hand.urdf', sn=SN))
    return value


def line(value):
    return json.dumps(value, ensure_ascii=True) + '\n'


def ready(**fields):
    return dict(type='ready', source='official_jsonl_native', serial=SN, side='left', **fields)


def output(*events):
    return dict(type='output', text=''.join(line(event) for event in events))


class DeferredThread:
    def __init__(self, target=None, args=(), **kwargs):
        self.target, self.args, self.started = target, args, False
    def start(self): self.started=True
    def is_alive(self): return self.started
    def join(self, timeout=None): pass


class FakeProcess:
    def __init__(self, events, code=0):
        self.stdin=io.StringIO()
        self.stdout=io.StringIO(''.join(line(event) for event in events))
        self.stderr=io.StringIO()
        self.code, self.returncode=code, None
    def poll(self): return self.returncode
    def wait(self, timeout=None):
        self.returncode=self.code
        return self.code


@contextmanager
def temporary_root():
    root=Path(__file__).resolve().parents[3]/'outputs/cli-workspace-0231/qa/backend-temp'
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as folder:
        yield Path(folder)


@contextmanager
def native_environment(events=(), code=0, clean=True):
    with temporary_root() as folder, ExitStack() as stack:
        stack.enter_context(patch('calibration_identity.read_current_sdk_user',return_value=copy.deepcopy(SDK_USER)))
        stack.enter_context(patch('calibration_portability.file_digest',return_value=('d'*64,32)))
        stack.enter_context(patch.dict(os.environ, WUJI_OFFICIAL_CLI_DATA=str(folder)))
        identity=lambda pid:'synthetic-live' if pid == os.getpid() else None
        stack.enter_context(patch.object(cli_coordination, 'identity', side_effect=identity))
        stack.enter_context(patch.object(native, 'identity', side_effect=identity))
        stack.enter_context(patch.object(native_cli, 'prepare', return_value=copy.deepcopy(COMPONENT)))
        query=stack.enter_context(patch.object(native_cli, 'json_command', return_value=profile()))
        process=FakeProcess(events, code)
        job=SimpleNamespace(process=process, close=Mock(return_value=clean), terminate_and_wait=Mock(return_value=clean))
        spawn=stack.enter_context(patch('windows_process_job.OwnedProcess', return_value=job))
        stack.enter_context(patch.object(native.threading, 'Thread', DeferredThread))
        owner=SimpleNamespace(lock=threading.RLock(), metadata_check=Mock(),
            run=dict(running=False,status='idle'), process=None, thread=None, cache=None,
            reports=folder/'reports')
        owner.snapshot=Mock(side_effect=lambda **kwargs:copy.deepcopy(owner.run))
        service=native.NativeCalibration(owner)
        yield SimpleNamespace(folder=folder, owner=owner, process=process, job=job,
                              query=query, spawn=spawn, service=service)


class ProfileContractTests(unittest.TestCase):
    def test_default_invalid_names_and_mismatched_identity_rejected(self):
        for value, name in [(profile('Default'),'Default'), (profile('default'),'default'),
                            ({**profile(),'is_default':True},USER), (profile('Other'),USER),
                            (None,USER), (profile(),'bad name'), (profile(),'x'*33)]:
            with self.subTest(value=value,name=name), self.assertRaises(ValueError):
                validate_profile(value,name,'left',False)

    def test_unknown_model_and_non_boolean_consent_rejected(self):
        for model in [None,{},dict(calibrated=None),dict(calibrated=0),dict(calibrated='false')]:
            value=profile();value['left_hand']=model
            with self.subTest(model=model), self.assertRaises(ValueError):
                validate_profile(value,USER,'left',False)
        for side,replace in [('LEFT',False),(None,False),('left',0),('left',1),('left',None)]:
            with self.subTest(side=side,replace=replace), self.assertRaises(ValueError):
                validate_profile(profile(),USER,side,replace)

    def test_existing_side_requires_explicit_scoped_replacement(self):
        value=profile(calibrated=True)
        with self.assertRaisesRegex(ValueError,'replacement'):
            validate_profile(value,USER,'left',False)
        accepted=validate_profile(value,USER,'left',True)
        accepted['left_hand']['calibrated']=False
        self.assertTrue(value['left_hand']['calibrated'])
        validate_profile(value,USER,'right',False)


class OfficialEventTests(unittest.TestCase):
    def decoder(self): return OfficialEvents(SN,'left',USER,SDK_USER)

    def test_arbitrary_character_chunks_and_final_unterminated_line(self):
        events=[record(progress=dict(step_name='pinch_index',progress=.4)),record('result')]
        data='diagnostic text\r\n'+line(events[0])+json.dumps(events[1])
        decoder=self.decoder();actual=[]
        for character in data:actual.extend(decoder.feed(character))
        actual.extend(decoder.feed('',final=True))
        self.assertEqual(actual,events)
        self.assertEqual(decoder.terminal,'result')
        self.assertEqual(decoder.result,events[-1])

    def test_non_protocol_diagnostics_do_not_invent_progress(self):
        noise=['bad JSON', 'null','[]',line({**record(),'schema_version':True}),
               line({**record(),'schema_version':3}),line({**record(),'operation':'other'}),
               line({**record(),'calibration':'other'}),line({**record(),'event':'unknown'})]
        decoder=self.decoder()
        self.assertEqual(decoder.feed('\n'.join(noise),final=True),[])
        self.assertIsNone(decoder.terminal)

    def test_protocol_events_after_terminal_always_rejected(self):
        for terminal in [record('result'),record('error',error='synthetic'),record('cancelled')]:
            for later in [record(),record('result'),record('error')]:
                with self.subTest(terminal=terminal['event'],later=later['event']):
                    decoder=self.decoder();decoder.feed(line(terminal))
                    with self.assertRaisesRegex(ValueError,'terminal event'):decoder.feed(line(later))

    def test_result_requires_exact_user_side_success_and_path(self):
        mutations=[(dict(status='error'),'confirm success'),(dict(result=None),'confirm success'),(dict(result={}),'hand side'),
            (dict(result=dict(sdk_user='Other',handedness='left',calibrated_urdf='x')),'wearer identity'),
            (dict(result=dict(sdk_user=USER,handedness='right',calibrated_urdf='x')),'hand side'),
            (dict(result=dict(sdk_user=USER,handedness='left',calibrated_urdf=' ')),'model location')]
        for mutation,reason in mutations:
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError,reason):
                self.decoder().feed(line({**record('result'),**mutation}))

    def test_any_returned_serial_must_match_selected_identity(self):
        for target in ['event','event_serial','sn','serial','event_device','result_device']:
            value=record('result')
            if target=='event':value['sn']='WG_OTHER'
            elif target=='event_serial':value['serial']='WG_OTHER'
            elif target=='event_device':value['device']=dict(sn='WG_OTHER')
            elif target=='result_device':value['result']['device']=dict(serial='WG_OTHER')
            else:value['result'][target]='WG_OTHER'
            with self.subTest(target=target), self.assertRaisesRegex(ValueError,'another glove'):
                self.decoder().feed(line(value))

    def test_conflicting_user_or_invalid_result_device_context_rejected(self):
        for change in ['event_user','result_user','bad_device']:
            value=record('result')
            if change=='event_user':value['sdk_user']='Other'
            elif change=='result_user':value['result']['user']='Other'
            else:value['result']['device']=['unexpected']
            with self.subTest(change=change),self.assertRaises(ValueError):self.decoder().feed(line(value))

    def test_bounded_records_reject_long_complete_or_partial_line(self):
        for text in ['x'*65537,'x'*65537+'\n']:
            with self.subTest(complete=text.endswith('\n')), self.assertRaisesRegex(ValueError,'limit'):
                self.decoder().feed(text)


class GuidedCollectionTests(unittest.TestCase):
    def collect(self, events, code=0, clean=True, cancel=False):
        with native_environment(events,code,clean) as env:
            env.service.start_guided(SN,'left',USER)
            if cancel:env.service.cancel()
            env.service._collect()
            return copy.deepcopy(env.owner.run), cli_coordination.reservation(env.folder)

    def test_verified_ready_fragmented_progress_and_result_complete_only_after_exit(self):
        progress=record(progress=dict(step_index=0,step_total=6,step_name='pinch_index',
                    phase='done',progress=1,variance_ok=True,constraints_ok=True))
        data=line(progress)+json.dumps(record('result'))
        chunks=[dict(type='output',text=data[:17]),dict(type='output',text=data[17:143]),dict(type='output',text=data[143:])]
        state,marker=self.collect([ready(),*chunks,dict(type='exit',exitstatus=0)])
        self.assertEqual(state['status'],'completed')
        self.assertEqual(state['event_count'],2)
        self.assertEqual(state['captured_poses'],[0])
        self.assertEqual(state['result']['result']['sdk_user'],SDK_USER)
        self.assertTrue(state['cleanup_confirmed']);self.assertFalse(state['running']);self.assertEqual(marker,{})

    def test_progress_without_ready_is_failure_even_when_child_exits_zero(self):
        state,_=self.collect([output(record(),record('result')),dict(type='exit',exitstatus=0)])
        self.assertEqual(state['status'],'error')
        self.assertIn('before verified',state['error'])
        self.assertIsNone(state['result'])

    def test_wrong_ready_serial_or_side_never_completes(self):
        for bad in [dict(type='ready',serial='WG_OTHER',side='left'),dict(type='ready',serial=SN,side='right'),
                    dict(type='ready',serial=SN,side=None)]:
            with self.subTest(bad=bad):
                state,_=self.collect([bad,output(record('result')),dict(type='exit',exitstatus=0)])
                self.assertEqual(state['status'],'error');self.assertIsNone(state['result'])

    def test_eof_missing_result_or_exit_and_nonzero_worker_never_complete(self):
        cases=[([ready(),dict(type='exit',exitstatus=0)],0),
               ([ready(),output(record('result'))],0),
               ([ready(),output(record('result')),dict(type='exit',exitstatus=None)],0),
               ([ready(),output(record('result')),dict(type='exit',exitstatus=0)],1),
               ([ready(),output(record('result')),dict(type='exit',exitstatus=5)],0)]
        for events,code in cases:
            with self.subTest(events=events,code=code):
                state,marker=self.collect(events,code)
                self.assertEqual(state['status'],'error');self.assertEqual(marker,{})

    def test_official_error_and_protocol_after_result_are_failure(self):
        for payload in [(record('error',error='synthetic algorithm failure'),),
                        (record('result'),record()),(record('result'),record('error'))]:
            with self.subTest(payload=payload):
                state,_=self.collect([ready(),output(*payload),dict(type='exit',exitstatus=0)])
                self.assertEqual(state['status'],'error')

    def test_official_cancelled_exit_and_explicit_cancel_do_not_claim_success(self):
        state,marker=self.collect([ready(),output(record('cancelled')),dict(type='exit',exitstatus=9)])
        self.assertEqual(state['status'],'cancelled');self.assertIsNone(state['result']);self.assertEqual(marker,{})
        state,_=self.collect([ready(),output(record('result')),dict(type='exit',exitstatus=0)],cancel=True)
        self.assertEqual(state['status'],'stopped');self.assertFalse(state['running'])

    def test_unconfirmed_cleanup_retains_ownership_and_recovery_can_finish(self):
        with native_environment([ready(),output(record('result')),dict(type='exit',exitstatus=0)],clean=False) as env:
            env.service.start_guided(SN,'left',USER);token=env.service.token
            env.service._collect()
            self.assertTrue(env.owner.run['running']);self.assertEqual(env.owner.run['status'],'error')
            self.assertFalse(env.owner.run['cleanup_confirmed'])
            self.assertEqual(cli_coordination.reservation(env.folder)['token'],token)
            env.job.close.return_value=True
            env.owner.thread.started=False
            env.service.cancel()
            self.assertFalse(env.owner.run['running']);self.assertTrue(env.owner.run['cleanup_confirmed'])
            self.assertEqual(cli_coordination.reservation(env.folder),{})

    def test_guided_session_rejects_raw_terminal_input(self):
        with native_environment() as env:
            env.service.start_guided(SN,'left',USER)
            with self.assertRaisesRegex(ValueError,'no terminal keystrokes'):
                env.service.input(dict(run_id=env.owner.run['id'],type='write',text='y\r'))
            self.assertEqual(env.process.stdin.getvalue(),'')

    def test_diagnostic_json_cannot_masquerade_as_machine_result(self):
        # stderr is retained for diagnostics, never accepted as stdout protocol.
        state,_=self.collect([ready(),dict(type='diagnostic',text=line(record('result'))),
                              dict(type='exit',exitstatus=0)])
        self.assertEqual(state['status'],'error');self.assertIsNone(state['result'])
        self.assertEqual(state['event_count'],0)

    def test_machine_records_after_transport_exit_do_not_complete(self):
        for events in [[ready(),dict(type='exit',exitstatus=0),output(record('result'))],
                       [ready(),output(record('result')),dict(type='exit',exitstatus=5),dict(type='exit',exitstatus=0)]]:
            with self.subTest(events=events):
                state,_=self.collect(events)
                self.assertEqual(state['status'],'error')


class GuidedWorkerPreflightTests(unittest.TestCase):
    @contextmanager
    def environment(self,current=None,override=None):
        token='c'*32
        with temporary_root() as folder,ExitStack() as stack:
            stack.enter_context(patch('calibration_identity.read_current_sdk_user',return_value=copy.deepcopy(SDK_USER)))
            stack.enter_context(patch.dict(os.environ,WUJI_OFFICIAL_CLI_DATA=str(folder)))
            stack.enter_context(patch.object(native,'_attach_pipes'))
            capture=io.StringIO();stack.enter_context(patch.object(native.sys,'stdout',capture))
            stack.enter_context(patch.object(native,'identity',return_value='synthetic-live'))
            contained=stack.enter_context(patch('windows_process_job.current_in_named_job',return_value=True))
            prepare=stack.enter_context(patch.object(native_cli,'prepare',return_value=COMPONENT))
            def query(*args):
                if args==('user','show'):return copy.deepcopy(profile() if current is None else current)
                if args==('get','hand_side','--sn',SN):return dict(value='left',sn=SN)
                raise AssertionError('Unexpected device request: '+repr(args))
            query=stack.enter_context(patch.object(native_cli,'json_command',side_effect=query))
            pipe=stack.enter_context(patch('native_jsonl_worker.run',return_value=0))
            tty=stack.enter_context(patch('native_terminal_worker.run',side_effect=AssertionError('Guided must not use ConPTY')))
            marker=dict(kind='calibration',token=token,pid=os.getpid(),start='synthetic-live',
                job_name='Local\\HandWorkbench-OfficialCalibration-'+token,serial=SN,side='left',
                interface='guided',user=USER,replace=False,cli_version=COMPONENT['version'],sdk_user=copy.deepcopy(SDK_USER))
            marker.update(override or {});cli_coordination.atomic(folder/'session.json',marker)
            yield SimpleNamespace(args=[token,SN,'left','900','guided',USER,'keep'],capture=capture,
                query=query,prepare=prepare,contained=contained,pipe=pipe,tty=tty)

    def test_worker_rechecks_profile_before_typed_side_and_machine_pipe(self):
        with self.environment() as env:
            self.assertEqual(native.worker_main(env.args),0)
            self.assertEqual(env.query.call_args_list[0].args,('user','show'))
            self.assertEqual(env.query.call_args_list[1].args,('get','hand_side','--sn',SN))
            env.pipe.assert_called_once_with([COMPONENT['binary'],'--jsonl','calib','hand-model','--sn',SN,'--timeout-s','900'])
            env.tty.assert_not_called()
            self.assertEqual(json.loads(env.capture.getvalue()),ready())

    def test_worker_rejects_same_name_different_captured_id_before_device_read(self):
        with self.environment(override=dict(sdk_user=dict(SDK_USER,user_id='owner-other'))) as env:
            self.assertEqual(native.worker_main(env.args),1)
            self.assertEqual([call.args for call in env.query.call_args_list],[('user','show')])
            env.pipe.assert_not_called();env.tty.assert_not_called()
            self.assertIn('wearer identity changed',env.capture.getvalue())

    def test_changed_user_unknown_model_or_new_overwrite_need_refuses_before_device_read(self):
        cases=[profile('Other'),profile('Default'),{**profile(),'left_hand':{}},profile(calibrated=True)]
        for current in cases:
            with self.subTest(current=current),self.environment(current=current) as env:
                self.assertEqual(native.worker_main(env.args),1)
                env.query.assert_called_once_with('user','show');env.prepare.assert_not_called();env.pipe.assert_not_called()
                self.assertEqual(json.loads(env.capture.getvalue())['type'],'error')

    def test_changed_job_user_or_consent_blocks_even_profile_read(self):
        for change in [dict(user='Other'),dict(interface='terminal'),dict(replace=True)]:
            with self.subTest(change=change),self.environment(override=change) as env:
                self.assertEqual(native.worker_main(env.args),1)
                env.query.assert_not_called();env.pipe.assert_not_called()


class NativeCancelRaceTests(unittest.TestCase):
    def test_old_cancel_cannot_overwrite_a_new_run_published_during_cleanup(self):
        with native_environment() as env:
            env.service.start_guided(SN,'left',USER)
            old_token=env.service.token
            env.owner.thread.started=False
            new_run=dict(id='NEW_SYNTHETIC_RUN',running=True,status='collecting',serial='WG_OTHER',cleanup_confirmed=False)
            next_token=[]
            def finish_old_and_publish_new(**kwargs):
                # Collector finishes between cancel's running snapshot and its
                # dead-thread cleanup. A subsequent start owns a new identity.
                cli_coordination.release(old_token,env.folder)
                next_token.append(cli_coordination.reserve('calibration',env.folder))
                env.owner.run=copy.deepcopy(new_run)
                return True
            env.job.close.side_effect=finish_old_and_publish_new
            state=env.service.cancel()
            self.assertEqual(state,new_run)
            self.assertEqual(env.owner.run,new_run)
            self.assertEqual(cli_coordination.reservation(env.folder)['token'],next_token[0])
            self.assertIsNone(env.service.token)

    def test_job_cleanup_wait_holds_neither_owner_lock_nor_global_gate(self):
        RealThread=threading.Thread
        with native_environment() as env:
            env.service.start_guided(SN,'left',USER);env.owner.thread.started=False
            acquired=threading.Event()
            def wait_on_both():
                with env.owner.lock,cli_coordination.gate(env.folder):acquired.set()
            def clean(**kwargs):
                worker=RealThread(target=wait_on_both,daemon=True);worker.start();worker.join(1)
                self.assertTrue(acquired.is_set(),'Cancel waited on Job while holding a shared lock')
                return True
            env.job.close.side_effect=clean
            env.service.cancel()
            self.assertTrue(env.owner.run['cleanup_confirmed'])

    def test_cancel_during_reserved_launch_is_sticky_and_prevents_process_spawn(self):
        with native_environment() as env:
            original=native.atomic
            def cancel_after_marker(path,value):
                result=original(path,value)
                if value.get('backend')=='windows_native':
                    self.assertTrue(env.service.launching)
                    env.service.cancel()
                return result
            with patch.object(native,'atomic',side_effect=cancel_after_marker):
                with self.assertRaisesRegex(ValueError,'startup was cancelled'):
                    env.service.start_guided(SN,'left',USER)
            env.spawn.assert_not_called();self.assertFalse(env.service.launching)
            self.assertTrue(env.service.cancel_requested);self.assertIsNone(env.service.token)
            self.assertEqual(cli_coordination.reservation(env.folder),{})


class NativeHostStartTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        with temporary_root() as folder, ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ,WUJI_OFFICIAL_CLI_DATA=str(folder)))
            stack.enter_context(patch('official_device_discovery.use_native',return_value=True))
            stack.enter_context(patch.object(calibration_cli,'load_config',return_value=dict(mode='native_cli')))
            stack.enter_context(patch.object(cli_coordination,'identity',return_value='synthetic-start'))
            service=calibration_cli.CalibrationCLI(device_provider=lambda fresh=False:[])
            service.metadata_check=Mock();service.profile_read_check=Mock()
            state={'current':profile(),'selected':profile(),'users':[profile()]}
            calls=[]
            def query(config,*args):
                calls.append(args)
                if args==('user','list'):return dict(users=copy.deepcopy(state['users']))
                if args==('user','show'):return copy.deepcopy(state['current'])
                if args==('user','show',USER):return copy.deepcopy(state['selected'])
                if args==('user','switch',USER):state['current']=profile();return dict(ok=True)
                if args==('user','create',USER,'--switch'):state['current']=profile();state['users'].append(profile());return dict(ok=True)
                raise AssertionError('Unexpected synthetic CLI request: '+repr(args))
            call=stack.enter_context(patch.object(calibration_cli,'cli_json',side_effect=query))
            worker=Mock();worker.start_guided.return_value=dict(synthetic=True);worker.start_human.return_value=dict(synthetic=True)
            spawn=stack.enter_context(patch.object(native,'NativeCalibration',return_value=worker))
            connected=dict(source='native_viewer',serial=SN,side='left',owner_pid=os.getpid(),
                           owner_start='synthetic-start',viewer_token='a'*32,checked_at=time.time())
            yield SimpleNamespace(service=service,state=state,calls=calls,call=call,worker=worker,spawn=spawn,connected=connected)

    def test_direct_native_start_without_verified_connection_never_reads_users_or_spawns(self):
        for connected in [None,{},dict(source='discovery',serial=SN)]:
            with self.subTest(connected=connected), self.environment() as env:
                with self.assertRaisesRegex(ValueError,'Start calibration for the selected glove'):
                    env.service.start('left',SN,profile=USER,connected=connected)
                env.call.assert_not_called();env.spawn.assert_not_called();env.service.metadata_check.assert_not_called()

    def test_stale_wrong_or_different_process_handover_is_rejected(self):
        for mutation in [dict(serial='WG_OTHER'),dict(side='right'),dict(owner_pid=-1),
                         dict(owner_start='old'),dict(checked_at=time.time()-61),dict(checked_at=time.time()+61)]:
            with self.subTest(mutation=mutation), self.environment() as env:
                with self.assertRaises(ValueError):
                    env.service.start('left',SN,profile=USER,connected={**env.connected,**mutation})
                env.call.assert_not_called();env.spawn.assert_not_called()

    def test_guided_valid_user_starts_one_exact_session_after_repeat_preflight(self):
        with self.environment() as env:
            self.assertEqual(env.service.start('left',SN,profile=USER,connected=env.connected),dict(synthetic=True))
            env.worker.start_guided.assert_called_once_with(SN,'left',USER,False,900)
            env.worker.start_human.assert_not_called()
            self.assertEqual(env.calls,[('user','list'),('user','show',USER),('user','show'),('user','show')])

    def test_unknown_default_and_existing_unconfirmed_profile_do_not_spawn(self):
        for alteration in ['unknown','default','unknown_model','existing']:
            with self.subTest(alteration=alteration), self.environment() as env:
                name=USER
                if alteration=='unknown':env.state['users']=[]
                if alteration=='default':name='Default'
                if alteration=='unknown_model':env.state['selected']['left_hand']={}
                if alteration=='existing':env.state['selected']['left_hand']['calibrated']=True
                with self.assertRaises(ValueError):env.service.start('left',SN,profile=name,connected=env.connected)
                env.spawn.assert_not_called()

    def test_profile_changed_between_preflight_and_spawn_is_rejected(self):
        with self.environment() as env:
            env.state['current']=profile('Other')
            original=env.call.side_effect
            def changed(config,*args):
                value=original(config,*args)
                if args==('user','switch',USER):env.state['current']=profile('Unexpected')
                return value
            env.call.side_effect=changed
            with self.assertRaises(ValueError):env.service.start('left',SN,profile=USER,connected=env.connected)
            env.spawn.assert_not_called()

    def test_new_profile_creation_is_explicit_and_confirmed_before_capture(self):
        with self.environment() as env:
            env.state['users']=[];env.state['current']=profile('Default')
            env.service.start('left',SN,profile=USER,create_profile=True,connected=env.connected)
            self.assertEqual(env.calls.count(('user','create',USER,'--switch')),1)
            env.worker.start_guided.assert_called_once()

    def test_explicit_terminal_mode_does_not_read_mutate_profile(self):
        with self.environment() as env:
            env.service.start('left',SN,interface='terminal',connected=env.connected)
            env.call.assert_not_called();env.worker.start_guided.assert_not_called()
            env.worker.start_human.assert_called_once_with(SN,expected_side='left',timeout=900)


if __name__=='__main__':unittest.main()
