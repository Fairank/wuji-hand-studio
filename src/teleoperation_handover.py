"""Explicit owned native Viewer -> existing SDK teleoperation controller.

This opens only the original controller discovery/user-selection session. It
never opens a glove, prepares a hand, enables motors, or starts following.
"""
import copy
import re
import secrets
import threading
import time

from cli_coordination import gate,reservation
from glove_protocol import CancellationNotDispatched


class TeleoperationHandover:
    def __init__(self,controller,idle_check=None):
        self.controller=controller
        self.lock=threading.RLock()
        self.workflow_lock=controller.calibration_handover.workflow_lock
        self.idle_check=idle_check or self._local_idle
        self._thread=None;self._cancel=False;self.viewer_token=None
        self.state=dict(busy=False,phase='idle',serial=None,viewer_token=None,attempt_id=None,side=None,
            viewer_released=False,sdk_session=None,runtime=None,error=None,cleanup_error=None,
            automatically_opens_glove=False,automatically_prepares_hand=False,automatically_follows=False)

    @property
    def internal(self):return self._thread==threading.get_ident()

    def _local_idle(self):
        from studio_component import require_idle
        require_idle(self.controller.snapshot(),self.controller.doctor.snapshot())

    def _set(self,**values):
        with self.lock:self.state.update(**values)

    def request_cancel(self):
        with self.lock:
            if self.state['busy'] and not self.internal:self._cancel=True

    def _not_cancelled(self):
        with self.lock:
            if self._cancel:raise ValueError('遥操作切换已取消，未打开手套 / Teleoperation handover cancelled; no glove was opened')

    def _runtime(self):
        # Configuration and local runtime status only: no SDK or device access.
        from bridge_config import load_config
        config=load_config();mode=config.get('mode')
        if mode in ('wsl','macvm'):
            from managed_runtime import status
            state=status()
            if state.get('busy'):raise ValueError('请等待控制环境安装完成 / Wait for controller installation')
            if state.get('ready') is not True:
                raise ValueError('请先在设置中准备遥操作控制环境 / Prepare the teleoperation controller in Settings first')
        elif mode in ('local','ssh'):
            if not config.get('agent_directory') or not config.get('python'):
                raise ValueError('请先配置遥操作控制端 / Configure the teleoperation controller first')
            if mode=='ssh' and not all(config.get(k) for k in ('host','username')):
                raise ValueError('请先配置Linux控制端地址和用户 / Configure the Linux controller host and user first')
        else:raise ValueError('Unknown teleoperation controller')
        return dict(mode=mode,label='Windows managed Linux' if mode=='wsl' else mode,
            sdk_profiles='controller_local',windows_profiles_migrated=False)

    def snapshot(self):
        with self.lock:result=copy.deepcopy(self.state)
        if result['busy'] or result.get('sdk_session') is None:return result
        glove=self.controller.glove
        with glove.lock:
            if glove.session!=result['sdk_session']:
                return {**result,'phase':'session_changed','error':'遥操作会话已改变 / Teleoperation session changed'}
            state=glove.snapshot()
        connection=state.get('connection')
        if state.get('busy') and glove.stopping:phase='cancelling'
        elif connection=='ready' and state.get('busy'):
            phase='select_user'
        elif connection=='receiving' and state.get('busy'):
            phase='preview' if not (state.get('feedback') or {}).get('device_id') else 'hand_feedback'
        elif connection=='connecting' and state.get('busy'):phase='starting'
        elif connection=='error':phase='error'
        elif state.get('busy'):phase='cancelling' if glove.stopping else 'starting'
        else:phase='disconnected'
        return {**result,'phase':phase,'error':(state.get('message') if phase=='error' else None) or result.get('error'),
            'glove_connected':connection=='receiving','stream_fresh':(state.get('stream') or {}).get('fresh') is True}

    def cancel(self,expected=None):
        finished='遥操作切换已完成或会话已变化，请在原会话中暂停或断开 / Teleoperation handover finished or session changed; pause or disconnect its owning session'
        with self.lock:
            if expected is not None:
                if (not isinstance(expected,dict) or set(expected)!={'viewer_token','attempt_id'}
                        or any(not isinstance(expected[k],str) or not re.fullmatch('[0-9a-f]{32}',expected[k]) for k in expected)
                        or any(expected[k]!=self.state.get(k) for k in expected)):
                    raise ValueError('遥操作切换任务已改变，请核对当前任务 / Teleoperation handover attempt changed; review the current task')
            elif self.state['busy'] and not self.internal:self._cancel=True
            captured=(self.state.get('viewer_token'),self.state.get('attempt_id'),self.state.get('sdk_session'))
        if expected is not None and captured[2] is not None:
            # The bridge owns the final stage/session check. In particular, a
            # late cancel cannot become a disconnect of a completed preview.
            glove=self.controller.glove.snapshot()
            if (glove.get('session_id')!=captured[2] or glove.get('busy') is not True
                    or glove.get('connection') not in ('connecting','ready')
                    or (glove.get('hardware') or {}).get('active',False) is not False
                    or (glove.get('feedback') or {}).get('device_id')):
                raise ValueError(finished)
            with self.lock:
                if captured!=(self.state.get('viewer_token'),self.state.get('attempt_id'),self.state.get('sdk_session')):
                    raise ValueError('遥操作切换任务已改变 / Teleoperation handover attempt changed')
            try:self.controller.glove.disconnect_session(captured[2],pending_only=True)
            except CancellationNotDispatched:raise
            except Exception as error:
                with self.lock:
                    if captured==(self.state.get('viewer_token'),self.state.get('attempt_id'),self.state.get('sdk_session')):
                        self._cancel=True
                        reason='遥操作切换退出尚未确认 / Teleoperation handover cleanup is unconfirmed: '+str(error)[:500]
                        self.state.update(phase='error',error=reason,cleanup_error=reason)
                raise
            with self.lock:
                if captured==(self.state.get('viewer_token'),self.state.get('attempt_id'),self.state.get('sdk_session')):
                    self._cancel=True;self.state.update(phase='cancelling',error=None)
            return dict(teleoperation_connection=self.snapshot(),glove=self.controller.glove.snapshot())
        with self.lock:
            if expected is not None:
                if captured!=(self.state.get('viewer_token'),self.state.get('attempt_id'),self.state.get('sdk_session')):
                    raise ValueError('遥操作切换任务已改变 / Teleoperation handover attempt changed')
                if not self.state['busy']:raise ValueError(finished)
                self._cancel=True;self.state.update(phase='cancelling',error=None)
            session=captured[2]
        # A stop during Viewer teardown is sticky; the launch path checks it.
        # After publication only the exact newly started SDK session is closed.
        if session is not None:
            self.controller.glove.disconnect_session(session)
            with self.lock:
                if captured==(self.state.get('viewer_token'),self.state.get('attempt_id'),self.state.get('sdk_session')):
                    self.state['phase']='cancelling'
        return dict(teleoperation_connection=self.snapshot(),glove=self.controller.glove.snapshot())

    def start(self,command):
        if (not isinstance(command,dict) or set(command)-{'name','serial','viewer_token'}
                or not isinstance(command.get('serial'),str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',command['serial'])
                or not isinstance(command.get('viewer_token'),str)
                or not re.fullmatch(r'[0-9a-f]{32}',command['viewer_token'])):
            raise ValueError('Invalid explicit teleoperation handover')
        serial=command['serial'];token=command['viewer_token']
        if not self.workflow_lock.acquire(blocking=False):
            raise ValueError('设备任务正在切换，请稍候 / A device task transition is in progress')
        stopped=False;viewer=self.controller.official_viz
        try:
            with self.lock:
                self._thread=threading.get_ident();self._cancel=False;self.viewer_token=token
                self.state.update(busy=True,phase='validating',serial=serial,viewer_token=token,attempt_id=secrets.token_hex(16),
                    side=None,viewer_released=False,sdk_session=None,runtime=None,error=None,cleanup_error=None)
            from studio_component import enforce_session_owner
            # Use the existing glove ownership gate as well as our exact Viewer.
            enforce_session_owner(dict(name='glove_scan'))
            with gate(viewer.folder):
                record=viewer.connection_record(serial,token)
                self.idle_check()
                runtime=self._runtime()
                profile=self.controller.state['device_profile']
                if record.get('side') not in ('left','right') or record['side']!=profile.get('side'):
                    raise ValueError('手套左右与遥操作目标不符，请先确认工作区型号 / Glove side differs from the teleoperation target; review the workspace profile')
                self._not_cancelled();self._set(phase='stopping',side=record['side'],runtime=runtime)
            # Reader cleanup may need the shared gate and controller lock.
            viewer.stop_owned(serial,token)
            stopped=True;self._set(phase='starting',viewer_released=True)
            with gate(viewer.folder):
                self._not_cancelled()
                if reservation(viewer.folder):
                    raise ValueError('设备已由另一会话接管；未启动遥操作 / Another session acquired ownership; teleoperation was not started')
                self.idle_check();self._not_cancelled()
                # Reuse the original bridge and original protocol. User/source
                # selection, glove open, hand preparation and follow stay explicit.
                self.controller.action(dict(name='glove_scan'))
                with self.controller.glove.lock:session=self.controller.glove.session
                self._set(sdk_session=session)
                with self.lock:cancelled=self._cancel
                if cancelled:
                    self.controller.glove.disconnect_session(session)
                    self._set(phase='cancelling')
                else:self._set(phase='select_user')
            return dict(teleoperation_connection={**self.snapshot(),'busy':False},glove=self.controller.glove.snapshot())
        except Exception as error:
            self._set(phase='error',viewer_released=stopped,error=str(error)[:900],failed_at=time.time())
            raise
        finally:
            with self.lock:self.state['busy']=False;self._thread=None;self.viewer_token=None
            self.workflow_lock.release()
