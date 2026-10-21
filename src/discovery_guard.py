"""Preflight for official host broadcast discovery alongside pure hand feedback.

Only the pinned native Windows devices command uses this exception. Viewer,
calibration, software updates and device control retain their original guards.
No device import, handshake, stream subscription or configuration is performed.
"""
from studio_component import require_idle


def require_discovery_idle(state, doctor=None):
    if not isinstance(state, dict):
        raise ValueError('Workspace state is unavailable')
    if state.get('connection') != 'connected':
        return require_idle(state, doctor)
    identity = state.get('identified_device') or {}
    profile = state.get('device_profile') or {}
    serial = state.get('device_id')
    if (not isinstance(identity, dict) or not isinstance(profile, dict)
            or not isinstance(serial, str) or not serial
            or identity.get('source') != 'sdk_identity' or identity.get('serial') != serial
            or not isinstance(profile.get('id'), str)
            or profile['id'] not in ('hand1_left', 'hand1_right', 'hand2_left', 'hand2_right')
            or identity.get('profile') != profile['id']
            or state.get('stale') is not False):
        raise ValueError('请先核对现有灵巧手的身份和反馈状态 / Verify the connected hand identity and feedback before discovery')
    # The unchanged idle check still rejects motors, programs, gloves, capture,
    # diagnostics and unconfirmed termination. Do not mutate the live snapshot.
    require_idle({**state, 'connection': 'disconnected'}, doctor)
