"""Read-only model anatomy. Model indices are NOT verified hardware node IDs."""
import math
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from device_profiles import profile

FINGERS = [('拇指', 'Thumb'), ('食指', 'Index'), ('中指', 'Middle'), ('无名指', 'Ring'), ('小指', 'Pinky')]
AXES = {
    'cmc_flex': ('掌根屈伸', 'Base flexion', '拇指掌根向掌心弯曲或伸开', 'Bend or extend the thumb at its base'),
    'cmc_abd': ('掌根侧摆', 'Base spread', '拇指掌根向侧面展开或收拢', 'Spread or bring in the thumb at its base'),
    'mcp_flex': ('指根屈伸', 'Knuckle flexion', '整根手指从指根向掌心弯曲或伸开', 'Bend or extend the finger at the knuckle'),
    'mcp_abd': ('指根侧摆', 'Knuckle spread', '整根手指左右侧摆，分开或并拢', 'Move the finger sideways, apart or together'),
    'mcp': ('拇指近节', 'Thumb knuckle', '弯曲或伸直拇指的近节关节', 'Bend or straighten the thumb knuckle'),
    'ip': ('拇指末节', 'Thumb tip joint', '弯曲或伸直拇指靠近指尖的关节', 'Bend or straighten the joint near the thumb tip'),
    'pip': ('中节屈伸', 'Middle joint', '弯曲或伸直手指中间的关节', 'Bend or straighten the middle joint'),
    'dip': ('末节屈伸', 'Tip joint', '弯曲或伸直靠近指尖的关节', 'Bend or straighten the joint near the fingertip'),
}

@lru_cache(maxsize=8)
def catalog(profile_id, language='zh'):
    p = profile(profile_id)
    lang = 1 if language == 'en' else 0
    tree = ET.parse(Path(__file__).resolve().parent / p['model'])
    joints = {j.get('name'): j for j in tree.findall('.//worldbody//joint')}
    rows = []
    for index, actuator in enumerate(tree.findall('./actuator/position')):
        joint = joints[actuator.get('joint')]
        name = joint.get('name')
        axis = next((k for k in AXES if name.endswith('_' + k)), None)
        if axis:
            label, movement = AXES[axis][lang], AXES[axis][lang + 2]
        else:
            label = (['根部轴 1', '根部轴 2', '近端关节', '末端关节'] if lang == 0 else ['Base axis 1', 'Base axis 2', 'Proximal joint', 'Distal joint'])[index % 4]
            movement = '按一代原生模型显示位置与转轴；运动方向请核对设备' if lang == 0 else 'Native Hand 1 position and axis; verify movement on the device'
        lo, hi = map(float, joint.get('range').split())
        rows.append(dict(index=index, fingerLabel=FINGERS[index // 4][lang], jointLabel=label,
            movement=movement, modelName=name, actuatorName=actuator.get('name'),
            channelLabel=f'{index} (0–19)', rangeLabel=f'{math.degrees(lo):.1f}° … {math.degrees(hi):.1f}°',
            imageUrl=f'/api/joint-reference?profile={profile_id}&index={index}',
            range_rad=[lo, hi]))
    if len(rows) != 20:
        raise ValueError('Expected 20 model actuators')
    return dict(profile=profile_id, profileLabel=p['en' if lang else 'zh'], items=rows,
        source='Bundled official MJCF: ' + p['model'], hardware_mapping_verified=False)

def image_path(profile_id, index):
    profile(profile_id)
    if type(index) is not int or not 0 <= index < 20:
        raise ValueError('Invalid model index')
    return Path(__file__).resolve().parent / 'web' / 'joints' / f'{profile_id}-{index}.png'
