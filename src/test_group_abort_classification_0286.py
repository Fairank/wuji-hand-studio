"""Public group cleanup remains a stop across the existing shared gates.

Controllers, participants, transition/file gates and reader ACK handling are real.
Only hand status transport and the explicitly labelled maintenance marker are
synthetic. No SDK, firmware process, network or device is started.
"""
from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import cli_coordination
from calibration_handover import workflow_action
from console_server import Controller
import desktop_lifecycle
import test_group_abort_owner_0284 as status_fixture
import test_group_member_renewal_0285 as member_fixture


class GroupAbortClassificationTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.folder=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ,WUJI_OFFICIAL_CLI_DATA=str(self.folder/'cli')))
        self.stack.enter_context(patch('managed_runtime.registered',return_value=None))
        self.stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('No actual network')))
        self.stack.enter_context(patch.object(subprocess,'Popen',side_effect=AssertionError('No actual process')))
        self.controllers=[];self.transports=[];self.addCleanup(self.cleanup)

    def cleanup(self):
        for c in self.controllers:
            with c.lock:
                if c.group.active:c.group.stop('Owned test cleanup')
            if c.group.watch:c.group.watch.join(3);self.assertFalse(c.group.watch.is_alive())
        for transport in self.transports:transport.close()
        for c in self.controllers:
            for record in c.feedback_sessions.values():
                record['thread'].join(3);self.assertFalse(record['thread'].is_alive())

    def wait(self,predicate,message):
        deadline=time.monotonic()+3
        while not predicate():
            if time.monotonic()>=deadline:self.fail(message)
            time.sleep(.001)

    def controller(self,hardware=False):
        transport=status_fixture.Transport('SYNTHETIC-ABORT-CLASSIFICATION')
        self.transports.append(transport)
        c=Controller(factory=lambda:transport,reports=self.folder/str(len(self.controllers))/'reports')
        self.controllers.append(c)
        c.action(dict(name='device_profile_select',profile='hand2_left'))
        if hardware:
            c.action(dict(name='connect',serial=transport.serial))
            self.wait(lambda:c.state['connection']=='connected','Synthetic reader connection')
        return c,transport

    def prepare(self,c,token,mode='preview'):
        return c.action(dict(name='group_prepare',token=token,mode=mode,kind='pair',
            action='pair_wave',speed=1,cycles=1,amplitude=.5,workspace_clear=True))

    def arm(self,c,token):
        c.action(dict(name='group_arm',token=token))
        self.wait(lambda:c.state['hardware'].get('active') is True,'Synthetic start ACK')

    def test_abort_is_stop_but_prepare_arm_commit_and_controls_remain_transitions(self):
        for name in ('group_abort','group_stop','group_beat'):
            with self.subTest(name=name):self.assertFalse(workflow_action(dict(name=name)))
        for name in ('group_prepare','group_arm','group_commit','group_pause','group_resume'):
            with self.subTest(name=name):self.assertTrue(workflow_action(dict(name=name)))

    def test_old_abort_cannot_take_transition_lock_or_block_replacement_commit(self):
        for mode in ('preview','hardware'):
            with self.subTest(mode=mode):
                c,transport=self.controller(mode=='hardware');a='a'*32;b='b'*32
                self.prepare(c,a,mode);c.action(dict(name='group_abort',token=a));self.prepare(c,b,mode)
                if mode=='hardware':self.arm(c,b)
                c.action(dict(name='group_commit',token=b,start_s=time.monotonic()+1.5))
                identity=(c.generation,c.state.get('device_id'),c.hardware_lease,c.player.run_id)
                entered=threading.Event();release=threading.Event();done=threading.Event();beat_stop=threading.Event()
                original=c._workflow_action;old={};boundary={}
                def held(command):
                    if command.get('name')=='group_abort' and command.get('token')==a:
                        boundary['workflow_owned']=c.calibration_handover.workflow_lock._is_owned()
                        entered.set()
                        if not release.wait(3):raise AssertionError('Owned old-abort barrier')
                    return original(command)
                def old_abort():
                    try:c.action(dict(name='group_abort',token=a))
                    except BaseException as error:old['error']=str(error)
                    finally:done.set()
                def heartbeat():
                    while not beat_stop.wait(.02):
                        c.action(dict(name='group_beat',token=b))
                c._workflow_action=held
                thread=threading.Thread(target=old_abort);beat=threading.Thread(target=heartbeat)
                thread.start();beat.start()
                try:
                    self.assertTrue(entered.wait(2))
                    error=None
                    try:c.action(dict(name='group_commit',token=b,start_s=time.monotonic()+1.5))
                    except ValueError as caught:error=str(caught)
                    self.assertIsNone(error,f'Old abort boundary {boundary} blocked current commit: {error}')
                    self.assertIs(boundary['workflow_owned'],False)
                    commands=copy.deepcopy(transport.sent)
                    release.set();thread.join(3)
                    self.assertTrue(done.is_set());self.assertEqual(old.get('error'),'Expired group token')
                    self.assertEqual(transport.sent,commands,'Old abort sent a command to the replacement hand')
                    self.assertEqual((c.generation,c.state.get('device_id'),c.hardware_lease,c.player.run_id),identity)
                    self.assertTrue(c.group.active);self.assertEqual(c.group.token,b)
                finally:
                    release.set();beat_stop.set();thread.join(3);beat.join(3);c._workflow_action=original
                    self.assertFalse(thread.is_alive());self.assertFalse(beat.is_alive())

    def test_current_abort_bypasses_owned_transition_but_new_prepare_stays_blocked(self):
        c,_=self.controller();token='a'*32;self.prepare(c,token)
        entered=threading.Event();release=threading.Event()
        def hold():
            with c.calibration_handover.workflow_lock:
                entered.set();release.wait(3)
        thread=threading.Thread(target=hold);thread.start()
        try:
            self.assertTrue(entered.wait(2))
            c.action(dict(name='group_abort',token=token))
            self.assertFalse(c.group.active)
            with self.assertRaisesRegex(ValueError,'transition is in progress'):self.prepare(c,'b'*32)
            self.assertEqual(c.group.token,token)
        finally:release.set();thread.join(3);self.assertFalse(thread.is_alive())

    def test_abort_does_not_wait_for_a_component_owned_file_gate(self):
        c,_=self.controller();token='a'*32;self.prepare(c,token)
        entered=threading.Event();release=threading.Event();done=threading.Event();errors=[]
        def hold():
            with cli_coordination.gate():entered.set();release.wait(3)
        def abort():
            try:c.action(dict(name='group_abort',token=token))
            except BaseException as error:errors.append(str(error))
            finally:done.set()
        holder=threading.Thread(target=hold);request=threading.Thread(target=abort);holder.start()
        try:
            self.assertTrue(entered.wait(2));request.start()
            self.assertTrue(done.wait(.2),'Scoped group abort waited for the component file gate')
            self.assertEqual(errors,[]);self.assertFalse(c.group.active)
        finally:
            release.set();holder.join(3)
            if request.ident is not None:request.join(3);self.assertFalse(request.is_alive())
            self.assertFalse(holder.is_alive())

    def test_public_close_allows_exact_idempotent_abort_but_not_new_prepare(self):
        c,_=self.controller();token='a'*32;self.prepare(c,token)
        # This stop-only test has no official wearer catalog. Preserve an
        # existing fresh unavailable DTO so inactive calibration.cancel does
        # not perform an unrelated first-time CLI metadata/download lookup.
        with c.calibration.lock:
            c.calibration.cache=dict(available=False,calibration_supported=False,mode=None,
                users=[],current=None,devices=[],error=None,metadata_checked_at=None)
            c.calibration.cached_at=time.monotonic()
        entered=threading.Event();release=threading.Event();errors=[]
        actual=desktop_lifecycle.close_sessions
        def held(*args,**kwargs):
            entered.set()
            if not release.wait(3):raise AssertionError('Owned session-close barrier')
            return actual(*args,**kwargs)
        def close():
            try:c.action(dict(name='session_close'))
            except BaseException as error:errors.append(str(error))
        with patch.object(desktop_lifecycle,'close_sessions',side_effect=held):
            thread=threading.Thread(target=close);thread.start()
            try:
                self.assertTrue(entered.wait(2));self.assertTrue(c.desktop_closing);self.assertFalse(c.group.active)
                c.action(dict(name='group_abort',token=token))
                with self.assertRaisesRegex(ValueError,'Session is closing'):self.prepare(c,'b'*32)
                self.assertEqual(c.group.token,token)
            finally:release.set();thread.join(3);self.assertFalse(thread.is_alive())
        self.assertEqual(errors,[])

    def test_maintenance_marker_allows_readonly_release_without_forging_hardware_ack(self):
        c,transport=self.controller(True);token='a'*32;self.prepare(c,token,'hardware')
        hardware=copy.deepcopy(c.state['hardware']);journal=c.termination.snapshot();commands=copy.deepcopy(transport.sent)
        # Reserved-phase marker only: this does not claim FirmwareHost can flash
        # concurrently with active hardware, and no firmware child is started.
        with cli_coordination.gate():owner=cli_coordination.reserve('firmware',metadata={'writer_stage':'reserved'})
        marker=cli_coordination.root_folder()/'session.json';before=marker.read_bytes()
        try:
            result=c.action(dict(name='group_abort',token=token))['group']
            self.assertEqual(result,dict(stopping=True,token=token,mode='hardware',read_only_released=True))
            self.assertEqual(transport.sent,commands);self.assertEqual(c.state['hardware'],hardware)
            self.assertEqual(c.termination.snapshot(),journal)
            with self.assertRaisesRegex(ValueError,'maintenance is in progress'):self.prepare(c,'b'*32,'hardware')
            self.assertEqual(marker.read_bytes(),before)
        finally:cli_coordination.release(owner)

    def test_current_armed_abort_still_requires_real_reader_stop_ack_under_marker(self):
        c,transport=self.controller(True);token='a'*32;self.prepare(c,token,'hardware');self.arm(c,token)
        lease=c.hardware_lease;before=transport.disable_receipts
        with cli_coordination.gate():owner=cli_coordination.reserve('firmware',metadata={'writer_stage':'reserved'})
        try:
            self.assertTrue(c.termination.snapshot())
            c.action(dict(name='group_abort',token=token))
            self.wait(lambda:c.hardware_lease is None and c.state['hardware'].get('stop_confirmed') is True,
                'Exact synthetic worker disable ACK and public stopped state')
            self.assertEqual(transport.disable_receipts,before+1);self.assertIsNotNone(lease)
            self.assertIs(c.state['hardware']['stop_confirmed'],True);self.assertEqual(c.termination.snapshot(),[])
            with self.assertRaisesRegex(ValueError,'maintenance is in progress'):self.prepare(c,'b'*32,'hardware')
        finally:cli_coordination.release(owner)

    def test_unknown_maintenance_marker_is_preserved_and_still_blocks_new_starts(self):
        c,_=self.controller();token='a'*32;self.prepare(c,token)
        folder=cli_coordination.root_folder();folder.mkdir(parents=True,exist_ok=True)
        marker=folder/'session.json';original=b'{not a reservation';marker.write_bytes(original)
        try:
            c.action(dict(name='group_abort',token=token));self.assertFalse(c.group.active)
            with self.assertRaisesRegex(ValueError,'maintenance is in progress'):self.prepare(c,'b'*32)
            self.assertEqual(marker.read_bytes(),original)
        finally:marker.unlink()

    def test_synthetic_feedback_never_reverses_its_own_stop_ack(self):
        # A real SDK worker serializes its own state. The synthetic producer
        # must not derive motor state from the desktop's not-yet-consumed ACK.
        helper=member_fixture.MemberRenewalTests()
        with helper.members() as fixture:
            self.assertTrue(fixture['held'].wait(3))
            c=fixture['controllers']['0'];transport=fixture['transports']['0']
            self.wait(lambda:c.state['hardware'].get('active') is True,'Original synthetic arm')
            ack=threading.Event();refreshed=threading.Event();after_ack=[];actual=transport.emit
            def observed(event):
                result=actual(event)
                if event.get('type')=='state':
                    hardware=event['hardware']
                    if ack.is_set():
                        after_ack.append((hardware['active'],hardware['stop_confirmed']))
                        refreshed.set()
                    elif hardware['active'] is False and hardware['stop_confirmed'] is True:ack.set()
                return result
            transport.emit=observed
            try:
                with c.lock:
                    c.action(dict(name='group_abort',token=fixture['token']))
                    self.assertTrue(ack.is_set())
                    self.assertIsNotNone(c.hardware_lease,'Reader ACK is deliberately pending behind this lock')
                    self.assertTrue(refreshed.wait(.4),'No synthetic frame after the stop ACK')
                    self.assertTrue(after_ack)
                    self.assertTrue(all(active is False and confirmed is True for active,confirmed in after_ack),
                        f'Synthetic vendor reversed its own stop ACK: {after_ack}')
            finally:transport.emit=actual

    def test_synthetic_refresh_keeps_unknown_until_its_own_explicit_ack(self):
        transport=member_fixture.Status('SYNTHETIC-UNKNOWN-REFRESH','left')
        transport.ready(active=True);transport.ready(active=None,confirmed=False);transport.refresh()
        events=[json.loads(transport.lines.get_nowait().removeprefix('WUJI_JSON:')) for _ in range(3)]
        self.assertIs(events[-1]['hardware']['active'],None)
        self.assertIs(events[-1]['hardware']['stop_confirmed'],False)
        transport.ready(active=False,confirmed=True);transport.refresh()
        events=[json.loads(transport.lines.get_nowait().removeprefix('WUJI_JSON:')) for _ in range(2)]
        self.assertIs(events[-1]['hardware']['active'],False)
        self.assertIs(events[-1]['hardware']['stop_confirmed'],True)
        transport.close()


if __name__=='__main__':unittest.main()
