"""Actual worker loops publish stop facts before owned report persistence.

SDK messages and its control clock are synthetic. Motion/Recorder/worker bodies
and the bounded drain remain real; no physical or timing-performance claim.
"""
import copy
import functools
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import patch

import console_agent as agent
import first_generation as first
import hardware_showcase as motion


LEASE='b'*32
SERIAL='SYNTHETIC-REPORT-ORDER'
NIDS=[finger*5+joint+1 for finger in range(5) for joint in range(4)]


class Clock:
    def __init__(self):self.now=1.
    def monotonic(self):return self.now
    def time(self):return 1700000000+self.now
    def sleep(self,seconds):self.now+=max(0.,seconds)


class Hand:
    def __init__(self,clock,*,first=False,disable_fails=False):
        self.clock=clock;self.first=first;self.disable_fails=disable_fails
        self.serial_number=SERIAL;self.info=N(serial_number=SERIAL,firmware_version='1.2.3')
        self.q=[0.]*20;self.enabled=False;self.cap=.3;self.params=(3.,.05)
        self.calls=[];self.streams=[];self.closed=False
    def handedness(self):return N(get=lambda:'left')
    def handedness_name(self):return 'Left'
    def joints(self):return [N(index=i,nid=n) for i,n in enumerate(NIDS)]
    def subscribe(self,diagnostics=False):
        hand=self
        class Stream:
            empty=False;closed=False;seq=0
            def recv(self):
                if self.closed:return None
                self.empty=not self.empty
                if not self.empty:return None
                self.seq+=1;header=N(seq=self.seq,timestamp_us=int(hand.clock.now*1e6),frame_id='left')
                if hand.first:return N(position=hand.q[:],velocity=[0.]*20,header=header)
                if diagnostics:
                    joints=[N(nid=n,error_code_current=0,status_word=N(ext_state=2 if hand.enabled else 1,
                        ext_state_name='Enabled' if hand.enabled else 'Ready',position_limit_active=False),
                        current=0.,vbus_v_fb=24.,mcu_temp_c_fb=25.,comm_response_rate_pct=100.,comm_timeout_total=0) for n in NIDS]
                    return N(header=header,joints=joints,comm=N(age_ms=0))
                return N(header=header,num_joints=20,joints=[N(nid=n,position=hand.q[i],velocity=0.,effort=0.) for i,n in enumerate(NIDS)])
            def close(self):self.closed=True
        value=Stream();self.streams.append(value);return value
    def joint_states(self):return N(subscribe=lambda:self.subscribe())
    def joint_diagnostics(self):return N(subscribe=lambda:self.subscribe(True))
    def effort_limit(self):
        def put(value):self.cap=value
        return N(set=put,get=lambda:[self.cap]*20)
    def mit_params(self):
        def put(value):self.params=value
        return N(set=put,get=lambda:[N(kp=self.params[0],kd=self.params[1]) for _ in NIDS])
    def joint_command(self):
        hand=self
        class Publisher:
            def send(self,commands):hand.q=[v.position for v in commands]
            def close(self):hand.calls.append('publisher_close')
        return N(publish=Publisher)
    def set_all_effort_limit(self,value):self.cap=value
    def realtime_controller(self,*args):
        hand=self
        class LowPass:
            def __enter__(self):return self
            def __exit__(self,*args):hand.calls.append('lowpass_close')
            def set_target_position(self,value):hand.q=list(value)
        return LowPass()
    def enable(self,**kwargs):self.calls.append('enable');self.enabled=True
    def disable(self):
        self.calls.append('disable')
        if self.disable_fails:raise OSError('Synthetic disable is unconfirmed')
        self.enabled=False
    def disconnect(self):self.calls.append('disconnect');self.closed=True


class WorkerReportOrderTests(unittest.TestCase):
    def exercise(self,*,first_generation=False,disable_fails=False):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);root=Path(folder.name)
        clock=Clock();hand=Hand(clock,first=first_generation,disable_fails=disable_fails)
        entered=threading.Event();release=threading.Event();rows=[];failures=[];calls=[0]
        rows_lock=threading.RLock()
        def put(event):
            with rows_lock:rows.append(copy.deepcopy(event))
        def commands():
            calls[0]+=1;clock.now+=.005 if first_generation else .02
            if calls[0]>1000:raise AssertionError('Synthetic worker did not reach its report')
            if entered.is_set():return dict(name='disconnect')
            if first_generation:
                values=[dict(name='hardware_trial',lease=LEASE,action='open',amplitude=.25,speed=1.,cycles=1,workspace_clear=True),
                    dict(name='hardware_stop'),dict(name='record',label='baseline',seconds=10),dict(name='stop')]
                if disable_fails:
                    values=[dict(name='hardware_probe',lease=LEASE,index=6,direction=1,workspace_clear=True),
                        dict(name='record',label='baseline',seconds=10),dict(name='hardware_stop'),dict(name='stop')]
                if calls[0]<=len(values):return values[calls[0]-1]
                raise queue.Empty
            if calls[0]==1:return dict(name='hardware_probe',lease=LEASE,index=6,direction=1,workspace_clear=True)
            return dict(name='hardware_keepalive',lease=LEASE)
        original_open=Path.open;original_write=Path.write_text
        def hold():
            entered.set()
            if not release.wait(3):raise AssertionError('Report hold deadline exceeded')
        def write(path,*args,**kwargs):
            if path.parent==root/'motion_reports':hold()
            return original_write(path,*args,**kwargs)
        def open_file(path,*args,**kwargs):
            if path.name=='summary.pending.json':hold()
            return original_open(path,*args,**kwargs)
        sdk=N(SdkManager=N(instance=lambda:N()),DeviceType=N(),Handedness=N(Left='left',Right='right'),
            JointCommand=lambda *args,**kwargs:N(position=args[0]) if args else N(**kwargs),LowPass=lambda **kwargs:N(**kwargs),
            WujiHand2=N(describe_error=lambda code:{}))
        clock_api=N(monotonic=clock.monotonic,time=clock.time,sleep=clock.sleep,perf_counter=clock.monotonic)
        original_drain=agent.drain_available;real_recorder=agent.Recorder
        def run():
            try:
                if first_generation:first.worker_first('',N(get_nowait=commands),N(put=put),prepared_hand=hand)
                else:agent.worker('',N(get_nowait=commands),N(put=put),prepared=(hand,N(close=lambda:None)))
            except BaseException as error:failures.append(error)
        with patch.dict('os.environ',WUJI_HAND_PROFILE='hand1_left' if first_generation else 'hand2_left'), \
                patch.dict('sys.modules',wuji_sdk=sdk),patch.object(agent,'time',clock_api),patch.object(first,'time',clock_api), \
                patch.object(motion,'time',clock_api),patch.object(agent,'drain_available',lambda sub:original_drain(sub,clock=clock.monotonic)), \
                patch.object(agent,'Recorder',functools.partial(real_recorder,root=root/'capture')), \
                patch.object(agent,'__file__',str(root/'console_agent.py')),patch.object(Path,'write_text',write),patch.object(Path,'open',open_file):
            thread=threading.Thread(target=run,name='Actual worker report ordering')
            thread.start()
            try:
                self.assertTrue(entered.wait(3),str(rows[-3:]))
                with rows_lock:at_hold=copy.deepcopy(rows)
                self.assertIn('disable',hand.calls)
                expected_active=None if disable_fails else False
                terminal=[event for event in at_hold if event.get('type')=='state'
                    and event.get('device_id')==SERIAL and event['hardware'].get('handled_lease')==LEASE
                    and event['hardware'].get('active') is expected_active
                    and event['hardware'].get('stop_confirmed') is (not disable_fails)]
                self.assertTrue(terminal,'Actual stop state must precede report serialization/persistence')
                self.assertFalse(any(event['type'] in ('motion_report','report') for event in at_hold))
                held_host=terminal[-1]['latest']['host_s'];clock.now+=1.
            finally:
                release.set();thread.join(3)
            self.assertFalse(thread.is_alive());self.assertFalse(failures)
        self.assertTrue(hand.closed);self.assertTrue(all(stream.closed for stream in hand.streams))
        final=rows[-1];self.assertEqual(final['type'],'closed')
        self.assertIs(final['hardware']['active'],None if disable_fails else False)
        self.assertIs(final['hardware']['stop_confirmed'],not disable_fails)
        self.assertEqual(final['hardware']['handled_lease'],LEASE)
        self.assertTrue(any(event['type']==('report' if first_generation else 'motion_report') for event in rows))
        for event in rows[len(at_hold):]:
            if event['type']=='state' and event['latest']['host_s']==held_host:
                self.assertGreaterEqual(event['metrics']['age_ms'],1000-1e-6,'Old samples must not be stamped fresh after report I/O')

    def test_hand2_disable_ack_precedes_blocked_motion_report(self):self.exercise()
    def test_hand2_failed_disable_precedes_report_as_unknown(self):self.exercise(disable_fails=True)
    def test_hand1_disable_ack_precedes_blocked_recording_summary(self):self.exercise(first_generation=True)
    def test_hand1_failed_disable_is_not_fabricated_as_ack(self):self.exercise(first_generation=True,disable_fails=True)

    def early_identity_failure(self,first_generation):
        hand=Hand(Clock(),first=first_generation)
        hand.handedness=lambda:N(get=lambda:'right')
        hand.handedness_name=lambda:'Right'
        sdk=N(SdkManager=N(instance=lambda:N()),DeviceType=N(),Handedness=N(Left='left',Right='right'),
            JointCommand=lambda *args,**kwargs:None,LowPass=lambda **kwargs:None,WujiHand2=N())
        events=queue.Queue();commands=queue.Queue()
        with patch.dict('os.environ',WUJI_HAND_PROFILE='hand1_left' if first_generation else 'hand2_left'), \
                patch.dict('sys.modules',wuji_sdk=sdk):
            if first_generation:first.worker_first('',commands,events,prepared_hand=hand)
            else:agent.worker('',commands,events,prepared=(hand,N(close=lambda:None)))
        rows=list(events.queue)
        self.assertTrue(any(row['type']=='error' for row in rows))
        self.assertFalse(any(row['type']=='state' for row in rows))
        self.assertEqual(rows[-1]['type'],'closed')
        self.assertIsNot(rows[-1]['hardware'].get('stop_confirmed'),True)
        self.assertNotIn('enable',hand.calls);self.assertTrue(hand.closed)

    def test_hand2_early_identity_failure_does_not_emit_uninitialized_state(self):self.early_identity_failure(False)
    def test_hand1_early_identity_failure_does_not_emit_uninitialized_state(self):self.early_identity_failure(True)


if __name__=='__main__':unittest.main()
