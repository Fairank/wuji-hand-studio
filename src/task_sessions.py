"""Authoritative views of existing device owners, separate from view lifecycle.

Reading, selecting or laying out a task never creates a controller, connects a
device or renews a control lease. Mutations use a typed adapter and an exact
owner-context digest which the destination must recheck under its workflow lock.
"""
import copy
import hashlib
import json
import threading
from urllib.parse import urlencode, urlsplit

PROFILES = {'hand1_left', 'hand1_right', 'hand2_left', 'hand2_right'}


def object_value(value):
    return value if isinstance(value, dict) else {}


def serial(value):
    return value if isinstance(value, str) and 0 < len(value) <= 128 and not any(ord(c) < 32 for c in value) else None


def context_digest(state):
    """Identity/session scope, never rates, selected labels or connection age."""
    state = object_value(state)
    glove = object_value(state.get('glove'))
    feedback = object_value(glove.get('feedback'))
    viewer = object_value(state.get('official_viz'))
    calibration = object_value(state.get('calibration'))
    # /api/calibration has a nested run; Controller.snapshot intentionally uses
    # a compact, flat run. Both carry the same owner epoch.
    run = object_value(calibration.get('run')) or calibration
    profile = object_value(state.get('device_profile'))
    value = dict(hand=state.get('device_id'), identity=state.get('identified_device'),
                 generation=state.get('session_generation'), profile=profile.get('id'),
                 glove=glove.get('glove_serial'), session=glove.get('session_id'),glove_profile=glove.get('profile'),
                 user=object_value(glove.get('user')).get('user_id'),
                 target=feedback.get('device_id'), target_identity=feedback.get('identified_device'),
                 viewer=viewer.get('session_id'), run=run.get('id'))
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class TaskSessions:
    ACTIONS = {'pause', 'disconnect', 'pair', 'unpair', 'continue_teleoperation'}

    def __init__(self, read_endpoints, dispatch, *, pair=None, unpair=None,
                 continuation=None, continuation_status=None, read_groups=None):
        self.read_endpoints, self.dispatch = read_endpoints, dispatch
        self.pair, self.unpair = pair, unpair
        self.continuation, self.continuation_status = continuation, continuation_status
        self.read_groups=read_groups or (lambda: [])
        self.lock = threading.RLock()
        self.records = {}
        self.revision = 0

    @staticmethod
    def _origin(value):
        parsed = urlsplit(value)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
            raise ValueError('Invalid registered task endpoint')
        return f'http://127.0.0.1:{parsed.port}'

    def _observe(self, endpoint):
        ident, state = endpoint['id'], endpoint.get('state')
        origin = self._origin(endpoint['origin'])
        if not isinstance(state, dict):
            return []
        profile = object_value(state.get('device_profile'))
        glove, viewer = object_value(state.get('glove')), object_value(state.get('official_viz'))
        hardware, feedback = object_value(state.get('hardware')), object_value(glove.get('feedback'))
        ghardware = object_value(glove.get('hardware'))
        identity = object_value(state.get('identified_device'))
        found = []
        hand = serial(state.get('device_id'))
        profile_valid = (profile.get('id') in PROFILES and profile.get('side') in ('left', 'right')
                         and profile['id'].endswith('_' + profile['side']))
        if (hand and profile_valid and state.get('connection') == 'connected'
                and identity.get('source') == 'sdk_identity' and identity.get('serial') == hand
                and identity.get('profile') == profile.get('id')):
            found.append(dict(kind='hand', hand=hand, glove=None, profile=profile.get('id'),
                              side=profile.get('side'), active=hardware.get('active'),
                              stale=state.get('stale') is not False,
                              status='running' if hardware.get('active') is True else 'connected',
                              page='library', source='sdk', session=None))
        glove_sn = serial(glove.get('glove_serial'))
        if glove_sn and profile_valid and glove.get('connection') == 'receiving':
            paired = serial(feedback.get('device_id'))
            fid = object_value(feedback.get('identified_device'))
            verified_pair = (paired and feedback.get('connection') == 'connected'
                             and fid.get('source') == 'sdk_identity' and fid.get('serial') == paired
                             and fid.get('profile') == profile.get('id'))
            found.append(dict(kind='glove_pair' if verified_pair else 'glove',
                              hand=paired if verified_pair else None, glove=glove_sn,
                              profile=profile.get('id'), side=profile.get('side'),
                              active=False if not paired and 'active' not in ghardware and glove.get('can_prepare') is True else ghardware.get('active'),
                              stale=(object_value(glove.get('stream')).get('fresh') is not True
                                     or bool(paired) and feedback.get('stale') is not False),
                              status='following' if ghardware.get('active') is True else 'preview',
                              page='glove', source='sdk', session=glove.get('session_id')))
        reported = object_value(viewer.get('reported_device'))
        native_sn = serial(reported.get('sn') or reported.get('serial'))
        if (viewer.get('backend') == 'windows_native' and viewer.get('active') is True
                and viewer.get('phase') == 'running' and native_sn):
            kind = 'glove' if 'glove' in str(reported.get('kind') or reported.get('type') or reported.get('model')).lower() or native_sn.startswith('WG') else 'hand'
            found.append(dict(kind=kind, hand=native_sn if kind == 'hand' else None,
                              glove=native_sn if kind == 'glove' else None,
                              profile=profile.get('id'), side=reported.get('side') or reported.get('handedness'),
                              active=False, stale=False, status='viewing', page='official-viz',
                              source='native', session=viewer.get('session_id')))
        continuation=object_value(state.get('calibrated_teleoperation'))
        if (not found and continuation.get('can_continue') is True
                and serial(continuation.get('serial')) and continuation.get('side') in ('left','right')):
            found.append(dict(kind='glove',hand=None,glove=continuation['serial'],profile=profile.get('id'),
                side=continuation['side'],active=None,stale=True,status='calibrated',page='official-viz',
                source='native',session=None))
        return [(row, ident, origin, state) for row in found]

    def snapshot(self):
        # The reader only inspects existing processes; it must never spawn one.
        endpoints = self.read_endpoints()
        observations = [item for endpoint in endpoints for item in self._observe(endpoint)]
        endpoint_states = {row['id']: row.get('state') for row in endpoints}
        endpoint_counts={}
        for observed,endpoint,origin,state in observations:
            if observed['status']!='calibrated':endpoint_counts[endpoint]=endpoint_counts.get(endpoint,0)+1
        conflicting_endpoints={endpoint for endpoint,count in endpoint_counts.items() if count>1}
        with self.lock:
            seen = set()
            for observed, endpoint, origin, state in observations:
                key = ('glove:' + observed['glove']) if observed['glove'] else ('hand:' + observed['hand'])
                task_id = hashlib.sha256(key.encode()).hexdigest()[:24]
                record = self.records.get(task_id)
                scope = context_digest(state)
                if record and record.get('_seen_owner') != endpoint and record['task_id'] in seen:
                    record.update(status='conflict',_pair_target=False, capabilities=dict(blocked_reason='设备存在多个会话归属，请先核对 / Conflicting device owners; review the sessions'))
                    continue
                if not record:
                    record = dict(task_id=task_id, revision=0)
                    self.records[task_id] = record
                signature = (scope, endpoint, observed['kind'], observed['source'])
                if record.get('_signature') != signature:
                    record['revision'] += 1
                    self.revision += 1
                side = observed['side'] if observed['side'] in ('left', 'right') else None
                label = ('Glove' if observed['glove'] else 'Hand') + (' · ' + side if side else '')
                if observed['kind'] == 'glove_pair':
                    label = 'Glove ↔ Hand' + (' · ' + side if side else '')
                unknown = bool(state.get('unconfirmed_devices')) or object_value(state.get('calibration')).get('cleanup_required') is True
                can_edit = observed['active'] is False and not unknown and not observed['stale']
                continuation = object_value(state.get('calibrated_teleoperation'))
                if not continuation and callable(self.continuation_status):continuation=object_value(self.continuation_status(endpoint))
                continuation_verified = (not unknown and observed['status']=='calibrated' and continuation.get('can_continue') is True and bool(observed['glove'])
                    and continuation.get('serial') == observed['glove'] and continuation.get('side') == side)
                reason = ('停止结果尚未确认 / Stop confirmation required' if unknown else
                          '' if continuation_verified and observed['status']=='calibrated' else
                          '等待新鲜数据 / Waiting for fresh data' if observed['stale'] else
                          '请先暂停并确认停止 / Pause and confirm stop first' if observed['active'] is not False else '')
                capabilities = dict(can_pause=observed['active'] is True or unknown,
                                    can_disconnect=observed['status']!='calibrated',
                                    can_pair=can_edit and observed['kind'] == 'glove' and observed['source'] == 'sdk'
                                        and object_value(state.get('glove')).get('can_prepare') is not False and callable(self.pair),
                                    can_unpair=can_edit and observed['kind'] == 'glove_pair' and callable(self.unpair),
                                    can_continue_teleoperation=continuation_verified and callable(self.continuation),
                                    blocked_reason=reason)
                params = urlencode(dict(embedded='1', task_view='1', task_id=task_id))
                side_zh = {'left':'左','right':'右'}.get(side,'')
                label_zh = (('手套 ↔ 灵巧手' if observed['kind']=='glove_pair' else '手套' if observed['glove'] else '灵巧手') + (' · '+side_zh if side_zh else ''))
                record.update(endpoint_id=endpoint, kind=observed['kind'], label=label,
                              label_zh=label_zh,label_en=label,
                              status=observed['status'], devices=dict(hand=observed['hand'], glove=observed['glove']),
                              intent=dict(profile_id=observed['profile'], side=side,
                                          sdk_user=object_value(object_value(state.get('glove')).get('user')).get('user_id')),
                              observed=dict(stale=observed['stale'], hardware_active=observed['active'],
                                            source=observed['source'], session=observed['session'],
                                            continuation_verified=continuation_verified),
                              capabilities=capabilities,
                              view=dict(origin=origin, page=observed['page'], allow_native=endpoint=='main' and observed['source']=='native',
                                        url=origin+'/?'+params+('&task_native=1' if endpoint=='main' and observed['source']=='native' else '')+'#'+observed['page']),
                              _signature=signature, _context=scope, _seen_owner=endpoint,
                              _pair_target=bool(observed['kind']=='hand' and observed['source']=='sdk' and can_edit
                                  and not object_value(state.get('program')).get('active')
                                  and not object_value(state.get('group')).get('active')))
                if endpoint in conflicting_endpoints:
                    record.update(status='conflict',_pair_target=False,
                        capabilities=dict(blocked_reason='工作区存在重叠设备会话，请先核对 / This workspace has overlapping device sessions; review ownership first'))
                seen.add(task_id)
            for task_id, record in self.records.items():
                if task_id in seen:
                    continue
                state = endpoint_states.get(record['endpoint_id'])
                if isinstance(state, dict):
                    scope = context_digest(state)
                    if record.get('_context') != scope:
                        record['_context'] = scope
                        record['revision'] += 1
                        self.revision += 1
                    continuation = object_value(state.get('calibrated_teleoperation'))
                    if not continuation and callable(self.continuation_status):continuation=object_value(self.continuation_status(record['endpoint_id']))
                    verified=(not endpoint_counts.get(record['endpoint_id']) and not state.get('unconfirmed_devices') and object_value(state.get('calibration')).get('cleanup_required') is not True
                        and continuation.get('can_continue') is True and bool(record['devices'].get('glove'))
                        and continuation.get('serial')==record['devices']['glove']
                        and continuation.get('side')==record['intent'].get('side'))
                    continuing = object_value(state.get('calibration_connection')).get('busy') is True or object_value(state.get('calibration')).get('running') is True
                    record['status'] = 'calibrating' if continuing else 'calibrated' if verified else 'disconnected'
                    record['observed'].update(stale=True,hardware_active=None,
                        continuation_verified=verified)
                    record['_pair_target']=False
                    record['capabilities'] = dict(can_pause=False, can_disconnect=continuing,
                        can_pair=False, can_unpair=False, can_continue_teleoperation=verified and callable(self.continuation),
                        blocked_reason=continuation.get('blocked_reason', ''))
                else:
                    record['_pair_target']=False
                    record['observed'].update(stale=True,hardware_active=None,continuation_verified=False)
                    record.update(status='offline', capabilities=dict(blocked_reason='控制端暂不可用 / Controller unavailable'))
            claims={}
            for record in self.records.values():
                hand=record['devices'].get('hand')
                if record['task_id'] in seen and hand and record['kind'] in ('hand','glove_pair'):
                    claims.setdefault(hand,[]).append(record)
            for owners in claims.values():
                if len(owners)>1:
                    for owner in owners:
                        previous=owner['capabilities']
                        owner.update(status='conflict',_pair_target=False,
                            capabilities=dict(can_pause=previous.get('can_pause') is True,
                                can_disconnect=previous.get('can_disconnect') is True,
                                can_pair=False,can_unpair=False,can_continue_teleoperation=False,
                                blocked_reason='同一灵巧手仍有多个反馈归属，请停止并核对后继续 / This hand still has multiple feedback owners; stop and review before continuing'))
            pairs={hand:owners[0] for hand,owners in claims.items()
                   if len(owners)==1 and owners[0]['kind']=='glove_pair' and owners[0]['status']!='conflict'}
            for record in self.records.values():
                record.pop('paired_into',None)
                if record['kind']=='hand' and record['devices']['hand'] in pairs:
                    pair=pairs[record['devices']['hand']]
                    record['paired_into']=pair['task_id'];record['capabilities']={}
                    record['status']='paired'
            tasks = [{key: copy.deepcopy(value) for key, value in record.items() if not key.startswith('_')} for record in self.records.values()]
            candidates = []
            for task in tasks:
                if task['kind'] == 'hand' and task['observed']['source'] == 'sdk':
                    candidates.append(dict(task_id=task['task_id'], revision=task['revision'],
                                           label=task['label'],label_zh=task['label_zh'],label_en=task['label_en'], kind='hand', devices=task['devices'],
                                           intent=task['intent'], can_pair=self.records[task['task_id']].get('_pair_target') is True and not task.get('paired_into'),
                                           blocked_reason=task['capabilities'].get('blocked_reason','')))
            return dict(revision=self.revision, tasks=tasks, pair_candidates=candidates)

    def action(self, command):
        if not isinstance(command, dict) or set(command) - {'name','task_id','expected_revision','action','hand_task_id','hand_revision'}:
            raise ValueError('Invalid task action fields')
        action = command.get('action')
        if (action not in self.ACTIONS or command.get('name','task_session_action')!='task_session_action'
                or action!='pair' and any(key in command for key in ('hand_task_id','hand_revision'))):
            raise ValueError('Unknown task action')
        self.snapshot()
        with self.lock:
            record = self.records.get(command.get('task_id'))
            revision = command.get('expected_revision')
            if record is None or type(revision) is not int or revision != record['revision']:
                raise ValueError('任务已变化，请刷新后重试 / Task changed; refresh before retrying')
            task = copy.deepcopy(record)
            if task.get('capabilities', {}).get('can_'+action) is not True:
                raise ValueError(task.get('capabilities', {}).get('blocked_reason') or 'This task action is unavailable')
            target = None
            if action == 'pair':
                target = self.records.get(command.get('hand_task_id'))
                if not target or type(command.get('hand_revision')) is not int or target['revision'] != command['hand_revision']:
                    raise ValueError('Target task changed; choose it again')
                if (target.get('_pair_target') is not True or target.get('paired_into')
                        or task['kind']!='glove' or target['kind']!='hand'
                        or task['endpoint_id']==target['endpoint_id']
                        or any(task['intent'].get(key)!=target['intent'].get(key) for key in ('profile_id','side'))
                        or task['intent'].get('profile_id') not in PROFILES or task['intent'].get('side') not in ('left','right')):
                    raise ValueError('目标灵巧手不满足配对条件，请核对当前设备 / Target hand is not eligible for pairing; review its current state')
                target = copy.deepcopy(target)
        if action == 'pair':
            return self.pair(task, target)
        if action == 'unpair':
            return self.unpair(task)
        if action == 'continue_teleoperation':
            return self.continuation(task)
        return self.dispatch(task['endpoint_id'], task['_context'], action, {})
