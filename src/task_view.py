"""Bind an embedded task page to its current local resource, never its tab label."""
import copy
import hashlib
import json
import re

from task_sessions import TaskSessions,context_digest,object_value,serial

TASK=re.compile(r'[0-9a-f]{24}\Z')
CONTEXT=re.compile(r'[0-9a-f]{64}\Z')
GROUP_ACTIONS=frozenset({'ensemble_start','ensemble_stop','ensemble_beat','workspace_camera'})


def _task(kind,number):
    return hashlib.sha256((kind+':'+number).encode()).hexdigest()[:24]


def _owner_context(state):
    value=dict(actor=context_digest(state))
    for name,keys in (
            ('calibration_connection',('serial','viewer_token','session_id','run_id')),
            ('teleoperation_connection',('serial','viewer_token','attempt_id','sdk_session'))):
        row=object_value(state.get(name));value[name]={key:row.get(key) for key in keys}
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _resource(state):
    # Reuse the registry's verified SDK/native/continuation observation rules,
    # with a local endpoint. A fleet child's own endpoint is also called main.
    registry=TaskSessions(lambda:[],lambda *args:None)
    observed=[row[0] for row in registry._observe(dict(id='main',origin='http://127.0.0.1:1',state=state))]
    if (state.get('connection') in ('connecting','connected')
            and not any(row['source']=='sdk' and row['kind']=='hand' for row in observed)):
        return None
    glove=object_value(state.get('glove'));continuation=object_value(state.get('calibrated_teleoperation'))
    pending_continuation=(continuation.get('busy') is True and serial(continuation.get('serial'))
        and isinstance(continuation.get('attempt_id'),str) and re.fullmatch('[0-9a-f]{32}',continuation['attempt_id']))
    if glove.get('busy') is True and not any(row['source']=='sdk' and row.get('glove') for row in observed):
        if not pending_continuation:return None
    recovery=any(row.get('recovery') is True for row in observed)
    claims=[dict(kind='glove' if row.get('glove') else 'hand',
                 serial=row.get('glove') or row.get('hand')) for row in observed]
    calibration=object_value(state.get('calibration'))
    run=object_value(calibration.get('run')) or calibration
    active=(run.get('running') is True or calibration.get('cleanup_required') is True
            or (run.get('id') and run.get('cleanup_confirmed') is False))
    if active:
        number=serial(run.get('serial'))
        if not number or run.get('source') not in ('official_tty','official_jsonl_native'):
            return None
        claims.append(dict(kind='glove',serial=number))
    # A handover's saved identity is the same resource as its Viewer/run. It
    # covers the interval after release and before the new owner is published.
    transitions=[]
    for name in ('calibration_connection',):
        transition=object_value(state.get(name))
        if transition.get('busy') is True:
            number=serial(transition.get('serial'))
            if not number:return None
            transitions.append(dict(kind='glove',serial=number))
    if len(claims)>1:return None
    if claims and any(row!=claims[0] for row in transitions):return None
    if not claims:
        if len({(row['kind'],row['serial']) for row in transitions})>1:return None
        claims=transitions[:1]
    if len(claims)!=1:return None
    return dict(claims[0],recovery=True) if recovery else claims[0]


def _group(task_id,read_groups):
    if read_groups is None:return None
    value=read_groups()
    rows=value.get('tasks') if isinstance(value,dict) else value
    if not isinstance(rows,list):raise ValueError('Task group registry is unavailable')
    matches=[row for row in rows if isinstance(row,dict) and row.get('task_id')==task_id and row.get('kind')=='ensemble']
    if len(matches)!=1:return None
    row=matches[0];intent=object_value(row.get('intent'));ident=intent.get('group_id');members=intent.get('members')
    hands=object_value(row.get('devices')).get('hands')
    if (not isinstance(ident,str) or not re.fullmatch(r'(main|[0-9a-f]{16})',ident)
            or _task('ensemble',ident)!=task_id or type(row.get('revision')) is not int or row['revision']<1
            or row.get('status') not in ('connected','running')
            or not isinstance(members,list) or not 2<=len(members)<=8
            or any(not isinstance(member,str) or not member for member in members)
            or len(set(members))!=len(members) or not isinstance(hands,list) or len(hands)!=len(members)
            or any(not serial(hand) for hand in hands) or len(set(hands))!=len(hands)):
        return None
    context=hashlib.sha256(json.dumps(dict(group=ident,revision=row['revision'],members=members,hands=hands),sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return dict(kind='ensemble',group_id=ident,members=members,owner_context=context)


def _scope(state,task_id,read_groups):
    resource=_resource(state)
    if resource is not None and _task(resource['kind'],resource['serial'])==task_id:
        return dict(task_id=task_id,current=True,owner_context=_owner_context(state)),resource
    group=_group(task_id,read_groups)
    if group is not None:
        return dict(task_id=task_id,current=True,owner_context=group['owner_context']),group
    if resource is None or _task(resource['kind'],resource['serial'])!=task_id:
        return dict(task_id=task_id,current=False,owner_context=None),None
    return dict(task_id=task_id,current=True,owner_context=_owner_context(state)),resource


def scope(controller,task_id,*,read_groups=None):
    """Read cached owner state only; never discover, connect, or refresh a lease."""
    if not isinstance(task_id,str) or not TASK.fullmatch(task_id):raise ValueError('Invalid task view identity')
    state=controller.snapshot()
    if not isinstance(state,dict):raise ValueError('Task owner state is unavailable')
    return _scope(state,task_id,read_groups)[0]


def _command(command,resource):
    if not isinstance(command,dict) or not isinstance(command.get('name'),str):raise ValueError('Invalid task command')
    name=command['name']
    if resource.get('recovery') and name not in ('glove_stop','glove_disconnect','hardware_stop'):
        raise ValueError('手套会话需要先结束，请在原任务中暂停或断开 / Pause or disconnect the failed glove session first')
    if resource['kind']=='ensemble':
        if name not in GROUP_ACTIONS or command.get('workspace')!=resource['group_id']:
            raise ValueError('此任务页仅能操作所属多手工作区 / This task page can control only its ensemble')
        if name=='workspace_camera' and command.get('member') not in resource['members']:
            raise ValueError('Camera member is outside this ensemble')
        if name=='ensemble_start':
            members=command.get('members')
            if (not isinstance(members,list) or not 2<=len(members)<=8
                    or any(not isinstance(member,str) for member in members) or len(set(members))!=len(members)
                    or any(member not in resource['members'] for member in members)):
                raise ValueError('Start members are outside this ensemble')
        return
    if name.startswith(('workspace_','ensemble_','fleet_','task_')):
        raise ValueError('A device task cannot act as a workspace controller')
    if name in ('connect','official_viz_start','calibration_start','calibration_handover','teleoperation_handover'):
        if command.get('serial')!=resource['serial']:raise ValueError('Task command selects another device')
    if name=='glove_open' and (resource['kind']!='glove' or command.get('serial')!=resource['serial']):
        raise ValueError('Task command selects another glove')
    if name=='retarget_binding_save':
        binding=object_value(command.get('binding'))
        if resource['kind']!='glove' or binding.get('glove_serial')!=resource['serial']:
            raise ValueError('Task binding selects another glove')


def execute(controller,task_id,expected_context,command,*,read_groups=None,group_action=None,group_lock=None):
    """Recheck the exact owner under the existing workflow lock before dispatch."""
    if (not isinstance(task_id,str) or not TASK.fullmatch(task_id)
            or not isinstance(expected_context,str) or not CONTEXT.fullmatch(expected_context)):
        raise ValueError('Invalid task view context')
    command=copy.deepcopy(command)
    def checked(groups=None,*,receipt=True):
        state=controller.snapshot()
        if not isinstance(state,dict):raise ValueError('Task owner state is unavailable')
        current,resource=_scope(state,task_id,groups)
        if not current['current'] or current['owner_context']!=expected_context:
            raise ValueError('任务设备或会话已变化，请刷新后重试 / Task device or session changed; refresh before retrying')
        _command(command,resource)
        if resource['kind']=='ensemble':
            if not callable(group_action):raise ValueError('Ensemble task dispatch is unavailable')
            result=group_action(command)
        else:
            result=controller.action(command)
        # A successful operation may replace its own owner epoch (Viewer ->
        # calibration -> SDK). Bind that one response to both epochs while we
        # still hold the corresponding workflow/catalog lock. A later browser
        # read must match the after epoch; this is never a general stale bypass.
        if isinstance(result,dict):
            result=dict(result);result.pop('task_receipt',None)
            # Controller methods return payloads; Handler adds the public ok
            # flag only after execute returns. Explicit failure is never a
            # receipt, including a payload supplied by a service adapter.
            if receipt and result.get('ok') is not False and not result.get('error'):
                after,_=_scope(controller.snapshot(),task_id,groups)
                if after['current']:
                    result['task_receipt']=dict(task_id=task_id,before_context=expected_context,
                        after_context=after['owner_context'])
        return result
    # The continuation worker itself holds workflow_lock. Its cancel is the
    # sole fast path: a complete run/attempt pair is rechecked atomically by
    # CalibratedTeleoperation.cancel, even if a replacement races this snapshot.
    if (isinstance(command,dict) and set(command)=={'name','run_id','attempt_id'}
            and command.get('name')=='calibrated_teleoperation_cancel'
            and all(isinstance(command[k],str) and re.fullmatch('[0-9a-f]{32}',command[k]) for k in ('run_id','attempt_id'))):
        # This path deliberately has no workflow lock. It must not certify an
        # unrelated replacement which races an exact-attempt cancellation.
        return checked(receipt=False)
    if (isinstance(command,dict) and set(command)=={'name','viewer_token','attempt_id'}
            and command.get('name')=='teleoperation_handover_cancel'
            and all(isinstance(command[k],str) and re.fullmatch('[0-9a-f]{32}',command[k]) for k in ('viewer_token','attempt_id'))):
        # Viewer teardown owns the same workflow lock. The handover service's
        # exact-attempt CAS and bridge pending-only check own this cancellation.
        return checked(receipt=False)
    # WorkspaceGroups already takes its catalog lock before posting to a
    # member's workflow lock. Never invert that order here. Only an ensemble
    # candidate takes the catalog lock; device checks below are purely local.
    if group_lock is not None and read_groups is not None:
        local=_scope(controller.snapshot(),task_id,None)[0]
        if not local['current']:
            if not group_lock.acquire(blocking=False):raise ValueError('Ensemble workspace is changing')
            try:
                if _group(task_id,read_groups) is not None:return checked(read_groups)
            finally:group_lock.release()
    lock=controller.calibration_handover.workflow_lock
    if not lock.acquire(blocking=False):raise ValueError('Another device task is switching')
    try:return checked()
    finally:lock.release()
