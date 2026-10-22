"""Explicit, exact-session pairing; browsing a candidate never runs this code.

Only the existing read-feedback connection is transferred. Starting motor
following remains a separate action in the original glove control surface.
"""
import copy
import re
import threading
import time
import uuid

from task_sessions import context_digest, object_value


class TaskPairing:
    def __init__(self, read, dispatch, *, group_check=None, timeout=16, clock=time.monotonic,
                 sleep=time.sleep):
        self.read, self.dispatch = read, dispatch
        self.group_check = group_check or (lambda task: None)
        self.timeout, self.clock, self.sleep = timeout, clock, sleep
        self.lock = threading.RLock()
        self.thread = None
        self.cancel_event = threading.Event()
        self.state = dict(busy=False, phase='idle', error=None, task_id=None,
                          hand_task_id=None, released_hand=False,release_requested=False)

    def snapshot(self):
        with self.lock:return copy.deepcopy(self.state)

    def _check(self, task):
        state = self.read(task['endpoint_id'])
        if not isinstance(state, dict) or context_digest(state) != task['_context']:
            raise ValueError('设备会话已改变，请重新选择 / Device session changed; choose it again')
        self.group_check(task)
        glove=object_value(state.get('glove'));feedback=object_value(glove.get('feedback'))
        ghardware=object_value(glove.get('hardware'))
        if (state.get('unconfirmed_devices') or object_value(state.get('hardware')).get('active') is not False
                or object_value(state.get('group')).get('active')
                or object_value(state.get('program')).get('active')
                or 'active' in ghardware and ghardware['active'] is not False):
            raise ValueError('请先停止此设备的运行并确认停用 / Stop this device and confirm disable first')
        profile=object_value(state.get('device_profile'))
        if (profile.get('id')!=task['intent'].get('profile_id')
                or profile.get('side')!=task['intent'].get('side')):
            raise ValueError('Task profile or side changed')
        if task['kind']=='hand':
            identity=object_value(state.get('identified_device'))
            if (state.get('connection')!='connected' or state.get('stale') is not False
                    or state.get('device_id')!=task['devices']['hand']
                    or identity.get('source')!='sdk_identity' or identity.get('serial')!=task['devices']['hand']
                    or identity.get('profile')!=profile.get('id')):
                raise ValueError('Target hand feedback is not current and verified')
        else:
            if (glove.get('connection')!='receiving' or glove.get('glove_serial')!=task['devices']['glove']
                    or glove.get('session_id')!=task['observed'].get('session')
                    or glove.get('session_id') is None or not isinstance(task['intent'].get('sdk_user'),str)
                    or object_value(glove.get('stream')).get('fresh') is not True
                    or object_value(glove.get('user')).get('user_id')!=task['intent'].get('sdk_user')
                    or glove.get('profile') not in (None,profile.get('id'))):
                raise ValueError('Glove identity, wearer or fresh stream changed')
            if task['kind']=='glove':
                if (feedback.get('device_id') or glove.get('can_prepare') is False
                        or 'active' not in ghardware and glove.get('can_prepare') is not True):
                    raise ValueError('Glove preview is not ready for a new target')
            else:
                identity=object_value(feedback.get('identified_device'))
                if (ghardware.get('active') is not False or feedback.get('stale') is not False
                        or feedback.get('connection')!='connected' or feedback.get('device_id')!=task['devices']['hand']
                        or identity.get('source')!='sdk_identity' or identity.get('serial')!=task['devices']['hand']
                        or identity.get('profile')!=profile.get('id')):
                    raise ValueError('Paired hand feedback or disable confirmation changed')
        return state

    def start(self, source, target):
        if source['kind'] != 'glove' or target['kind'] != 'hand' or source['endpoint_id'] == target['endpoint_id']:
            raise ValueError('请选择另一只已连接且空闲的灵巧手 / Choose another connected idle hand')
        if (not isinstance(target['devices'].get('hand'),str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',target['devices']['hand'])
                or not isinstance(source['intent'].get('sdk_user'),str) or not re.fullmatch(r'[A-Za-z0-9_.-]{0,100}',source['intent']['sdk_user'])):
            raise ValueError('设备或佩戴者标识不受支持，未释放灵巧手 / Unsupported device or wearer identity; hand feedback was not released')
        if (source['intent'].get('profile_id') not in ('hand1_left','hand1_right','hand2_left','hand2_right')
                or source['intent'].get('side') not in ('left','right')
                or any(source['intent'].get(k) != target['intent'].get(k) for k in ('profile_id', 'side'))):
            raise ValueError('手套与灵巧手的型号、侧别须一致 / Glove and hand model and side must match')
        self._check(source);self._check(target)
        with self.lock:
            if self.state['busy']:raise ValueError('另一个配对正在处理中 / Another pairing is in progress')
            self.state = dict(busy=True, phase='releasing_feedback', error=None,
                              task_id=source['task_id'], hand_task_id=target['task_id'], released_hand=False,
                              release_requested=False,cancel_requested=False,run_id=uuid.uuid4().hex,
                              source_endpoint=source['endpoint_id'],source_session=source['observed'].get('session'),
                              source_profile=source['intent'].get('profile_id'),source_user=source['intent'].get('sdk_user'),
                              source_serial=source['devices'].get('glove'))
            self.cancel_event=threading.Event()
            self.thread = threading.Thread(target=self._work, args=(copy.deepcopy(source),copy.deepcopy(target),self.cancel_event),
                                           name='Explicit device pairing',daemon=True)
            self.thread.start()
        return dict(pairing=self.snapshot())

    def _set(self, **values):
        with self.lock:self.state.update(**values)

    def cancel(self, endpoint, expected_context, state):
        """Stop a pending workflow, never claim that hardware was disabled."""
        with self.lock:
            if (not self.state['busy'] or self.state.get('source_endpoint')!=endpoint
                    or self.state.get('phase') in ('paired','error','cancelled')):return False
            # The old request must describe the current owner, and that owner
            # must still be the source of this exact pending workflow. Check
            # under the job lock so a replacement start cannot slip between
            # identity validation and setting its cancellation event.
            if not isinstance(state,dict) or context_digest(state)!=expected_context:return False
            glove=object_value(state.get('glove'))
            if (glove.get('glove_serial')!=self.state.get('source_serial')
                    or glove.get('session_id')!=self.state.get('source_session')
                    or object_value(glove.get('user')).get('user_id')!=self.state.get('source_user')
                    or object_value(state.get('device_profile')).get('id')!=self.state.get('source_profile')):return False
            self.cancel_event.set();self.state.update(cancel_requested=True,phase='cancelling')
            return True

    @staticmethod
    def _checkpoint(cancel):
        if cancel.is_set():raise ValueError('配对流程已停止，请核对当前设备；不会自动重新接入 / Pairing workflow stopped; review current devices. No automatic reconnection')

    def raise_if_cancelled(self, endpoint):
        with self.lock:
            if self.state['busy'] and self.state.get('source_endpoint')==endpoint:self._checkpoint(self.cancel_event)

    def _work(self, source, target, cancel):
        try:
            self._checkpoint(cancel)
            self._check(source);self._check(target)
            self._checkpoint(cancel)
            self._set(release_requested=True)
            result = self.dispatch(target['endpoint_id'], target['_context'], 'disconnect', {})
            if isinstance(result, dict) and result.get('ok') is False:
                raise ValueError(result.get('error') or 'Hand feedback release was not confirmed')
            deadline = self.clock()+self.timeout
            while True:
                self._checkpoint(cancel)
                target_state = self.read(target['endpoint_id'])
                if not isinstance(target_state, dict):raise ValueError('Original hand controller became unavailable')
                if target_state.get('connection') == 'disconnected' and not target_state.get('device_id'):
                    if target_state.get('unconfirmed_devices') or object_value(target_state.get('hardware')).get('active') is not False:
                        raise ValueError('灵巧手停用尚未确认 / Hand disable is not confirmed')
                    break
                if target_state.get('device_id') not in (None, target['devices']['hand']):
                    raise ValueError('Target hand controller now owns a different device')
                if self.clock() >= deadline:raise ValueError('原灵巧手反馈尚未释放，请核对后重试 / Original hand feedback has not been released; review before retrying')
                self.sleep(.1)
            self._set(released_hand=True, phase='preparing_feedback')
            self._checkpoint(cancel)
            self._check(source)
            payload = dict(hand_serial=target['devices']['hand'], profile=target['intent']['profile_id'],
                           sdk_user=source['intent'].get('sdk_user'))
            self._checkpoint(cancel)
            result=self.dispatch(source['endpoint_id'], source['_context'], 'pair_prepare', payload)
            if isinstance(result,dict) and result.get('ok') is False:raise ValueError(result.get('error') or 'Pair preparation was not accepted')
            deadline = self.clock()+self.timeout
            while True:
                self._checkpoint(cancel)
                state = self.read(source['endpoint_id'])
                if not isinstance(state, dict):raise ValueError('Glove controller became unavailable')
                glove = object_value(state.get('glove'));feedback=object_value(glove.get('feedback'))
                if (glove.get('connection')!='receiving' or object_value(glove.get('stream')).get('fresh') is not True
                        or glove.get('glove_serial') != source['devices']['glove']
                        or glove.get('session_id') != source['observed']['session']
                        or object_value(glove.get('user')).get('user_id') != source['intent'].get('sdk_user')
                        or object_value(state.get('device_profile')).get('id')!=source['intent'].get('profile_id')
                        or glove.get('profile') not in (None,source['intent'].get('profile_id'))):
                    raise ValueError('Glove identity changed during pairing')
                identity = object_value(feedback.get('identified_device'))
                if (feedback.get('device_id') not in (None,target['devices']['hand'])
                        or feedback.get('connection')=='connected' and (
                            identity.get('source')!='sdk_identity' or identity.get('serial')!=target['devices']['hand']
                            or identity.get('profile')!=target['intent']['profile_id'])):
                    raise ValueError('Prepared hand identity differs from the selected target')
                if (feedback.get('connection') == 'connected' and feedback.get('device_id') == target['devices']['hand']
                        and identity.get('source') == 'sdk_identity' and identity.get('serial') == target['devices']['hand']
                        and identity.get('profile') == target['intent']['profile_id']):
                    if (object_value(glove.get('hardware')).get('active') is not False
                            or feedback.get('stale') is not False
                            or object_value(glove.get('stream')).get('fresh') is not True
                            or state.get('unconfirmed_devices')):
                        raise ValueError('Unexpected hardware state after feedback preparation')
                    # The last feedback read may finish after cancellation.
                    # Commit success under the same lock as cancel(), so the
                    # winning outcome cannot be rewritten by a late callback.
                    with self.lock:
                        self._checkpoint(cancel)
                        self.state.update(phase='paired')
                    return
                if self.clock() >= deadline:raise ValueError('配对反馈尚未确认，请查看此手套任务 / Pair feedback has not been confirmed; open this glove task')
                self.sleep(.1)
        except Exception as error:
            # Never silently reconnect the old controller or undo another task.
            self._set(phase='cancelled' if cancel.is_set() else 'error',error=str(error)[:700])
        finally:self._set(busy=False)

    def unpair(self, task):
        self._check(task)
        if task['kind'] != 'glove_pair':raise ValueError('This task does not own a verified pair')
        result=self.dispatch(task['endpoint_id'],task['_context'],'unpair',{})
        return dict(unpair=result)
