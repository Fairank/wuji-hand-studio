"""Actual controller/catalog/export scoped playback; no SDK, devices or sockets."""
import copy
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from console_server import Controller,Handler
from demonstration_scope import DemonstrationScope
from device_profiles import PROFILES,profile
from demo_player import DemoPlayer


class DemonstrationScopeTests(unittest.TestCase):
    def setUp(self):
        guard=patch('studio_component.enforce_session_owner')
        self.component_guard=guard.start();self.addCleanup(guard.stop)

    def controller(self,busy=True):
        c=object.__new__(Controller);c.lock=threading.RLock();c.desktop_closing=False
        c.state=dict(device_profile=profile('hand2_left'),view_source='glove',connection='connected',
            latest={'joints':[{'nid':1,'position_rad':.2}]},stale=False,device_id='WH_SYNTHETIC',
            mapping=[{'index':0,'nid':1}],mapping_device_id='WH_SYNTHETIC',mapping_verified=True,
            identified_device={'serial':'WH_SYNTHETIC'},joint_rates=[{'nid':1,'status':'fresh'}],
            metrics={'age_ms':0},hardware={'active':False,'trial_ready':True,'trial_controls_version':3,'gesture_library_version':4},
            recording={'active':False},glove={'busy':True,'glove_serial':'WG_SYNTHETIC','user':{'user_id':'private-owner'},
                'stream':{'q':[.2]*20,'fresh':True},'profile':'hand2_left'})
        c.player=DemoPlayer();c.group=N(active=busy,internal=False,stop=Mock(side_effect=AssertionError('must not stop group')))
        c.program=N(thread=None,snapshot=Mock(return_value={'active':busy}),check=Mock(side_effect=AssertionError('must not advance program')))
        c.glove=N(busy=busy,lease=None,action=Mock(side_effect=AssertionError('must not send to glove')))
        c.calibration=N(run={'running':busy});c.hardware_lease=None
        c.doctor=N(snapshot=lambda:{'running':False});c.parameters=N(sync_status={'busy':False})
        c.send=Mock();c.log=Mock()
        c.snapshot=lambda:dict(copy.deepcopy(c.state),playback=c.player.snapshot(),
            demonstration_playback=c._demonstration_scope().snapshot())
        return c

    def start(self,c,pid,**kw):
        return c.action(dict(name='demo_start',display_profile=pid,action='fist',speed=.5,cycles=3,**kw))

    def test_sdk_group_and_calibration_busy_do_not_block_or_modify_local_four_scope_playback(self):
        c=self.controller();before=copy.deepcopy(c.state)
        with patch('firmware_host.enforce_maintenance',side_effect=AssertionError('device gate must not be called')):
            for pid in PROFILES:self.start(c,pid)
            states=c._demonstration_scope().snapshot()
            self.assertTrue(all(p['active'] and p['running'] for p in states.values()))
            run=states['hand1_right']['run_id']
            c.action(dict(name='demo_pause',display_profile='hand1_right',expected_run=run))
            self.assertFalse(c._demonstration_scope().snapshot('hand1_right')['running'])
            self.assertTrue(c._demonstration_scope().snapshot('hand2_left')['running'])
            c.action(dict(name='demo_resume',display_profile='hand1_right',expected_run=run))
            c.action(dict(name='demo_stop',display_profile='hand1_right',expected_run=run))
        self.assertFalse(c._demonstration_scope().snapshot('hand1_right')['active'])
        self.assertTrue(c._demonstration_scope().snapshot('hand2_right')['active'])
        self.assertEqual(c.state,before);self.assertFalse(c.player.snapshot()['active'])
        c.send.assert_not_called();c.group.stop.assert_not_called();c.glove.action.assert_not_called()

    def test_scope_local_old_run_invalid_profile_and_request_shape_refuse_before_side_effects(self):
        c=self.controller();self.start(c,'hand2_left');first=c._demonstration_scope().snapshot('hand2_left')['run_id']
        self.start(c,'hand2_left');current=c._demonstration_scope().snapshot('hand2_left')['run_id']
        self.assertNotEqual(first,current)
        for command in (dict(name='demo_stop',display_profile='hand2_left',expected_run=first),
                        dict(name='demo_stop',display_profile='hand1_left',expected_run=current),
                        dict(name='demo_start',display_profile=None,action='fist',speed=.5,cycles=1),
                        dict(name='demo_start',display_profile='hand3_left',action='fist',speed=.5,cycles=1),
                        dict(name='hardware_trial',display_profile='hand2_left'),
                        dict(name='demo_stop',display_profile='hand2_left',lease='not-an-actuator-command')):
            with self.subTest(command=command),self.assertRaises(ValueError):c.action(command)
        self.assertEqual(c._demonstration_scope().snapshot('hand2_left')['run_id'],current)
        self.assertTrue(c._demonstration_scope().snapshot('hand2_left')['running'])
        c.send.assert_not_called();c.group.stop.assert_not_called()

    def test_display_snapshot_never_inherits_glove_or_wrong_profile_feedback(self):
        c=self.controller();before=copy.deepcopy(c.state)
        same=c.demonstration_snapshot('hand2_left')
        self.assertEqual(same['latest'],before['latest']);self.assertEqual(same['view_source'],'feedback')
        self.assertFalse(same['glove']['busy']);self.assertNotIn('user',same['glove'])
        other=c.demonstration_snapshot('hand1_right')
        self.assertEqual(other['device_profile']['id'],'hand1_right');self.assertEqual(other['view_source'],'preview')
        self.assertIsNone(other['latest']);self.assertEqual(other['mapping'],[]);self.assertIsNone(other['device_id'])
        self.start(c,'hand2_left')
        active=c.demonstration_snapshot('hand2_left')
        self.assertEqual(active['view_source'],'preview');self.assertIsNone(active['latest'])
        self.assertEqual(c.state,before)

    def test_closing_rejects_new_local_playback_but_scoped_stop_remains_available(self):
        c=self.controller();self.start(c,'hand1_left');c.desktop_closing=True
        with self.assertRaises(ValueError):self.start(c,'hand1_right')
        c.action(dict(name='demo_stop',display_profile='hand1_left'))
        self.assertFalse(c._demonstration_scope().snapshot('hand1_left')['active'])

    def test_wrong_hardware_expected_profile_rejects_before_group_program_or_sending(self):
        c=self.controller();before=copy.deepcopy(c.state)
        for value in ('hand2_right','hand1_left','',None,False,{},'hand2_left '):
            with self.subTest(value=value),self.assertRaises(ValueError):
                c._coordinated_action(dict(name='hardware_trial',expected_profile=value,expected_serial='WH_SYNTHETIC'))
        self.assertEqual(c.state,before);self.assertIsNone(c.hardware_lease)
        c.send.assert_not_called();c.group.stop.assert_not_called();c.program.check.assert_not_called()

    def test_matching_hardware_profile_dispatches_only_after_actual_gates_and_is_rechecked_at_send(self):
        c=self.controller(busy=False)
        command=dict(name='hardware_trial',expected_profile='hand2_left',expected_serial='WH_SYNTHETIC',action='fist',speed=.5,amplitude=.25,cycles=1,workspace_clear=True)
        c._coordinated_action(command)
        self.assertEqual(c.send.call_count,1);sent=c.send.call_args.args[0]
        self.assertNotIn('expected_profile',sent);self.assertNotIn('expected_serial',sent);self.assertEqual(sent['action'],'fist')
        c=self.controller(busy=False)
        def changed():
            result=copy.deepcopy(c.state);c.state['device_profile']=profile('hand2_right');return result
        c.snapshot=changed
        with self.assertRaisesRegex(ValueError,'differs'):c._coordinated_action(command)
        c.send.assert_not_called();self.assertIsNone(c.hardware_lease)

    def test_same_model_different_hand_and_partial_or_dirty_target_identity_never_dispatch(self):
        c=self.controller();before=copy.deepcopy(c.state)
        commands=[dict(name='hardware_trial',expected_profile='hand2_left'),
                  dict(name='hardware_trial',expected_serial='WH_SYNTHETIC')]
        commands += [dict(name='hardware_trial',expected_profile='hand2_left',expected_serial=value)
                     for value in ('WH_OTHER','',None,False,'WH SYNTHETIC','x'*81,{},'WH_SYNTHETIC\n')]
        for command in commands:
            with self.subTest(command=command),self.assertRaises(ValueError):c._coordinated_action(command)
        self.assertEqual(c.state,before);c.send.assert_not_called();c.group.stop.assert_not_called()
        c.program.check.assert_not_called();self.assertIsNone(c.hardware_lease)
        c=self.controller(busy=False)
        command=dict(name='hardware_trial',expected_profile='hand2_left',expected_serial='WH_SYNTHETIC',
                     action='fist',speed=.5,amplitude=.25,cycles=1,workspace_clear=True)
        def replaced():
            result=copy.deepcopy(c.state);c.state['device_id']='WH_OTHER';return result
        c.snapshot=replaced
        with self.assertRaisesRegex(ValueError,'Connected hand changed'):c._coordinated_action(command)
        c.send.assert_not_called();self.assertIsNone(c.hardware_lease)
        c=self.controller(busy=False)
        c._coordinated_action({key:value for key,value in command.items() if key not in ('expected_profile','expected_serial')})
        c.send.assert_called_once()  # Legacy internal API keeps its original device guards.

    def test_feedback_and_preview_selection_are_read_only_and_do_not_stop_scoped_playback(self):
        c=self.controller();before=copy.deepcopy(c.state);self.start(c,'hand2_left')
        run=c._demonstration_scope().snapshot('hand2_left')['run_id']
        feedback=c.demonstration_snapshot('hand2_left',display='feedback')
        self.assertEqual(feedback['view_source'],'feedback');self.assertEqual(feedback['latest'],before['latest'])
        self.assertFalse(feedback['playback']['active']);self.assertEqual(feedback['demonstration_display'],'feedback')
        self.assertTrue(c._demonstration_scope().snapshot('hand2_left')['running'])
        preview=c.demonstration_snapshot('hand2_left',display='preview')
        self.assertEqual(preview['view_source'],'preview');self.assertIsNone(preview['latest'])
        self.assertEqual(preview['playback']['run_id'],run);self.assertTrue(preview['playback']['active'])
        absent=c.demonstration_snapshot('hand1_right',display='feedback')
        self.assertEqual(absent['view_source'],'feedback');self.assertIsNone(absent['latest'])
        self.assertEqual(absent['mapping'],[]);self.assertIsNone(absent['device_id'])
        static=c.demonstration_snapshot('hand1_right',display='preview')
        self.assertEqual(static['view_source'],'preview');self.assertFalse(static['playback']['active'])
        for value in ('',False,'glove','automatic'):
            with self.subTest(value=value),self.assertRaises(ValueError):c.demonstration_snapshot('hand2_left',display=value)
        self.assertEqual(c.state,before);c.send.assert_not_called();c.group.stop.assert_not_called()


class DemonstrationReadTests(unittest.TestCase):
    def request(self,path):
        handler=object.__new__(Handler);handler.path=path;handler.allowed_host=lambda:True
        handler.server=N(controller=N(state={'device_profile':profile('hand2_left')}))
        results=[];handler.reply=lambda *args,**kw:results.append((args,kw))
        handler.do_GET();self.assertEqual(len(results),1)
        return results[0]

    def test_four_catalog_scopes_and_json_csv_exports_match_requested_profile(self):
        for pid in PROFILES:
            with self.subTest(profile=pid):
                args,_=self.request('/api/catalog?profile='+pid)
                self.assertEqual(args[0],200);self.assertEqual(args[1]['device_profile']['id'],pid)
                self.assertTrue(args[1]['actions'])
                args,headers=self.request('/api/performance?action=text_sequence&text=A&profile='+pid)
                self.assertEqual(args[0],200);self.assertEqual(args[1]['device_profile'],pid)
                self.assertIn(pid,headers['filename'])
                args,headers=self.request('/api/performance?action=text_sequence&text=A&format=csv&profile='+pid)
                self.assertEqual(args[0],200);self.assertIn(pid.encode(),args[1]);self.assertIn(pid,headers['filename'])

    def test_invalid_blank_duplicate_selectors_do_not_fall_back_to_global_profile(self):
        for suffix in ('profile=','profile=hand3_left','profile=hand1_left&profile=hand2_left'):
            for path in ('/api/catalog?'+suffix,'/api/performance?action=text_sequence&text=A&'+suffix):
                with self.subTest(path=path):self.assertEqual(self.request(path)[0][0],400)
        self.assertNotIn('device_profile',self.request('/api/catalog')[0][1])
        self.assertEqual(self.request('/api/performance?action=text_sequence&text=A')[0][1]['device_profile'],'hand2_left')


if __name__=='__main__':unittest.main()
