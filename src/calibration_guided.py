"""Strict adapter for official hand-model JSONL; no calibration algorithm."""
import copy
import json
import re
from calibration_identity import require_sdk_user, sdk_user_identity

NAME = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,31}\Z')


def validate_profile(profile, name, side, replace):
    if (not isinstance(name, str) or not NAME.fullmatch(name) or name.lower() == 'default'
            or not isinstance(profile, dict) or profile.get('name') != name
            or profile.get('is_default') is True):
        raise ValueError('请选择命名佩戴者 / Select a named SDK user')
    if side not in ('left', 'right') or type(replace) is not bool:
        raise ValueError('请先确认手套左右手 / Verify the selected glove side')
    model = profile.get(side + '_hand')
    if not isinstance(model, dict) or type(model.get('calibrated')) is not bool:
        raise ValueError('无法确认已有标定，请刷新佩戴者信息 / Calibration model state is unknown')
    if model['calibrated'] and not replace:
        raise ValueError('已有此侧标定，请明确确认覆盖 / Confirm replacement of this hand model')
    return copy.deepcopy(profile)


def validate_result(event, serial, side, user, identity):
    expected=sdk_user_identity(identity,user)
    result=event.get('result') if isinstance(event,dict) else None
    if (not isinstance(event,dict) or type(event.get('schema_version')) is not int
            or event['schema_version']!=2 or event.get('calibration')!='hand_model'
            or event.get('operation')!='calibrate' or event.get('event')!='result'
            or event.get('status')!='ok' or not isinstance(result,dict)):
        raise ValueError('官方未确认本次标定成功 / Official calibration did not confirm success')
    if result.get('handedness')!=side:
        raise ValueError('标定左右手与所选手套不一致 / Calibration hand side differs from the selected glove')
    if not isinstance(result.get('calibrated_urdf'),str) or not result['calibrated_urdf'].strip():
        raise ValueError('官方未返回保存的手型位置 / Official calibration did not return the saved hand model location')
    attested=False
    for context in (event.get('user'),event.get('sdk_user'),result.get('user'),result.get('sdk_user')):
        if context is None:continue
        if isinstance(context,dict):
            require_sdk_user(context,expected,user);attested=True
        elif not isinstance(context,str) or context!=user:
            raise ValueError('标定结果的佩戴者身份不一致 / Calibration result wearer identity differs')
    if (result.get('sdk_user') is None or not attested
            or isinstance(result.get('sdk_user'),str) and not isinstance(event.get('user'),dict)):
        raise ValueError('官方结果缺少准确佩戴者身份，请重新标定 / Official result lacks the exact wearer identity; calibrate again')
    contexts=[]
    for context in (event.get('device'),result.get('device')):
        if context is not None and not isinstance(context,dict):raise ValueError('官方设备身份格式无效 / Invalid official device identity')
        contexts.append(context or {})
    for sn in (event.get('sn'),event.get('serial'),result.get('sn'),result.get('serial'),
               *[context.get('sn') for context in contexts],*[context.get('serial') for context in contexts]):
        if sn is not None and sn!=serial:
            raise ValueError('标定结果属于另一只手套 / Calibration result belongs to another glove')
    return expected


class OfficialEvents:
    """Accept complete schema-v2 lines across arbitrary raw pipe boundaries."""
    def __init__(self, serial, side, user, identity=None):
        self.serial, self.side, self.user = serial, side, user
        self.identity=copy.deepcopy(identity)
        self.pending = ''
        self.terminal = None
        self.result = None

    def feed(self, text, final=False):
        self.pending += text
        if len(self.pending) > 65536 and '\n' not in self.pending:
            raise ValueError('Official calibration record exceeds its limit')
        lines = self.pending.split('\n')
        self.pending = lines.pop()
        if final and self.pending:
            lines.append(self.pending); self.pending = ''
        output = []
        for line in lines:
            if len(line) > 65536: raise ValueError('Official calibration record exceeds its limit')
            try: event = json.loads(line.strip())
            except (ValueError, TypeError): continue  # diagnostics remain in the raw transcript
            if (not isinstance(event, dict) or type(event.get('schema_version')) is not int
                    or event['schema_version'] != 2 or event.get('calibration') != 'hand_model'
                    or event.get('operation') != 'calibrate'):
                continue
            kind = event.get('event')
            if kind not in ('progress', 'result', 'error', 'cancelled'): continue
            if self.terminal is not None:
                raise ValueError('Official calibration emitted data after its terminal event')
            for context in (event.get('user'),event.get('sdk_user')):
                if context is not None:
                    if isinstance(context,dict):require_sdk_user(context,self.identity,self.user)
                    elif not isinstance(context,str) or context!=self.user:
                        raise ValueError('官方事件的佩戴者身份不一致 / Official event wearer identity differs')
            device=event.get('device')
            if device is not None and not isinstance(device,dict):raise ValueError('官方设备身份格式无效 / Invalid official device identity')
            for sn in (event.get('sn'),event.get('serial'),(device or {}).get('sn'),(device or {}).get('serial')):
                if sn is not None and sn!=self.serial:raise ValueError('标定事件属于另一只手套 / Calibration event belongs to another glove')
            if kind != 'progress': self.terminal = kind
            if kind == 'result':
                validate_result(event,self.serial,self.side,self.user,self.identity)
                self.result = copy.deepcopy(event)
            output.append(event)
        return output
