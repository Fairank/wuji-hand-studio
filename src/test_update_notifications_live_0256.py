"""Current fleet ownership filters firmware items; history and software remain."""
import copy
import threading
import unittest
from unittest.mock import Mock

from update_notifications import UpdateNotifications


GLOVE='WG1KA00000000000'
HAND='WH2KA00000000001'


class UpdateNotificationsLive0256Tests(unittest.TestCase):
    def setUp(self):
        self.now=100000.;self.states=[]
        self.app,self.cli,self.sdk,self.firmware=[Mock() for _ in range(4)]
        self.app.snapshot.return_value=dict(available=True,current_version='0.2.55',latest_version='0.2.56')
        self.cli.snapshot.return_value=dict(current=dict(version='2026.10.12'),update=dict(available=True,latest_version='2026.10.26'))
        self.sdk.snapshot.return_value=dict(phase='available',release=dict(version='2026.10.26',checked_at=self.now),proof=dict(version='2026.10.12'))
        self.firmware.snapshot.return_value={}
        self.devices,self.idle=Mock(),Mock()
        self.monitor=UpdateNotifications(states=lambda:copy.deepcopy(self.states),app=lambda:self.app,cli=lambda:self.cli,
            sdk=lambda:self.sdk,firmware=lambda:self.firmware,devices=self.devices,idle=self.idle,clock=lambda:self.now)

    def glove(self,*,epoch=35,sn=GLOVE,version='1.0.4'):
        return dict(connection='disconnected',session_generation=1,device_profile=dict(id='hand2_left',generation='hand2'),
            glove=dict(connection='receiving',busy=True,glove_serial=sn,session_id=epoch,
                device_version=dict(source='sdk_handle_info',serial=sn,firmware_version=version)))

    def hand(self,*,epoch=2,sn=HAND,generation='hand2'):
        profile=generation+'_left'
        return dict(connection='connected',session_generation=epoch,device_id=sn,
            device_profile=dict(id=profile,generation=generation),
            identified_device=dict(source='sdk_identity',serial=sn,profile=profile),
            device_version=dict(source='sdk_handle_info',serial=sn,firmware_version='1.0.4'))

    def viewer(self,*,sn=GLOVE,kind='wuji_glove'):
        return dict(connection='disconnected',official_viz=dict(active=True,phase='running',session_id='a'*32,
            device=dict(sn=sn,device_type=kind),reported_device=dict(sn=sn)))

    def report(self,sn=GLOVE,kind='wuji_glove',current='1.0.4',latest='1.0.5'):
        self.firmware.snapshot.return_value=dict(device=dict(sn=sn,kind='glove' if kind=='wuji_glove' else 'hand'),
            checked_at=self.now,notification=dict(serial=sn,device_type=kind,current=current,latest=latest,update_available=True))

    def firmware_items(self,snapshot=None):return [row for row in (snapshot or self.monitor.snapshot())['items'] if row['kind']=='firmware']

    def no_io(self):
        self.app.start.assert_not_called();self.cli.start.assert_not_called();self.sdk.check_background.assert_not_called()
        self.firmware.start_check.assert_not_called();self.devices.assert_not_called();self.idle.assert_not_called()

    def seed_glove(self):
        self.states=[self.glove()];self.report();snapshot=self.monitor.snapshot()
        self.assertEqual([row['serial'] for row in self.firmware_items(snapshot)],[GLOVE]);return snapshot

    def test_fresh_get_after_disconnect_hides_firmware_and_keeps_history_and_software(self):
        self.seed_glove();known=copy.deepcopy(self.monitor.known);reports=copy.deepcopy(self.monitor.reports)
        # A persistent generation and retained identity are not a connection.
        self.states[0]['glove'].update(connection='disconnected',busy=False)
        self.monitor.observe(self.states);after=self.monitor.snapshot()
        self.assertEqual(self.firmware_items(after),[])
        self.assertEqual({row['kind'] for row in after['items']},{'workbench','cli','sdk'})
        self.assertEqual(self.monitor.known,known);self.assertEqual(self.monitor.reports,reports)
        self.assertEqual(after['devices'][0]['serial'],GLOVE);self.no_io()

    def test_child_owner_survives_an_idle_main_workspace_partial_observation(self):
        self.states=[dict(connection='disconnected',session_generation=9),self.glove()];self.report()
        self.monitor.observe([self.states[0]])
        self.assertEqual([row['serial'] for row in self.firmware_items()],[GLOVE])
        self.states=self.states[:1]
        self.assertEqual(self.firmware_items(),[]);self.assertIn(GLOVE,self.monitor.known);self.no_io()

    def test_current_hand_identity_and_generation_filter_exact_firmware_type(self):
        self.states=[self.hand()];self.report(HAND,'wuji_hand_2')
        self.assertEqual(len(self.firmware_items()),1)
        for changes in ({'connection':'disconnected'},{'session_generation':None},
                        {'identified_device':dict(source='selection',serial=HAND,profile='hand2_left')},
                        {'identified_device':dict(source='sdk_identity',serial='ANOTHER',profile='hand2_left')},
                        {'identified_device':dict(source='sdk_identity',serial=HAND,profile='hand1_left')}):
            with self.subTest(changes=changes):
                self.states=[dict(self.hand(),**changes)];self.assertEqual(self.firmware_items(),[])
        self.states=[self.hand(generation='hand1')]
        self.assertEqual(self.firmware_items(),[],'A Hand 1 owner cannot expose a Hand 2 report')
        self.no_io()

    def test_discovery_history_and_retained_epoch_never_authorize_items(self):
        self.seed_glove();old=self.glove()
        cases=[dict(connection='disconnected',devices=[dict(sn=GLOVE,device_type='wuji_glove')]),
            dict(connection='disconnected',glove=dict(connection='disconnected',session_id=35,glove_serial=GLOVE)),
            dict(connection='disconnected',official_viz=dict(active=False,scanning=True,phase='running',session_id='a'*32,
                device=dict(sn=GLOVE,device_type='wuji_glove'),reported_device=dict(sn=GLOVE))),
            dict(connection='disconnected',identified_device=dict(source='sdk_identity',serial=GLOVE),device_id=GLOVE),
            dict(connection='disconnected',history=[dict(serial=GLOVE,kind='glove')])]
        for state in cases:
            with self.subTest(state=state):
                self.states=[state];self.monitor.observe([old]);self.assertEqual(self.firmware_items(),[])
        self.assertIn(GLOVE,self.monitor.reports);self.no_io()

    def test_glove_owner_requires_receiving_session_and_consistent_explicit_identity(self):
        self.seed_glove()
        for changes in ({'connection':'error'},{'busy':False},{'session_id':None},{'session_id':False},
                        {'glove_serial':'ANOTHER'},{'identified_glove':dict(source='sdk_identity',serial='ANOTHER')}):
            with self.subTest(changes=changes):
                value=self.glove();value['glove'].update(changes);self.states=[value]
                self.assertEqual(self.firmware_items(),[])
        self.states=[self.glove(epoch=36)];self.assertEqual(len(self.firmware_items()),1);self.no_io()

    def test_current_official_viewer_matches_started_and_selected_identity(self):
        self.states=[self.viewer()];self.report();self.assertEqual(len(self.firmware_items()),1)
        for changes in ({'active':False},{'phase':'stopping'},{'session_id':None},
                        {'device':dict(sn='ANOTHER',device_type='wuji_glove')},
                        {'device':dict(sn=GLOVE,device_type='wuji_hand_2')}):
            with self.subTest(changes=changes):
                value=self.viewer();value['official_viz'].update(changes);self.states=[value]
                self.assertEqual(self.firmware_items(),[])
        self.no_io()

    def test_running_official_calibration_sources_are_owners_until_they_end(self):
        self.report()
        for source in ('official_tty','official_jsonl_native'):
            with self.subTest(source=source):
                self.states=[dict(calibration=dict(running=True,serial=GLOVE,id='b'*32,source=source))]
                self.assertEqual(len(self.firmware_items()),1)
                self.states[0]['calibration']['running']=False;self.assertEqual(self.firmware_items(),[])
        self.states=[dict(calibration=dict(running=True,serial=GLOVE,id='b'*32,source='discovery'))]
        self.assertEqual(self.firmware_items(),[]);self.no_io()

    def test_paired_hand_requires_the_current_glove_and_connected_verified_feedback(self):
        value=self.glove();value['glove']['feedback']=dict(connection='connected',device_id=HAND,
            identified_device=dict(source='sdk_identity',serial=HAND,profile='hand2_left'))
        self.states=[value];self.report(HAND,'wuji_hand_2');self.assertEqual(len(self.firmware_items()),1)
        for branch,key,new in [('glove','connection','disconnected'),('feedback','connection','disconnected'),
                              ('feedback','identified_device',dict(source='sdk_identity',serial='ANOTHER'))]:
            with self.subTest(branch=branch,key=key):
                changed=copy.deepcopy(value);target=changed['glove'] if branch=='glove' else changed['glove']['feedback']
                target[key]=new;self.states=[changed];self.assertEqual(self.firmware_items(),[])
        self.no_io()

    def test_other_device_or_conflicting_type_cannot_publish_an_inactive_devices_item(self):
        self.seed_glove();self.states=[self.hand()]
        self.assertEqual(self.firmware_items(),[])
        self.states=[self.glove(),self.hand(sn=GLOVE)]
        self.assertEqual(self.firmware_items(),[],'Conflicting live family claims must not choose a firmware model')
        self.no_io()

    def test_fleet_read_failure_or_empty_result_keeps_software_and_history_only(self):
        self.seed_glove();reports=copy.deepcopy(self.monitor.reports)
        for result in ([],None,dict(connection='connected')):
            with self.subTest(result=result):
                self.monitor.states=lambda:copy.deepcopy(result);snapshot=self.monitor.snapshot()
                self.assertEqual(self.firmware_items(snapshot),[])
                self.assertEqual({row['kind'] for row in snapshot['items']},{'workbench','cli','sdk'})
        self.monitor.states=Mock(side_effect=OSError('Synthetic fleet unavailable'))
        self.assertEqual(self.firmware_items(),[]);self.assertEqual(self.monitor.reports,reports);self.no_io()

    def test_stale_partial_observe_cannot_reopen_disconnected_items_on_a_new_get(self):
        self.seed_glove();old=copy.deepcopy(self.states)
        self.states=[dict(connection='disconnected',glove=dict(connection='disconnected',busy=False,session_id=35))]
        self.assertEqual(self.firmware_items(),[])
        self.monitor.observe(old)
        self.assertEqual(self.firmware_items(),[]);self.assertIn(GLOVE,self.monitor.known);self.no_io()

    def test_older_delayed_fleet_read_cannot_publish_after_a_new_disconnected_get(self):
        self.seed_glove();old=copy.deepcopy(self.states);entered=threading.Event();resume=threading.Event();results=[]
        def states():
            if threading.current_thread().name=='old-notification-read':
                entered.set()
                if not resume.wait(3):raise AssertionError('Synthetic reader watchdog')
                return copy.deepcopy(old)
            return copy.deepcopy(self.states)
        self.monitor.states=states
        worker=threading.Thread(target=lambda:results.append(self.monitor.snapshot()),name='old-notification-read',daemon=True)
        worker.start();self.assertTrue(entered.wait(2));self.states=[];new=self.monitor.snapshot()
        resume.set();worker.join(3);self.assertFalse(worker.is_alive());self.assertEqual(self.firmware_items(new),[])
        self.assertEqual(self.firmware_items(results[0]),[]);self.assertIn(GLOVE,self.monitor.reports);self.no_io()

    def test_older_get_blocked_in_software_status_cannot_restore_firmware(self):
        self.seed_glove();entered=threading.Event();resume=threading.Event();results=[];software=copy.deepcopy(self.app.snapshot.return_value)
        def app_status():
            if threading.current_thread().name=='old-notification-read':
                entered.set()
                if not resume.wait(3):raise AssertionError('Synthetic software status watchdog')
            return copy.deepcopy(software)
        self.app.snapshot.side_effect=app_status
        worker=threading.Thread(target=lambda:results.append(self.monitor.snapshot()),name='old-notification-read',daemon=True)
        worker.start();self.assertTrue(entered.wait(2));self.states=[];self.monitor.snapshot();resume.set();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(self.firmware_items(results[0]),[]);self.no_io()

    def test_new_epoch_current_version_is_not_rolled_back_by_an_older_get(self):
        self.seed_glove();old=copy.deepcopy(self.states);entered=threading.Event();resume=threading.Event();results=[]
        def states():
            if threading.current_thread().name=='old-notification-read':
                entered.set()
                if not resume.wait(3):raise AssertionError('Synthetic epoch watchdog')
                return copy.deepcopy(old)
            return copy.deepcopy(self.states)
        self.monitor.states=states
        worker=threading.Thread(target=lambda:results.append(self.monitor.snapshot()),name='old-notification-read',daemon=True)
        worker.start();self.assertTrue(entered.wait(2));self.now+=1;self.states=[self.glove(epoch=36,version='1.0.5')]
        self.assertEqual(self.firmware_items(),[]);resume.set();worker.join(3);self.assertFalse(worker.is_alive())
        self.assertEqual(self.firmware_items(results[0]),[])
        self.assertEqual(self.monitor.known[GLOVE]['firmware_version'],'1.0.5');self.no_io()

    def test_full_fleet_status_is_called_without_holding_the_notification_lock(self):
        self.seed_glove();state=copy.deepcopy(self.states);acquired=[]
        def read():
            def other_thread():
                got=self.monitor.lock.acquire(timeout=.5);acquired.append(got)
                if got:self.monitor.lock.release()
            worker=threading.Thread(target=other_thread);worker.start();worker.join(1)
            self.assertFalse(worker.is_alive());return copy.deepcopy(state)
        self.monitor.states=read;self.assertEqual(len(self.firmware_items()),1);self.assertEqual(acquired,[True]);self.no_io()

    def test_closed_monitor_retains_software_but_does_not_offer_device_updates(self):
        self.seed_glove();self.monitor.close();snapshot=self.monitor.snapshot()
        self.assertEqual(self.firmware_items(snapshot),[])
        self.assertEqual({row['kind'] for row in snapshot['items']},{'workbench','cli','sdk'});self.no_io()


if __name__=='__main__':unittest.main()
