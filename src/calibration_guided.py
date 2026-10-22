"""Strict adapter for official hand-model JSONL; no calibration algorithm."""
import copy
import json
import re

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


class OfficialEvents:
    """Accept complete schema-v2 lines across arbitrary raw pipe boundaries."""
    def __init__(self, serial, side, user):
        self.serial, self.side, self.user = serial, side, user
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
            if kind != 'progress': self.terminal = kind
            if kind == 'result':
                result = event.get('result')
                if (event.get('status') != 'ok' or not isinstance(result, dict)
                        or result.get('sdk_user') != self.user or result.get('handedness') != self.side
                        or not isinstance(result.get('calibrated_urdf'), str) or not result['calibrated_urdf'].strip()):
                    raise ValueError('Official result does not confirm the selected user and hand model')
                contexts=[]
                for context in (event.get('device'),result.get('device')):
                    if context is not None and not isinstance(context,dict):raise ValueError('Invalid official result device context')
                    contexts.append(context or {})
                for sn in (event.get('sn'),event.get('serial'),result.get('sn'),result.get('serial'),
                           *[context.get('sn') for context in contexts],*[context.get('serial') for context in contexts]):
                    if sn is not None and sn != self.serial:
                        raise ValueError('Official result device identity differs from the selected glove')
                for user in (event.get('sdk_user'),result.get('user')):
                    if user is not None and user!=self.user:raise ValueError('Official result user context differs from the selected wearer')
                self.result = copy.deepcopy(event)
            output.append(event)
        return output
