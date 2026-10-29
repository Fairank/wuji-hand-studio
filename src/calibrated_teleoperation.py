"""One explicit continuation of a verified calibration into glove preview.

Callbacks keep workspace/fleet policy in the controller. This adapter owns no
SDK handle and never prepares a robot hand or starts motor following.
"""
import copy
import hashlib
import json
import re
import secrets
import threading

from calibration_portability import calibration_proof, file_digest
from glove_protocol import CancellationNotDispatched


class CalibratedTeleoperation:
    def __init__(self,get_calibration,start_session,stop_session,get_glove,idle_check,
                 export_bundle,*,owner_workspace='main',workflow_lock=None,
                 gate_factory=None,owner_check=None):
        self.get_calibration=get_calibration;self.start_session=start_session
        self.stop_session=stop_session;self.get_glove=get_glove
        self.idle_check=idle_check;self.export_bundle=export_bundle
        self.owner_workspace=owner_workspace;self.owner_check=owner_check or (lambda proof:None)
        self.workflow_lock=workflow_lock or threading.RLock()
        if gate_factory is None:
            from cli_coordination import gate
            gate_factory=gate
        self.gate_factory=gate_factory;self.lock=threading.RLock()
        self.thread=None;self._thread=None;self._cancel=threading.Event();self._proof=None
        self.state=dict(busy=False,phase='idle',error=None,cleanup_error=None,sdk_session=None,run_id=None,attempt_id=None,
                        connected=False,automatically_prepares_hand=False,automatically_follows=False)

    @property
    def internal(self):return self._thread==threading.get_ident()

    def _set(self,**values):
        with self.lock:self.state.update(**values)

    def _not_cancelled(self):
        if self._cancel.is_set():raise ValueError('接续已取消，未开启新的跟随 / Continuation cancelled; no following was started')

    def _current_proof(self):
        snapshot=self.get_calibration()
        if not isinstance(snapshot,dict):raise ValueError('Calibration state is unavailable')
        run=snapshot.get('run') if isinstance(snapshot,dict) else None
        # Capture the model hash once per official result. A later request must
        # still match both this result and the original captured model bytes.
        key=hashlib.sha256(json.dumps(dict(run=run,cleanup_required=snapshot.get('cleanup_required')),
            sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
        with self.lock:cached=copy.deepcopy(self._proof)
        if cached is not None and cached.get('_snapshot_key')==key:
            from pathlib import Path
            try:
                info=Path(cached['model_path']).stat()
                same=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)==cached['_model_stat']
            except OSError:same=False
            if not same:raise ValueError('保存的标定文件已改变或不可用，请重新核对 / Saved calibration changed or is unavailable; review it first')
            return cached
        proof=calibration_proof(snapshot,workspace=self.owner_workspace)
        if cached is not None and run and cached['source_run_id']==run.get('id'):
            if proof['expected_revision']!=cached['expected_revision']:
                raise ValueError('本次已保存的标定资料已改变，请核对后再继续 / Saved calibration changed; review it before continuing')
        proof['_snapshot_key']=key
        from pathlib import Path
        info=Path(proof['model_path']).stat()
        proof['_model_stat']=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
        with self.lock:self._proof=copy.deepcopy(proof)
        return proof

    def snapshot(self):
        with self.lock:state=copy.deepcopy(self.state);cancelled=self._cancel.is_set()
        proof=None;owned_session=False
        try:
            proof=self._current_proof()
            offered=dict(can_continue=not state['busy'],blocked_reason=None,
                run_id=proof['source_run_id'],expected_revision=proof['expected_revision'],
                serial=proof['serial'],side=proof['side'],profile=proof['source_profile'],model_sha256=proof['model_sha256'])
        except (ValueError,OSError,TypeError) as error:
            offered=dict(can_continue=False,blocked_reason=str(error)[:500],expected_revision=None)
        if state.get('sdk_session') is not None:
            glove=self.get_glove()
            if glove.get('session_id')==state['sdk_session']:
                if glove.get('busy'):
                    owned_session=True
                    offered['can_continue']=False
                    if glove.get('connection') in ('connecting','ready'):state['busy']=True
                receipt=glove.get('model_provenance') or {}
                if cancelled:
                    # Disconnect is asynchronous. A last matching receiving
                    # frame cannot turn a cancelled attempt back into preview.
                    state.update(busy=bool(glove.get('busy')),connected=False,
                        phase='error' if state.get('cleanup_error') and glove.get('busy') else 'cancelling' if glove.get('busy') else 'cancelled')
                    if glove.get('busy'):offered['can_continue']=False
                elif (glove.get('connection')=='receiving' and receipt.get('source_run_id')==state.get('run_id')
                        and receipt.get('model_sha256')==state.get('model_sha256')
                        and glove.get('glove_serial')==state.get('serial')):
                    state.update(busy=False,phase='preview',connected=True)
                    offered['can_continue']=False
                elif glove.get('connection')=='error':
                    state.update(busy=False,phase='error',connected=False,error=glove.get('message') or 'Controller continuation failed')
                elif not glove.get('busy'):
                    state.update(busy=False,phase='cancelled' if self._cancel.is_set() else 'disconnected',connected=False)
            else:state.update(busy=False,phase='session_changed',connected=False,error='遥操作会话已改变 / Teleoperation session changed')
        if proof is not None and state.get('run_id') not in (None,proof['source_run_id']):
            if state.get('busy') or owned_session or state.get('connected'):
                # Cancellation refers to the launched attempt. Never combine
                # its token/session with a newer offered calibration result.
                offered.update(can_continue=False,
                    blocked_reason='标定任务已改变，原接续仍在结束 / Calibration task changed; the original continuation is still closing',
                    run_id=state['run_id'],expected_revision=state.get('expected_revision'),
                    serial=state.get('serial'),side=state.get('side'),profile=state.get('profile'),model_sha256=state.get('model_sha256'))
            else:
                state.update(phase='idle',error=None,cleanup_error=None,sdk_session=None,attempt_id=None,connected=False)
        return {**state,**offered}

    def execute(self,command,*,synchronous=False):
        if (not isinstance(command,dict) or set(command)!={'run_id','expected_revision'}
                or not isinstance(command['run_id'],str) or not re.fullmatch('[0-9a-f]{32}',command['run_id'])
                or not isinstance(command['expected_revision'],str) or not re.fullmatch('[0-9a-f]{64}',command['expected_revision'])):
            raise ValueError('Invalid calibration continuation request')
        proof=self._current_proof()
        if proof['source_run_id']!=command['run_id'] or proof['expected_revision']!=command['expected_revision']:
            raise ValueError('标定任务已改变，请刷新当前任务 / Calibration task changed; refresh the current task')
        if self.snapshot().get('can_continue') is not True:
            raise ValueError('本次设备会话尚未结束 / This device session has not ended')
        with self.lock:
            if self.state['busy'] or self.thread is not None and self.thread.is_alive():
                raise ValueError('本次接续仍在处理中 / Continuation is still in progress')
            self._cancel.clear()
            self.state.update(busy=True,phase='validating',error=None,cleanup_error=None,sdk_session=None,connected=False,
                run_id=proof['source_run_id'],serial=proof['serial'],side=proof['side'],profile=proof['source_profile'],
                model_sha256=proof['model_sha256'],expected_revision=proof['expected_revision'],attempt_id=secrets.token_hex(16))
            if not synchronous:
                try:
                    self.thread=threading.Thread(target=self._work,args=(proof,),name='Calibrated glove continuation',daemon=True)
                    self.thread.start()
                except Exception as error:
                    # No worker/session was launched. Reconcile only this new
                    # local intent; never disconnect an existing SDK owner.
                    self.thread=None
                    self.state.update(busy=False,phase='error',error=str(error)[:700],connected=False)
                    raise
        if synchronous:self._work(proof)
        return self.snapshot()

    def _work(self,proof):
        acquired=False
        try:
            # The private task adapter publishes this intent while holding the
            # same workflow lock. Let that bounded dispatch return and release
            # it, then recheck every owner/proof/idle condition below.
            acquired=self.workflow_lock.acquire(timeout=1.)
            if not acquired:raise ValueError('另一个设备任务正在切换 / Another device task is switching')
            self._thread=threading.get_ident()
            with self.gate_factory():
                self.idle_check();self.owner_check(proof);self._not_cancelled()
                current=self._current_proof()
                if current['expected_revision']!=proof['expected_revision']:
                    raise ValueError('Calibration task changed before continuation')
                if file_digest(proof['model_path'])!=(proof['model_sha256'],proof['model_bytes']):
                    raise ValueError('Saved calibration model changed before continuation')
                self._set(phase='exporting')
                private={key:value for key,value in proof.items() if not key.startswith('_')}
                bundle=self.export_bundle(private,cancel_check=self._not_cancelled)
                self._not_cancelled();self.idle_check();self.owner_check(proof)
                if self._current_proof()['expected_revision']!=proof['expected_revision']:
                    raise ValueError('Calibration task changed during export')
                self._set(phase='connecting')
                # Idle/owner/proof validation may wait on another software
                # callback. A cancellation received there must still prevent
                # opening a new controller session when validation returns.
                self._not_cancelled()
                session=self.start_session(bundle)
                if type(session) is not int:raise ValueError('Controller did not return an exact session identity')
                self._set(sdk_session=session)
                if self._cancel.is_set():
                    self.stop_session(session);self._set(phase='cancelling')
        except Exception as error:
            self._set(phase='cancelled' if self._cancel.is_set() else 'error',error=str(error)[:700],connected=False)
        finally:
            with self.lock:self.state['busy']=False;self._thread=None
            if acquired:self.workflow_lock.release()

    def cancel(self,expected=None):
        finished='接续已完成或会话已变化，请在遥操作中暂停并断开 / Continuation finished or session changed; pause and disconnect in teleoperation'
        with self.lock:
            if expected is not None:
                if (not isinstance(expected,dict) or set(expected)!={'run_id','attempt_id'}
                        or any(not isinstance(expected[k],str) or not re.fullmatch('[0-9a-f]{32}',expected[k]) for k in expected)
                        or any(expected[k]!=self.state.get(k) for k in expected)):
                    raise ValueError('接续任务已改变，请核对当前任务 / Continuation attempt changed; review the current task')
            captured=(self.state.get('run_id'),self.state.get('attempt_id'),self.state['sdk_session'])
            worker_busy=self.state['busy']
        if expected is not None:
            # Never call the bridge/controller callback while holding this
            # service lock. The destination repeats this stage check atomically
            # with the actual disconnect under its own lock.
            if captured[2] is None:
                if not worker_busy:raise ValueError(finished)
            else:
                glove=self.get_glove()
                if (not isinstance(glove,dict) or glove.get('session_id')!=captured[2]
                        or glove.get('busy') is not True or glove.get('connection') not in ('connecting','ready')
                        or (glove.get('hardware') or {}).get('active',False) is not False
                        or (glove.get('feedback') or {}).get('device_id')):
                    raise ValueError(finished)
        with self.lock:
            if expected is not None:
                if captured!=(self.state.get('run_id'),self.state.get('attempt_id'),self.state['sdk_session']):
                    raise ValueError('接续任务已改变，请核对当前任务 / Continuation attempt changed; review the current task')
                if captured[2] is None and not self.state['busy']:raise ValueError(finished)
        if expected is not None and captured[2] is not None:
            # A pending-only destination can reject after the read above if
            # this exact session has already become preview/following. Do not
            # publish a cancellation flag before it accepts: the worker could
            # otherwise observe that flag and issue a trusted cleanup itself.
            try:self.stop_session(captured[2],pending_only=True)
            except CancellationNotDispatched:
                raise
            except Exception as error:
                with self.lock:
                    if captured==(self.state.get('run_id'),self.state.get('attempt_id'),self.state.get('sdk_session')):
                        self._cancel.set()
                        reason='接续退出尚未确认 / Continuation cleanup is unconfirmed: '+str(error)[:500]
                        self.state.update(phase='error',connected=False,error=reason,cleanup_error=reason)
                raise
            with self.lock:
                if captured==(self.state.get('run_id'),self.state.get('attempt_id'),self.state.get('sdk_session')):
                    self._cancel.set()
                    self.state.update(phase='cancelling',connected=False,error=None,cleanup_error=None)
            return self.snapshot()
        with self.lock:
            if expected is not None:
                if captured!=(self.state.get('run_id'),self.state.get('attempt_id'),self.state['sdk_session']):
                    raise ValueError('接续任务已改变，请核对当前任务 / Continuation attempt changed; review the current task')
                if not self.state['busy']:raise ValueError(finished)
            self._cancel.set()
            session=self.state['sdk_session'];busy=self.state['busy']
            # Publish under the same identity check. A slow exact-session stop
            # must not overwrite the state of a later retry when it returns.
            run_id=self.state.get('run_id');attempt_id=self.state.get('attempt_id')
            self.state.update(phase='cancelling' if busy or session is not None else 'cancelled',connected=False,error=None,cleanup_error=None)
        if session is not None:
            try:
                self.stop_session(session)
            except Exception as error:
                with self.lock:
                    if (self.state.get('run_id'),self.state.get('attempt_id'),self.state.get('sdk_session'))==(run_id,attempt_id,session):
                        reason='接续退出尚未确认 / Continuation cleanup is unconfirmed: '+str(error)[:500]
                        self.state.update(phase='error',connected=False,error=reason,cleanup_error=reason)
                raise
        return self.snapshot()

    def cancel_and_wait(self,timeout=5.):
        self.cancel()
        with self.lock:thread=self.thread
        if thread is not None and thread is not threading.current_thread():thread.join(timeout)
        return not bool(thread and thread.is_alive())
