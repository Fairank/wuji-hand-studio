"""Renderer input; distinguish intended glove targets from measured hand pose."""
import math
import hashlib
import struct


def channel_ids(generation):
    if generation=='hand1':return tuple(range(20))
    if generation=='hand2':return tuple(f*5+j+1 for f in range(5) for j in range(4))
    return None


def pose(glove,generation=None,mode=None,collision=None):
    if mode not in (None,'automatic','target','constrained','feedback'):raise ValueError('Unknown teleoperation display source')
    mode='automatic' if mode is None else mode
    feedback=glove.get('feedback') or {};stream=glove.get('stream') or {}
    finite=lambda values:isinstance(values,(list,tuple)) and len(values)==20 and all(type(x) in (int,float) and math.isfinite(x) for x in values)
    hand_view=mode=='feedback' or bool(feedback.get('device_id') or glove.get('hardware'))
    if mode in ('target','constrained') or mode=='automatic' and not hand_view:
        from collision_preview import SOURCE
        from mapping_collision import ALGORITHM,COVERAGE,PAIR_COUNT,MIN_CLEARANCE_M
        result=dict(collision) if isinstance(collision,dict) else {}
        q=result.get('display_q');raw=stream.get('q')
        measured=lambda key:type(result.get(key)) in (int,float) and math.isfinite(result[key]) and result[key]>=0
        valid=(channel_ids(generation) is not None and stream.get('fresh') is True and finite(raw) and finite(q)
            and glove.get('connection')=='receiving' and glove.get('busy') is True
            and result.get('status')=='ready' and result.get('source')==SOURCE
            and result.get('preview_only') is True and result.get('target_modified') is False
            and result.get('hardware_validated') is False and result.get('stable') is True
            and result.get('profile')==glove.get('profile') and str(result.get('profile','')).startswith(generation+'_')
            and result.get('bridge_session_id')==glove.get('session_id')
            and ((type(glove.get('session_id')) is int and glove['session_id']>=0) or (isinstance(glove.get('session_id'),str) and bool(glove['session_id'])))
            and result.get('glove_serial')==glove.get('glove_serial') and bool(glove.get('glove_serial'))
            and type(stream.get('seq')) is int and result.get('source_seq')==stream['seq']
            and measured('max_all_contact_penetration_m') and result['max_all_contact_penetration_m']==0
            and measured('limit_excess_rad') and result['limit_excess_rad']==0
            and result.get('coverage')==COVERAGE and result.get('algorithm')==ALGORITHM
            and result.get('geometry_only') is True and result.get('frame_verified') is True
            and result.get('path_certified') is True and result.get('geometry_pairs_verified')==PAIR_COUNT
            and result.get('visual_meshes_verified')==26
            and measured('minimum_verified_clearance_m') and result['minimum_verified_clearance_m']>=MIN_CLEARANCE_M-1e-12
            and not (result.get('budget_limited') is True and result.get('progressed') is False)
            and result.get('source_target_sha256')==hashlib.sha256(struct.pack('<20d',*raw)).hexdigest())
        if not valid:
            if result.get('status') not in ('blocked','error'):
                result['message']='映射预览校验未通过，等待下一帧 / Mapping preview is unverified; waiting for the next frame'
            result.update(status=result.get('status') if result.get('status') in ('blocked','error') else 'blocked')
            result.setdefault('message','映射预览暂无有效结果 / No valid mapping preview')
            return None,'glove_constrained_unavailable',dict(collision_preview=result)
        return list(q),'glove_constrained',dict(collision_preview=result)
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
