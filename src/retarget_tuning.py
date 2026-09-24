"""Editable official open-retargeting configurations, NOT SDK live settings.

No device access or solver execution. Export is a complete upstream YAML, with
its original optimizer paths and all untuned parameters preserved. Users put
it under that pinned repository's example/config directory for tuning_tool.py.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
import threading
import yaml

from mapping_library import binding_key, validate_binding
from runtime_paths import RESOURCE

FINGERS=('thumb','index','middle','ring','pinky')
ROOT=RESOURCE/'official_data/retarget'
EDITABLE={'segment_scaling','pinch_thresholds','lp_alpha','norm_delta'}


def source(binding):
    b=validate_binding(binding)
    infix='_wuji_hand_2' if b['generation']=='hand2' else ''
    filename=f'adaptive_analytical_wuji_glove{infix}_{b["side"]}.yaml'
    manifest=json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
    item=next(x for x in manifest['files'] if x['file']==filename)
    raw=(ROOT/filename).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('Official template checksum mismatch')
    template=yaml.safe_load(raw)
    return template,dict(filename=filename,commit=manifest['commit'],repository=manifest['repository'])


def defaults(binding):
    template,_=source(binding)
    return {key:copy.deepcopy(template['retarget'][key]) for key in EDITABLE}


def finite(value,label,positive=False):
    if type(value) not in (int,float) or not math.isfinite(value):raise ValueError(label+': finite number required / 请输入有限数值')
    if value<0 or (positive and value==0):raise ValueError(label+': positive value required / 比例须大于零')
    return float(value)


def validate(values):
    if not isinstance(values,dict) or set(values)!=EDITABLE:raise ValueError('Unexpected tuning fields')
    result={}
    scales=values['segment_scaling']
    if not isinstance(scales,dict) or set(scales)!=set(FINGERS):raise ValueError('Five finger scales required')
    result['segment_scaling']={}
    widths=set()
    for finger in FINGERS:
        row=scales[finger]
        if not isinstance(row,list) or len(row) not in (3,4):raise ValueError('Each finger needs PIP/DIP/TIP or MCP/PIP/DIP/TIP')
        widths.add(len(row))
        result['segment_scaling'][finger]=[finite(x,finger,True) for x in row]
    if len(widths)!=1:raise ValueError('Use the same column count for every finger')
    pins=values['pinch_thresholds']
    if not isinstance(pins,dict) or set(pins)!=set(FINGERS[1:]):raise ValueError('Four thumb pair thresholds required')
    result['pinch_thresholds']={}
    for finger in FINGERS[1:]:
        row=pins[finger]
        if not isinstance(row,dict) or set(row)!={'d1','d2'}:raise ValueError('Expected d1 and d2 in cm')
        d1,d2=(finite(row[key],finger+'.'+key) for key in ('d1','d2'))
        if d2<=d1:raise ValueError('d2 must be greater than d1 / d2 必须大于 d1')
        result['pinch_thresholds'][finger]=dict(d1=d1,d2=d2)
    result['lp_alpha']=finite(values['lp_alpha'],'lp_alpha',True)
    if result['lp_alpha']>1:raise ValueError('lp_alpha must be in (0, 1]')
    result['norm_delta']=finite(values['norm_delta'],'norm_delta')
    return result


class TuningStore:
    def __init__(self,folder):
        self.folder=Path(folder);self.lock=threading.RLock()

    def context(self,binding):
        with self.lock:
            binding=validate_binding(binding)
            path=self.folder/(binding_key(binding)+'.json')
            template,origin=source(binding);values=defaults(binding);revision=0
            if path.exists():
                if path.stat().st_size>65536:raise ValueError('Tuning profile too large')
                saved=json.loads(path.read_text(encoding='utf-8'))
                if saved.get('schema_version')!=1 or saved.get('binding')!=binding or saved.get('commit')!=origin['commit']:
                    raise ValueError('Tuning profile version differs; preserve it for review')
                values=validate(saved['values']);revision=saved['revision']
                if type(revision) is not int or revision<1:raise ValueError('Invalid tuning revision')
            return dict(binding=binding,values=values,defaults=defaults(binding),revision=revision,
                        source=origin,runtime_applied=False,mode='official_yaml_export')

    def save(self,binding,values,revision):
        with self.lock:
            context=self.context(binding)
            if type(revision) is not int or context['revision']!=revision:raise ValueError('Tuning changed elsewhere; reload first / 参数已变更，请重新载入')
            clean=validate(values)
            self.folder.mkdir(parents=True,exist_ok=True)
            path=self.folder/(binding_key(context['binding'])+'.json')
            stage=path.with_suffix('.pending')
            stage.write_text(json.dumps(dict(schema_version=1,binding=context['binding'],values=clean,
                                  revision=revision+1,commit=context['source']['commit']),ensure_ascii=False,indent=2),encoding='utf-8')
            stage.replace(path)
            return self.context(binding)

    def export(self,binding,values):
        clean=validate(values)
        template,origin=source(binding)
        template['retarget'].update(clean)
        notice=('# Hand Workbench edited configuration. Not applied to the SDK live session.\n'
                '# Upstream commit: '+origin['commit']+'\n'
                '# Place this file in the upstream example/config directory to retain relative model paths.\n')
        return dict(filename=origin['filename'],yaml=notice+yaml.safe_dump(template,sort_keys=False),source=origin,runtime_applied=False)
