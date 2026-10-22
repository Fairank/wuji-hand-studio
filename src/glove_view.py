"""Renderer input; distinguish intended glove targets from measured hand pose."""
import math


def channel_ids(generation):
    if generation=='hand1':return tuple(range(20))
    if generation=='hand2':return tuple(f*5+j+1 for f in range(5) for j in range(4))
    return None


def pose(glove,generation=None,mode=None):
    if mode not in (None,'target','feedback'):raise ValueError('Unknown teleoperation display source')
    feedback=glove.get('feedback') or {};stream=glove.get('stream') or {}
    if mode=='target':
        # Preparing measured feedback does not remove the operator's chosen
        # target preview. A generated target is always labelled as a preview.
        q=stream.get('q')
        valid=(channel_ids(generation) is not None and stream.get('fresh') is True
               and isinstance(q,(list,tuple)) and len(q)==20
               and all(type(x) in (int,float) and math.isfinite(x) for x in q))
        return (list(q) if valid else None),'glove_preview',{}
    # Once a hand feedback session is opened, a lost frame never becomes a
    # convincing target animation in its place.
    hand_view=mode=='feedback' or bool(feedback.get('device_id') or glove.get('hardware'))
    if hand_view:
        rows=(feedback.get('latest') or {}).get('joints',[])
        expected=channel_ids(generation)
        by_id={j['nid']:j for j in rows}
        q=[by_id[n]['position_rad'] for n in expected] if expected is not None and not feedback.get('stale',True) and set(by_id)==set(expected) else None
        return q,'glove_live',feedback
    return (stream.get('q') if channel_ids(generation) is not None and stream.get('fresh') else None),'glove_preview',{}
