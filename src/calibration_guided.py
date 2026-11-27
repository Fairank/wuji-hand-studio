"""Strict adapter for official hand-model JSONL; no calibration algorithm."""
import copy
import json
import re
from calibration_identity import require_sdk_user, sdk_user_identity

NAME = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,31}\Z')
EVENTS = ('progress', 'result', 'error', 'cancelled')
TERMINAL_STATUS_V3 = {'result': 'ok', 'error': 'failed', 'cancelled': 'cancelled'}


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


def event_schema(event):
    if isinstance(event, dict) and event.get('calibration') == 'hand_model':
        schema = event.get('schema_version')
        if type(schema) is int and schema in (2, 3):
            return schema
    return None


def event_kind(event, *, legacy_stream=False):
    schema = event_schema(event)
    if schema is None:
        # Argument validation can fail before the CLI constructs a calibration event.
        error = event.get('error') if isinstance(event, dict) and set(event) == {'error'} else None
        if (isinstance(error, dict) and type(error.get('code')) is int and error['code'] > 0
                and all(isinstance(error.get(key), str) for key in ('kind', 'reason', 'message', 'hint'))):
            return 'error'
        return None
    if schema == 2 and legacy_stream:
        kind = event.get('event') or event.get('type') or 'progress'
    elif event.get('operation') == 'calibrate':
        kind = event.get('event')
    else:
        return None
    if kind not in EVENTS:
        return None
    if schema == 3 and kind in TERMINAL_STATUS_V3 and event.get('status') != TERMINAL_STATUS_V3[kind]:
        raise ValueError('Official calibration event has an invalid terminal status')
    return kind


def event_error(event):
    error = event.get('error')
    message = error.get('message') if isinstance(error, dict) else error
    return str(message or 'Official calibration failed')[:800]


def interrupted(code):
    # Python reports SIGINT as -2; Windows can return the signed or unsigned NTSTATUS.
    return code in (-2, 130, -1073741510, 0xC000013A)


def _result_payload(event, side):
    result = event.get('result') if isinstance(event, dict) else None
    if (not event_schema(event) or event.get('operation') != 'calibrate' or event.get('event') != 'result'
            or event.get('status') != 'ok' or not isinstance(result, dict)):
        raise ValueError('官方未确认本次标定成功 / Official calibration did not confirm success')
    if result.get('handedness')!=side:
        raise ValueError('标定左右手与所选手套不一致 / Calibration hand side differs from the selected glove')
    if not isinstance(result.get('calibrated_urdf'),str) or not result['calibrated_urdf'].strip():
        raise ValueError('官方未返回保存的手型位置 / Official calibration did not return the saved hand model location')
    return result


def validate_result(event, serial, side, user, identity):
    expected = sdk_user_identity(identity, user)
    result = _result_payload(event, side)
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
    _validate_device(event, serial)
    _validate_device(result, serial)
    return expected


def validate_named_result(event, serial, side, user):
    """The Linux wrapper checks the selected profile under its capture lease."""
    result = _result_payload(event, side)
    if result.get('sdk_user') is None:
        raise ValueError('Official result did not identify the wearer')
    for context in (event.get('user'), event.get('sdk_user'), result.get('user'), result['sdk_user']):
        if context is not None and (context.get('display_name') if isinstance(context, dict) else context) != user:
            raise ValueError('Official result wearer differs from the selected profile')
    _validate_device(event, serial)
    _validate_device(result, serial)


def _validate_device(event, serial):
    device = event.get('device')
    if device is not None and not isinstance(device, dict):
        raise ValueError('Invalid official device identity')
    for row in (event, device or {}):
        for key in ('sn', 'serial'):
            if row.get(key) is not None and row[key] != serial:
                raise ValueError('标定事件属于另一只手套 / Calibration event belongs to another glove')


class OfficialEvents:
    """Accept complete official calibration events across arbitrary raw pipe boundaries."""
    def __init__(self, serial, side, user, identity=None):
        self.serial, self.side, self.user = serial, side, user
        self.identity=copy.deepcopy(identity)
        self.pending = ''
        self.terminal = None
        self.result = None
        self.schema_version = None

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
            kind = event_kind(event)
            if kind is None: continue
            if self.terminal is not None:
                raise ValueError('Official calibration emitted data after its terminal event')
            schema = event_schema(event)
            if schema is not None:
                if self.schema_version not in (None, schema):
                    raise ValueError('Official calibration event schema changed during capture')
                self.schema_version = schema
            for context in (event.get('user'),event.get('sdk_user')):
                if context is not None:
                    if isinstance(context,dict):require_sdk_user(context,self.identity,self.user)
                    elif not isinstance(context,str) or context!=self.user:
                        raise ValueError('官方事件的佩戴者身份不一致 / Official event wearer identity differs')
            _validate_device(event, self.serial)
            if kind != 'progress': self.terminal = kind
            if kind == 'result':
                validate_result(event,self.serial,self.side,self.user,self.identity)
                self.result = copy.deepcopy(event)
            output.append(event)
        return output
