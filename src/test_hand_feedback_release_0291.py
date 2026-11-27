"""Read-only hand feedback release is distinct from a physical disable ACK."""
import copy
import io
from pathlib import Path
import queue
import sys
import tempfile
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

import feedback_stream
from console_server import Controller
from connection_switch import ConnectionSwitch
from hardware_showcase import HardwareShowcase


class HandFeedbackReleaseTests(unittest.TestCase):
    def record(self):
        return dict(serial='HAND-SYNTHETIC',identity=dict(serial='HAND-SYNTHETIC',profile='hand2_right'),
            worker_closed=True,reader_finished=True,transport_closed=True,thread=N(is_alive=lambda:False,join=Mock()),
            hardware=dict(active=False,stop_confirmed=False,enable_attempted=False),
            feedback_cleanup=dict(version=1,kind='readonly_feedback',serial='HAND-SYNTHETIC',profile='hand2_right',
                enable_attempted=False,resources_released=True))

    def helper(self):
        method=getattr(feedback_stream,'feedback_release_confirmed',None)
        self.assertTrue(callable(method),'Readonly release requires one shared exact proof rule')
        return method

    def test_exact_closed_never_enabled_receipt_releases_feedback_without_disable_claim(self):
        record=self.record();self.assertTrue(self.helper()(record))
        self.assertFalse(record['hardware']['stop_confirmed'])
        for field,value in [('worker_closed',False),('reader_finished',False),('transport_closed',False),('thread',N(is_alive=lambda:True))]:
            bad={**record,field:value};self.assertFalse(self.helper()(bad))
        for field,value in [('version',True),('kind','motor_stop'),('serial','OTHER'),('profile','hand2_left'),('enable_attempted',True),('resources_released',False)]:
            bad=copy.deepcopy(record);bad['feedback_cleanup'][field]=value
            self.assertFalse(self.helper()(bad))
        for change in ({'active':None},{'active':True},{'enable_attempted':True}):
            bad=copy.deepcopy(record);bad['hardware'].update(change);self.assertFalse(self.helper()(bad))
        bad=copy.deepcopy(record);bad.pop('feedback_cleanup');self.assertFalse(self.helper()(bad))

    def test_controller_and_failed_switch_share_exact_readonly_release_proof(self):
        record=self.record();c=N(lock=threading.RLock(),generation=2,state=dict(device_id=record['serial'],hardware=dict(active=False)),
            feedback_sessions={2:record},hardware_lease=None,action=Mock())
        self.assertTrue(Controller.stop_feedback_confirmed(c,2,record['serial'])['cleanup_confirmed'])
        switch=ConnectionSwitch.__new__(ConnectionSwitch);switch.lock=threading.RLock();switch._cleanup_owner=None;switch.controller=c
        switch.state=dict(cleanup_error='Original feedback release unknown',source=dict(mode='hand',serial=record['serial'],session=2))
        self.assertIsNone(switch._cleanup_error(dict(connection='disconnected',glove={})))
        record['hardware']['enable_attempted']=True
        self.assertIsNotNone(switch._cleanup_error(dict(connection='disconnected',glove={})))

    def test_enable_boundary_is_recorded_before_publisher_or_enable_can_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            hand=N(joint_command=Mock(side_effect=RuntimeError('Synthetic publisher refused')))
            motion=HardwareShowcase(hand,'HAND-SYNTHETIC',Mock(),Path(tmp)/'missing.json')
            self.assertIs(motion.status().get('enable_attempted'),False)
            with self.assertRaisesRegex(RuntimeError,'publisher refused'):motion.enable_verified([0.]*20,1.)
            self.assertIs(motion.status().get('enable_attempted'),True)

    def run_readonly_worker(self,generation,fail_close=False,*,resolved=False,failing_outer=None,direct=False,serial='HAND-SYNTHETIC'):
        calls=[];sub=N(recv=lambda:None,close=lambda:calls.append('subscription') or (False if fail_close else True))
        hand=N(serial_number=serial,handedness=lambda:N(get=lambda:'right'),handedness_name=lambda:'right',
            joint_states=lambda:N(subscribe=lambda:sub),joint_diagnostics=lambda:N(subscribe=lambda:N(recv=lambda:None,close=lambda:calls.append('diagnostics') or True)),
            disconnect=lambda:calls.append('hand') or True,enable=Mock(side_effect=AssertionError('No enable permitted')),disable=Mock(side_effect=AssertionError('No disable permitted')))
        manager=N(connect=Mock(return_value=hand),scan=Mock(return_value=[N(sn=serial,address='fixture',device_type='DeviceType.WujiHand')]))
        sdk=N(SdkManager=N(instance=lambda:manager),Handedness=N(Left='left',Right='right'),WujiHand2=N(describe_error=lambda _:{}),
            DeviceType=N(WujiHand='DeviceType.WujiHand'),JointCommand=Mock(),LowPass=Mock())
        requests=queue.Queue();requests.put({'name':'disconnect'});events=queue.Queue()
        route=N(close=lambda:calls.append('route') or failing_outer!='route')
        ownership=N(close=lambda:calls.append('ownership') or failing_outer!='ownership')
        with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict('os.environ',WUJI_HAND_PROFILE=generation+'_right'):
            if generation=='hand1':
                from first_generation import worker_first
                invoke=lambda:worker_first('',requests,events,prepared_hand=None if direct else hand)
            else:
                from console_agent import worker
                invoke=lambda:worker('',requests,events,prepared=None if direct else (hand,None))
            if resolved:
                from console_agent import worker_auto
                from device_profiles import profile
                with patch('device_discovery.connect_discovered',return_value=(hand,route,profile(generation+'_right'))),\
                        patch('device_ownership.DeviceOwnership',return_value=ownership),patch.object(sys,'platform','linux'):
                    worker_auto('',hand.serial_number,requests,events)
            elif fail_close:
                with self.assertRaises(RuntimeError):invoke()
            else:invoke()
        records=list(events.queue);closed=[row for row in records if row['type']=='closed']
        hand.enable.assert_not_called();hand.disable.assert_not_called()
        if fail_close or failing_outer:self.assertEqual(closed,[])
        else:
            self.assertEqual(len(closed),1)
            self.assertEqual(closed[0].get('feedback_cleanup'),dict(version=1,kind='readonly_feedback',serial=hand.serial_number,
                profile=generation+'_right',enable_attempted=False,resources_released=True))
            self.assertFalse(closed[0]['hardware']['stop_confirmed'])
        if resolved:self.assertIn('ownership',calls)
        if direct:self.assertEqual([row for row in records if row['type']=='identity'],[dict(type='identity',device_id=serial,profile=generation+'_right')])
        if fail_close:self.assertIn('hand',calls,'A failed earlier close must not skip remaining cleanup')
        return closed[0] if closed else None

    def test_hand2_readonly_worker_closes_resources_before_exact_receipt(self):self.run_readonly_worker('hand2')
    def test_hand1_readonly_worker_uses_same_receipt_without_motor_operations(self):self.run_readonly_worker('hand1')
    def test_subscription_false_is_not_a_closed_worker_proof(self):
        for generation in ('hand1','hand2'):
            with self.subTest(generation=generation):self.run_readonly_worker(generation,fail_close=True)

    def test_resolved_worker_owns_outer_route_and_ownership_before_releasing_closed_proof(self):
        for generation,failure in (('hand1','route'),('hand2','ownership')):
            with self.subTest(generation=generation,failure=failure):self.run_readonly_worker(generation,resolved=True,failing_outer=failure)

    def test_direct_worker_publishes_verified_identity_before_its_readonly_receipt(self):
        for generation in ('hand1','hand2'):
            with self.subTest(generation=generation):self.run_readonly_worker(generation,direct=True)

    def test_direct_worker_proves_release_only_for_a_hand_that_was_never_opened(self):
        from console_agent import worker_auto
        from device_discovery import candidates
        serial='WH2JA00000000000';address='192.0.2.40:7447'
        advertisement=N(sn=serial,address='udp/'+address,device_type='DeviceType.WujiHand2')
        self.assertEqual(candidates([advertisement])[0]['serial'],serial)
        for fault in ('absent','route_cleanup','sdk_open'):
            with self.subTest(fault=fault):
                route=N(open=Mock(),close=Mock(return_value=fault!='route_cleanup'))
                manager=N(scan=Mock(return_value=[advertisement] if fault=='sdk_open' else []),
                    connect=Mock(side_effect=RuntimeError('SDK connect did not return a hand')))
                sdk=N(SdkManager=N(instance=lambda:manager));events=queue.Queue()
                with (patch.dict(sys.modules,wuji_sdk=sdk),
                    patch('device_discovery.managed_wsl',return_value=True),
                    patch('device_discovery.DeviceRoute',return_value=route),
                    patch('device_discovery.time.monotonic',side_effect=[0.,4.])):
                    worker_auto(address,serial,queue.Queue(),events)
                rows=list(events.queue);closed=[row for row in rows if row['type']=='closed']
                self.assertEqual(rows[0]['type'],'error')
                self.assertEqual(manager.connect.call_count,int(fault=='sdk_open'))
                route.close.assert_called_once()
                if fault!='absent':
                    self.assertEqual(closed,[])
                    continue
                self.assertEqual(len(closed),1);record=self.record();record.pop('identity')
                record.update(serial=serial,hardware=closed[0]['hardware'],feedback_cleanup=closed[0]['feedback_cleanup'])
                self.assertTrue(self.helper()(record))
                for changes in (dict(serial='OTHER'),dict(resources_released=False),dict(enable_attempted=0),dict(version=True)):
                    forged=copy.deepcopy(record);forged['feedback_cleanup'].update(changes)
                    self.assertFalse(self.helper()(forged),forged)
                record['identity']=dict(serial=serial,profile='hand2_left')
                self.assertFalse(self.helper()(record),'An observed identity requires the corresponding exact stop or release')

    def test_paired_worker_checks_actual_side_for_an_unknown_directory_side(self):
        from device_discovery import candidates
        from device_profiles import profile
        from glove_agent import paired_hand_worker
        serial='WH2-LEGACY-FIXTURE'
        row=N(sn=serial,address='zenoh://'+serial,device_type='DeviceType.WujiHand2')
        self.assertIsNone(candidates([row])[0]['side_hint'])
        for side in ('right','left'):
            with self.subTest(side=side):
                hand=N(serial_number=serial,handedness=lambda:N(get=lambda:side),disconnect=Mock(return_value=True),
                    enable=Mock(side_effect=AssertionError('Read-only preparation cannot enable')))
                manager=N(scan=Mock(return_value=[row]),connect=Mock(return_value=hand))
                sdk=N(SdkManager=N(instance=lambda:manager))
                def prepared_driver(_address,_requests,events,_factory,*,prepared):
                    device,route=prepared
                    self.assertIs(device,hand);device.disconnect();route.close()
                    events.put(dict(type='closed',hardware=dict(active=False,enable_attempted=False,stop_confirmed=False),
                        feedback_cleanup=dict(version=1,kind='readonly_feedback',serial=serial,profile='hand2_right',
                            enable_attempted=False,resources_released=True)))
                events=queue.Queue()
                with (patch.dict(sys.modules,wuji_sdk=sdk),
                    patch('device_discovery.managed_wsl',return_value=False),
                    patch('device_ownership.DeviceOwnership',return_value=N(close=lambda:True)),
                    patch('console_agent.worker',side_effect=prepared_driver) as driver):
                    paired_hand_worker('',serial,profile('hand2_right'),queue.Queue(),events,Mock())
                manager.connect.assert_called_once_with(sn=serial,device_name='wuji_hand_2')
                hand.disconnect.assert_called_once();hand.enable.assert_not_called()
                records=list(events.queue)
                self.assertTrue(records[-1]['cleanup_confirmed'])
                if side=='right':
                    driver.assert_called_once()
                    self.assertEqual(records[0],dict(type='identity',profile='hand2_right',device_id=serial))
                    self.assertEqual(records[-1]['feedback_cleanup']['kind'],'readonly_feedback')
                else:
                    driver.assert_not_called()
                    self.assertEqual(records[0]['type'],'error')
                    self.assertIn('side differs',records[0]['message'])
                    self.assertEqual(records[-1]['hand_driver_cleanup']['kind'],'hand_driver_not_created')

    def test_actual_readonly_worker_proof_flows_through_host_release_cli_switch_and_fresh_discovery(self):
        import test_connection_switch as fixtures
        from device_profiles import profile
        closed=self.run_readonly_worker('hand2',resolved=True,serial=fixtures.HAND)
        record=self.record();record.update(serial=fixtures.HAND,hardware=closed['hardware'],feedback_cleanup=closed['feedback_cleanup'],
            identity=dict(serial=fixtures.HAND,profile='hand2_right'))
        self.assertFalse(record['hardware']['stop_confirmed'])
        with fixtures.SwitchTests().environment('hand') as e:
            e.state['device_profile']=profile('hand2_right');e.state['identified_device']['profile']='hand2_right'
            e.state['hardware']=copy.deepcopy(closed['hardware'])
            e.c.generation=1;e.c.hardware_lease=None;e.c.feedback_sessions={1:record}
            action=e.c.action.side_effect
            def dispatch(command):
                if command['name']=='disconnect':
                    e.c.generation+=1;e.state.update(connection='disconnected',device_id=None,identified_device=None,session_generation=2)
                    return
                return action(command)
            e.c.action.side_effect=dispatch
            e.c.stop_feedback_confirmed=lambda generation,serial:Controller.stop_feedback_confirmed(e.c,generation,serial)
            result=e.service.start(fixtures.SwitchTests().command(e))
            self.assertEqual(result['connection_switch']['phase'],'connected')
            e.c.official_viz.discover_inline.assert_called_once()
            self.assertFalse(any(row['name'] in ('hardware_start','hardware_probe','hardware_trial','glove_follow') for row in e.calls))

    def test_real_enable_then_successful_disable_still_releases_without_readonly_exception(self):
        import test_hardware_showcase as fixtures
        with tempfile.TemporaryDirectory() as tmp:
            import json
            path=Path(tmp)/'profile.json';path.write_text(json.dumps(fixtures.profile()),'utf8')
            hand=fixtures.FakeHand();motion=HardwareShowcase(hand,'synthetic-left',lambda **kw:N(**kw),path)
            motion.start(dict(action='index',speed=.5,cycles=1,lease='a'*32),fixtures.feedback(1.),fixtures.diagnostics(1.),1.)
            motion.stop('explicit stop')
            record=self.record();record.update(hardware=motion.status(),feedback_cleanup=None)
            self.assertTrue(record['hardware']['enable_attempted']);self.assertTrue(record['hardware']['stop_confirmed'])
            self.assertTrue(self.helper()(record));self.assertIn(('enable',{}),hand.calls);self.assertIn(('disable',),hand.calls)

    def test_main_allows_owned_hand_worker_to_finish_cleanup_before_forced_containment(self):
        import console_agent
        alive=[False];joins=[];terminated=[]
        def join(timeout=None):
            joins.append(timeout)
            if timeout is None or timeout>=2:alive[0]=False
        process=N(start=lambda:alive.__setitem__(0,True),is_alive=lambda:alive[0],join=join,
            terminate=lambda:terminated.append(True) or alive.__setitem__(0,False))
        context=N(Queue=queue.Queue,Process=Mock(return_value=process))
        source=io.StringIO('{"name":"connect","auto_detect":true}\n{"name":"disconnect"}\n')
        with patch.object(console_agent.mp,'get_context',return_value=context),patch.object(console_agent.sys,'stdin',source),\
                patch.object(console_agent.select,'select',return_value=([source],[],[])),\
                patch.object(console_agent,'emit'),patch('sdk_runtime.probe_runtime',return_value={}):
            console_agent.main()
        self.assertEqual(terminated,[]);self.assertTrue(joins);self.assertGreaterEqual(joins[0],2)

    def test_main_delivers_terminal_receipt_when_worker_exits_after_queue_looks_empty(self):
        import console_agent
        terminal=dict(type='closed',hardware=dict(active=False,stop_confirmed=False),feedback_cleanup={'test':'exact receipt'})
        alive=[True];first_poll=[True]
        class LateEventQueue(queue.Queue):
            def get_nowait(self):
                if first_poll[0]:
                    first_poll[0]=False;alive[0]=False;self.put(terminal);raise queue.Empty
                return super().get_nowait()
        events=LateEventQueue()
        process=N(start=Mock(),is_alive=lambda:alive[0],join=Mock(),
            terminate=Mock(side_effect=AssertionError('This worker has already exited')))
        context=N(Queue=Mock(side_effect=[queue.Queue(),events]),Process=Mock(return_value=process))
        source=io.StringIO('{"name":"connect","auto_detect":true,"serial":"HAND-SYNTHETIC"}\n')
        reads=iter(([source],[],[]))
        with (patch.object(console_agent.mp,'get_context',return_value=context),
            patch.object(console_agent.sys,'stdin',source),
            patch.object(console_agent.select,'select',side_effect=lambda *_:(next(reads),[],[])),
            patch.object(console_agent,'emit') as emit,patch('sdk_runtime.probe_runtime',return_value={}),
            patch('calibration_portability.prepare_transfer_directory')):
            console_agent.main()
        self.assertIn(terminal,[call.args[0] for call in emit.call_args_list])
        process.terminate.assert_not_called()


if __name__=='__main__':unittest.main()
