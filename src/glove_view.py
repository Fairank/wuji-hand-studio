"""Renderer input; distinguish intended glove targets from measured hand pose."""


def channel_ids(generation):
    if generation=='hand1':return tuple(range(20))
    if generation=='hand2':return tuple(f*5+j+1 for f in range(5) for j in range(4))
    return None


def pose(glove,generation=None):
    feedback=glove.get('feedback') or {};stream=glove.get('stream') or {}
    # Once a hand feedback session is opened, a lost frame never becomes a
    # convincing target animation in its place.
    hand_view=bool(feedback.get('device_id') or glove.get('hardware'))
    if hand_view:
        rows=(feedback.get('latest') or {}).get('joints',[])
        expected=channel_ids(generation)
        by_id={j['nid']:j for j in rows}
        q=[by_id[n]['position_rad'] for n in expected] if expected is not None and not feedback.get('stale',True) and set(by_id)==set(expected) else None
        return q,'glove_live',feedback
    return (stream.get('q') if channel_ids(generation) is not None and stream.get('fresh') else None),'glove_preview',{}
