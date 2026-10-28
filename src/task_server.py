"""Main-window adapter for verified device tasks and explicit pairing."""
import os
import threading
import json
from urllib.error import HTTPError

from task_pairing import TaskPairing
from task_sessions import TaskSessions, object_value


class TaskServer:
    def __init__(self, server):
        self.server=server
        self.pairing=TaskPairing(self.read,self.dispatch,group_check=self.check_group)
        self.registry=TaskSessions(self.endpoints,self.dispatch,
            pair=self.pairing.start,unpair=self.pairing.unpair,
            continuation=self.continue_task,continuation_status=self.continuation_status)

    def read(self, ident):
        if ident=='main':return self.server.controller.snapshot()
        fleet=getattr(self.server,'_fleet',None)
        if fleet is None:return None
        with fleet.lock:record=fleet.children.get(ident)
        if record is None:return None
        return fleet.get(record,'/api/state')

    def endpoints(self):
        origin=f'http://127.0.0.1:{self.server.server_address[1]}'
        rows=[dict(id='main',origin=origin,state=self.read('main'))]
        fleet=getattr(self.server,'_fleet',None)
        if fleet is not None:
            with fleet.lock:ports={ident:row['port'] for ident,row in fleet.children.items()}
            for row in fleet.inventory_snapshots():
                if row['id'] in ports:
                    rows.append(dict(id=row['id'],origin=f'http://127.0.0.1:{ports[row["id"]]}',state=row['state']))
        return rows

    def check_group(self, task):
        workspaces=getattr(self.server,'_workspaces',None)
        if workspaces is None:return
        with workspaces.lock:
            for row in workspaces.rows:
                coordinator=workspaces.coordinators.get(row['id'])
                if not coordinator:continue
                run=coordinator.snapshot()
                if not run.get('active'):continue
                actual=run.get('members')
                valid=(isinstance(actual,list) and 2<=len(actual)<=8
                    and all(isinstance(member,dict) and isinstance(member.get('id'),str) for member in actual))
                ids=[member['id'] for member in actual] if valid else []
                valid=valid and len(set(ids))==len(ids) and all(ident in row['members'] for ident in ids)
                # Catalog siblings can be idle while a selected subset runs.
                # Unknown participant metadata retains the conservative gate.
                participants=ids if valid else set(row['members'])|set(ids)
                if task['endpoint_id'] in participants:
                    raise ValueError('请先停止此设备所属的多手演示 / Stop this device\'s ensemble first')

    def dispatch(self, endpoint, expected_context, action, payload=None):
        if action in ('pause','stop','disconnect'):
            try:current=self.read(endpoint)
            except (OSError,ValueError):current=None
            self.pairing.cancel(endpoint,expected_context,current)
        elif action=='pair_prepare':self.pairing.raise_if_cancelled(endpoint)
        if endpoint=='main':
            return self.server.controller.task_context_action(expected_context,action,payload or {})
        fleet=getattr(self.server,'_fleet',None)
        if fleet is None:raise ValueError('Task controller is unavailable')
        with fleet.lock:record=fleet.children.get(endpoint)
        if record is None:raise ValueError('Task controller no longer exists')
        try:return fleet.scoped_post(record,expected_context,action,payload or {})
        except HTTPError as error:
            # A destination's typed refusal is the actionable result, not a
            # generic "409 Conflict". Never retry or switch to another owner.
            try:
                body=error.read(16385)
                value=json.loads(body) if len(body)<=16384 else {}
                reason=value.get('error') if isinstance(value,dict) else None
            except (ValueError,OSError):reason=None
            raise ValueError(reason[:700] if isinstance(reason,str) and reason else '目标工作区未接受此操作，请核对当前任务 / The destination workspace rejected this operation; review the task') from None

    def continuation_status(self, endpoint):
        try:return object_value(object_value(self.read(endpoint)).get('calibrated_teleoperation'))
        except (ValueError,OSError):return {}

    def continue_task(self, task):
        self.check_group(task)
        return self.dispatch(task['endpoint_id'],task['_context'],'continue_teleoperation',{})

    def snapshot(self):
        value=self.registry.snapshot();pairing=self.pairing.snapshot()
        for task in value['tasks']:
            if (task['task_id']!=pairing.get('task_id') or task['endpoint_id']!=pairing.get('source_endpoint')
                    or task['observed'].get('session')!=pairing.get('source_session')
                    or task['intent'].get('profile_id')!=pairing.get('source_profile')
                    or task['intent'].get('sdk_user')!=pairing.get('source_user')):continue
            if pairing['busy']:
                task['status']='pairing';task['observed']['pairing']=True
                task['capabilities'].update(can_pair=False,can_unpair=False,can_continue_teleoperation=False)
                if not task['capabilities'].get('blocked_reason'):
                    task['capabilities']['blocked_reason']='正在核对设备配对 / Verifying device pairing'
            elif pairing.get('phase') in ('error','cancelled') and pairing.get('error'):
                task['observed']['error']=pairing['error']
                if not task['capabilities'].get('blocked_reason'):task['capabilities']['blocked_reason']=pairing['error']
        return dict(**value,pairing=pairing)

    def action(self, command):
        pairing=self.pairing.snapshot()
        if (isinstance(command,dict) and pairing['busy']
                and command.get('task_id') in (pairing.get('task_id'),pairing.get('hand_task_id'))
                and command.get('action') in ('pair','unpair','continue_teleoperation')):
            raise ValueError('配对仍在处理中，请等待当前设备核对 / Pairing is still in progress; wait for the current devices')
        return self.registry.action(command)

    def allows_embedded_main(self, task_id):
        if not isinstance(task_id,str):return False
        return any(row['task_id']==task_id and row['endpoint_id']=='main'
                   for row in self.registry.snapshot()['tasks'])
