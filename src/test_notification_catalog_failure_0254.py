"""Real reminder lifecycle with synthetic exact-device service snapshots only."""
import copy
import json
import unittest
from unittest.mock import Mock

from device_versions import observe_official_check_failure
from update_notifications import FIRMWARE_INTERVAL,UpdateNotifications


SN='SYNTHETIC-NOTICE-54'
OTHER='SYNTHETIC-OTHER-54'
ERROR='firmware API HTTP 404: hardware version not found for this device type'


class NotificationCatalogFailureTests(unittest.TestCase):
    def setUp(self):
        self.now=100000.
        self.app,self.cli,self.sdk,self.firmware=[Mock() for _ in range(4)]
        for service in (self.app,self.cli,self.sdk,self.firmware):service.snapshot.return_value={}
        self.states=[dict(glove=dict(connection='receiving',busy=True,session_id=1,glove_serial=sn))
                     for sn in (SN,OTHER)]
        self.monitor=UpdateNotifications(states=lambda:copy.deepcopy(self.states),app=lambda:self.app,cli=lambda:self.cli,
            sdk=lambda:self.sdk,firmware=lambda:self.firmware,devices=lambda:[],idle=Mock(),clock=lambda:self.now)
        self.monitor._remember(SN,'glove')
        self.monitor._remember(OTHER,'glove')

    def state(self,*,failed=False,sn=SN,kind='wuji_glove',current='1.0.3',latest='1.0.4',stamp=None,code=None):
        row=dict(sn=sn,device_type=kind,current_version=current,latest_version=None if failed else latest,
            status='failed' if failed else 'up to date' if current==latest else 'update available',error=ERROR if failed else None)
        raw=dict(returncode=(1 if failed else 0) if code is None else code,stdout=json.dumps(dict(devices=[row])),stderr='')
        return dict(device=dict(sn=sn,kind='glove',generation='glove1'),check_report=raw,
            notification=None,phase='check_failed' if failed else 'available',checked_at=self.now if stamp is None else stamp,
            current_version=current,check_observation=observe_official_check_failure(raw['stdout'],sn,kind) if failed else None)

    def show(self,state):
        self.firmware.snapshot.return_value=copy.deepcopy(state)
        return self.monitor.snapshot()

    def assert_no_io(self):
        self.firmware.start_check.assert_not_called()
        self.app.start.assert_not_called();self.cli.start.assert_not_called();self.sdk.check_background.assert_not_called()

    def test_success_failure_duplicate_late_success_and_explicit_retry(self):
        old=self.state();first=self.show(old)
        self.assertEqual([(v['current_version'],v['version']) for v in first['items']],[('1.0.3','1.0.4')])
        self.now+=10;failed=self.state(failed=True,current='1.0.4')
        after=self.show(failed)
        self.assertEqual(after['items'],[])
        known=next(d for d in after['devices'] if d['serial']==SN)
        self.assertEqual(known['firmware_version'],'1.0.3','Failed observation must not promote update authority')
        self.assertEqual(known['firmware_version_source'],'official_cli_check')
        self.assertNotIn('_failed_check_at',known)
        self.assertEqual(self.show(failed)['items'],[])
        self.assertEqual(self.show(old)['items'],[],'Late earlier success must not revive stale alert')
        self.now+=10
        restored=self.show(self.state(current='1.0.4',latest='1.0.5'))
        self.assertEqual([(v['current_version'],v['version']) for v in restored['items']],[('1.0.4','1.0.5')])
        self.now+=10;self.assertEqual(self.show(self.state(current='1.0.5',latest='1.0.5'))['items'],[])
        self.assert_no_io()

    def test_failed_fact_never_creates_available_or_up_to_date_notice(self):
        for current in ('1.0.4',None):
            for code in (0,1):
                for source in ('check_report','verification_report','result'):
                    with self.subTest(current=current,code=code,source=source):
                        self.setUp();self.show(self.state())
                        self.now+=10;state=self.state(failed=True,current=current,code=code)
                        if source!='check_report':state[source]=state.pop('check_report')
                        result=self.show(state)
                        self.assertEqual(result['items'],[])
                        self.assertEqual(self.monitor.reports,{})
                        self.assertEqual(next(d for d in result['devices'] if d['serial']==SN)['firmware_version'],'1.0.3')
                        self.assert_no_io()

    def test_wrong_identity_duplicate_schema_untrusted_observation_and_time_do_not_invalidate(self):
        changes=[]
        state=self.state(failed=True,current='1.0.4',stamp=self.now+1);changes.append(state)
        state=self.state(failed=True,current='1.0.4',stamp=self.now-FIRMWARE_INTERVAL);changes.append(state)
        state=self.state(failed=True,sn=OTHER,current='1.0.4');changes.append(state)
        state=self.state(failed=True,kind='wuji_hand_2',current='1.0.4');changes.append(state)
        state=self.state(failed=True,current='1.0.4');state['device']['sn']=OTHER;changes.append(state)
        state=self.state(failed=True,current='1.0.4');state['device']['kind']='hand';changes.append(state)
        state=self.state(failed=True,current='1.0.4');state['device']['device_type']='wuji_hand_2';changes.append(state)
        state=self.state(failed=True,current='1.0.4');state['check_observation']=None;changes.append(state)
        state=self.state(failed=True,current='1.0.4');state['check_observation']['error']='different';changes.append(state)
        state=self.state(failed=True,current='1.0.4');state['current_version']='1.0.2';changes.append(state)
        state=self.state(failed=True,current='1.0.4');state['check_report']['returncode']=True;changes.append(state)
        state=self.state(failed=True,current='1.0.4');raw=state['check_report']['stdout'];state['check_report']['stdout']=raw.replace('"devices":','"devices":[],"devices":');changes.append(state)
        for changed in changes:
            with self.subTest(changed=changed):
                self.setUp();first=self.show(self.state());self.now+=.5
                self.assertEqual(self.show(changed)['items'],first['items'])
                self.assert_no_io()

    def test_older_failed_observation_cannot_hide_newer_success_for_same_device(self):
        old_failure=self.state(failed=True,current='1.0.3')
        self.now+=10;new=self.state();first=self.show(new)
        self.assertEqual(self.show(old_failure)['items'],first['items'])
        self.assertEqual(self.show(new)['items'],first['items'])
        self.assert_no_io()

    def test_failure_only_withdraws_exact_device_notice_and_offline_does_not_query(self):
        first=self.show(self.state())
        self.now+=10;self.show(self.state(sn=OTHER,current='1.0.0',latest='1.0.7'))
        self.now+=10;remaining=self.show(self.state(failed=True,current='1.0.4'))
        self.assertEqual([v['serial'] for v in remaining['items']],[OTHER])
        self.firmware.snapshot.side_effect=OSError('synthetic offline')
        self.assertEqual(self.monitor.snapshot()['items'],remaining['items'])
        self.assert_no_io()

    def test_equal_timestamp_conflict_is_conservative_until_later_success(self):
        old=self.state();self.show(old)
        self.show(self.state(failed=True,current='1.0.4'))
        self.assertEqual(self.show(old)['items'],[])
        self.now+=1
        self.assertEqual(len(self.show(self.state())['items']),1)
        self.assert_no_io()

    def test_failure_watermark_and_withdrawal_are_scoped_to_exact_hand_generation(self):
        self.monitor._remember(SN,'hand')
        self.states[0]=dict(connection='connected',device_id=SN,session_generation=1,
            device_profile=dict(id='hand2_left',generation='hand2'),
            identified_device=dict(source='sdk_identity',serial=SN,profile='hand2_left'))
        old=self.state(kind='wuji_hand_2')
        old['device'].update(kind='hand',generation='hand2')
        first=self.show(old)
        self.assertEqual(len(first['items']),1)
        self.assertEqual(first['items'][0]['model'],'Wuji Hand 2')
        self.now+=10
        wrong=self.state(failed=True,kind='wuji_hand',current='1.0.4')
        wrong['device'].update(kind='hand',generation='hand1')
        self.assertEqual(self.show(wrong)['items'],first['items'])
        # Even without the previous cached report, this other-type failure
        # must not block an older valid Hand 2 observation from being accepted.
        self.monitor.reports.clear()
        self.assertEqual(self.show(old)['items'],first['items'])
        self.now+=10
        matching=self.state(failed=True,kind='wuji_hand_2',current='1.0.4')
        matching['device'].update(kind='hand',generation='hand2')
        self.assertEqual(self.show(matching)['items'],[])
        self.assertEqual(self.show(old)['items'],[])
        self.assert_no_io()


if __name__=='__main__':unittest.main()
