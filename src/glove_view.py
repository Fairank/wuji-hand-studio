"""Renderer input; distinguish intended glove targets from measured hand pose."""
import math
import hashlib
import json
import struct


def channel_ids(generation):
    if generation=='hand1':return tuple(range(20))
    if generation=='hand2':return tuple(f*5+j+1 for f in range(5) for j in range(4))
    return None


def target_context(glove):
    stream=glove.get('stream') or {};solver=stream.get('solver') or {}
    scope=(glove.get('profile'),glove.get('session_id'),glove.get('glove_serial'),
        (glove.get('user') or {}).get('user_id'),stream.get('retarget'),solver.get('engine'),
        solver.get('values_sha256'),(glove.get('model_provenance') or {}).get('model_sha256'))
    return hashlib.sha256(json.dumps(scope,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def target_still_current(state,frame):
    """A newer SDK sample may follow this frame, but cannot change its owner."""
    try:
        if state.get('view_source')!='glove':return False
        selected=state.get('device_profile') or {};glove=state.get('glove') or {}
        if glove.get('profile')!=selected.get('id'):return False
        q,mode,current=pose(glove,selected.get('generation'),state.get('teleoperation_view_mode','automatic'))
        current=current.get('target_frame') or {}
        if (q is None or mode!='glove_preview' or current.get('source_context_sha256')!=frame.get('source_context_sha256')
                or type(frame.get('source_seq')) is not int or current['source_seq']<frame['source_seq']):return False
        return current['source_seq']!=frame['source_seq'] or current['source_target_sha256']==frame.get('source_target_sha256')
    except (ValueError,TypeError,KeyError,AttributeError):return False


def pose(glove,generation=None,mode=None,collision=None):
    if mode not in (None,'automatic','target','constrained','feedback'):raise ValueError('Unknown teleoperation display source')
    mode='automatic' if mode is None else mode
    feedback=glove.get('feedback') or {};stream=glove.get('stream') or {}
    finite=lambda values:isinstance(values,(list,tuple)) and len(values)==20 and all(type(x) in (int,float) and math.isfinite(x) for x in values)
    hand_view=mode=='feedback' or bool(feedback.get('device_id') or glove.get('hardware'))
    if mode in ('target','constrained') or mode=='automatic' and not hand_view:
        from device_profiles import native_ranges
        raw=stream.get('q');profile=glove.get('profile');user=glove.get('user') or {};solver=stream.get('solver') or {}
        age=stream.get('age_ms');timeout=stream.get('timeout_ms',250)
        number=lambda value:type(value) in (int,float) and math.isfinite(value)
        valid=(channel_ids(generation) is not None and stream.get('fresh') is True and finite(raw)
            and glove.get('connection')=='receiving' and glove.get('busy') is True
            and profile in tuple(generation+'_'+side for side in ('left','right'))
            and type(glove.get('session_id')) is int and glove['session_id']>0
            and isinstance(glove.get('glove_serial'),str) and bool(glove['glove_serial'])
            and isinstance(user.get('user_id'),str) and solver.get('engine')=='sdk' and solver.get('applied') is True
            and type(stream.get('seq')) is int and stream['seq']>=0
            and number(age) and age>=0 and number(timeout) and timeout>0 and age<=min(500,timeout))
        if not valid:return None,'glove_preview',{}
        try:
            ranges=native_ranges(profile)
            if len(ranges)!=20 or any(q<lo-1e-6 or q>hi+1e-6 for q,(lo,hi) in zip(raw,ranges)):
                return None,'glove_preview',{}
            frame=dict(source='official_sdk',profile=profile,bridge_session_id=glove['session_id'],
                glove_serial=glove['glove_serial'],sdk_user_id=user['user_id'],source_seq=stream['seq'],
                source_target_sha256=hashlib.sha256(struct.pack('<20d',*raw)).hexdigest(),source_context_sha256=target_context(glove))
        except (ValueError,TypeError,KeyError):return None,'glove_preview',{}
        # The SDK already solved this target. No display projection, smoothing,
        # angle ramp or collision certificate changes these twenty values.
        return list(raw),'glove_preview',dict(target_frame=frame)
    # Once a hand feedback session is opened, a lost frame never becomes a
    # convincing target animation in its place.
    if hand_view:
        latest=feedback.get('latest') or {}
        rows=latest.get('joints') if isinstance(latest,dict) else None
        expected=channel_ids(generation)
        valid=(expected is not None and feedback.get('stale') is False
            and isinstance(rows,list) and len(rows)==20
            and all(isinstance(j,dict) and type(j.get('nid')) is int
                and type(j.get('position_rad')) in (int,float) and math.isfinite(j['position_rad']) for j in rows))
        by_id={j['nid']:j for j in rows} if valid else {}
        q=[by_id[n]['position_rad'] for n in expected] if valid and set(by_id)==set(expected) else None
        return q,'glove_live',feedback
    raise ValueError('No valid teleoperation display source')
