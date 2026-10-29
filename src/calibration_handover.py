"""Explicit same-workspace native Viewer to calibration handover.

The Viewer is a real connected process. Discovery/selection never calls this
transaction. The shared file gate is deliberately released while joining the
Viewer: its reader needs that same gate to finish releasing its marker.
"""
import copy
from collections import OrderedDict
from contextlib import ExitStack
import re
import threading
import time

from cli_coordination import gate, reservation


_SERIAL=re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z')
_TOKEN=re.compile(r'[0-9a-f]{32}\Z')
_STOPS={'disconnect','hardware_stop','glove_stop','glove_disconnect','calibration_cancel',
        'calibration_terminal_input','session_close','program_stop','group_stop',
        'official_viz_stop','session_keepalive','hardware_keepalive','glove_keepalive',
        'program_keepalive','group_beat'}


def workflow_action(command):
    """Only starts/config changes share the local transition lock; stops work."""
    if not isinstance(command,dict):return False
    name=command.get('name','')
    if name in _STOPS:return False
    return (name in {'connect','parameters_sync','doctor_run','doctor_version','doctor_support',
                    'control_tools_check','runtime_install','runtime_select','bridge_config_save','retarget_binding_save',
                    'bridge_password_save','bridge_password_forget','device_profile_select',
                    'solver_install','solver_probe','hardware_start','hardware_trial','hardware_probe',
                    'program_start','program_resume','demo_start','official_cli_check',
                    'official_cli_update','official_cli_rollback','official_viz_scan',
                    'device_inventory_scan','official_viz_start'}
            or isinstance(name,str) and name.startswith(('calibration_','glove_','group_')))


class CalibrationHandover:
    def __init__(self,controller,idle_check=None):
        self.controller=controller
        self.lock=threading.RLock()
        self.workflow_lock=threading.RLock()
        self._thread=None
        self._cancel=False
        self._cancelled_requests=OrderedDict()
        self._used_requests=OrderedDict()
        self.viewer_token=None
        self.idle_check=idle_check or self._local_idle
        self.state=dict(busy=False,phase='idle',serial=None,viewer_token=None,interface=None,
                        connected=False,device_owned=False,stream_freshness='not_reported',error=None,request_id=None,run_id=None)

    @property
    def internal(self):
        return self._thread==threading.get_ident()

    def _set(self,**values):
        with self.lock:self.state.update(**values)

    def _local_idle(self):
        from studio_component import require_idle
        require_idle(self.controller.snapshot(),self.controller.doctor.snapshot())

    def request_cancel(self):
        with self.lock:
            if self.state['busy'] and not self.internal:
                self._cancel=True
                if self.state.get('request_id'):self._remember_cancel(self.state['request_id'])

    def _remember_cancel(self,request_id):
        now=time.monotonic()
        while self._cancelled_requests and (len(self._cancelled_requests)>=256 or next(iter(self._cancelled_requests.values()))<now-600):
            self._cancelled_requests.popitem(last=False)
        self._cancelled_requests[request_id]=now;self._cancelled_requests.move_to_end(request_id)

    def cancel_request(self,request_id):
        if not isinstance(request_id,str) or not _TOKEN.fullmatch(request_id):raise ValueError('Invalid calibration request identity')
        with self.lock:
            self._remember_cancel(request_id)
            if self.state.get('request_id')!=request_id:return None
            if self.state['busy']:
                self._cancel=True
                return None
            return self.state.get('run_id')

    def _intent(self,request_id):
        if request_id is not None:
            if not isinstance(request_id,str) or not _TOKEN.fullmatch(request_id):raise ValueError('Invalid calibration request identity')
            if request_id in self._cancelled_requests and self._cancelled_requests[request_id]>=time.monotonic()-600:
                raise ValueError('本次开始已取消，未启动标定 / This calibration start was cancelled')
            if request_id in self._used_requests:raise ValueError('本次开始请求已受理，请明确重新开始 / This calibration request was already accepted; explicitly start a new request')
            while len(self._used_requests)>=256:self._used_requests.popitem(last=False)
            self._used_requests[request_id]=time.monotonic()
        self.state.update(request_id=request_id,run_id=None)

    def _not_cancelled(self):
        with self.lock:
            if self._cancel:raise ValueError('切换已取消，未启动新的标定 / Handover cancelled; no new calibration was started')

    def snapshot(self):
        with self.lock:result=copy.deepcopy(self.state)
        if result['busy']:return result
        viewer=self.controller.official_viz.snapshot()
        if viewer.get('active') is True and viewer.get('phase')=='running':
            serial=(viewer.get('reported_device') or {}).get('sn')
            try:record=self.controller.official_viz.connection_record(serial,viewer.get('session_id'))
            except (ValueError,OSError,RuntimeError):return {**result,'connected':False,'device_owned':False}
            return {**result,**record,'session_id':record['viewer_token'],'phase':'connected',
                    'connected':True,'device_owned':True,'stream_freshness':'not_reported'}
        with self.controller.calibration.lock:run=copy.deepcopy(self.controller.calibration.run)
        if run.get('running') is True and run.get('serial'):
            return {**result,'phase':'calibration','serial':run['serial'],'run_id':run.get('id'),
                    'connected':False,'device_owned':True,'interface':run.get('interface'),
                    'calibration_phase':run.get('phase'),'stream_freshness':'not_reported'}
        return {**result,'connected':False,'device_owned':False,
                'phase':'error' if result.get('error') else 'disconnected'}

    def begin(self,command):
        """One explicit Start works with or without this glove's live Viewer.

        Merely selecting the calibration tab does not call this transaction.
        The browser cannot supply the internal connection/discovery receipt.
        """
        allowed={'name','serial','side','profile','replace','create_profile','interface','request_id'}
        serial=command.get('serial');side=command.get('side')
        interface=command.get('interface','guided')
        replace=command.get('replace',False);create=command.get('create_profile',False)
        if (set(command)-allowed or not isinstance(serial,str) or not _SERIAL.fullmatch(serial)
                or side not in (None,'left','right') or interface not in ('guided','terminal')
                or type(replace) is not bool or type(create) is not bool
                or create and interface!='guided'
                or 'request_id' in command and (not isinstance(command['request_id'],str) or not _TOKEN.fullmatch(command['request_id']))):
            raise ValueError('Invalid explicit calibration request')
        if not self.workflow_lock.acquire(blocking=False):
            raise ValueError('设备任务正在切换，请等待本次操作完成 / A device task transition is in progress')
        viewer=self.controller.official_viz;pauses=ExitStack();intent_owned=False
        try:
            # Publish the cancellable intent before any snapshot, metadata
            # read or wait. A Stop during those operations must not be lost.
            with self.lock:
                self._intent(command.get('request_id'))
                self._thread=threading.get_ident();self._cancel=False;self.viewer_token=None
                self.state.update(busy=True,phase='validating',serial=serial,viewer_token=None,
                    interface=interface,connected=False,device_owned=False,error=None)
                intent_owned=True
            from studio_component import enforce_session_owner
            enforce_session_owner(command)
            with gate(viewer.folder):
                current=viewer.snapshot()
                if current.get('active') is True:
                    reported=current.get('reported_device') or {}
                    if (reported.get('sn') or reported.get('serial'))!=serial:
                        raise ValueError('请先结束另一设备的画面 / Another device owns the Viewer')
                    token=current.get('session_id')
                    if not isinstance(token,str) or not _TOKEN.fullmatch(token):
                        raise ValueError('Official Viewer ownership is unverified')
                else:token=None
            self._not_cancelled()
            # The existing exact-owner handover joins its reader outside the
            # global gate. Do not wrap that join in the gate above.
            if token is not None:
                return self.start({**command,'name':'calibration_handover','viewer_token':token,
                                   'interface':interface},_continuing=True)
            # Pause only this Viewer object's finite read-only discovery. Wait
            # outside the CLI gate so its reader can release its own marker.
            priority=getattr(viewer,'discovery_priority',None)
            if priority is not None:pauses.enter_context(priority(timeout=20))
            self._not_cancelled()
            switch=getattr(self.controller,'connection_switch',None)
            if switch is not None:switch.release_for_calibration(command,cancel_check=self._not_cancelled)
            self._not_cancelled()
            with gate(viewer.folder):
                self.idle_check()
                if reservation(viewer.folder):
                    raise ValueError('请先结束已有官方会话 / Finish the active official session first')
                self._not_cancelled()
                # A stale selection is refreshed only after the final explicit
                # Start. Discovery never opens a streaming connection.
                rows=self.controller.calibration_devices(fresh=True)
                from calibration_cli import is_glove_device
                matches=[row for row in rows if isinstance(row,dict)
                         and (row.get('sn') or row.get('serial'))==serial and is_glove_device(row)]
                if len(matches)!=1:
                    raise ValueError('所选手套暂不可见，请重新查找 / The selected glove is unavailable')
                actual=str(matches[0].get('handedness') or matches[0].get('side') or '').lower().removeprefix('handedness.')
                if actual in ('left','right'):
                    if side is not None and side!=actual:raise ValueError('所选手套左右手不一致 / Selected glove side does not match')
                    side=actual
                if side not in ('left','right'):
                    raise ValueError('请确认所选手套的左右手 / Confirm the selected glove hand side')
                self._not_cancelled();self.idle_check()
                from cli_coordination import identity
                import os
                record=dict(source='explicit_calibration',serial=serial,side=side,
                            owner_pid=os.getpid(),owner_start=identity(os.getpid()),checked_at=time.time())
                self._set(phase='starting')
                state=self.controller.calibration.start(side,serial,replace,interface=interface,
                    connected=record,profile=command.get('profile'),create_profile=create,
                    cancel_check=self._not_cancelled)
                with self.lock:
                    self.state['run_id']=(state.get('run') or {}).get('id');cancelled=self._cancel
                if cancelled:
                    self._set(phase='cancelling')
                    state=self.controller.calibration.cancel(**({'expected_run':self.state['run_id']} if command.get('request_id') is not None else {}))
                else:self._set(phase='calibration',device_owned=True,run_id=(state.get('run') or {}).get('id'))
                return dict(calibration=state,calibration_connection={**self.snapshot(),'busy':False})
        except Exception as error:
            if intent_owned:self._set(phase='error',error=str(error)[:900],failed_at=time.time())
            raise
        finally:
            try:pauses.close()
            finally:
                if intent_owned:
                    with self.lock:self.state['busy']=False;self._thread=None;self.viewer_token=None
                self.workflow_lock.release()

    def start(self,command,*,_continuing=False):
        serial=command.get('serial');token=command.get('viewer_token')
        interface=command.get('interface');requested_side=command.get('side')
        profile=command.get('profile');replace=command.get('replace',False)
        create_profile=command.get('create_profile',False)
        if (not isinstance(serial,str) or not _SERIAL.fullmatch(serial)
                or not isinstance(token,str) or not _TOKEN.fullmatch(token)
                or interface not in ('guided','terminal') or requested_side not in (None,'left','right')
                or type(replace) is not bool or type(create_profile) is not bool
                or create_profile and interface!='guided'
                or 'request_id' in command and (not isinstance(command['request_id'],str) or not _TOKEN.fullmatch(command['request_id']))):
            raise ValueError('Invalid explicit calibration handover request')
        if not self.workflow_lock.acquire(blocking=False):
            raise ValueError('设备任务正在切换，请等待本次操作完成 / A device task transition is in progress')
        viewer=self.controller.official_viz;stopped=False;record=None;intent_owned=False
        try:
            with self.lock:
                if _continuing:
                    if not self.internal or not self.state['busy'] or self.state.get('serial')!=serial or self.state.get('request_id')!=command.get('request_id'):
                        raise ValueError('Calibration start intent changed')
                else:
                    self._intent(command.get('request_id'))
                    self._thread=threading.get_ident();self._cancel=False
                self.viewer_token=token
                self.state.update(busy=True,phase='validating',serial=serial,viewer_token=token,
                    interface=interface,connected=True,device_owned=True,error=None)
                intent_owned=True
            self._not_cancelled()
            from studio_component import enforce_session_owner
            enforce_session_owner(command)
            with gate(viewer.folder):
                record=viewer.connection_record(serial,token)
                self.idle_check()
                side=record['side'] or requested_side
                if side not in ('left','right'):
                    raise ValueError('请先确认本次手套的左右手 / Confirm the selected glove hand side first')
                if interface=='guided':self.controller.calibration.preflight_guided(profile,side,replace,create_profile=create_profile)
                self._not_cancelled()
                # This marks only our local transition, not a claim of new
                # SDK data or a permission to stop any other process.
                self._set(phase='stopping')
            # No controller lock or global file gate may be held here.
            viewer.stop_owned(serial,token)
            stopped=True
            self._set(phase='starting',connected=False,device_owned=False)
            with gate(viewer.folder):
                self._not_cancelled()
                if reservation(viewer.folder):
                    raise ValueError('另一个会话已接管；原画面已关闭，未开始标定 / Another session acquired the device; the previous Viewer is closed and calibration has not started')
                self.idle_check()
                self._not_cancelled()
                # Only a server-created exact record reaches start. The HTTP
                # calibration_start route never accepts this from a browser.
                state=self.controller.calibration.start(side,serial,replace,
                    interface=interface,connected=record,profile=profile,create_profile=create_profile,
                    cancel_check=self._not_cancelled)
                with self.lock:
                    self.state['run_id']=(state.get('run') or {}).get('id');cancelled=self._cancel
                if cancelled:
                    # Cancellation can race a slow profile read/process launch.
                    # Once a run has been published, cancel that exact new run
                    # instead of raising and abandoning it in the background.
                    self._set(phase='cancelling',connected=False)
                    state=self.controller.calibration.cancel(**({'expected_run':self.state['run_id']} if command.get('request_id') is not None else {}))
                    return dict(calibration=state,calibration_connection={**self.snapshot(),'busy':False})
                self._set(phase='calibration',device_owned=True,connected=False,
                          run_id=(state.get('run') or {}).get('id'))
                return dict(calibration=state,calibration_connection={**self.snapshot(),'busy':False})
        except Exception as error:
            if intent_owned:
                self._set(phase='error',error=str(error)[:900],connected=not stopped and record is not None,
                    device_owned=not stopped and record is not None,viewer_released=stopped,
                    failed_at=time.time())
            raise
        finally:
            if intent_owned:
                with self.lock:self.state['busy']=False;self._thread=None;self.viewer_token=None
            self.workflow_lock.release()
