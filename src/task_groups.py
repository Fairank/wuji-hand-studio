"""Expose the existing ensemble coordinator as a stable task view.

This adapter neither creates a group nor connects any device. The original
ensemble panel remains the sole start/lease owner.
"""
import copy
import hashlib
import json
import threading
from urllib.parse import urlencode


class TaskWorkspaces:
    def __init__(self, server):
        from task_server import TaskServer
        self.server=server;self.devices=TaskServer(server)
        self.lock=threading.RLock();self.groups={};self.revision=0;self.signature=None

    def _groups(self, base):
        workspaces=getattr(self.server,'_workspaces',None)
        if workspaces is None:return []
        hands={t['endpoint_id']:t for t in base['tasks']
               if t['kind']=='hand' and not t.get('paired_into') and t['observed']['source']=='sdk'
               and t['status'] in ('connected','running')}
        with workspaces.lock:
            rows=copy.deepcopy(workspaces.rows)
            coordinators=dict(workspaces.coordinators)
        origin=f'http://127.0.0.1:{self.server.server_address[1]}'
        found=[]
        for row in rows:
            coordinator=coordinators.get(row['id']);run=coordinator.snapshot() if coordinator else {}
            if run.get('active') is True:
                # A workspace can contain idle siblings which did not join
                # this run. Scope the running task to the coordinator's exact
                # participants, never to the whole workspace catalog.
                actual=run.get('members')
                if (not isinstance(actual,list) or not 2<=len(actual)<=8
                        or any(not isinstance(member,dict) or not isinstance(member.get('id'),str) for member in actual)):
                    continue
                member_ids=[member['id'] for member in actual]
                if (len(set(member_ids))!=len(member_ids)
                        or any(member not in row['members'] or member not in hands for member in member_ids)):
                    continue
            else:member_ids=[member for member in row['members'] if member in hands]
            members=[hands[member] for member in member_ids]
            if len(members)<2:continue
            ident=hashlib.sha256(('ensemble:'+row['id']).encode()).hexdigest()[:24]
            # The private coordinator token identifies a restart; it is never
            # exposed to the task frame or accepted from a task command.
            token=getattr(coordinator,'token',None)
            scope=hashlib.sha256(json.dumps([row['id'],[(m['task_id'],m['revision']) for m in members],token],separators=(',',':')).encode()).hexdigest()
            previous=self.groups.get(ident)
            revision=previous['revision']+(previous['_scope']!=scope) if previous else 1
            params=urlencode(dict(embedded='1',task_view='1',task_id=ident,task_group=row['id']))
            label=str(row.get('label') or 'Hand ensemble')[:120]
            task=dict(task_id=ident,revision=revision,endpoint_id='main',kind='ensemble',
                label=label,label_zh=label,label_en=label,
                status='running' if run.get('active') is True else 'connected',
                devices=dict(hands=[m['devices']['hand'] for m in members]),
                intent=dict(group_id=row['id'],members=[m['endpoint_id'] for m in members]),
                observed=dict(stale=any(m['observed']['stale'] for m in members),hardware_active=run.get('active') is True,source='ensemble'),
                capabilities=dict(can_pause=run.get('active') is True,can_disconnect=False,can_pair=False,
                                  can_unpair=False,can_continue_teleoperation=False),
                view=dict(origin=origin,url=origin+'/?'+params+'#devices',page='devices',allow_native=False),
                _scope=scope)
            self.groups[ident]=task;found.append(task)
        return found

    def snapshot(self):
        base=self.devices.snapshot()
        with self.lock:
            groups=self._groups(base)
            signature=(base['revision'],tuple((g['task_id'],g['revision']) for g in groups))
            if signature!=self.signature:self.revision+=1;self.signature=signature
            return dict(base,revision=self.revision,tasks=base['tasks']+[
                {k:copy.deepcopy(v) for k,v in group.items() if not k.startswith('_')} for group in groups])

    def action(self, command):
        current=self.snapshot()
        ident=command.get('task_id') if isinstance(command,dict) else None
        group=next((t for t in current['tasks'] if t['task_id']==ident and t['kind']=='ensemble'),None)
        if group is None:return self.devices.action(command)
        if (set(command)-{'name','task_id','expected_revision','action'}
                or command.get('name','task_session_action')!='task_session_action'
                or type(command.get('expected_revision')) is not int
                or command['expected_revision']!=group['revision'] or command.get('action')!='pause'
                or group['capabilities']['can_pause'] is not True):
            raise ValueError('多手演示任务已变化，请核对当前任务 / Ensemble task changed; review the current task')
        workspaces=self.server._workspaces
        with workspaces.lock:
            # Re-observe under the catalog lock immediately before stopping.
            check=next((t for t in self._groups(self.devices.snapshot()) if t['task_id']==ident),None)
            if check is None or check['revision']!=group['revision'] or check['capabilities']['can_pause'] is not True:
                raise ValueError('Ensemble owner changed')
            return workspaces.coordinator(group['intent']['group_id']).stop()

    def allows_embedded_main(self, task_id):
        return any(t['task_id']==task_id and t['endpoint_id']=='main' for t in self.snapshot()['tasks'])
