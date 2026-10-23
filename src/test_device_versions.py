import copy
import json
import os
import queue
import sys
import threading
import time
import unittest
from types import SimpleNamespace as N
from unittest.mock import Mock, patch

from device_versions import parse_official_check_json, parse_official_check_report, parse_official_check_table, read_handle_version


def table(rows):
    return '\n'.join([
        '1/2 device(s) have updates available',
        '┌──────────────────┬─────────────┬─────────┬────────┬──────────────────┐',
        '│ SN               ┆ Device Type ┆ Current ┆ Latest ┆ Status           │',
        '╞══════════════════╪═════════════╪═════════╪════════╪══════════════════╡',
        *['│ '+' ┆ '.join(row)+' │' for row in rows],
        '└──────────────────┴─────────────┴─────────┴────────┴──────────────────┘'])


ROWS = [('WG1KXXXXXXXXX001', 'wuji_glove', '0.11.0', '0.11.1', 'update available'),
        ('WG1KXXXXXXXXX002', 'wuji_glove', '0.11.1', '0.11.1', 'up to date')]


class HandleMetadataTests(unittest.TestCase):
    def test_exact_metadata_is_read_once_in_the_calling_thread_without_rpc(self):
        owner = threading.get_ident()
        calls = []
        class Handle:
            @property
            def info(self):
                calls.append(threading.get_ident())
                return N(serial_number='EXACT', firmware_version='2.5.1')
            def __getattr__(self, name):
                raise AssertionError('Unexpected SDK operation: '+name)
        self.assertEqual(read_handle_version(Handle(), 'EXACT'),
                         dict(serial='EXACT', firmware_version='2.5.1', source='sdk_handle_info'))
        self.assertEqual(calls, [owner])

    def test_missing_info_exception_or_mismatched_serial_is_unknown(self):
        class Broken:
            @property
            def info(self):raise RuntimeError('synthetic metadata failure')
        for handle in (N(), N(info=None), Broken(), N(info=N(serial_number='OTHER', firmware_version='1.2.3')),
                       N(info=N(serial_number='EXACT')), N(info=N(firmware_version='1.2.3'))):
            with self.subTest(handle=handle):self.assertIsNone(read_handle_version(handle, 'EXACT'))

    def test_version_metadata_is_never_invented_from_other_resources(self):
        for value in (None, 123, '', ' ', '1.2.3\n', '1.2.3\x00', 'x'*129, '版本1'):
            with self.subTest(value=value):
                handle=N(info=N(serial_number='EXACT', firmware_version=value), version=Mock(), hw_version=Mock())
                self.assertIsNone(read_handle_version(handle, 'EXACT'))
                handle.version.assert_not_called();handle.hw_version.assert_not_called()

    def test_bad_expected_serial_does_not_even_access_info(self):
        handle=Mock()
        for value in (None, '', 'X\n', [], True, 'X'*129):
            with self.subTest(value=value):self.assertIsNone(read_handle_version(handle,value))
        self.assertEqual(handle.mock_calls,[])


class DocumentedCheckTableTests(unittest.TestCase):
    def test_public_documented_rows_produce_exact_serial_notifications(self):
        report=table(ROWS)
        self.assertEqual(parse_official_check_table(report,ROWS[0][0],'wuji_glove'),
                         dict(serial=ROWS[0][0],device_type='wuji_glove',current='0.11.0',latest='0.11.1',update_available=True))
        self.assertIs(parse_official_check_table(report,ROWS[1][0])['update_available'],False)

    def test_version_comparison_is_numeric_and_cannot_request_downgrade(self):
        for before,after,status,accepted in [('0.9.0','0.10.0','update available',True),
                                            ('2.5.1','1.2.4','update available',False),
                                            ('0.11.0','0.11.1','up to date',False),
                                            ('0.11.1','0.11.1','update available',False)]:
            with self.subTest(before=before,after=after,status=status):
                result=parse_official_check_table(table([('EXACT','wuji_hand_2',before,after,status)]),'EXACT')
                self.assertEqual(result is not None,accepted)

    def test_exact_serial_and_type_required_not_prefix_or_other_device(self):
        report=table(ROWS)
        for serial,kind in [('WG1KXXXXXXXXX00',None),('OTHER',None),(ROWS[0][0],'wuji_hand_2'),(ROWS[0][0],[]),([],None)]:
            with self.subTest(serial=serial,kind=kind):self.assertIsNone(parse_official_check_table(report,serial,kind))

    def test_duplicate_rows_or_multiple_reports_are_ambiguous(self):
        for report in (table([ROWS[0],ROWS[0]]),table(ROWS)+'\n'+table(ROWS)):
            self.assertIsNone(parse_official_check_table(report,ROWS[0][0]))

    def test_only_documented_header_status_and_full_table_are_accepted(self):
        report=table(ROWS)
        variants=[report.replace('Device Type','Model'),report.replace('update available','new firmware'),
                  report.replace('update available','ok'),report.rsplit('\n',1)[0],report.replace('┆','|'),
                  report.replace('│ WG1','│\nWG1'),report.replace('wuji_glove','unknown_device'),
                  report.replace('0.11.0','0.11'),report.replace('0.11.0','v0.11.0'),
                  report.replace('0.11.0','0.11.0-beta'),report.replace('0.11.0','00.11.0')]
        for value in variants:
            with self.subTest(report=value):self.assertIsNone(parse_official_check_table(value,ROWS[0][0]))

    def test_ansi_colour_and_windows_line_endings_are_supported_but_commands_are_not(self):
        report=table(ROWS)
        self.assertIsNotNone(parse_official_check_table('\x1b[32m'+report.replace('\n','\r\n')+'\x1b[0m',ROWS[0][0]))
        for control in ('\x1b[2J','\x00','\b','\r'):
            self.assertIsNone(parse_official_check_table(control+report,ROWS[0][0]))

    def test_unrelated_json_flash_catalog_and_summary_are_never_check_results(self):
        for report in ('1/2 device(s) have updates available',
                       '{"sn":"EXACT","status":"update available","firmware_before":"1.0.0","firmware_expected":"2.0.0"}',
                       table([('EXACT','wuji_glove','0.11.0','0.11.1','ok')]).replace('Current','Before').replace('Latest','After'),
                       'EXACT (current: 0.11.0)\nVersion  Latest  0.11.1'):
            with self.subTest(report=report):self.assertIsNone(parse_official_check_table(report,'EXACT'))

    def test_output_is_bounded_and_not_coerced(self):
        for value in (None,[],True,'x'*262145,('\n'*2049)+table(ROWS),('x'*4097)+'\n'+table(ROWS)):
            with self.subTest(value=type(value)):self.assertIsNone(parse_official_check_table(value,ROWS[0][0]))


class CapturedCheckJsonTests(unittest.TestCase):
    def report(self,**changes):
        row=dict(sn='EXACT',device_type='wuji_glove',current_version='1.0.0',
                 latest_version='1.0.3',status='update available',error=None)
        row.update(changes)
        return json.dumps(dict(devices=[row]))

    def test_real_cli_check_envelope_produces_exact_version_notice_only(self):
        expected=dict(serial='EXACT',device_type='wuji_glove',current='1.0.0',latest='1.0.3',update_available=True)
        self.assertEqual(parse_official_check_json(self.report(),'EXACT','wuji_glove'),expected)
        self.assertEqual(parse_official_check_report(self.report(),'EXACT','wuji_glove'),expected)
        self.assertNotIn('download_url',expected)

    def test_only_exact_serial_type_and_unambiguous_device_scope_are_accepted(self):
        base=json.loads(self.report());row=base['devices'][0]
        for value in (dict(devices=[row,row]),dict(devices=[]),dict(devices=[row,dict(row,sn='OTHER')]),
                      dict(devices=dict(row)),dict(devices=[row],status='ok')):
            with self.subTest(value=value):self.assertIsNone(parse_official_check_json(json.dumps(value),'EXACT'))
        self.assertIsNone(parse_official_check_json(self.report(),'OTHER'))
        self.assertIsNone(parse_official_check_json(self.report(),'EXACT','wuji_hand_2'))
        self.assertIsNone(parse_official_check_json(self.report(),'EXACT',[]))

    def test_failed_malformed_and_inconsistent_rows_never_become_update_advice(self):
        for changes in (dict(error='device unavailable'),dict(error=False),dict(current_version=True),
                        dict(latest_version=None),dict(latest_version='1.0.0'),dict(latest_version='0.9.0'),
                        dict(status='ok'),dict(status='up to date'),dict(device_type='other'),
                        dict(latest_version='1.0.3-beta'),dict(current_version='01.0.0')):
            with self.subTest(changes=changes):self.assertIsNone(parse_official_check_json(self.report(**changes),'EXACT'))

    def test_current_or_ahead_device_never_suggests_downgrade(self):
        for version in ('1.0.3','1.1.0'):
            result=parse_official_check_json(self.report(current_version=version,status='up to date'),'EXACT')
            self.assertIs(result['update_available'],False)

    def test_duplicate_keys_catalog_and_flash_reports_are_not_check_authority(self):
        row=json.loads(self.report())['devices'][0]
        for text in ('{"devices":[],"devices":['+json.dumps(row)+']}',
                     self.report().replace('"current_version": "1.0.0"','"current_version": "1.0.0", "current_version": "0.1.0"'),
                     json.dumps(dict(devices=[dict(row,firmwares=[])])),
                     json.dumps(dict(devices=[dict(sn='EXACT',status='ok',before='1.0.0',after='1.0.3')]))):
            with self.subTest(text=text):self.assertIsNone(parse_official_check_json(text,'EXACT'))

    def test_unknown_or_unbounded_formats_remain_unknown(self):
        for text in (None,[],True,'x'*262145,'['*1100+']'*1100,'{"devices":[NaN]}'):
            with self.subTest(value=type(text)):self.assertIsNone(parse_official_check_json(text,'EXACT'))

    def test_public_table_fallback_remains_supported_without_json_guessing(self):
        result=parse_official_check_report(table(ROWS),ROWS[0][0],'wuji_glove')
        self.assertTrue(result['update_available'])


class WorkerVersionTests(unittest.TestCase):
    def test_glove_reads_only_after_explicit_open_and_publishes_same_snapshot(self):
        from glove_agent import worker_glove
        from test_glove_transport_lifecycle import GloveWorkerLifecycleTests
        sdk,calls=GloveWorkerLifecycleTests().sdk();glove=sdk.SdkManager.instance().connect(sn='SYNTHETIC-GLOVE')
        class Handle:
            reads=[]
            def __getattr__(self,name):return getattr(glove,name)
            @property
            def info(self):
                self.reads.append(threading.get_ident())
                return N(serial_number='SYNTHETIC-GLOVE',firmware_version='0.11.0')
        handle=Handle();manager=sdk.SdkManager.instance();manager.connect=Mock(return_value=handle)
        commands=queue.Queue();commands.put(dict(name='glove_open',serial='SYNTHETIC-GLOVE',user_id='owner',timeout_ms=250))
        class Events(queue.Queue):
            def put(self,event):
                super().put(copy.deepcopy(event))
                if event['type']=='glove_state' and event['state']['connection']=='receiving':
                    commands.put(dict(name='glove_disconnect'))
        events=Events()
        with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict(os.environ,WUJI_HAND_PROFILE='hand2_right'),patch('glove_agent.sys.platform','win32'):
            worker_glove(commands,events)
        states=[e['state'] for e in list(events.queue) if e['type']=='glove_state' and e['state']['connection']=='receiving']
        self.assertTrue(states,list(events.queue));self.assertEqual(handle.reads,[threading.get_ident()])
        self.assertEqual(states[0]['device_version'],dict(serial='SYNTHETIC-GLOVE',firmware_version='0.11.0',source='sdk_handle_info'))
        manager.connect.assert_called_once()
        self.assertNotIn(('enable',),calls)

    def test_glove_metadata_failure_keeps_successful_connection_and_stream(self):
        from glove_agent import worker_glove
        from test_glove_transport_lifecycle import GloveWorkerLifecycleTests
        sdk,_=GloveWorkerLifecycleTests().sdk();glove=sdk.SdkManager.instance().connect(sn='SYNTHETIC-GLOVE')
        class Handle:
            reads=0
            def __getattr__(self,name):return getattr(glove,name)
            @property
            def info(self):
                self.reads+=1;raise RuntimeError('synthetic unavailable metadata')
        handle=Handle();sdk.SdkManager.instance().connect=Mock(return_value=handle)
        commands=queue.Queue();commands.put(dict(name='glove_open',serial='SYNTHETIC-GLOVE',user_id='owner',timeout_ms=250))
        class Events(queue.Queue):
            def put(self,event):
                super().put(copy.deepcopy(event))
                if event['type']=='glove_state' and event['state']['connection']=='receiving':commands.put(dict(name='glove_disconnect'))
        events=Events()
        with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict(os.environ,WUJI_HAND_PROFILE='hand2_right'),patch('glove_agent.sys.platform','win32'):
            worker_glove(commands,events)
        states=[e['state'] for e in list(events.queue) if e['type']=='glove_state' and e['state']['connection']=='receiving']
        self.assertTrue(states,list(events.queue));self.assertIsNone(states[0]['device_version']);self.assertEqual(handle.reads,1)
        self.assertFalse(any(e['type'] in ('glove_error','glove_notice') for e in list(events.queue)))

    def test_paired_hand_version_is_preserved_through_glove_worker_and_bridge(self):
        from glove_agent import worker_glove
        from glove_bridge import GloveBridge
        from test_glove_transport_lifecycle import GloveWorkerLifecycleTests
        sdk,_=GloveWorkerLifecycleTests().sdk();commands=queue.Queue();phase=[0];deadline=time.monotonic()+3
        version=dict(serial='SYNTHETIC-HAND',firmware_version='2.5.1',source='sdk_handle_info')
        class Source:
            def __init__(self,*args):pass
            def invalidate(self,message):pass
            def target(self,now):return [0.]*20
            def snapshot(self,now):return dict(fresh=True,q=[0.]*20,seq=1,age_ms=0,timeout_ms=250)
        def hand_worker(address,serial,selected,requests,events,*args):
            events.put(dict(type='identity',device_id=serial,profile=selected['id']))
            events.put(dict(type='state',device_id=serial,device_version=copy.deepcopy(version),hardware=dict(active=False,stop_confirmed=True),latest=None))
            self.assertEqual(requests.get(timeout=2)['name'],'disconnect')
            events.put(dict(type='closed',hardware=dict(active=False,stop_confirmed=True)))
            events.put(dict(type='hand_session_closed',cleanup_confirmed=True))
        bridge=GloveBridge(Mock())
        class Events(queue.Queue):
            def put(self,event):
                super().put(copy.deepcopy(event))
                if event['type']!='glove_state':return
                bridge.receive(event)
                state=event['state']
                if time.monotonic()>deadline:commands.put(dict(name='glove_disconnect'));return
                if phase[0]==0 and state['connection']=='receiving':
                    phase[0]=1;commands.put(dict(name='glove_prepare',serial='SYNTHETIC-HAND',address=''))
                elif phase[0]==1 and state['feedback'].get('device_version'):
                    phase[0]=2;commands.put(dict(name='glove_disconnect'))
        events=Events();commands.put(dict(name='glove_open',serial='SYNTHETIC-GLOVE',user_id='owner',timeout_ms=250))
        with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict(os.environ,WUJI_HAND_PROFILE='hand2_right'), \
                patch('glove_agent.sys.platform','win32'),patch('glove_agent.GloveSource',Source), \
                patch('glove_agent.paired_hand_worker',side_effect=hand_worker):
            worker_glove(commands,events)
        self.assertEqual(phase[0],2,list(events.queue))
        snapshot=bridge.snapshot();self.assertEqual(snapshot['feedback']['device_version'],version)
        version['firmware_version']='CHANGED';self.assertEqual(snapshot['feedback']['device_version']['firmware_version'],'2.5.1')

    def test_scanning_without_an_open_does_not_read_metadata_or_connect(self):
        from glove_agent import worker_glove
        from test_glove_transport_lifecycle import GloveWorkerLifecycleTests
        sdk,_=GloveWorkerLifecycleTests().sdk();manager=sdk.SdkManager.instance();manager.connect=Mock(side_effect=AssertionError('Not authorized'))
        commands=queue.Queue();commands.put(dict(name='glove_disconnect'));events=queue.Queue()
        with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict(os.environ,WUJI_HAND_PROFILE='hand2_right'), \
                patch('glove_agent.sys.platform','win32'),patch('device_versions.read_handle_version') as read:
            worker_glove(commands,events)
        manager.connect.assert_not_called();read.assert_not_called()

    def test_failed_glove_open_does_not_sample_version_or_change_error_path(self):
        from glove_agent import worker_glove
        from test_glove_transport_lifecycle import GloveWorkerLifecycleTests
        sdk,_=GloveWorkerLifecycleTests().sdk();manager=sdk.SdkManager.instance();manager.connect=Mock(side_effect=ValueError('synthetic open failure'))
        commands=queue.Queue();commands.put(dict(name='glove_open',serial='SYNTHETIC-GLOVE',user_id='owner',timeout_ms=250));commands.put(dict(name='glove_disconnect'));events=queue.Queue()
        with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict(os.environ,WUJI_HAND_PROFILE='hand2_right'), \
                patch('glove_agent.sys.platform','win32'),patch('device_versions.read_handle_version') as read:
            worker_glove(commands,events)
        read.assert_not_called();self.assertTrue(any(e['type']=='glove_notice' and 'synthetic open failure' in e['message'] for e in list(events.queue)))

    def test_first_generation_reports_version_once_without_enabling(self):
        from first_generation import worker_first
        reads=[];clock=[1.];receives=[False]
        def now():clock[0]+=.01;return clock[0]
        def receive():
            receives[0]=not receives[0]
            return N(position=[0.]*20) if receives[0] else None
        class Hand:
            serial_number='HAND1'
            @property
            def info(self):
                reads.append(threading.get_ident());return N(serial_number='HAND1',firmware_version='1.2.4')
            def handedness_name(self):return 'left'
            def joint_states(self):return N(subscribe=lambda:N(recv=receive,close=lambda:None))
            def disconnect(self):pass
            def enable(self):raise AssertionError('Version sample enabled hardware')
            def disable(self):raise AssertionError('Version sample disabled hardware')
        sdk=N(SdkManager=N(instance=lambda:N()),DeviceType=object(),JointCommand=lambda *a:a,LowPass=lambda **k:k)
        step=[0]
        def command():
            step[0]+=1
            if step[0]>=3:return dict(name='disconnect')
            raise queue.Empty
        events=queue.Queue()
        with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict(os.environ,WUJI_HAND_PROFILE='hand1_left'), \
                patch('first_generation.time.monotonic',side_effect=now),patch('first_generation.time.sleep'):
            worker_first('',N(get_nowait=command),events,prepared_hand=Hand())
        states=[e for e in list(events.queue) if e['type']=='state']
        self.assertTrue(states,list(events.queue));self.assertEqual(reads,[threading.get_ident()])
        self.assertEqual(states[0]['device_version']['firmware_version'],'1.2.4')

    def test_hand2_reports_version_once_without_new_sdk_connect_or_rpc(self):
        from console_agent import worker
        reads=[];clock=[1.]
        def now():clock[0]+=.01;return clock[0]
        class Hand:
            serial_number='HAND2'
            @property
            def info(self):
                reads.append(threading.get_ident());return N(serial_number='HAND2',firmware_version='2.5.1')
            def joint_diagnostics(self):return N(subscribe=lambda:N(close=lambda:None))
            def joint_states(self):return N(subscribe=lambda:N(close=lambda:None))
            def disconnect(self):pass
        motion=N(active=False,reason='',pending_report=None,trial=None,refresh_before_enable=None,
                 check_probe_ready=Mock(),tick=Mock(),stop=Mock(),status=lambda:dict(active=False))
        manager=N(connect=Mock(side_effect=AssertionError('Must reuse selected handle')))
        sdk=N(SdkManager=N(instance=lambda:manager),Handedness=N(Left='left',Right='right'),WujiHand2=N())
        step=[0]
        def command():
            step[0]+=1
            if step[0]>=3:return dict(name='disconnect')
            raise queue.Empty
        events=queue.Queue()
        with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict(os.environ,WUJI_HAND_PROFILE='hand2_left'), \
                patch('console_agent.require_left'),patch('console_agent.drain_available',return_value=([],True)), \
                patch('console_agent.time.monotonic',side_effect=now),patch('console_agent.time.sleep'),patch('hardware_trial.make_trial'):
            worker('',N(get_nowait=command),events,lambda *a:motion,prepared=(Hand(),N(close=lambda:None)))
        states=[e for e in list(events.queue) if e['type']=='state']
        self.assertTrue(states,list(events.queue));self.assertEqual(reads,[threading.get_ident()])
        self.assertEqual(states[0]['device_version']['firmware_version'],'2.5.1');manager.connect.assert_not_called()


if __name__=='__main__':unittest.main()
