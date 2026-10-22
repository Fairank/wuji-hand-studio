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
    feedback=glove.get('feedback') or {};stream=glove.get('stream') or {}
    finite=lambda values:isinstance(values,(list,tuple)) and len(values)==20 and all(type(x) in (int,float) and math.isfinite(x) for x in values)
    if mode=='constrained':
        from collision_preview import DISPLAY_MAX_PENETRATION_M,DISPLAY_MAX_LIMIT_EXCESS_RAD,SOURCE
        result=dict(collision) if isinstance(collision,dict) else {}
        q=result.get('display_q');raw=stream.get('q')
        measured=lambda key:type(result.get(key)) in (int,float) and math.isfinite(result[key]) and result[key]>=0
        valid=(channel_ids(generation) is not None and stream.get('fresh') is True and finite(raw) and finite(q)
            and glove.get('connection')=='receiving' and glove.get('busy') is True
            and result.get('status') in ('ready','partial') and result.get('source')==SOURCE
            and result.get('preview_only') is True and result.get('target_modified') is False
            and result.get('hardware_validated') is False and result.get('stable') is True
            and result.get('profile')==glove.get('profile') and str(result.get('profile','')).startswith(generation+'_')
            and result.get('bridge_session_id')==glove.get('session_id')
            and ((type(glove.get('session_id')) is int and glove['session_id']>=0) or (isinstance(glove.get('session_id'),str) and bool(glove['session_id'])))
            and result.get('glove_serial')==glove.get('glove_serial') and bool(glove.get('glove_serial'))
            and type(stream.get('seq')) is int and result.get('source_seq')==stream['seq']
            and measured('max_all_contact_penetration_m') and result['max_all_contact_penetration_m']<=DISPLAY_MAX_PENETRATION_M
            and measured('limit_excess_rad') and result['limit_excess_rad']<=DISPLAY_MAX_LIMIT_EXCESS_RAD
            and result.get('coverage') in ('partial_original_hand1_masks','original_hand2_masks_unvalidated')
            and (result['coverage']=='partial_original_hand1_masks')==(generation=='hand1')
            and result.get('source_target_sha256')==hashlib.sha256(struct.pack('<20d',*raw)).hexdigest())
        if not valid:
            if result.get('status') not in ('blocked','error'):
                result['message']='碰撞约束预演校验未通过，未更新画面 / Collision-constrained preview is unverified; frame withheld'
            result.update(status=result.get('status') if result.get('status') in ('blocked','error') else 'blocked')
            result.setdefault('message','碰撞约束预演暂无有效结果 / No valid collision-constrained preview')
            return None,'glove_constrained_unavailable',dict(collision_preview=result)
        return list(q),'glove_constrained',dict(collision_preview=result)
    if mode=='target':
        # Preparing measured feedback does not remove the operator's chosen
        # target preview. A generated target is always labelled as a preview.
        q=stream.get('q')
        valid=channel_ids(generation) is not None and stream.get('fresh') is True and finite(q)
        return (list(q) if valid else None),'glove_preview',{}
    # Once a hand feedback session is opened, a lost frame never becomes a
    # convincing target animation in its place.
    hand_view=mode=='feedback' or bool(feedback.get('device_id') or glove.get('hardware'))
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
    q=stream.get('q')
    valid=channel_ids(generation) is not None and stream.get('fresh') is True and finite(q)
    return (list(q) if valid else None),'glove_preview',{}
