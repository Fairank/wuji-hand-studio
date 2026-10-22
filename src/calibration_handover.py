"""Explicit same-workspace native Viewer to calibration handover.

The Viewer is a real connected process. Discovery/selection never calls this
transaction. The shared file gate is deliberately released while joining the
Viewer: its reader needs that same gate to finish releasing its marker.
"""
import copy
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
                    'control_tools_check','runtime_install','runtime_select','bridge_config_save',
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
        self.viewer_token=None
        self.idle_check=idle_check or self._local_idle
        self.state=dict(busy=False,phase='idle',serial=None,viewer_token=None,interface=None,
                        connected=False,device_owned=False,stream_freshness='not_reported',error=None)

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
            if self.state['busy'] and not self.internal:self._cancel=True

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

    def start(self,command):
        serial=command.get('serial');token=command.get('viewer_token')
        interface=command.get('interface');requested_side=command.get('side')
        profile=command.get('profile');replace=command.get('replace',False)
        create_profile=command.get('create_profile',False)
        if (not isinstance(serial,str) or not _SERIAL.fullmatch(serial)
                or not isinstance(token,str) or not _TOKEN.fullmatch(token)
                or interface not in ('guided','terminal') or requested_side not in (None,'left','right')
                or type(replace) is not bool or type(create_profile) is not bool
                or create_profile and interface!='guided'):
            raise ValueError('Invalid explicit calibration handover request')
        if not self.workflow_lock.acquire(blocking=False):
            raise ValueError('设备任务正在切换，请等待本次操作完成 / A device task transition is in progress')
        viewer=self.controller.official_viz;stopped=False;record=None
        try:
            with self.lock:
                self._thread=threading.get_ident();self._cancel=False;self.viewer_token=token
                self.state.update(busy=True,phase='validating',serial=serial,viewer_token=token,
                    interface=interface,connected=True,device_owned=True,error=None)
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
                with self.lock:cancelled=self._cancel
                if cancelled:
                    # Cancellation can race a slow profile read/process launch.
                    # Once a run has been published, cancel that exact new run
                    # instead of raising and abandoning it in the background.
                    self._set(phase='cancelling',connected=False)
                    state=self.controller.calibration.cancel()
                    return dict(calibration=state,calibration_connection={**self.snapshot(),'busy':False})
                self._set(phase='calibration',device_owned=True,connected=False,
                          run_id=(state.get('run') or {}).get('id'))
                return dict(calibration=state,calibration_connection={**self.snapshot(),'busy':False})
        except Exception as error:
            self._set(phase='error',error=str(error)[:900],connected=not stopped and record is not None,
                device_owned=not stopped and record is not None,viewer_released=stopped,
                failed_at=time.time())
            raise
        finally:
            with self.lock:self.state['busy']=False;self._thread=None;self.viewer_token=None
            self.workflow_lock.release()
