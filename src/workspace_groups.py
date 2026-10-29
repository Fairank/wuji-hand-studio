"""Logical workspaces contain one or more independent hand sessions."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import secrets
import threading
import time
from group_coordinator import GroupCoordinator


class Discovery:
    def __init__(self,factory):
        self.factory=factory;self.lock=threading.RLock();self.state=dict(running=False,devices=[],error='',updated=0.)

    def snapshot(self):
        with self.lock:return copy.deepcopy(self.state)

    def start(self,address=''):
        from console_agent import validate_command
        validate_command(dict(name='discover',address=address))
        with self.lock:
            if self.state['running']:return self.snapshot()
            self.state=dict(running=True,devices=[],error='',updated=0.,address=address)
        try:threading.Thread(target=self.run,args=(address,),daemon=True,name='Read-only hand discovery').start()
        except Exception as error:
            with self.lock:self.state.update(running=False,error=str(error)[:700])
            raise
        return self.snapshot()

    def run(self,address):
        client=None;timer=None
        try:
            client=self.factory();timer=threading.Timer(20,client.close);timer.daemon=True;timer.start()
            stdin,stdout,stderr=client.exec_command(client.agent_command,timeout=20)
            def drain():
                try:
                    while stderr.read(4096):pass
                except Exception:pass
            threading.Thread(target=drain,daemon=True).start()
            stdin.write(json.dumps(dict(name='discover',address=address))+'\n');stdin.flush()
            for line in stdout:
                if not line.startswith('WUJI_JSON:'):continue
                event=json.loads(line[10:])
                if event.get('type')=='discovery':
                    with self.lock:self.state.update(devices=event['devices'],updated=time.time())
                    return
                if event.get('type') in ('error','notice'):raise ValueError('控制端不支持发现或扫描失败，请更新控制端 / Update controller or check discovery')
            raise ValueError('发现未完成，请检查控制端 / Discovery did not complete')
        except Exception as e:
            with self.lock:self.state['error']=str(e) if isinstance(e,ValueError) else '无法连接控制端 / Controller unavailable'
        finally:
            if timer:timer.cancel()
            if client:client.close()
            with self.lock:self.state['running']=False


class WorkspaceGroups:
    def __init__(self,fleet,controller,data):
        self.fleet,self.controller=fleet,controller;self.path=Path(data)/'hand_workspaces.json';self.lock=threading.RLock()
        self.discovery=Discovery(controller.factory);self.coordinators={}
        self.pending_serials={};self.persistence_error=None
        try:self.rows=json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:self.rows=[dict(id='main',label='主工作区 / Main workspace',members=['main'])]
        if not isinstance(self.rows,list) or any(not isinstance(r,dict) or not isinstance(r.get('members'),list) for r in self.rows):raise ValueError('Invalid workspace catalog')
        # Preserve earlier independent hand workspaces on migration.
        assigned={m for row in self.rows for m in row['members']}
        for r in fleet.children.values():
            if r['id'] not in assigned:self.rows.append(dict(id=r['id'],label=r['label'],members=[r['id']]))

    def save(self):
        temp=self.path.with_suffix('.pending');temp.write_text(json.dumps(self.rows,ensure_ascii=False),encoding='utf-8');temp.replace(self.path)

    def _commit_rows(self, candidate):
        """Caller holds lock; readers never see a failed metadata edit."""
        previous=self.rows;self.rows=candidate
        try:self.save()
        except Exception:
            self.rows=previous
            raise
        self.persistence_error=None

    def _restore_visible_rows(self, previous, message):
        # A refused external cleanup still owns a real child. Preserve its
        # visible membership even if storage also refuses the rollback. The
        # fleet catalog remains the source for constructor recovery.
        try:self._commit_rows(previous)
        except Exception:
            self.rows=previous;self.persistence_error=message

    def get(self,ident):
        row=next((r for r in self.rows if r['id']==ident),None)
        if row is None:raise ValueError('Unknown workspace / 工作区不存在')
        return row

    def read(self,ident):
        if ident=='main':return self.controller.snapshot()
        with self.fleet.lock:r=self.fleet.children.get(ident)
        if not r:raise ValueError('Hand session no longer exists')
        return self.fleet.get(r,'/api/state')

    def post(self,ident,command):
        if ident=='main':return self.controller.action(command)
        with self.fleet.lock:r=self.fleet.children.get(ident)
        if not r:raise ValueError('Hand session no longer exists')
        return self.fleet.post(r,command)

    def coordinator(self,ident):
        self.get(ident)
        if ident not in self.coordinators:self.coordinators[ident]=GroupCoordinator(self.read,self.post)
        return self.coordinators[ident]

    def snapshot(self):
        from bimanual_program import catalog
        with self.lock:rows=copy.deepcopy(self.rows);persistence_error=self.persistence_error
        endpoints=[dict(id='main',label='主手 / Primary hand',port=None)]+[self.fleet.public(r) for r in list(self.fleet.children.values())]
        def describe(r):
            try:
                s=self.read(r['id']);r.update(side=s['device_profile']['side'],generation=s['device_profile']['generation'],connected=s['connection']=='connected' and not s['stale'],serial=s.get('device_id'),busy=s['hardware'].get('active') is not False or s.get('group',{}).get('active') or s.get('glove',{}).get('busy'),message=s.get('message'),connection=s['connection'])
            except Exception:r.update(connected=False,connection='offline',busy=True)
            return r
        with ThreadPoolExecutor(max_workers=8) as pool:endpoints=list(pool.map(describe,endpoints))
        for row in rows:row['run']=self.coordinators[row['id']].snapshot() if row['id'] in self.coordinators else dict(active=False,phase='idle',members=[],reason='')
        return dict(workspaces=rows,endpoints=endpoints,actions=catalog(),discovery=self.discovery.snapshot(),persistence_error=persistence_error)

    def action(self,c):
        name=c['name'];ident=c.get('workspace','main')
        with self.lock:
            if name=='workspace_scan':return dict(discovery=self.discovery.start(c.get('address','')))
            if name=='workspace_create':
                label=c.get('label')
                if not isinstance(label,str) or not 1<=len(label.strip())<=40:raise ValueError('工作区名称为1–40字 / Name: 1–40 characters')
                if len(self.rows)>=8:raise ValueError('最多8个工作区 / Up to eight workspaces')
                row=dict(id=secrets.token_hex(8),label=label.strip(),members=[])
                self._commit_rows([*copy.deepcopy(self.rows),row]);return dict(workspace=copy.deepcopy(row))
            row=self.get(ident)
            if name=='workspace_camera':
                member=c.get('member')
                if member not in row['members']:raise ValueError('Wrong workspace member')
                return self.post(member,dict(name='view_camera',camera=c.get('camera')))
            if name.startswith('ensemble_'):
                if name=='ensemble_start' and self.persistence_error:
                    raise ValueError(self.persistence_error)
                run=self.coordinator(ident)
                if name=='ensemble_start':
                    if not isinstance(c.get('members'),list) or any(m not in row['members'] for m in c['members']):raise ValueError('Members must belong to the selected workspace')
                    return run.start(c)
                if name=='ensemble_beat':run.beat(c.get('lease'));return {}
                if name=='ensemble_stop':return run.stop()
                if name in ('ensemble_pause','ensemble_resume'):return run.request_control(name.removeprefix('ensemble_'),c.get('lease'))
            if any(r.snapshot()['active'] for r in self.coordinators.values()):raise ValueError('先停止编组再修改工作区 / Stop groups before editing workspaces')
            if name=='workspace_move':
                member=c.get('member');s=self.read(member)
                if s.get('group',{}).get('active') or s['hardware'].get('active') is not False or s['program']['active'] or s['glove'].get('busy'):raise ValueError('先停止此手的动作 / Stop this hand before moving it')
                candidate=copy.deepcopy(self.rows)
                for r in candidate:r['members']=[m for m in r['members'] if m!=member]
                next(r for r in candidate if r['id']==ident)['members'].append(member)
                self._commit_rows(candidate);return dict(workspace=copy.deepcopy(self.get(ident)))
            if name=='workspace_remove':
                if ident=='main' or row['members']:raise ValueError('只能移除空的附加工作区 / Only empty additional workspaces can be removed')
                self._commit_rows([copy.deepcopy(r) for r in self.rows if r['id']!=ident]);return dict(removed=True)
            if name=='workspace_add':
                warning=None;created=False
                if c.get('preview_profile'):
                    from device_profiles import profile
                    p=profile(c['preview_profile']);label=p['zh'];serial=None
                else:
                    scan=self.discovery.snapshot()
                    if scan['running'] or time.time()-scan['updated']>60:raise ValueError('请重新发现设备 / Discover devices again')
                    found=next((d for d in scan['devices'] if d['serial']==c.get('serial')),None)
                    if not found:raise ValueError('Device is not in the fresh discovery results')
                    serial=found['serial'];p=dict(id=found['generation']+'_'+(found.get('side_hint') or 'left'));label=serial
                    for member in ['main']+list(self.fleet.children):
                        s=self.read(member)
                        # A stale disconnected/error poll cannot disprove a
                        # request whose dispatch acknowledgement was lost.
                        if s['connection']=='connected' and s.get('device_id'):
                            self.pending_serials.pop(member,None)
                        if self.pending_serials.get(member)==serial:
                            if s['connection']=='connecting':raise ValueError('该手正在连接，请等待 / This hand is already connecting')
                            raise ValueError('该设备已有待确认会话，请在现有卡片中继续或断开 / This device has an unconfirmed session; continue or disconnect its existing card')
                        if s.get('device_id')==serial and s['connection'] in ('connecting','connected'):raise ValueError('该手已在工作台中，请移动现有设备 / This hand is already assigned; move its existing session')
                # Reuse the empty primary session only in its own workspace.
                main=self.controller.snapshot()
                if serial and 'main' in row['members'] and main['connection']=='disconnected' and not main['glove'].get('busy') and not main.get('group',{}).get('active') and main['hardware'].get('active') is False:
                    member='main';self.post(member,dict(name='device_profile_select',profile=p['id']))
                else:
                    # Reject an already-unwritable catalog before starting a
                    # new passive service. Commit can still fail later; reclaim
                    # only this newly created child in that case.
                    self._commit_rows(copy.deepcopy(self.rows))
                    device=self.fleet.create(label,p['id']);member=device['id']
                    created=True
                    candidate=copy.deepcopy(self.rows)
                    next(r for r in candidate if r['id']==ident)['members'].append(member)
                    try:self._commit_rows(candidate)
                    except Exception as error:
                        try:cleaned=self.fleet.remove(member).get('removed') is True
                        except Exception:cleaned=False
                        if not cleaned:
                            message='工作区保存失败，新会话尚未确认回收，请重试移除 / Workspace save failed; new session cleanup is unconfirmed. Retry removal.'
                            self._restore_visible_rows(candidate,message)
                            raise ValueError(message) from error
                        raise
                    row=self.get(ident)
                    if c.get('preview_profile') and p['id'].startswith('hand2'):
                        from view_camera import DEFAULT
                        # Native left/right meshes have opposite palm normals.
                        # Rotate the camera, never mirror the image or joint data.
                        try:self.post(member,dict(name='view_camera',camera=dict(DEFAULT,azimuth=270. if p['id'].endswith('_left') else 90.)))
                        except Exception:
                            # The passive session is already durably added.
                            # A camera ACK must not invite duplicate creation.
                            warning='preview_camera_unconfirmed'
                if serial:
                    self.pending_serials[member]=serial
                    try:
                        response=self.post(member,dict(name='connect',serial=serial,address=scan.get('address',''),auto_detect=True))
                        if isinstance(response,dict) and (response.get('ok') is False or response.get('error')):
                            raise ValueError(response.get('error') or 'Connection request was refused')
                    except Exception:
                        if created:
                            try:cleaned=self.action(dict(name='workspace_forget',workspace=ident,member=member)).get('removed') is True
                            except Exception:cleaned=False
                            if cleaned:raise
                        # Never report a failed addition or invite another
                        # child when the original dispatch/cleanup is unknown.
                        warning='workspace_connection_unconfirmed'
                result=dict(member=member,workspace=copy.deepcopy(self.get(ident)),connecting=None if warning=='workspace_connection_unconfirmed' else bool(serial))
                if warning:result['warning']=warning
                return result
            if name=='workspace_connect':
                member=c.get('member')
                if member not in row['members']:raise ValueError('Wrong workspace member')
                return self.post(member,dict(name='connect',auto_detect=True,address=c.get('address',''),serial=c.get('serial','')))
            if name=='workspace_disconnect':
                member=c.get('member')
                if member not in row['members']:raise ValueError('Wrong workspace member')
                result=self.post(member,dict(name='session_close'))
                if not isinstance(result,dict) or result.get('ok') is not True:
                    raise ValueError(result.get('error') if isinstance(result,dict) and result.get('error') else '会话断开尚未确认，请重试 / Session disconnect is unconfirmed; retry')
                self.pending_serials.pop(member,None)
                return result
            if name=='workspace_forget':
                member=c.get('member')
                if member=='main' or member not in row['members']:raise ValueError('Primary hand session cannot be removed')
                previous=copy.deepcopy(self.rows);candidate=copy.deepcopy(self.rows)
                next(r for r in candidate if r['id']==ident)['members'].remove(member)
                # Persist the intended membership first. A storage failure must
                # never destroy the child and then resurrect a fake member.
                self._commit_rows(candidate)
                try:
                    result=self.fleet.remove(member)
                    if result.get('removed') is not True:
                        raise ValueError('会话移除尚未确认，请重试 / Session removal is unconfirmed; retry')
                except Exception:
                    self._restore_visible_rows(previous,'会话未移除，工作区恢复保存未完成，请重试 / Session retained; workspace rollback could not be saved. Retry removal.')
                    raise
                self.pending_serials.pop(member,None)
                return result
            raise ValueError('Unknown workspace command')
