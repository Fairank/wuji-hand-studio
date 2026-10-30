"""One explicit target change, confirmed release, then read-only connection.

The existing workspace workflow lock spans the whole transaction. The native
file gate is deliberately not held while readers finish. No motor-start action
is dispatched here, and a failed new connection is never retried implicitly.
"""
import copy
from contextlib import contextmanager
import hashlib
import json
import os
import re
import secrets
import threading
import time

from cli_coordination import gate,reservation
from task_sessions import context_digest

SERIAL=re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z')


def unsettled(state):
    switch=state.get('connection_switch') or {}
    return bool(switch.get('busy') or (switch.get('user_catalog') or {}).get('busy')
                or switch.get('cleanup_error') or ((state.get('glove') or {}).get('session_cleanup') or {}).get('error'))


def task_id(kind,serial):return hashlib.sha256((kind+':'+serial).encode()).hexdigest()[:24]


def owner(state):
    """Actual local owners only; a selection/history never owns a device."""
    glove=state.get('glove') or {};viz=state.get('official_viz') or {};found=[]
    if glove.get('busy') or glove.get('connection')=='receiving':
        cleanup=glove.get('session_cleanup') or {}
        retryable=(cleanup.get('retryable') is True and glove.get('connection')=='error')
        identity=cleanup.get('identity') if retryable else None
        if identity is not None:
            if (not isinstance(identity,dict) or set(identity)!={'glove_serial','sdk_user_id','profile'}
                    or cleanup.get('session_id')!=glove.get('session_id')
                    or glove.get('glove_serial') not in (None,'',identity.get('glove_serial'))):
                raise ValueError('Original SDK cleanup identity is unconfirmed')
            observed={**glove,'glove_serial':identity.get('glove_serial'),'user':{'user_id':identity.get('sdk_user_id')},'profile':identity.get('profile')}
        else:observed=glove
        if (glove.get('busy') is not True or glove.get('connection')!='receiving' and not retryable
                or type(glove.get('session_id')) is not int or not SERIAL.fullmatch(observed.get('glove_serial') or '')
                or not isinstance((observed.get('user') or {}).get('user_id'),str)
                or observed.get('profile')!=(state.get('device_profile') or {}).get('id')):
            raise ValueError('手套连接尚未核对完成 / Glove ownership is not confirmed')
        found.append(dict(mode='teleoperation',kind='glove',serial=observed['glove_serial'],session=glove['session_id'],
            user_id=observed['user']['user_id'],profile=observed.get('profile')))
    if any(viz.get(k) for k in ('active','busy','reserved','session_id','scanning')):
        reported=viz.get('reported_device') or {};selected=viz.get('device') or {};sn=reported.get('sn')
        if (viz.get('backend')!='windows_native' or viz.get('active') is not True or viz.get('phase')!='running'
                or viz.get('busy') or not SERIAL.fullmatch(sn or '') or not isinstance(viz.get('session_id'),str)):
            raise ValueError('官方画面连接尚未核对完成 / Viewer ownership is not confirmed')
        from device_inventory import normalize_devices
        if selected.get('sn',selected.get('serial',sn))!=sn:raise ValueError('Viewer selected identity changed')
        rows=normalize_devices([selected,reported] if selected else [reported],source='official_cli',observed_at=time.time())['devices']
        row=next((r for r in rows if r['sn']==sn and r['kind'] in ('glove','hand')),None)
        if row is None:raise ValueError('Viewer identity is ambiguous')
        found.append(dict(mode='official',kind=row['kind'],serial=sn,session=viz['session_id']))
    if state.get('connection')!='disconnected':
        ident=state.get('identified_device') or {};sn=state.get('device_id')
        if (state.get('connection')!='connected' or ident.get('source')!='sdk_identity'
                or ident.get('serial')!=sn or not SERIAL.fullmatch(sn or '')
                or ident.get('profile')!=(state.get('device_profile') or {}).get('id')):
            raise ValueError('灵巧手连接尚未核对完成 / Hand ownership is not confirmed')
        found.append(dict(mode='hand',kind='hand',serial=sn,session=state.get('session_generation')))
    if len(found)>1:raise ValueError('多个连接需要先核对所属任务 / Multiple local device owners require review')
    return found[0] if found else None


class ConnectionSwitch:
    def __init__(self,controller,*,clock=time.monotonic,sleep=time.sleep):
        self.controller=controller;self.workflow_lock=controller.calibration_handover.workflow_lock
        self.lock=threading.RLock();self._thread=None;self.viewer_token=None;self.cancelled=False
        self.clock=clock;self.sleep=sleep;self.resume=None;self.resume_config=None;self.deadline=None;self._saved_resume=None
        self.state=dict(busy=False,phase='idle',attempt_id=None,error=None,receipt=None,target=None,source=None,cancel_requested=False,
                        automatically_follows=False,automatically_enables=False)
        self.catalog=dict(busy=False,status='idle',users=[],error=None,checked_at=None)
        self.catalog_config=None;self.requests=set();self.catalog_cleanup_error=None
        self.catalog_client=None;self.catalog_thread=None;self.catalog_finished=None
        self._cleanup_owner=None
        self.idle_check=lambda:None
        self.preflight_check=lambda source:None

    @property
    def internal(self):return self._thread==threading.get_ident()

    def _set(self,**values):
        with self.lock:self.state.update(values)

    def request_cancel(self):
        with self.lock:
            if self.state['busy'] and not self.internal:self.cancelled=True

    def cancel(self,command):
        if (not isinstance(command,dict) or set(command)!={'name','request_id','attempt_id'}
                or command.get('name')!='connection_switch_cancel'
                or any(not isinstance(command[k],str) or not re.fullmatch('[0-9a-f]{32}',command[k])
                       for k in ('request_id','attempt_id'))):
            raise ValueError('Invalid connection switch cancellation')
        with self.lock:
            if (self.state.get('busy') is not True or self.state.get('phase') not in ('preflight','stopping','connecting')
                    or any(command[k]!=self.state.get(k) for k in ('request_id','attempt_id'))):
                raise ValueError('切换已完成或任务已变化，请核对当前设备任务 / Switch finished or changed; review the current device task')
            self.cancelled=True;self.state['cancel_requested']=True
            self.requests.add(command['request_id'])
        return dict(connection_switch=self.snapshot(self.controller.snapshot()))

    def _check_cancel(self):
        if self.cancelled:raise ValueError('已请求取消本次切换；请核对当前设备会话 / Switch cancellation requested; review the current device session')
        if self.deadline is not None and self.clock()>=self.deadline:raise ValueError('切换等待超时，未继续接入 / Switch time budget expired; no further connection was started')

    @contextmanager
    def open_guard(self,attempt_id):
        """Serialize the final local open dispatch with this attempt's cancel.

        The caller already owns Bridge.lock. This scope reads only local
        switch fields; it must not acquire controller locks or perform I/O.
        """
        with self.lock:
            if (not isinstance(attempt_id,str) or not re.fullmatch('[0-9a-f]{32}',attempt_id)
                    or self.state.get('busy') is not True or self.state.get('phase')!='connecting'
                    or self.state.get('attempt_id')!=attempt_id):
                raise ValueError('设备用途切换已改变，未接入手套 / Device purpose switch changed; glove was not opened')
            self._check_cancel()
            yield

    def check_parent(self):
        """A registered child retains its parent's existing workspace gates."""
        parent=os.environ.get('WUJI_FLEET_PARENT','')
        if not parent:return
        ident=os.environ.get('WUJI_FLEET_ID','');token=os.environ.get('WUJI_FLEET_OWNER_TOKEN','')
        if (not re.fullmatch(r'http://127\.0\.0\.1:[0-9]{4,5}',parent)
                or not re.fullmatch('[0-9a-f]{16}',ident) or not token):raise ValueError('Parent workspace identity is unavailable')
        from urllib.request import Request,urlopen
        state=self.controller.snapshot()
        body=dict(workspace=ident,request_id=self.state['request_id'],attempt_id=self.state['attempt_id'],
                  expected_owner=context_digest(state))
        request=Request(parent+'/api/connection-switch-owner-check',data=json.dumps(body).encode(),
            headers={'Content-Type':'application/json','X-Workbench-Owner':token})
        try:
            with urlopen(request,timeout=10) as response:result=json.load(response)
        except (OSError,ValueError):raise ValueError('无法核对主工作台的设备任务 / Parent workspace ownership could not be verified') from None
        if not isinstance(result,dict) or result.get('ok') is not True:raise ValueError('Parent workspace rejected this device transition')

    def _config(self):
        from bridge_config import load_config
        return hashlib.sha256(json.dumps(load_config(),sort_keys=True).encode()).hexdigest()

    def _saved_supported(self):
        from bridge_config import load_config
        from official_device_discovery import use_native
        try:return use_native(load_config()) is True
        except (OSError,ValueError,RuntimeError):return False

    def _cleanup_error(self,state):
        with self.lock:
            error=self.state.get('cleanup_error');source=self.state.get('source') or {}
            responsibility=copy.deepcopy(self._cleanup_owner);attempt=self.state.get('attempt_id')
        if not error:return None
        glove=state.get('glove') or {};cleanup=glove.get('session_cleanup') or {}
        if responsibility is not None:
            # A failed newly-started SDK belongs to this attempt, not the
            # already released source (which may have been no owner at all).
            # Late ACKs retire only this exact resource-cleanup diagnostic.
            hardware=glove.get('hardware') or {}
            if (responsibility.get('attempt_id')==attempt
                    and type(responsibility.get('session')) is int
                    and type(glove.get('session_id')) is int and type(cleanup.get('session_id')) is int
                    and glove.get('session_id')==responsibility['session']
                    and cleanup.get('session_id')==responsibility['session']
                    and all(cleanup.get(key) is True for key in ('acknowledged','cleanup_confirmed','reader_finished','transport_closed'))
                    and cleanup.get('error') is None and glove.get('busy') is False
                    and glove.get('follow_pending',False) is False and glove.get('follow_unconfirmed',False) is False
                    and hardware.get('active',False) is False
                    and ('stop_confirmed' not in hardware or hardware['stop_confirmed'] is True)):
                with self.lock:
                    if self._cleanup_owner==responsibility and self.state.get('attempt_id')==attempt:
                        self._cleanup_owner=None;self.state['cleanup_error']=None
                        return None
                    return self.state.get('cleanup_error')
            return error
        if source.get('mode')=='teleoperation' and glove.get('session_id')==source.get('session'):
            if cleanup.get('cleanup_confirmed') is True or cleanup.get('retryable') is True:return None
            hw=glove.get('hardware') or {}
            if (glove.get('connection')=='receiving' and hw.get('active') is False
                    and hw.get('stop_confirmed') is True and not cleanup.get('error')):return None
        elif source.get('mode')=='official':
            viz=state.get('official_viz') or {}
            if (viz.get('phase')=='idle' and not any(viz.get(k) for k in ('active','busy','session_id','reserved'))
                    and not reservation(self.controller.official_viz.folder)):return None
        elif source.get('mode')=='hand':
            row=getattr(self.controller,'feedback_sessions',{}).get(source.get('session')) or {}
            hw=row.get('hardware') or {}
            if (row.get('worker_closed') and row.get('reader_finished') and row.get('transport_closed')
                    and not row['thread'].is_alive() and hw.get('active') is False and hw.get('stop_confirmed') is True):return None
            hw=state.get('hardware') or {}
            if (state.get('connection')=='connected' and state.get('device_id')==source.get('serial')
                    and state.get('session_generation')==source.get('session')
                    and hw.get('active') is False and hw.get('stop_confirmed') is True):return None
        return error

    def _blocked(self,state):
        from device_termination import unconfirmed
        from desktop_lifecycle import calibration_cleanup_required
        if self.state['busy'] or self.catalog['busy']:return '设备任务正在切换 / Device transition is in progress'
        try:current=owner(state)
        except ValueError as error:return str(error)
        # A positively running, exact local owner may be stopped by this
        # explicit switch. Historical/unknown acknowledgements remain blocking.
        active_device=(state.get('glove') or {}).get('feedback',{}).get('device_id') if current and current['mode']=='teleoperation' else state.get('device_id')
        active_hw=(state.get('glove') or {}).get('hardware',{}) if current and current['mode']=='teleoperation' else state.get('hardware',{})
        epoch=(state.get('glove') or {}).get('session_id') if current and current['mode']=='teleoperation' else getattr(self.controller,'session_epochs',{}).get(state.get('session_generation'))
        pending=[r for r in unconfirmed(state) if not (current and active_hw.get('active') is True and active_device
                 and r.get('device_id')==active_device and r.get('source') in ('glove','hand')
                 and r.get('session',epoch)==epoch)]
        if pending or calibration_cleanup_required(state):return '设备退出或停用尚未确认 / Device cleanup or stop is unconfirmed'
        if (state.get('hardware') or {}).get('active') is not False and (state.get('hardware') or {}).get('active') is not True:return '灵巧手停用结果尚未确认 / Hand stop is unconfirmed'
        g=state.get('glove') or {};hw=g.get('hardware') or {}
        if (g.get('feedback') or {}).get('device_id') and type(hw.get('active')) is not bool:
            return '已配对灵巧手的停用状态未知，未切换会话 / Paired hand disable state is unknown; session was not switched'
        cleanup=g.get('session_cleanup') or {}
        if self._cleanup_error(state) or (cleanup.get('error') and not cleanup.get('retryable')):
            return '手套退出尚未确认 / Glove cleanup is unconfirmed'
        if (hw.get('active',False) is not False and hw.get('active',False) is not True) or g.get('follow_unconfirmed') or g.get('follow_pending'):
            return '灵巧手停用结果尚未确认 / Hand stop is unconfirmed'
        if g.get('hand_unpairing'):return '灵巧手正在退出 / Hand cleanup is in progress'
        if any((state.get(k) or {}).get('busy') for k in ('calibration_connection','teleoperation_connection','calibrated_teleoperation','maintenance','parameter_sync')):
            return '请等待当前设备任务完成 / Wait for the current device task'
        if (state.get('group') or {}).get('active') or (state.get('calibration') or {}).get('running'):
            return '请先结束当前动作或标定 / Finish the current action or calibration first'
        if any((state.get('official_studio') or {}).get(k) for k in ('active','busy','reserved')):
            return '请先结束 Studio 会话 / Finish the Studio session first'
        try:owner(state)
        except ValueError as error:return str(error)
        return None

    def snapshot(self,state):
        with self.lock:
            result=copy.deepcopy(self.state);catalog=copy.deepcopy(self.catalog);resume=copy.deepcopy(self.resume)
        reason=self._blocked(state)
        try:
            if self.resume_config!=self._config():resume=None
        except (OSError,ValueError,RuntimeError):resume=None
        result['cleanup_error']=self._cleanup_error(state)
        try:
            current=owner(state);owner_error=None
            if current is not None:
                current={**current,'purpose':{'official':'cli_view','teleoperation':'teleoperation','hand':'hand_feedback'}[current['mode']]}
        except ValueError as error:
            current=None;owner_error=str(error)[:700]
        return {**result,'expected_owner':context_digest(state),'can_switch':reason is None,
                'blocked_reason':reason,'resume':resume,'user_catalog':catalog,
                'current_owner':current,'owner_error':owner_error,
                'saved_profile_supported':self._saved_supported()}

    def users(self):
        if not self.workflow_lock.acquire(blocking=False):raise ValueError('Another device task is switching')
        client=None;finished=threading.Event();cancel=threading.Event();result={};started=False
        try:
            if self.catalog.get('busy') and self.catalog_thread is not None:
                if self.catalog_thread.is_alive():raise ValueError('Wearer directory is still closing')
                if self.catalog_client and self.catalog_client.close() is False:
                    raise ValueError('遥操作档案读取退出尚未确认 / Profile read cleanup is unconfirmed')
                self.catalog_cleanup_error=None;self.catalog['busy']=False
            if self.state['busy'] or self.catalog['busy']:raise ValueError('A device transition is in progress')
            self.controller.teleoperation_handover._runtime()
            config=self._config()
            with self.lock:self.catalog.update(busy=True,status='loading',error=None)
            def read():
                nonlocal client
                try:
                    client=self.controller.factory()
                    self.catalog_client=client
                    if cancel.is_set():raise ValueError('遥操作档案读取已停止 / Profile read was stopped')
                    stdin,stdout,_=client.exec_command(client.agent_command,timeout=12)
                    stdin.write('{"name":"sdk_user_catalog"}\n');stdin.flush()
                    for line in stdout:
                        if line.startswith('WUJI_JSON:'):
                            event=json.loads(line[10:])
                            if event.get('type')=='sdk_user_catalog':
                                if event.get('error'):raise ValueError(event['error'])
                                from sdk_user_catalog import validate_report
                                result.update(validate_report(event.get('catalog')))
                    if 'users' not in result:raise ValueError('Controller did not return SDK wearer identities')
                except Exception as error:result['error']=str(error)[:700]
                finally:
                    try:
                        if client and client.close() is False:raise ValueError('Metadata transport did not confirm exit')
                    except Exception as error:
                        result['error']='Controller metadata cleanup unconfirmed: '+str(error)[:500]
                        self.catalog_cleanup_error=result['error']
                    finished.set()
            thread=threading.Thread(target=read,name='SDK wearer directory',daemon=True)
            self.catalog_thread=thread;self.catalog_finished=finished;thread.start();started=True
            if not finished.wait(12):
                cancel.set()
                if client and client.close() is False:
                    self.catalog_cleanup_error='遥操作档案读取退出尚未确认 / Profile read cleanup is unconfirmed'
                if not finished.wait(2):raise ValueError('Wearer directory is still closing')
                raise ValueError('Wearer directory read timed out')
            thread.join(.2)
            if thread.is_alive():raise ValueError('遥操作档案读取正在结束 / Profile read is still closing')
            if result.get('error'):raise ValueError(result['error'])
            if config!=self._config():raise ValueError('Controller configuration changed')
            with self.lock:
                self.catalog.update(status='ready',users=result['users'],error=None,checked_at=time.time())
                self.catalog_config=config
            return dict(connection_switch=self.snapshot(self.controller.snapshot()))
        except Exception as error:
            with self.lock:self.catalog.update(status='failed',users=[],error=str(error)[:700])
            raise
        finally:
            with self.lock:
                if started:self.catalog['busy']=not finished.is_set() or thread.is_alive() or self.catalog_cleanup_error is not None
            self.workflow_lock.release()

    def _target(self,command,state,source,pair_target=None):
        if (not isinstance(command,dict) or set(command)!={'name','request_id','expected_owner','target'}
                or command.get('name')!='connection_switch' or command.get('expected_owner')!=context_digest(state)):
            raise ValueError('设备会话已变化，请重新选择 / Device session changed; select again')
        target=copy.deepcopy(command['target'])
        if (not isinstance(target,dict) or set(target)-{'mode','serial','profile','user_id','theme','address','timeout_ms','hand_serial'}
                or target.get('mode') not in ('official','teleoperation','hand')
                or not isinstance(target.get('serial'),str) or not SERIAL.fullmatch(target['serial'])):
            raise ValueError('Invalid explicit connection target')
        mode=target['mode'];sn=target['serial']
        if 'hand_serial' in target and (mode!='teleoperation' or not isinstance(target['hand_serial'],str)
                or not SERIAL.fullmatch(target['hand_serial'])):raise ValueError('Invalid paired hand target')
        if 'timeout_ms' in target and (type(target['timeout_ms']) is not int or not 100<=target['timeout_ms']<=2000):
            raise ValueError('Invalid glove timeout')
        inventory=self.controller.device_inventory()
        if inventory.get('workspace_errors'):raise ValueError('Another workspace state is unavailable')
        row=next((r for r in inventory['devices'] if r['sn']==sn and not r.get('conflict')
                  and (not r.get('stale') or source and source['serial']==sn)),None)
        if row is None:raise ValueError('请先查找并选择目标设备 / Find and select the target device first')
        if row.get('workspace_owners'):raise ValueError('所选设备由另一个任务使用 / Target device belongs to another task')
        if mode=='teleoperation':
            self.controller.teleoperation_handover._runtime()
            from device_profiles import profile
            side=row.get('side')
            if source and source['serial']==sn:
                if source['mode']=='official':
                    with gate(self.controller.official_viz.folder):
                        side=self.controller.official_viz.connection_record(sn,source['session']).get('side')
                elif source['mode']=='teleoperation':side=source['profile'].rsplit('_',1)[-1]
            if side is not None and side not in ('left','right'):
                raise ValueError('手套左右信息无效，请重新核对 / Glove side is invalid; review the selected device')
            # UDP discovery may omit side. The existing mapping selection is
            # then only target intent; the SDK still verifies actual hand_side
            # before subscribing or allowing any hand action.
            selected=profile(target['profile'] if 'profile' in target else
                (state['device_profile']['id'] if side is None else
                 state['device_profile']['generation']+'_'+side))
            if row['kind']!='glove' or side is not None and side!=selected['side']:
                raise ValueError('手套左右与目标型号不符 / Glove side differs from target profile')
            target['profile']=selected['id']
            if 'timeout_ms' not in target:
                timeout=((state.get('glove') or {}).get('stream') or {}).get('timeout_ms') if source and source['mode']=='teleoperation' and source['serial']==sn else (
                    self.resume.get('timeout_ms') if self.resume and self.resume_config==self._config() and self.resume['serial']==sn else None)
                if type(timeout) is int and 100<=timeout<=2000:target['timeout_ms']=timeout
            continuation=state.get('calibrated_teleoperation') or {}
            # A purpose change is not a request to reimport/reopen the exact
            # SDK glove already owned by this workspace. Its current user is
            # authoritative even if an earlier calibration remains offered.
            if 'user_id' not in target and source and source.get('mode')=='teleoperation' and source['serial']==sn:
                target['user_id']=source['user_id']
            # An ordinary native purpose choice uses the wearer selected now,
            # not an older completed run or remembered SDK profile. Resolve it
            # while the existing source is still retained. Explicit SDK user
            # choices and the actual current SDK owner remain authoritative.
            if ('user_id' not in target and self._saved_supported()
                    and callable(getattr(self.controller,'_saved_profile_preflight',None))):
                proof=self.controller._saved_profile_preflight(sn,selected['side'],cancel_check=self._check_cancel)
                self._check_cancel()
                if (not isinstance(proof,dict) or proof.get('source_kind')!='saved_profile'
                        or proof.get('serial')!=sn or proof.get('side')!=selected['side']
                        or not isinstance(proof.get('source_user_id'),str)):
                    raise ValueError('已存标定资料的设备或佩戴者不符 / Saved calibration device or wearer differs')
                target['_saved_profile']=proof;target['user_id']=proof['source_user_id']
            if ('user_id' not in target and continuation.get('can_continue') and continuation.get('serial')==sn
                    and continuation.get('side')==selected['side']):
                target['_continuation']={k:continuation[k] for k in ('run_id','expected_revision')}
                self.controller.calibrated_teleoperation._current_proof()
            else:
                if 'user_id' not in target:
                    if source and source.get('mode')=='teleoperation' and source['serial']==sn:
                        target['user_id']=source['user_id']
                    elif (self.resume and self.resume_config==self._config() and self.resume['serial']==sn
                          and self.resume['profile']==target['profile']):target['user_id']=self.resume['user_id']
                saved_hint=self._saved_resume
                saved_resume=(saved_hint and saved_hint['config']==self._config()
                    and saved_hint['serial']==sn and saved_hint['profile']==selected['id']
                    and target.get('user_id')==saved_hint['user_id']
                    and (not source or source['mode']!='teleoperation'))
                if ('_saved_profile' not in target and ('user_id' not in target or saved_resume) and self._saved_supported()
                        and callable(getattr(self.controller,'_saved_profile_preflight',None))):
                    proof=self.controller._saved_profile_preflight(sn,selected['side'],
                        **({'profile':saved_hint['source_profile']} if saved_resume else {}),cancel_check=self._check_cancel)
                    self._check_cancel()
                    if (not isinstance(proof,dict) or proof.get('source_kind')!='saved_profile'
                            or proof.get('serial')!=sn or proof.get('side')!=selected['side']
                            or not isinstance(proof.get('source_user_id'),str)
                            or saved_resume and proof.get('source_user_id')!=saved_hint['user_id']):
                        raise ValueError('已存标定资料的设备或佩戴者不符 / Saved calibration device or wearer differs')
                    target['_saved_profile']=proof;target['user_id']=proof['source_user_id']
                if not isinstance(target.get('user_id'),str):raise ValueError('请选择控制端佩戴者 / Select a controller wearer first')
                glove=state.get('glove') or {};known=glove.get('users') or []
                actual=(source and source['mode']=='teleoperation' and glove.get('controller_config')==self._config()
                        and any(isinstance(u,dict) and u.get('user_id')==target['user_id'] for u in known))
                resumed=(self.resume and self.resume_config==self._config() and self.resume['serial']==sn
                         and self.resume['profile']==target['profile'] and self.resume['user_id']==target['user_id'])
                catalog=(self.catalog_config==self._config() and self.catalog.get('status')=='ready'
                         and self.catalog.get('checked_at') and 0<=time.time()-self.catalog['checked_at']<120
                         and any(u['user_id']==target['user_id'] for u in self.catalog['users']))
                if not ('_saved_profile' in target or actual or resumed or catalog):raise ValueError('请刷新并选择实际SDK佩戴者 / Refresh and select an actual SDK wearer')
                from glove_protocol import validate
                validate(dict(name='glove_open',serial=sn,user_id=target['user_id'],timeout_ms=target.get('timeout_ms',250)))
            if 'hand_serial' in target:
                if '_continuation' in target or '_saved_profile' in target:raise ValueError('Continue calibration before selecting a hand')
                from glove_protocol import validate
                validate(dict(name='glove_prepare',serial=target['hand_serial'],address=target.get('address','')))
                feedback=(state.get('glove') or {}).get('feedback') or {};identity=feedback.get('identified_device') or {}
                exact_current=(source and source['mode']=='teleoperation' and source['serial']==sn
                    and feedback.get('connection')=='connected' and feedback.get('stale') is False
                    and feedback.get('device_id')==target['hand_serial'] and identity.get('source')=='sdk_identity'
                    and identity.get('serial')==target['hand_serial'] and identity.get('profile')==selected['id'])
                hand=next((r for r in inventory['devices'] if r['sn']==target['hand_serial']
                    and r.get('kind')=='hand' and (not r.get('stale') or exact_current) and not r.get('conflict')
                    and not r.get('workspace_owners') and r.get('generation')==selected['generation']
                    and r.get('side')==selected['side']),None)
                # The private pairing adapter has just verified and released
                # this exact hand in its original workspace. It may disappear
                # from that workspace's directory after release; the SDK still
                # verifies its actual identity before feedback is accepted.
                if (pair_target==dict(hand_serial=target['hand_serial'],profile=selected['id'],sdk_user=target['user_id'])):
                    hand=pair_target
                if hand is None:raise ValueError('请查找并选择空闲的同型号同侧灵巧手 / Find an available hand with the same model and side')
                target['_binding']=dict(generation=selected['generation'],side=selected['side'],
                    glove_serial=sn,sdk_user=target['user_id'],hand_serial=target['hand_serial'])
                target['_mapping']=self.controller.retarget.library.snapshot(target['_binding'])
        elif mode=='hand':
            self.controller.teleoperation_handover._runtime()
            if row['kind']!='hand':raise ValueError('Select a robotic hand')
            from device_profiles import profile
            selected=profile((row.get('generation') or '')+'_'+(row.get('side') or ''))
            target['profile']=selected['id']
            # The original connector verifies serial again. Caller-supplied
            # addresses never override the directory's exact target identity.
            target.pop('address',None)
        else:
            from bridge_config import load_config
            if load_config().get('mode') not in ('wsl','local'):
                raise ValueError('请先配置官方画面环境 / Configure the official Viewer environment first')
            if row['kind']=='hand' and row.get('generation')=='hand1':raise ValueError('官方画面暂不支持一代手 / Official Viewer does not support Hand 1')
            if target.get('theme','light') not in ('light','dark'):raise ValueError('Invalid Viewer theme')
        return target

    def _check_mapping(self,target):
        if '_mapping' in target and self.controller.retarget.library.snapshot(target['_binding'])!=target['_mapping']:
            raise ValueError('目标映射在切换中已变化，请重新连接 / Target mapping changed during the switch; connect again')

    def _can_attach(self,state,target):
        """An equivalent target can attach without restarting the glove stream."""
        g=state.get('glove') or {};stream=g.get('stream') or {};solver=stream.get('solver') or {}
        return bool('_mapping' in target and g.get('connection')=='receiving' and stream.get('fresh') is True
            and not (g.get('feedback') or {}).get('device_id') and not g.get('hardware')
            and not self.controller.glove.lease and g.get('glove_serial')==target['serial']
            and (g.get('user') or {}).get('user_id')==target['user_id'] and g.get('profile')==target['profile']
            and solver.get('engine')=='sdk' and solver.get('applied') is True
            and self.controller.tuning.context(target['_binding'])['engine']=='sdk'
            and stream.get('retarget')==target['_mapping']['settings'])

    def _official_preflight(self):
        """Validate the existing route without preparing software or a device."""
        from bridge_config import load_config
        from official_device_discovery import use_native
        config=load_config()
        self._check_cancel()
        # The original exact connection_record/stop_owned proof is native-only.
        # Keep explicit custom/local routes intact, rather than launch a Viewer
        # for which this transaction cannot prove the destination or release.
        if not use_native(config):
            raise ValueError('此控制端请使用原有官方连接入口，当前连接已保留 / Use the existing official connection entry for this controller; the current connection was retained')
        from native_cli import resolve
        try:resolve(strict=True)
        except (OSError,ValueError) as error:
            raise ValueError('请先在设置中修复官方组件 / Repair the official component in Settings first: '+str(error)[:500]) from error
        self._check_cancel()

    def _wait(self,predicate,seconds,message):
        until=min(self.clock()+seconds,self.deadline or float('inf'))
        while True:
            self._check_cancel()
            result=predicate()
            if result:return result
            if self.clock()>=until:raise ValueError(message)
            self.sleep(.04)

    def _stop(self,source):
        if source is None:return
        c=self.controller
        state=c.snapshot()
        if (state.get('program') or {}).get('active'):
            c.program.stop()
            self._wait(lambda:not c.program.snapshot().get('active'),3,'Playlist stop is unconfirmed')
        hardware=(state.get('glove') or {}).get('hardware') or {} if source['mode']=='teleoperation' else state.get('hardware') or {}
        if hardware.get('active') is True:
            c.action(dict(name='glove_stop' if source['mode']=='teleoperation' else 'hardware_stop'))
            def stopped():
                state=c.snapshot();now=owner(state)
                if now!=source:raise ValueError('Device owner changed during stop')
                hw=(state.get('glove') or {}).get('hardware') or {} if source['mode']=='teleoperation' else state.get('hardware') or {}
                return hw.get('active') is False and hw.get('stop_confirmed') is True
            self._wait(stopped,6,'Hand disable acknowledgement is missing; original owner retained')
        if source['mode']=='official':
            c.official_viz.stop_owned(source['serial'],source['session'],require_glove=False)
        elif source['mode']=='teleoperation':
            proof=c.glove.stop_session_confirmed(source['session'],expected_serial=source['serial'],
                expected_user=source['user_id'],expected_profile=source['profile'])
            if proof.get('cleanup_confirmed') is not True:raise ValueError('SDK cleanup is unconfirmed')
        else:c.stop_feedback_confirmed(source['session'],source['serial'])

    def release_for_calibration(self,command,*,cancel_check=None):
        """Final explicit calibration Start may release its exact SDK glove.

        Reuse the normal switch's owner, stop acknowledgement and cleanup
        checks. No Viewer, glove stream, or motor is started by this method.
        """
        if not self.workflow_lock.acquire(blocking=False):raise ValueError('Another device task is switching')
        begun=False;source=None;released=False
        def check():
            self._check_cancel()
            if cancel_check is not None:cancel_check()
        try:
            state=self.controller.snapshot();source=owner(state)
            if cancel_check is not None:cancel_check()
            if source is None or source.get('mode')!='teleoperation':return False
            if source.get('serial')!=command.get('serial'):
                raise ValueError('请先结束另一手套的会话 / Another glove owns the SDK session')
            transition=self.controller.calibration_handover
            checked=state
            if (getattr(transition,'internal',False) and (state.get('calibration_connection') or {}).get('serial')==command.get('serial')):
                checked=copy.deepcopy(state);checked['calibration_connection']={}
            reason=self._blocked(checked)
            if reason:raise ValueError(reason)
            if command.get('side') not in ('left','right'):
                raise ValueError('请确认手套左右手 / Confirm the selected glove hand side')
            with self.lock:
                begun=True;self._thread=threading.get_ident();self.cancelled=False;self.viewer_token=None
                self._cleanup_owner=None
                self.deadline=self.clock()+45
                self.state.update(busy=True,phase='preflight',source=copy.deepcopy(source),cancel_requested=False,
                    target=dict(mode='calibration',serial=source['serial']),source_released=False,
                    request_id=secrets.token_hex(16),attempt_id=secrets.token_hex(16),
                    error=None,cleanup_error=None,receipt=None)
            from studio_component import enforce_session_owner
            enforce_session_owner(command)
            self._official_preflight()
            check()
            with gate(self.controller.official_viz.folder):
                if owner(self.controller.snapshot())!=source:raise ValueError('SDK owner changed before calibration')
                if reservation(self.controller.official_viz.folder):raise ValueError('Another official device owner is active')
                self.preflight_check(source);self.check_parent();check()
                if command.get('interface','guided')=='guided':
                    self.controller.calibration.preflight_guided(command.get('profile'),command['side'],
                        command.get('replace',False),create_profile=command.get('create_profile',False))
                if owner(self.controller.snapshot())!=source:raise ValueError('SDK owner changed during calibration preflight')
                check()
            self.resume={k:source[k] for k in ('serial','profile','user_id')}
            self.resume['binding']=copy.deepcopy(state.get('retarget_binding'));self.resume_config=self._config()
            self._set(phase='stopping');check();self._stop(source);released=True
            self._set(source_released=True);check()
            with gate(self.controller.official_viz.folder):
                if reservation(self.controller.official_viz.folder):raise ValueError('Another session acquired the device')
                self.idle_check();self.check_parent();check()
                if owner(self.controller.snapshot()) is not None:raise ValueError('SDK release is unconfirmed')
            self._set(phase='released_for_calibration')
            return True
        except Exception as error:
            if begun:self._set(phase='error',error=str(error)[:900],source_released=released)
            raise
        finally:
            if begun:
                with self.lock:self.state['busy']=False;self._thread=None;self.deadline=None
            self.workflow_lock.release()

    def start(self,command,*,pair_target=None):
        if not self.workflow_lock.acquire(blocking=False):raise ValueError('Another device task is switching')
        new_session=None;source=None;stopped=False;attempt_started=False
        try:
            request_id=command.get('request_id') if isinstance(command,dict) else None
            if not isinstance(request_id,str) or not re.fullmatch('[0-9a-f]{32}',request_id):raise ValueError('Invalid switch request identity')
            if request_id in self.requests:raise ValueError('This switch request was already submitted; read its saved result')
            state=self.controller.snapshot();reason=self._blocked(state)
            if reason:raise ValueError(reason)
            if (set(command)!={'name','request_id','expected_owner','target'}
                    or command.get('name')!='connection_switch' or command.get('expected_owner')!=context_digest(state)):
                raise ValueError('设备会话已变化，请重新选择 / Device session changed; select again')
            source=owner(state)
            with self.lock:
                attempt_started=True
                self._thread=threading.get_ident();self.cancelled=False
                self._cleanup_owner=None
                self.deadline=self.clock()+45
                self.viewer_token=source['session'] if source and source['mode']=='official' else None
                self.state.update(busy=True,phase='preflight',request_id=request_id,error=None,cleanup_error=None,cancel_requested=False,
                    unchanged=False,source_released=False,receipt=None,attempt_id=secrets.token_hex(16),
                    source=copy.deepcopy(source),target=None)
            # Inventory, saved-profile reads and component checks may wait.
            # Their exact cancellable intent must be visible before they run.
            target=self._target(command,state,source,pair_target)
            self._check_cancel()
            self.requests.add(request_id)
            attach=self._can_attach(state,target) if 'hand_serial' in target else False
            same=source and all(source.get(k)==target.get(k) for k in ('mode','serial'))
            if same and target['mode']=='teleoperation':same=(source['profile']==target['profile'] and source['user_id']==target.get('user_id')
                and '_continuation' not in target and (state.get('glove') or {}).get('connection')=='receiving'
                and not ((state.get('glove') or {}).get('session_cleanup') or {}).get('error'))
            if same and 'hand_serial' in target:
                feedback=(state.get('glove') or {}).get('feedback') or {}
                same=(feedback.get('device_id')==target['hand_serial'] and feedback.get('stale') is False
                    and (feedback.get('identified_device') or {}).get('source')=='sdk_identity'
                    and (feedback.get('identified_device') or {}).get('serial')==target['hand_serial']
                    and (feedback.get('identified_device') or {}).get('profile')==target['profile']
                    and state.get('retarget_binding')==target['_binding']
                    and ((state.get('glove') or {}).get('stream') or {}).get('retarget')==target['_mapping']['settings']
                    and (((state.get('glove') or {}).get('stream') or {}).get('solver') or {}).get('engine')=='sdk'
                    and (((state.get('glove') or {}).get('stream') or {}).get('solver') or {}).get('applied') is True)
            if same:
                with self.lock:
                    self._check_cancel()
                    self.state.update(busy=False,phase='connected',unchanged=True,
                        target={k:copy.deepcopy(v) for k,v in target.items() if not k.startswith('_')},
                        source=copy.deepcopy(source),receipt=None,error=None,cleanup_error=None,source_released=False,cancel_requested=False)
                return dict(connection_switch=self.snapshot(state))
            self._set(target={k:v for k,v in target.items() if not k.startswith('_')})
            from studio_component import enforce_session_owner
            enforce_session_owner(dict(name='glove_scan'))
            if target['mode']=='official':self._official_preflight()
            with gate(self.controller.official_viz.folder):
                if context_digest(self.controller.snapshot())!=command['expected_owner']:raise ValueError('Device owner changed before release')
                if source and source['mode']=='official':
                    self.controller.official_viz.connection_record(source['serial'],source['session'],require_glove=False)
                elif reservation(self.controller.official_viz.folder):raise ValueError('Another official device owner is active')
                self.preflight_check(source)
                self.check_parent()
                self._check_mapping(target)
                if context_digest(self.controller.snapshot())!=command['expected_owner']:raise ValueError('Device owner changed during preflight')
                self._check_cancel()
            if (source and source['mode']=='teleoperation'
                    and (state.get('glove') or {}).get('controller_config')==self._config()):
                self.resume={k:source[k] for k in ('serial','profile','user_id')}
                self.resume['binding']=copy.deepcopy(state.get('retarget_binding'))
                saved=self._saved_resume
                if (saved and saved['config']==self._config()
                        and all(saved.get(k)==source.get(k) for k in ('serial','profile','user_id'))):
                    self.resume.update(saved_profile=True,source_profile=saved['source_profile'])
                timeout=((state.get('glove') or {}).get('stream') or {}).get('timeout_ms')
                if type(timeout) is int and 100<=timeout<=2000:self.resume['timeout_ms']=timeout
                self.resume_config=self._config()
            if not attach:
                self._set(phase='stopping');self._stop(source);stopped=True;self._set(source_released=True);self._check_cancel()
            with gate(self.controller.official_viz.folder):
                if reservation(self.controller.official_viz.folder):raise ValueError('Another session acquired the device')
                if not attach:self.idle_check()
                self.check_parent();self._check_cancel();self._check_mapping(target)
                self._set(phase='connecting')
                if target['mode'] in ('teleoperation','hand') and target.get('profile')!=self.controller.state['device_profile']['id']:
                    self.controller.action(dict(name='device_profile_select',profile=target['profile']))
                if '_binding' in target:
                    self.controller.action(dict(name='retarget_binding_save',binding=target['_binding']))
                if attach:pass
                elif target['mode']=='official':
                    # Discovery refreshes only after confirmed release. The
                    # explicit serial remains fixed throughout this refresh.
                    if self.deadline-self.clock()<20:raise ValueError('Switch cleanup used its time budget; find devices again before retrying')
                    self.controller.official_viz.discover_inline()
                    self._check_cancel()
                    self.controller.action(dict(name='official_viz_start',serial=target['serial'],theme=target.get('theme','light')))
                elif target['mode']=='hand':
                    self.controller.action(dict(name='connect',serial=target['serial'],auto_detect=True))
                elif '_saved_profile' in target:
                    proof=target['_saved_profile']
                    bundle=self.controller._prepare_saved_profile(proof,cancel_check=self._check_cancel)
                    self._check_cancel()
                    new_session=self.controller._start_saved_profile_session(bundle,proof,expected_profile=target['profile'],
                        timeout_ms=target.get('timeout_ms',250))
                    if type(new_session) is not int or new_session<=0:raise ValueError('Saved profile did not return an exact SDK session')
                elif '_continuation' in target:
                    self.controller._continuation_profile=target['profile']
                    continued=self.controller.calibrated_teleoperation.execute(target['_continuation'],synchronous=True)
                    new_session=continued.get('sdk_session')
                    if continued.get('phase') in ('error','cancelled'):
                        raise ValueError(continued.get('error') or 'Calibration continuation did not start')
                else:
                    self.controller.action(dict(name='glove_scan'))
                    new_session=self.controller.glove.snapshot()['session_id']
            if target['mode']=='teleoperation' and not {'_continuation','_saved_profile'}&set(target) and not attach:
                def ready():
                    g=self.controller.glove.snapshot()
                    if g.get('session_id')!=new_session:raise ValueError('SDK session changed before opening')
                    if g.get('connection')=='error' or not g.get('busy'):raise ValueError(g.get('message') or 'SDK connection failed')
                    return g if g.get('connection')=='ready' else None
                g=self._wait(ready,12,'SDK directory did not become ready')
                if (not any((r.get('sn') or r.get('serial'))==target['serial'] for r in g.get('devices',[]))
                        or not any(u.get('user_id')==target['user_id'] for u in g.get('users',[]))):
                    raise ValueError('The exact glove or SDK wearer is no longer available')
                self._check_cancel()
                self._check_mapping(target)
                self.controller.action(dict(name='glove_open',serial=target['serial'],user_id=target['user_id'],
                    timeout_ms=target.get('timeout_ms',250)))
            def connected():
                current=self.controller.snapshot()
                if target['mode']=='official' and (current.get('official_viz') or {}).get('phase')=='error':
                    raise ValueError((current.get('official_viz') or {}).get('error') or 'Viewer connection failed')
                if target['mode']=='teleoperation' and (current.get('glove') or {}).get('connection')=='error':
                    raise ValueError((current.get('glove') or {}).get('message') or 'SDK connection failed')
                actual=owner(current)
                if actual and actual['mode']==target['mode'] and actual['serial']==target['serial']:
                    if target['mode']=='teleoperation' and (actual['profile']!=target['profile']
                            or 'user_id' in target and actual['user_id']!=target['user_id']):raise ValueError('SDK wearer identity differs')
                    if '_saved_profile' in target:
                        stream=(current.get('glove') or {}).get('stream') or {};solver=stream.get('solver') or {}
                        if (stream.get('fresh') is not True or solver.get('engine')!='sdk'
                                or solver.get('applied') is not True):return None
                    return current
                if target['mode']=='official' and (current.get('official_viz') or {}).get('phase')=='error':raise ValueError('Viewer connection failed')
                if target['mode']=='hand' and current.get('connection')=='error':raise ValueError(current.get('message') or 'Hand connection failed')
                return None
            # While starting, owner() intentionally rejects incomplete claims;
            # those are pending, not successful receipts.
            def poll_connected():
                try:return connected()
                except ValueError as error:
                    text=str(error)
                    if 'ownership is not confirmed' in text:return None
                    raise
            after=self._wait(poll_connected,max(0,self.deadline-self.clock()) if '_saved_profile' in target else 12,'New device connection was not confirmed')
            if 'hand_serial' in target:
                self._check_cancel();self._check_mapping(target)
                self.controller.action(dict(name='glove_prepare',serial=target['hand_serial'],address=target.get('address','')))
                expected_session=(after.get('glove') or {}).get('session_id')
                def prepared():
                    current=self.controller.snapshot();g=current.get('glove') or {};feedback=g.get('feedback') or {}
                    identity=feedback.get('identified_device') or {};hardware=g.get('hardware') or {}
                    if (g.get('session_id')!=expected_session or g.get('glove_serial')!=target['serial']
                            or (g.get('user') or {}).get('user_id')!=target['user_id']):
                        raise ValueError('Glove identity changed while connecting the hand')
                    if g.get('connection')=='error' or hardware.get('phase')=='error':raise ValueError(g.get('message') or hardware.get('reason') or 'Hand connection failed')
                    if feedback.get('device_id') not in (None,target['hand_serial']):raise ValueError('Unexpected hand feedback identity')
                    if (feedback.get('connection')=='connected' and feedback.get('device_id')==target['hand_serial']
                            and feedback.get('stale') is False and identity.get('source')=='sdk_identity'
                            and identity.get('serial')==target['hand_serial'] and identity.get('profile')==target['profile']
                            and hardware.get('active') is False and (g.get('stream') or {}).get('fresh') is True
                            and (g.get('stream') or {}).get('retarget')==target['_mapping']['settings']
                            and ((g.get('stream') or {}).get('solver') or {}).get('engine')=='sdk'
                            and ((g.get('stream') or {}).get('solver') or {}).get('applied') is True):return current
                    return None
                after=self._wait(prepared,12,'Hand feedback was not confirmed')
            from task_view import _owner_context
            kind='hand' if target['mode']=='hand' else (owner(after) or {})['kind']
            receipt=dict(version=1,attempt_id=self.state['attempt_id'],request_id=request_id,
                source_task_id=task_id(source['kind'],source['serial']) if source else None,
                target_task_id=task_id(kind,target['serial']),expected_owner=command['expected_owner'],
                after_context=_owner_context(after),target=copy.deepcopy(self.state['target']),
                sdk_session=(after.get('glove') or {}).get('session_id') if target['mode']=='teleoperation' else None)
            with self.lock:
                self._check_cancel()
                self.state.update(phase='connected',receipt=receipt)
                if '_saved_profile' in target:
                    proof=target['_saved_profile']
                    self._saved_resume=dict(serial=target['serial'],profile=target['profile'],user_id=target['user_id'],
                        source_profile=proof['source_profile'],config=self._config())
            return dict(connection_switch={**self.snapshot(after),'busy':False},connection_switch_receipt=receipt)
        except Exception as error:
            if not attempt_started:raise
            if self.state.get('phase')=='stopping':self._set(cleanup_error=str(error)[:700])
            self._set(phase='failed',error=str(error)[:900],receipt=None,source_released=stopped)
            # No implicit retry or unrelated stop. An unfinished new session
            # remains visible and explicitly owned if cleanup cannot be proven.
            if new_session is not None:
                try:
                    glove=self.controller.glove.snapshot()
                    if (glove.get('session_id')==new_session and glove.get('busy') is True
                            and glove.get('connection') in ('connecting','ready')):
                        # Set the original bridge's sticky stop before a late
                        # calibrated-ready callback can open the pending glove.
                        from glove_protocol import CancellationNotDispatched
                        try:self.controller.glove.disconnect_session(new_session,pending_only=True)
                        except CancellationNotDispatched:pass
                    self.controller.glove.stop_session_confirmed(new_session)
                except Exception as cleanup:
                    with self.lock:
                        if stopped and type(new_session) is int and new_session>0:
                            self._cleanup_owner=dict(attempt_id=self.state.get('attempt_id'),session=new_session)
                        self.state['cleanup_error']=str(cleanup)[:700]
            raise
        finally:
            if attempt_started:
                with self.lock:self.state['busy']=False;self._thread=None;self.viewer_token=None
            self.workflow_lock.release()
