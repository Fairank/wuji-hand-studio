"""Upgrade readback facts retire old reminders; all device/CLI I/O is synthetic."""
import copy,json,unittest
from types import SimpleNamespace as N
from unittest.mock import Mock

from update_notifications import UpdateNotifications

SN='WG1KA00000000000'
OTHER='WG1KA00000000001'


class UpgradeReadbackNotificationTests(unittest.TestCase):
    def setUp(self):
        self.now=1000.;self.firmware={};self.states=[self.connected()]
        self.app=N(snapshot=lambda:dict(available=False))
        self.cli=N(snapshot=lambda:dict(current={},update={}))
        self.sdk=N(snapshot=lambda:dict(phase='unchecked'))
        self.monitor=UpdateNotifications(states=lambda:copy.deepcopy(self.states),app=lambda:self.app,
            cli=lambda:self.cli,sdk=lambda:self.sdk,firmware=lambda:N(snapshot=lambda:copy.deepcopy(self.firmware)),
            devices=lambda:[],idle=Mock(),clock=lambda:self.now)
        self.firmware=self.catalog('1.0.3','1.0.4')
        self.assertEqual(len(self.monitor.snapshot()['items']),1)
        self.now+=1

    @staticmethod
    def connected():
        return dict(glove=dict(connection='receiving',busy=True,session_id=1,glove_serial=SN,
            device_version=dict(serial=SN,firmware_version='1.0.3',source='sdk_handle_info')))

    def catalog(self,current,latest,stamp=None):
        row=dict(sn=SN,device_type='wuji_glove',current_version=current,latest_version=latest,
                 status='up to date' if current==latest else 'update available',error=None)
        return dict(device=dict(sn=SN,kind='glove',generation='glove1',device_type='wuji_glove'),
            check_report=dict(returncode=0,stdout=json.dumps(dict(devices=[row])),stderr=''),
            notification=None,checked_at=self.now if stamp is None else stamp)

    def readback(self):
        resources={path:dict(value=value,report=dict(exited=True,returncode=0,
            stdout=json.dumps(dict(path=path,value=value)),stderr=''))
            for path,value in (('serial_number',SN),('firmware_version','1.0.4'))}
        return dict(phase='verified',busy=False,locked=False,recovery_required=False,error=None,
            mode='official_file_confirmation',operation='upgrade',operation_generation=1,
            device=dict(sn=SN,kind='glove',generation='glove1',device_type='wuji_glove'),
            current_version='1.0.4',target_version='1.0.4',checked_at=self.now,finished_at=self.now,
            result=dict(returncode=0,sn=SN,verdict='target_version_verified',firmware_version='1.0.4',
                expected_version='1.0.4',version_verified=True,source='official_resource_readback'),
            verification_report=dict(source='official_resource_readback',sn=SN,firmware_version='1.0.4',resources=resources))

    def show(self,state):
        self.firmware=copy.deepcopy(state);return self.monitor.snapshot()

    @staticmethod
    def known(snapshot):return next(r for r in snapshot['devices'] if r['serial']==SN)

    def test_exact_successful_readback_retires_old_offer_without_inventing_a_catalog(self):
        state=self.readback();result=self.show(state)
        self.assertEqual(result['items'],[])
        self.assertEqual(self.known(result)['firmware_version'],'1.0.4')
        self.assertEqual(self.known(result)['firmware_version_source'],'official_resource_readback')
        self.assertFalse(any(k.startswith('_') for k in self.known(result)))
        self.assertEqual(self.firmware,state)
        for _ in range(3):
            self.now+=1;self.assertEqual(self.show(state)['items'],[])
            self.assertEqual(self.known(self.monitor.snapshot())['firmware_version'],'1.0.4')

    def test_actual_catalog_package_writer_and_readback_then_viewer_does_not_offer_installed_target(self):
        from test_firmware_direct_route_0255 import DirectHarness
        h=DirectHarness(self);h.now=self.now;h.inventory['observed_at']=self.now
        prepared=h.prepared();completed=h.install(prepared['package']['token'])
        self.assertEqual(completed['phase'],'verified');self.assertEqual(len(h.spawn_calls),1)
        self.assertEqual(len(h.resource_calls),2);self.assertEqual(h.marker,{})
        self.states.clear();self.show(completed)
        self.states.append(dict(official_viz=dict(active=True,phase='running',session_id='c'*32,
            reported_device=dict(sn=SN,handedness='right'),device=dict(serial=SN,device_type='wuji_glove'))))
        result=self.monitor.snapshot()
        self.assertEqual(result['items'],[])
        self.assertEqual(self.known(result)['firmware_version'],'1.0.4')

    def test_failure_unknown_mismatch_or_incomplete_receipt_never_promotes_version(self):
        changes=[lambda s:s.update(phase='failed'),lambda s:s.update(phase='unknown',locked=True,recovery_required=True),
            lambda s:s.update(busy=True),lambda s:s.update(error='Unconfirmed'),
            lambda s:s.update(operation='check'),lambda s:s.update(mode='official_confirmation'),
            lambda s:s['device'].update(sn=OTHER),lambda s:s['device'].update(kind='hand'),
            lambda s:s['result'].update(sn=OTHER),lambda s:s['result'].update(returncode=1),
            lambda s:s['result'].update(returncode=True),lambda s:s['result'].update(version_verified=False),
            lambda s:s['result'].update(verdict='official_process_exited'),lambda s:s['result'].update(source='package_manifest'),
            lambda s:s['result'].update(expected_version='1.0.5'),lambda s:s.update(target_version='1.0.5'),
            lambda s:s.update(current_version='1.0.5'),lambda s:s['verification_report'].update(sn=OTHER),
            lambda s:s['verification_report'].update(source='package_manifest'),
            lambda s:s['verification_report'].update(firmware_version='1.0.5'),
            lambda s:s['verification_report'].update(resources={}),lambda s:s.update(checked_at=self.now+1),
            lambda s:s.update(checked_at=self.now-86400),lambda s:s.update(checked_at=True)]
        for change in changes:
            with self.subTest(change=change):
                state=self.readback();change(state);result=self.show(state)
                self.assertEqual(self.known(result)['firmware_version'],'1.0.3')
                self.assertEqual(len(result['items']),1)

    def test_each_resource_requires_exact_success_exit_and_strict_raw_value(self):
        mutations=[lambda r:r.update(value='wrong'),lambda r:r['report'].update(exited=False),
            lambda r:r['report'].update(returncode=True),lambda r:r['report'].update(returncode=1),
            lambda r:r['report'].update(stdout='{}'),
            lambda r:r['report'].update(stdout='{"value":"1.0.4","value":"1.0.4"}'),
            lambda r:r['report'].update(stdout=json.dumps(dict(path='wrong',value=r['value'])))]
        for path in ('serial_number','firmware_version'):
            for change in mutations:
                with self.subTest(path=path,change=change):
                    state=self.readback();change(state['verification_report']['resources'][path]);result=self.show(state)
                    self.assertEqual(self.known(result)['firmware_version'],'1.0.3')

    def test_late_older_or_equal_catalog_response_cannot_roll_back_the_readback_fact(self):
        self.show(self.readback())
        for stamp in (self.now-1,self.now):
            result=self.show(self.catalog('1.0.3','1.0.4',stamp))
            self.assertEqual(result['items'],[])
            self.assertEqual(self.known(result)['firmware_version'],'1.0.4')
        self.now+=1;result=self.show(self.catalog('1.0.4','1.0.5'))
        self.assertEqual([(r['current_version'],r['version']) for r in result['items']],[('1.0.4','1.0.5')])

    def test_old_readback_cannot_replace_a_later_verified_catalog_or_another_device(self):
        old=self.readback();self.now+=1;self.show(self.catalog('1.0.5','1.0.6'))
        result=self.show(old)
        self.assertEqual(self.known(result)['firmware_version'],'1.0.5')
        self.assertEqual([(r['current_version'],r['version']) for r in result['items']],[('1.0.5','1.0.6')])

    def test_later_failed_catalog_keeps_readback_history_and_never_claims_up_to_date(self):
        self.show(self.readback());self.now+=1
        from device_versions import observe_official_check_failure
        state=self.catalog('1.0.5','1.0.6');row=dict(sn=SN,device_type='wuji_glove',current_version='1.0.5',latest_version=None,status='failed',error='synthetic offline')
        state['check_report']=dict(returncode=1,stdout=json.dumps(dict(devices=[row])),stderr='')
        state.update(current_version='1.0.5',check_observation=observe_official_check_failure(state['check_report']['stdout'],SN,'wuji_glove'))
        result=self.show(state)
        self.assertEqual(result['items'],[])
        self.assertEqual(self.known(result)['firmware_version'],'1.0.4')
        self.assertEqual(self.known(result)['firmware_version_source'],'official_resource_readback')

    def test_glove_readback_watermark_never_rejects_another_explicit_device_type(self):
        self.show(self.readback());self.monitor._remember(SN,'hand')
        self.states=[dict(connection='connected',device_id=SN,session_generation=2,
            identified_device=dict(source='sdk_identity',serial=SN,profile='hand2_right'),
            device_profile=dict(id='hand2_right',generation='hand2'))]
        state=self.catalog('2.0.0','2.1.0')
        state['device'].update(kind='hand',generation='hand2',device_type='wuji_hand_2')
        raw=json.loads(state['check_report']['stdout']);raw['devices'][0]['device_type']='wuji_hand_2'
        state['check_report']['stdout']=json.dumps(raw)
        result=self.show(state)
        self.assertEqual([(r['current_version'],r['version']) for r in result['items']],[('2.0.0','2.1.0')])


if __name__=='__main__':unittest.main()
