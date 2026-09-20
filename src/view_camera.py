"""Display-only orbit camera. No actuator state or device access."""
import math

DEFAULT = dict(azimuth=90., elevation=-5., distance=.38, lookat=[0.,0.,.115])


def validate_camera(value):
    if not isinstance(value,dict) or set(value) != set(DEFAULT):
        raise ValueError('视角参数不完整')
    def finite(x):
        if type(x) not in {int,float} or not math.isfinite(x):
            raise ValueError('视角参数必须为有限数值')
        return float(x)
    point=value['lookat']
    if not isinstance(point,list) or len(point)!=3:
        raise ValueError('视角中心需要三个坐标')
    return dict(azimuth=finite(value['azimuth'])%360.,
        elevation=max(-85.,min(85.,finite(value['elevation']))),
        distance=max(.16,min(.9,finite(value['distance']))),
        lookat=[max(-.4,min(.4,finite(x))) for x in point])


def preview_selected(state):
    # Lost physical feedback must never silently become a scripted animation.
    return state.get('view_source')=='preview' and state.get('playback',{}).get('active',False)
