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


if __name__=='__main__':unittest.main()
