"""Real participant leases survive concurrent response holds, never hardware I/O."""
from contextlib import contextmanager
import copy
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from console_server import Controller
from group_coordinator import GroupCoordinator
import test_group_abort_owner_0284 as status_fixture


class Status(status_fixture.Transport):
    def __init__(self,serial,side):
        super().__init__(serial);self.side=side
        self.status_lock=threading.RLock();self.last_hardware=(False,False)

    def exec_command(self,command,timeout=None):
        stream,output,errors=super().exec_command(command,timeout);lock=self.status_lock
        class Input:
            def write(self,text):
                with lock:return stream.write(text)
            def flush(self):
                with lock:return stream.flush()
        return Input(),output,errors

    def ready(self,*,active=False,confirmed=False):
        with self.status_lock:return super().ready(active=active,confirmed=confirmed)

    def refresh(self):
        # Repeat the synthetic SDK's last emitted state, not the desktop's
        # lease before its reader has consumed a newly emitted stop ACK.
        with self.status_lock:self.ready(active=self.last_hardware[0],confirmed=self.last_hardware[1])

    def emit(self,event):
        with self.status_lock:
            event=copy.deepcopy(event)
            if event.get('type')=='identity':event['profile']='hand2_'+self.side
            if event.get('type')=='state':
                hardware=event['hardware'];active=hardware['active']
                self.last_hardware=(active,hardware['stop_confirmed'])
                token=next((row.get('group_sync',{}).get('token') for row in reversed(self.sent)
                            if row.get('name')=='hardware_trial'),None)
                if token:
                    hardware['group_sync']=dict(token=token,mode='hardware',phase='ready' if active else 'completed',
                        active=active,stop_confirmed=hardware['stop_confirmed'],completed=not active,
                        timing=dict(entry_s=1,body_s=1,exit_s=1,hold_s=.2),clock_s=time.monotonic(),clock_id='synthetic-renewal')
            return super().emit(event)


class MemberRenewalTests(unittest.TestCase):
    def wait(self,predicate,message,timeout=4):
        deadline=time.monotonic()+timeout
        while not predicate():
            if time.monotonic()>deadline:self.fail(message)
            time.sleep(.005)

    @contextmanager
    def members(self,*,count=2,mode='hardware',hold='group_arm',hold_all=False,hold_before=False,
                post_hook=None,read_hook=None):
        controllers={};transports={};calls=[];release=threading.Event();held=threading.Event()
        stop=threading.Event();ui_stop=threading.Event();lock=threading.Lock();held_ids=set();ui=[];reads=[]
        coordinator=None;feeder=None;heartbeat=None
        with tempfile.TemporaryDirectory() as folder,patch('managed_runtime.registered',return_value=None):
            try:
                for index in range(count):
                    ident=str(index);side='left' if index%2==0 else 'right'
                    transport=Status('SYNTHETIC-RENEWAL-'+ident,side);transports[ident]=transport
                    c=Controller(factory=lambda t=transport:t,reports=Path(folder)/ident/'reports');controllers[ident]=c
                    c.action(dict(name='device_profile_select',profile='hand2_'+side))
                    if mode=='hardware':
                        c.action(dict(name='connect',serial=transport.serial))
                        self.wait(lambda:c.state['connection']=='connected','Synthetic feedback connection failed')
                def feed():
                    while not stop.wait(.02):
                        for ident,transport in transports.items():
                            transport.refresh()
                if mode=='hardware':feeder=threading.Thread(target=feed);feeder.start()
                def hold_response(ident,command):
                    should_hold=command['name']==hold and (hold_all or ident==str(count-1))
                    if should_hold:
                        with lock:
                            held_ids.add(ident)
                            if len(held_ids)==(count if hold_all else 1):held.set()
                        if not release.wait(5):raise AssertionError('Synthetic response hold watchdog')
                def post(ident,command):
                    if hold_before:hold_response(ident,command)
                    result=controllers[ident].action(command)
                    with lock:
                        calls.append((ident,copy.deepcopy(command),time.monotonic()))
                    if post_hook:post_hook(ident,command,result)
                    if not hold_before:hold_response(ident,command)
                    return result
                def read(ident):
                    result=controllers[ident].snapshot()
                    with lock:reads.append((ident,threading.current_thread().name,time.monotonic()))
                    if read_hook:read_hook(ident,result)
                    return result
                coordinator=GroupCoordinator(read,post)
                spec=dict(members=list(controllers),mode=mode,kind='pair' if count==2 else 'same',
                    action='pair_wave' if count==2 else 'wave',speed=1,cycles=1,amplitude=.5,workspace_clear=True)
                # Resolve the existing same-action catalog rather than inventing an eight-hand routine.
                if count!=2:
                    from bimanual_program import catalog
                    spec['action']=next(row['id'] for row in catalog() if row['kind']=='same')
                token=coordinator.start(spec)['group_lease']
                def ui_heartbeat():
                    while not ui_stop.wait(.03):
                        try:coordinator.beat(token);ui.append(time.monotonic())
                        except ValueError:return
                heartbeat=threading.Thread(target=ui_heartbeat);heartbeat.start()
                yield dict(coordinator=coordinator,controllers=controllers,transports=transports,calls=calls,
                    release=release,held=held,stop=stop,ui_stop=ui_stop,ui=ui,token=token,lock=lock,reads=reads,spec=spec)
            finally:
                release.set()
                if coordinator:
                    coordinator.stop()
                    if coordinator.thread:coordinator.thread.join(8)
                    self.assertFalse(coordinator.thread and coordinator.thread.is_alive(),'Coordinator did not finish')
                    coordinator.pool.shutdown(wait=True)
                for c in controllers.values():
                    if c.group.active:c.action(dict(name='group_abort',token=c.group.token))
                stop.set();ui_stop.set()
                for thread in (heartbeat,feeder):
                    if thread:thread.join(2);self.assertFalse(thread.is_alive())
                for c in controllers.values():
                    if c.group.watch:c.group.watch.join(2)
                for transport in transports.values():transport.close()
                for c in controllers.values():
                    for row in c.feedback_sessions.values():row['thread'].join(2);self.assertFalse(row['thread'].is_alive())

    def test_successful_arm_response_hold_keeps_both_exact_reservations_alive(self):
        with self.members() as fixture:
            self.assertTrue(fixture['held'].wait(3),'Arm did not apply before the response hold')
            time.sleep(1.05)  # Beyond the unchanged real participant .8s watchdog, while UI stays fresh.
            self.assertTrue(all(c.group.active for c in fixture['controllers'].values()),
                'A successful sibling response hold expired an existing reservation')
            fixture['release'].set()
            self.wait(lambda:len([row for row in fixture['calls'] if row[1]['name']=='group_commit'])==2,
                'Held startup did not reach commit')
            fixture['coordinator'].stop();fixture['coordinator'].thread.join(4)
            self.assertEqual(fixture['coordinator'].snapshot()['stop_unconfirmed'],[])

    def assert_response_hold(self,*,mode='hardware',hold='group_prepare',count=2,hold_all=False):
        with self.members(mode=mode,hold=hold,count=count,hold_all=hold_all) as fixture:
            self.assertTrue(fixture['held'].wait(4),'Response hold was not reached')
            start=time.monotonic();time.sleep(1.05)
            self.assertTrue(all(c.group.active for c in fixture['controllers'].values()))
            beats={ident for ident,command,stamp in fixture['calls'] if command['name']=='group_beat' and stamp>=start}
            self.assertEqual(beats,set(fixture['controllers']))
            fixture['release'].set()
            self.wait(lambda:len([row for row in fixture['calls'] if row[1]['name']=='group_commit'])==count,'No common commit')
            fixture['coordinator'].stop();fixture['coordinator'].thread.join(4)
            self.assertEqual(fixture['coordinator'].snapshot()['stop_unconfirmed'],[])
            self.assertTrue(all(not thread.is_alive() for _,thread in fixture['coordinator']._member_threads))

    def test_applied_prepare_can_be_observed_before_its_response_returns(self):
        for mode in ('hardware','preview'):
            with self.subTest(mode=mode):self.assert_response_hold(mode=mode,hold='group_prepare')

    def test_preview_arm_and_both_commit_response_holds_keep_the_original_leases(self):
        self.assert_response_hold(mode='preview',hold='group_arm')
        for mode in ('hardware','preview'):
            with self.subTest(mode=mode):self.assert_response_hold(mode=mode,hold='group_commit')

    def test_eight_occupied_arm_workers_cannot_starve_independent_renewals(self):
        self.assert_response_hold(count=8,hold='group_arm',hold_all=True)

    def test_prepare_not_yet_applied_never_receives_a_phantom_heartbeat(self):
        with self.members(hold='group_prepare',hold_before=True) as fixture:
            self.assertTrue(fixture['held'].wait(3));time.sleep(.35)
            self.assertIsNone(fixture['controllers']['1'].group.token)
            self.assertFalse(any(ident=='1' and command['name']=='group_beat' for ident,command,_ in fixture['calls']))
            self.assertTrue(fixture['controllers']['0'].group.active)
            fixture['release'].set()
            self.wait(lambda:len([row for row in fixture['calls'] if row[1]['name']=='group_commit'])==2,'Delayed prepare did not recover')

    def test_delayed_prepare_observation_requires_typed_live_reservation(self):
        for changed in (dict(active=1),dict(mode='preview'),dict(cleanup_required=True),dict(phase='failed')):
            with self.subTest(changed=changed):
                observed=threading.Event()
                def read(ident,result):
                    if ident=='1' and threading.current_thread().name=='Group member renewal':
                        result['group'].update(changed);observed.set()
                with self.members(hold='group_prepare',read_hook=read) as fixture:
                    self.assertTrue(observed.wait(3));time.sleep(.02);fixture['release'].set()
                    fixture['coordinator'].thread.join(4)
                    result=fixture['coordinator'].snapshot()
                    self.assertEqual(result['phase'],'failed');self.assertIn('reservation is unavailable',result['reason'])
                    self.assertFalse(any(ident=='1' and command['name']=='group_beat' for ident,command,_ in fixture['calls']))
                    self.assertEqual(result['stop_unconfirmed'],[])

    def test_cancel_during_held_arm_stops_new_renewals_and_still_aborts_all(self):
        with self.members() as fixture:
            self.assertTrue(fixture['held'].wait(3))
            coordinator=fixture['coordinator'];coordinator.stop();time.sleep(.25)
            before=len([row for row in fixture['calls'] if row[1]['name']=='group_beat'])
            time.sleep(.2)
            self.assertEqual(before,len([row for row in fixture['calls'] if row[1]['name']=='group_beat']))
            fixture['release'].set();coordinator.thread.join(4)
            self.assertEqual(coordinator.snapshot()['phase'],'stopped')
            self.assertEqual(coordinator.snapshot()['stop_unconfirmed'],[])
            self.assertEqual({ident for ident,command,_ in fixture['calls'] if command['name']=='group_abort'},{'0','1'})

    def test_ui_expiry_is_not_hidden_by_a_live_member_renewal_lane(self):
        with self.members() as fixture:
            self.assertTrue(fixture['held'].wait(3));fixture['ui_stop'].set()
            cutoff=fixture['coordinator'].heartbeat+.8
            time.sleep(1.05);fixture['release'].set();fixture['coordinator'].thread.join(4)
            result=fixture['coordinator'].snapshot()
            self.assertEqual(result['phase'],'failed');self.assertIn('UI lease expired',result['reason'])
            self.assertTrue(all(stamp<=cutoff+.04 for _,command,stamp in fixture['calls'] if command['name']=='group_beat'))
            self.assertEqual(result['stop_unconfirmed'],[])

    def test_known_member_heartbeat_failure_is_reported_after_all_member_cleanup(self):
        failed=threading.Event()
        def post(ident,command,_):
            if ident=='1' and command['name']=='group_beat':failed.set();raise OSError('Scheduled beat receipt lost')
        with self.members(post_hook=post) as fixture:
            self.assertTrue(failed.wait(3));fixture['release'].set();fixture['coordinator'].thread.join(4)
            result=fixture['coordinator'].snapshot()
            self.assertEqual(result['phase'],'failed');self.assertIn('Scheduled beat receipt lost',result['reason'])
            self.assertEqual({ident for ident,command,_ in fixture['calls'] if command['name']=='group_abort'},{'0','1'})

    def test_steady_read_response_hold_does_not_block_either_member_lane(self):
        held=threading.Event();release=threading.Event();committed=threading.Event()
        def post(_,command,__):
            if command['name']=='group_commit':committed.set()
        def read(ident,_):
            if ident=='1' and committed.is_set() and threading.current_thread().name!='Group member renewal':
                held.set()
                if not release.wait(4):raise AssertionError('Steady read watchdog')
        try:
            with self.members(hold=None,post_hook=post,read_hook=read) as fixture:
                self.assertTrue(held.wait(3));time.sleep(1.05)
                self.assertTrue(all(c.group.active for c in fixture['controllers'].values()))
                release.set();fixture['coordinator'].stop();fixture['coordinator'].thread.join(4)
                self.assertEqual(fixture['coordinator'].snapshot()['stop_unconfirmed'],[])
        finally:release.set()

    def test_actual_preview_pause_response_hold_uses_the_same_renewal_source(self):
        with self.members(mode='preview',hold='group_pause') as fixture:
            coordinator=fixture['coordinator']
            self.wait(lambda:coordinator.snapshot().get('can_pause') is True,'Preview never reached playing')
            coordinator.request_control('pause',fixture['token'])
            self.assertTrue(fixture['held'].wait(3));time.sleep(1.05)
            self.assertTrue(all(c.group.active for c in fixture['controllers'].values()))
            coordinator.stop();fixture['release'].set();coordinator.thread.join(4)
            self.assertEqual(coordinator.snapshot()['stop_unconfirmed'],[])

    def test_one_held_beat_response_does_not_create_a_sibling_barrier(self):
        entered=threading.Event();release=threading.Event()
        def post(ident,command,_):
            if ident=='0' and command['name']=='group_beat' and not entered.is_set():
                entered.set()
                if not release.wait(3):raise AssertionError('Beat response watchdog')
        try:
            with self.members(hold=None,post_hook=post) as fixture:
                self.assertTrue(entered.wait(3));start=time.monotonic();time.sleep(.35)
                self.assertTrue(any(ident=='1' and command['name']=='group_beat' and stamp>=start
                    for ident,command,stamp in fixture['calls']))
                self.assertTrue(all(c.group.active for c in fixture['controllers'].values()))
                release.set()
        finally:release.set()

    def test_bounded_join_keeps_live_lanes_and_refuses_new_start_until_they_exit(self):
        entered=threading.Event();release=threading.Event();seen=set();lock=threading.Lock()
        def post(ident,command,_):
            if command['name']=='group_beat':
                with lock:
                    first=ident not in seen;seen.add(ident)
                    if len(seen)==2:entered.set()
                if first and not release.wait(4):raise AssertionError('Held beat watchdog')
        try:
            with self.members(hold=None,post_hook=post) as fixture:
                coordinator=fixture['coordinator'];self.assertTrue(entered.wait(3));old=list(coordinator._member_threads)
                with patch('group_coordinator._MEMBER_RENEWAL_EXIT_WAIT',.03):
                    coordinator.stop();coordinator.thread.join(2)
                self.assertFalse(coordinator.thread.is_alive())
                self.assertEqual(set(coordinator.snapshot()['stop_unconfirmed']),{'0','1'})
                before=len(fixture['calls']);token=coordinator.token
                with self.assertRaisesRegex(ValueError,'renewal cleanup'):coordinator.start(fixture['spec'])
                self.assertEqual(coordinator.token,token);self.assertEqual(len(fixture['calls']),before)
                release.set()
                self.wait(lambda:all(not thread.is_alive() for _,thread in old),'Old exact lanes never exited')
                retry=coordinator.start(fixture['spec'])['group_lease'];self.assertNotEqual(retry,token)
                coordinator.stop();coordinator.thread.join(4)
                self.assertEqual(coordinator.snapshot()['phase'],'stopped')
        finally:release.set()

    def test_late_prepared_snapshot_after_cancel_cannot_dispatch_a_beat(self):
        entered=threading.Event();release=threading.Event()
        def read(ident,result):
            if ident=='1' and threading.current_thread().name=='Group member renewal' and result.get('group',{}).get('active') is True:
                entered.set()
                if not release.wait(4):raise AssertionError('Late read watchdog')
        try:
            with self.members(hold='group_prepare',read_hook=read) as fixture:
                self.assertTrue(entered.wait(3));coordinator=fixture['coordinator'];coordinator.stop();fixture['release'].set()
                with patch('group_coordinator._MEMBER_RENEWAL_EXIT_WAIT',.03):coordinator.thread.join(2)
                self.assertFalse(coordinator.thread.is_alive())
                release.set();self.wait(lambda:all(not thread.is_alive() for _,thread in coordinator._member_threads),'Late read lane remained alive')
                self.assertFalse(any(ident=='1' and command['name']=='group_beat' for ident,command,_ in fixture['calls']))
        finally:release.set()

    def test_join_failure_does_not_skip_exact_abort_or_forge_stop_confirmation(self):
        with self.members(hold=None) as fixture:
            coordinator=fixture['coordinator']
            self.wait(lambda:sum(row[1]['name']=='group_arm' for row in fixture['calls'])==2,'No real arm')
            member,thread=coordinator._member_threads[0]
            with patch.object(thread,'join',side_effect=RuntimeError('Scheduled join failure')):
                coordinator.stop();coordinator.thread.join(4)
            thread.join(2)
            result=coordinator.snapshot();self.assertIn(member,result['stop_unconfirmed'])
            self.assertIn('renewal cleanup unconfirmed',result['reason'])
            self.assertEqual({ident for ident,command,_ in fixture['calls'] if command['name']=='group_abort'},{'0','1'})
            self.assertTrue(all(c.state['hardware']['stop_confirmed'] is True for c in fixture['controllers'].values()))

    def test_late_run_finally_cannot_replace_the_new_token_state_or_control_request(self):
        with self.members() as fixture:
            self.assertTrue(fixture['held'].wait(3));coordinator=fixture['coordinator']
            replacement=dict(active=True,phase='replacement',reason='new owner',members=[])
            request=dict(action='pause',epoch=99)
            with coordinator.lock:
                coordinator.token='b'*32;coordinator.state=copy.deepcopy(replacement);coordinator.control_request=copy.deepcopy(request)
                coordinator.heartbeat=time.monotonic()
            fixture['release'].set();coordinator.thread.join(4)
            self.assertEqual(coordinator.snapshot(),replacement);self.assertEqual(coordinator.control_request,request)
            with self.assertRaisesRegex(ValueError,'Expired group lease'):
                coordinator.dispatch_control(['0','1'],fixture['token'],[])
            self.assertEqual(coordinator.control_request,request)

    def test_old_lanes_and_abort_cannot_touch_a_real_replacement_reservation(self):
        stop=threading.Event();thread=None
        try:
            with self.members() as fixture:
                self.assertTrue(fixture['held'].wait(3));new='c'*32;leases={}
                self.wait(lambda:sum(command['name']=='group_arm' for _,command,_ in fixture['calls'])==2,'Original arms did not apply')
                fixture['coordinator'].stop()
                self.wait(lambda:all(not lane.is_alive() for _,lane in fixture['coordinator']._member_threads),'Cancelled lanes did not exit')
                for ident,c in fixture['controllers'].items():
                    c.action(dict(name='group_abort',token=fixture['token']))
                    self.wait(lambda:not c.hardware_lease and c.state['hardware'].get('active') is False,'Original lease never stopped')
                    c.action(dict(fixture['spec'],name='group_prepare',token=new))
                    c.action(dict(name='group_arm',token=new));leases[ident]=c.hardware_lease
                    self.wait(lambda:c.state['hardware'].get('active') is True,'Replacement arm was not acknowledged')
                before={ident:t.disable_receipts for ident,t in fixture['transports'].items()}
                def renew_replacement():
                    while not stop.wait(.03):
                        try:
                            for c in fixture['controllers'].values():c.action(dict(name='group_beat',token=new))
                        except ValueError:return  # Exact cleanup may finish before this QA heartbeat exits.
                thread=threading.Thread(target=renew_replacement);thread.start()
                fixture['release'].set();fixture['coordinator'].thread.join(4)
                self.assertFalse(fixture['coordinator'].thread.is_alive())
                for ident,c in fixture['controllers'].items():
                    self.assertTrue(c.group.active);self.assertEqual(c.group.token,new)
                    self.assertEqual(c.hardware_lease,leases[ident]);self.assertIs(c.state['hardware']['active'],True)
                    self.assertEqual(fixture['transports'][ident].disable_receipts,before[ident])
                stop.set();thread.join(2)
        finally:
            stop.set()
            if thread:thread.join(2)

    def test_cancel_or_thread_failure_before_prepare_has_no_participant_side_effect(self):
        real_thread=threading.Thread
        for failure in ('cancel','ui','start','partial_start'):
            with self.subTest(failure=failure):
                calls=[];coordinator=GroupCoordinator(lambda _:self.fail('No read expected'),lambda *args:calls.append(args))
                coordinator.token='a'*32;coordinator.heartbeat=time.monotonic()
                coordinator.state=dict(active=True,phase='preparing',members=[dict(id=i,phase='preparing') for i in ('0','1')])
                spec=dict(members=['0','1'],mode='hardware')
                made=[]
                def make_thread(*args,**kwargs):
                    thread=real_thread(*args,**kwargs);start=thread.start
                    made.append(thread)
                    def begin():
                        if failure=='start' or failure=='partial_start' and len(made)>1:
                            raise RuntimeError('Scheduled renewal thread startup failure')
                        start()
                        if failure=='cancel':coordinator.stop()
                        if failure=='ui':coordinator.heartbeat=time.monotonic()-1.
                    thread.start=begin;return thread
                try:
                    with patch('group_coordinator.threading.Thread',side_effect=make_thread):coordinator.run(spec,coordinator.token)
                    self.assertEqual(calls,[]);self.assertEqual(coordinator.snapshot()['stop_unconfirmed'],[])
                    self.assertTrue(all(not thread.is_alive() for _,thread in coordinator._member_threads))
                finally:coordinator.pool.shutdown(wait=True)


if __name__=='__main__':unittest.main()
