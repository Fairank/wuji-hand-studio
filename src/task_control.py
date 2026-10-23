"""Private typed owner adapter. It never accepts a raw device command."""
import re

from task_sessions import context_digest,pending_teleoperation

ACTIONS=frozenset({'pause','disconnect','pair_prepare','unpair','continue_teleoperation'})


def execute(controller,expected_context,action,payload=None):
    if (not isinstance(expected_context,str) or not re.fullmatch('[0-9a-f]{64}',expected_context)
            or not isinstance(action,str) or action not in ACTIONS):
        raise ValueError('Invalid typed task operation')
    if action=='pair_prepare':
        if (not isinstance(payload,dict) or set(payload)!={'hand_serial','profile','sdk_user'}
                or not isinstance(payload.get('hand_serial'),str) or not re.fullmatch('[A-Za-z0-9_.-]{1,100}',payload['hand_serial'])
                or not isinstance(payload.get('sdk_user'),str) or not re.fullmatch('[A-Za-z0-9_.-]{0,100}',payload['sdk_user'])
                or payload.get('profile') not in ('hand1_left','hand1_right','hand2_left','hand2_right')):
            raise ValueError('Invalid server-selected pairing')
        payload=dict(payload)
    elif payload is not None and (not isinstance(payload,dict) or payload):
        raise ValueError('Unexpected task operation fields')
    if action=='disconnect':
        state=controller.snapshot()
        continuation=state.get('calibrated_teleoperation') or {}
        if continuation.get('busy') is True:
            if context_digest(state)!=expected_context:
                raise ValueError('任务设备或会话已变化，请刷新后重试 / Task device or session changed; refresh before retrying')
            if (any(not isinstance(continuation.get(key),str) or not re.fullmatch('[0-9a-f]{32}',continuation[key]) for key in ('run_id','attempt_id'))
                    or not isinstance(continuation.get('serial'),str)
                    or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,79}',continuation['serial'])):
                raise ValueError('Continuation task identity is unverified')
            # This worker owns the workflow lock until it finishes. Its exact
            # attempt cancellation must remain deliverable while it is busy;
            # Controller forwards these captured IDs to the strict cancel CAS.
            return controller._task_context_dispatch(action,payload,state)
        if pending_teleoperation(state):
            if context_digest(state)!=expected_context:
                raise ValueError('任务设备或会话已变化，请刷新后重试 / Task device or session changed; refresh before retrying')
            # The exact handover owns this workflow lock until SDK publication.
            # The destination forwards only its captured token/attempt to CAS.
            return controller._task_context_dispatch(action,payload,state)
    lock=controller.calibration_handover.workflow_lock
    if not lock.acquire(blocking=False):raise ValueError('Another device task is switching')
    try:
        state=controller.snapshot()
        if context_digest(state)!=expected_context:
            raise ValueError('任务设备或会话已变化，请刷新后重试 / Task device or session changed; refresh before retrying')
        return controller._task_context_dispatch(action,payload,state)
    finally:lock.release()
