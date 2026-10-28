"""Private typed owner adapter. It never accepts a raw device command."""
import re
from contextlib import nullcontext

from task_sessions import context_digest,pending_teleoperation,hand_playback,hand_group_active

ACTIONS=frozenset({'pause','resume','stop','disconnect','pair_prepare','unpair','continue_teleoperation'})


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
        initial=controller.snapshot()
        if context_digest(initial)!=expected_context:
            raise ValueError('任务设备或会话已变化，请刷新后重试 / Task device or session changed; refresh before retrying')
        dispatch_action=action;needs_gate=False
        if action in ('pause','resume'):
            glove=initial.get('glove') or {}
            if hand_group_active(initial):
                raise ValueError('Use the ensemble pause/resume control')
            controls=hand_playback(initial)
            if glove.get('busy') or not (controls['can_pause'] or controls['can_resume']):
                if action=='resume':raise ValueError('This playback control is unavailable; refresh the task')
                # A glove task pause means stop following. Unknown/pending
                # hand ownership also keeps its legacy safety-stop route.
                # Freeze that intent before locking: a late start ACK cannot
                # turn this gate-free operation into a reversible pause.
                dispatch_action='stop'
            else:needs_gate=True
        # Serialize the final playback identity check with start/stop. A late
        # task request must never control a replacement run on the same hand.
        owner_lock=getattr(controller,'lock',None) if action in ('pause','resume','stop') else None
        from cli_coordination import gate
        # Normal commands acquire CLI gate before owner lock. Keep that order,
        # while stop retains its gate-free route during component maintenance.
        with gate() if needs_gate else nullcontext():
            with owner_lock if owner_lock is not None else nullcontext():
                state=controller.snapshot()
                if context_digest(state)!=expected_context:
                    raise ValueError('任务设备或会话已变化，请刷新后重试 / Task device or session changed; refresh before retrying')
                return controller._task_context_dispatch(dispatch_action,payload,state)
    finally:lock.release()
