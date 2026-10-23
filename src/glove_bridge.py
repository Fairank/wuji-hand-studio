"""Operator/status bridge over the configured local or SSH SDK transport."""
import copy
import json
import secrets
import socket
import threading
import time
from glove_protocol import validate, CancellationNotDispatched

SDK_SOLVER_REQUIRED='请切回官方 SDK 映射预览后再接入或启动灵巧手 / Switch preview to the official SDK before preparing or following a real hand'


def _actual_sdk_solver(state):
    solver=(state.get('stream') or {}).get('solver') or {}
    return solver.get('engine')=='sdk' and solver.get('applied') is True

class GloveBridge:
    def __init__(self,factory,termination=None,release_resolver=None):
        self.factory=factory;self.lock=threading.RLock();self.thread=None;self.termination=termination;self.session=termination.new_session() if termination else 0
        self.client=self.stdin=None;self.stopping=False;self.last_update=0.;self.lease=None
        self.state=dict(connection='disconnected',message='',devices=[],users=[],stream={},hardware={},feedback={})
        self._continuation=None;self._on_model_ready=None
        self._unpair_pending=False
        self.seen_active=False;self._follow_unconfirmed=False
        self._follow_device=None;self._follow_session=None;self._follow_reason=''
        self.release_resolver=release_resolver
        self._sdk_release=None;self._sdk_proof=None
        self._follow_sdk_requirement=None;self._follow_sdk_proof=None
        self._follow_sdk_session=None

    @property
    def busy(self):return bool(self.thread and self.thread.is_alive())

    def remember_termination(self):
        if self.termination:
            device=self._follow_device if self._follow_unconfirmed else (self.state.get('feedback') or {}).get('device_id')
            self.termination.observe('glove',device,self.state.get('hardware') or {},self.session)

    def _retain_follow_uncertainty(self):
        if self._follow_unconfirmed:
            hardware=dict(self.state.get('hardware') or {})
            # A late actual active status remains active. An ordinary inactive
            # status cannot acknowledge a possibly dispatched newer request.
            same=(self._follow_session==self.session
                and (self.state.get('feedback') or {}).get('device_id')==self._follow_device)
            if hardware.get('active') is not True or not same:hardware['active']=None
            hardware.update(stop_confirmed=False,probe_ready=False,reason=self._follow_reason)
            self.state['hardware']=hardware

    def _mark_follow_unconfirmed(self,reason):
        self._follow_unconfirmed=True;self._follow_reason=reason
        self._retain_follow_uncertainty()

    def snapshot(self):
        with self.lock:
            s=copy.deepcopy(self.state);age=max(0,time.monotonic()-self.last_update)*1000
            s['busy']=self.busy;s['transport_age_ms']=age if self.last_update else None
            s['session_id']=self.session
            s['sdk_release']=copy.deepcopy(self._sdk_release)
            s['sdk_proof']=copy.deepcopy(self._sdk_proof)
            s['follow_unconfirmed']=self._follow_unconfirmed
            s['follow_pending']=bool(self.lease and not self.seen_active)
            s['hand_unpairing']=bool(s.get('hand_unpairing') or self._unpair_pending)
            stream=s['stream'];remote=stream.get('age_ms')
            if remote is not None:stream['age_ms']=remote+age
            stream['fresh']=bool(stream.get('fresh') and remote is not None and remote+age<=stream.get('timeout_ms',250))
            if not stream['fresh']:stream['retarget_hz']=None
            feedback=s.get('feedback') or {};metrics=feedback.get('metrics') or {}
            if metrics.get('age_ms') is not None:metrics['age_ms']+=age
            feedback['stale']=metrics.get('age_ms') is None or metrics['age_ms']>100 or age>200
            s['feedback']=feedback
            if self._follow_unconfirmed:
                s['can_prepare']=False;s['prepare_blocked_reason']=self._follow_reason
            elif not _actual_sdk_solver(s):
                s['can_prepare']=False;s['prepare_blocked_reason']=SDK_SOLVER_REQUIRED
            elif not s['stream']['fresh'] or not self.busy or self.stopping or self.lease or s['hand_unpairing']:
                s['can_prepare']=False
                s['prepare_blocked_reason']=('等待新鲜手套数据 / Wait for fresh glove data' if not s['stream']['fresh']
                    else '会话正在停止或跟随 / Session is closing or following')
            elif s.get('can_prepare') is not True:
                s['can_prepare']=False
                s.setdefault('prepare_blocked_reason','请等待控制端核对灵巧手会话 / Wait for controller hand-session verification')
            return s

    def confirm_termination(self,device,session):
        # A later explicit stop of this serial can resolve an ended glove
        # session, without treating an unrelated/new connection as evidence.
        with self.lock:
            current=self._follow_device if self._follow_unconfirmed else (self.state.get('feedback') or {}).get('device_id')
            if (device and not self.busy and self.session<=session and
                    current==device):
                self.state['hardware']=dict(active=False,stop_confirmed=True,ready=False,
                                            reason='对应设备已确认停用 / Matching device disable acknowledged')
                self._follow_unconfirmed=False;self._follow_reason=''

    def send(self,c):
        if not self.stdin:raise ValueError('控制端尚未就绪 / Controller not ready')
        self.stdin.write(json.dumps(c)+'\n');self.stdin.flush()

    def disconnect_session(self,session,*,pending_only=False):
        """A handover cancellation must never disconnect a replacement session."""
        with self.lock:
            if type(session) is not int or session!=self.session:
                raise CancellationNotDispatched('遥操作会话已改变，请在所属会话中断开 / Teleoperation session changed; disconnect its owning session')
            if pending_only and (self.state.get('connection') not in ('connecting','ready')
                    or (self.state.get('hardware') or {}).get('active',False) is not False
                    or (self.state.get('feedback') or {}).get('device_id') or self.lease):
                raise CancellationNotDispatched('接续已完成，请在遥操作中暂停或断开 / Continuation completed; pause or disconnect from teleoperation')
            return self.action(dict(name='glove_disconnect'))

    def start_calibrated(self,bundle,on_model_ready):
        """Private controller entry after a server-verified continuation intent.

        The public glove_session action remains rejected below. No browser
        command may supply a bundle, path, user ID or bootstrap context.
        """
        from calibration_portability import CONTEXT_FIELDS,validate_continuation,file_digest,MAX_BUNDLE
        context=validate_continuation({k:bundle[k] for k in CONTEXT_FIELDS|{'bundle_path'}},path=True)
        if not callable(on_model_ready):raise ValueError('Continuation requires a workspace binding callback')
        if file_digest(context['bundle_path'],MAX_BUNDLE)!=(context['bundle_sha256'],context['bundle_bytes']):
            raise ValueError('Calibration bundle changed before controller startup')
        with self.lock:
            if self.busy:raise ValueError('请先结束原手套会话 / Finish the existing glove session first')
            self._continuation=context;self._on_model_ready=on_model_ready
            self._begin_session()
            return self.session

    def _begin_session(self):
        self.remember_termination();self.session=self.termination.new_session() if self.termination else self.session+1
        self.stopping=False;self.lease=None;self.last_update=0.
        self._unpair_pending=False
        self.seen_active=False;self._follow_unconfirmed=False
        self._follow_device=None;self._follow_session=None;self._follow_reason=''
        self._sdk_release=None;self._sdk_proof=None
        self._follow_sdk_requirement=None;self._follow_sdk_proof=None
        self._follow_sdk_session=None
        self.state=dict(connection='connecting',message='正在连接控制端 / Connecting controller',devices=[],users=[],stream={},hardware={},feedback={})
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()

    def _stage_calibration(self,client,context):
        """Reuse this controller's existing bounded file transport and process."""
        import hashlib
        from pathlib import Path
        import uuid
        from calibration_portability import MAX_BUNDLE,controller_transfer_directory
        root=controller_transfer_directory(client.agent_directory)
        remote=root+'/'+context['bundle_sha256']+'.zip'
        def cancelled():
            with self.lock:
                if self.stopping:raise ValueError('Calibration continuation was cancelled before opening the glove')
        def digest(files,path):
            h=hashlib.sha256();size=0
            with files.open(path,'rb') as stream:
                for block in iter(lambda:stream.read(64*1024),b''):
                    cancelled();size+=len(block)
                    if size>MAX_BUNDLE:raise ValueError('Controller calibration bundle exceeds its limit')
                    h.update(block)
            return h.hexdigest(),size
        expected=context['bundle_sha256'],context['bundle_bytes']
        with client.open_sftp() as files:
            if hasattr(files,'path'):
                if not files.path(remote).parent.is_dir():raise ValueError('Controller did not prepare its private calibration directory')
            else:
                import stat
                mode=files.lstat(root).st_mode
                if not stat.S_ISDIR(mode) or mode & 0o077:raise ValueError('Controller transfer directory is not private')
            try:existing=digest(files,remote)
            except FileNotFoundError:existing=None
            except OSError as error:
                if getattr(error,'errno',None)==2:existing=None
                else:raise ValueError('Cannot read the controller calibration cache') from None
            if existing is not None and existing!=expected:raise ValueError('Controller calibration cache checksum differs')
            if existing is None:
                stage=remote+'.'+uuid.uuid4().hex+'.pending'
                with Path(context['bundle_path']).open('rb') as source,files.open(stage,'wb') as target:
                    size=0
                    for block in iter(lambda:source.read(64*1024),b''):
                        cancelled();size+=len(block)
                        if size>MAX_BUNDLE:raise ValueError('Calibration bundle exceeds its limit')
                        target.write(block)
                if digest(files,stage)!=expected:raise ValueError('Controller calibration transfer checksum differs')
                cancelled();files.posix_rename(stage,remote)
            if digest(files,remote)!=expected:raise ValueError('Controller calibration cache changed')
        return dict(context,bundle_path=remote)

    def _calibrated_ready(self,event,context):
        from calibration_portability import IDENT
        receipt=event.get('receipt')
        if (not isinstance(receipt,dict) or receipt.get('source_run_id')!=context['source_run_id']
                or receipt.get('serial')!=context['serial'] or receipt.get('side')!=context['side']
                or receipt.get('model_sha256')!=context['model_sha256']
                or receipt.get('source_profile')!=context['source_profile']
                or receipt.get('source_workspace')!=context['source_workspace']
                or not isinstance(receipt.get('owner_user_id'),str) or not IDENT.fullmatch(receipt['owner_user_id'])
                or type(receipt.get('imported')) is not bool
                or set(receipt)!={'source_run_id','source_workspace','source_profile','serial','side','model_sha256','owner_user_id','imported'}):
            raise ValueError('Controller calibration identity differs from this task')
        with self.lock:
            if self.stopping:return
        # Controller callback may acquire its own lock. Never hold bridge.lock.
        settings=self._on_model_ready(copy.deepcopy(receipt))
        if not isinstance(settings,dict) or set(settings)-{'timeout_ms','retarget','solver'}:
            raise ValueError('Invalid calibrated preview settings')
        command=dict(settings,name='glove_open',serial=context['serial'],user_id=receipt['owner_user_id'])
        validate(command)
        with self.lock:
            if not self.stopping:self.send(command)

    def verify_follow_sdk(self):
        """Check a new follow outside transport/controller locks, never keepalive.

        A preview can outlive the stable catalog. Approval belongs only to this
        worker/session and is consumed by the final locked dispatch preflight.
        """
        with self.lock:
            if self.lease or self.state.get('hardware',{}).get('active') is True:
                raise ValueError('已有启动请求 / Start already requested')
            if self._follow_unconfirmed:raise ValueError(self._follow_reason)
            if (not self.busy or self.stopping or self.state.get('hardware',{}).get('active') is not False):
                raise ValueError('等待灵巧手停用确认后再开始 / Wait for confirmed hand disable before following')
            session=self.session;proof=copy.deepcopy(self._sdk_proof)
            self._follow_sdk_requirement=None;self._follow_sdk_proof=None;self._follow_sdk_session=None
            if not isinstance(proof,dict) or proof.get('available') is not True or proof.get('api_compatible') is not True:
                raise ValueError('实际手套 SDK 尚未确认，请重新接入 / Actual glove SDK is not confirmed; reconnect')
            if not _actual_sdk_solver(self.state):raise ValueError(SDK_SOLVER_REQUIRED)
        from sdk_release import resolve_release,requirement,select_wheel
        from sdk_runtime import verify_proof
        release=(self.release_resolver or resolve_release)()
        expected=requirement(release)
        verify_proof(proof,expected);select_wheel(release,proof)
        with self.lock:
            if (session!=self.session or self._sdk_proof!=proof or not self.busy or self.stopping
                    or self.lease or self.state.get('hardware',{}).get('active') is not False
                    or self._follow_unconfirmed or not _actual_sdk_solver(self.state)):
                raise ValueError('遥操作会话已改变，请重新开始 / Teleoperation session changed; start again')
            self._follow_sdk_requirement=copy.deepcopy(expected);self._follow_sdk_proof=proof;self._follow_sdk_session=session
            return dict(version=expected['version'],checked_at=expected['checked_at'],session_id=session)

    def _require_follow_sdk(self):
        from sdk_release import validate_requirement,MAX_AGE
        from sdk_runtime import verify_proof
        if not _actual_sdk_solver(self.state):raise ValueError(SDK_SOLVER_REQUIRED)
        expected=validate_requirement(self._follow_sdk_requirement)
        stamp=expected['checked_at'];now=time.time()
        if (now-stamp>MAX_AGE or stamp>now+30 or self._sdk_proof!=self._follow_sdk_proof
                or self.session!=self._follow_sdk_session):
            raise ValueError('SDK 启动检查已过期，请重新开始 / SDK follow check expired; start again')
        verify_proof(self._sdk_proof,expected)

    def action(self,c):
        validate(c)
        with self.lock:
            name=c['name']
            if name=='glove_session':raise ValueError('Use scan to open a session')
            if name=='glove_scan' and not self.busy:
                self._continuation=None;self._on_model_ready=None
                self._begin_session();return
            if name=='glove_disconnect':
                self.stopping=True
                if self.stdin:self.send(c)
                elif not self.busy:self.state['connection']='disconnected'
                return
            if not self.busy:raise ValueError('先扫描控制端 / Scan controller first')
            if name=='glove_hand_scan':
                state=self.snapshot();hardware=state.get('hardware') or {};proof=self._sdk_proof or {}
                if (self.stopping or state.get('connection')!='receiving'
                        or state.get('can_prepare') is not True or not _actual_sdk_solver(state)
                        or proof.get('available') is not True or proof.get('api_compatible') is not True
                        or not state.get('stream',{}).get('fresh')
                        or self.lease or self._follow_unconfirmed or state.get('hand_unpairing')
                        or (state.get('feedback') or {}).get('device_id')
                        or ('active' in hardware and hardware['active'] is not False)
                        or hardware.get('stop_confirmed') is False):
                    raise ValueError('仅可在未配对的新鲜 SDK 手套预览中查找灵巧手 / Search hands from a fresh unpaired SDK glove preview')
            if name=='glove_prepare':
                state=self.snapshot()
                if state.get('can_prepare') is not True:
                    raise ValueError(state.get('prepare_blocked_reason') or 'Hand preparation is unavailable')
            if name=='glove_unpair':
                state=self.snapshot()
                if (self.stopping or self.lease or state.get('connection')!='receiving'
                        or not (state.get('feedback') or {}).get('device_id')
                        or state.get('hardware',{}).get('active') is not False
                        or state.get('hand_unpairing')):
                    raise ValueError('先暂停并确认灵巧手已停用，再解除配对 / Pause and confirm hand disable before unpairing')
                self.send(c);self._unpair_pending=True
                return dict(unpairing=True,session_id=self.session)
            if name=='glove_follow':
                s=self.snapshot()
                if self._follow_unconfirmed:raise ValueError(self._follow_reason)
                if s.get('hand_unpairing'):raise ValueError('Hand cleanup is still in progress')
                if self.lease:raise ValueError('已有启动请求 / Start already requested')
                if not s['stream'].get('fresh'):raise ValueError('等待新鲜手套数据 / Wait for fresh glove data')
                if s['feedback']['stale'] or not s['hardware'].get('probe_ready') or s['hardware'].get('active') is not False:
                    raise ValueError('等待完整灵巧手反馈 / Wait for ready hand feedback')
                self._require_follow_sdk()
                self._follow_sdk_requirement=None;self._follow_sdk_proof=None
                self._follow_sdk_session=None
                self.lease=secrets.token_hex(16);c=dict(c,lease=self.lease)
                self.requested=time.monotonic();self.seen_active=False
                self._follow_device=s['feedback'].get('device_id');self._follow_session=self.session
                try:self.send(c)
                except Exception:
                    self._mark_follow_unconfirmed('启动请求结果未确认，请先结束并核对会话 / Follow request is unconfirmed; end and review this session')
                    self.lease=None;self.remember_termination();raise
                return dict(lease=self.lease)
            if name=='glove_keepalive' and c.get('lease')!=self.lease:raise ValueError('Expired control lease')
            self.send(c)

    def receive(self,event,session=None):
        with self.lock:
            if session is not None and (type(session) is not int or session!=self.session):return
            kind=event.get('type')
            if kind=='glove_state':
                self.state=copy.deepcopy(event['state']);self.last_update=time.monotonic()
                if self.state.get('hand_unpaired') is True:self._unpair_pending=False
                active=self.state['hardware'].get('active')
                if self.lease:
                    if (self.state.get('feedback') or {}).get('device_id')!=self._follow_device:
                        self._mark_follow_unconfirmed('目标反馈身份已改变，原启动结果未确认 / Target feedback changed; original follow request is unconfirmed')
                        self.lease=None
                    elif active:self.seen_active=True
                    elif active is False and self.seen_active:self.lease=None
                    elif active is False and time.monotonic()-self.requested>5:
                        self._mark_follow_unconfirmed('启动结果尚未确认，请先结束并核对会话 / Follow startup is unconfirmed; end and review this session')
                        self.lease=None
                self._retain_follow_uncertainty()
            elif kind=='glove_follow_rejected':
                hardware=self.state.get('hardware') or {}
                if (type(session) is not int or session!=self.session or self._follow_session!=session
                        or not self.lease or self._follow_unconfirmed or self.seen_active or self.stopping
                        or hardware.get('active') is not False or self._follow_device is None
                        or event.get('lease')!=self.lease or event.get('device_id')!=self._follow_device):return
                # Only a current pre-forward refusal releases pending intent.
                # It never changes hardware/disable confirmation, nor resolves
                # write/flush/EOF uncertainty or a request that became active.
                self.lease=None;self.state['message']=str(event.get('message',''))[:400]
            elif kind in ('glove_error','glove_notice','notice'):
                self.state['message']=str(event.get('message',''))[:400]
                if kind=='glove_error':self.state['connection']='error'
            elif kind=='glove_closed':
                failed=self._continuation is not None and self.state.get('connection')=='error'
                self.state['connection']='error' if failed else 'disconnected';self.state['hardware']=event.get('hardware',{})
                if self._follow_unconfirmed or self.lease:
                    hardware=self.state['hardware']
                    exact=(self._follow_session==self.session and self._follow_device is not None
                        and event.get('device_id')==self._follow_device and event.get('hand_cleanup_confirmed') is True
                        and hardware.get('active') is False and hardware.get('stop_confirmed') is True)
                    if exact:self._follow_unconfirmed=False;self._follow_reason=''
                    else:self._mark_follow_unconfirmed('会话已结束，停用结果未确认 / Session ended; disable is unconfirmed')
                self.state['stream']['fresh']=False;self.lease=None
            self.remember_termination()

    def run(self):
        client=None;ready=False;model_ready=False;sdk_ready=False
        with self.lock:continuation=copy.deepcopy(self._continuation);session=self.session
        try:
            from sdk_release import resolve_release,requirement,select_wheel
            from sdk_runtime import verify_proof
            release=(self.release_resolver or resolve_release)()
            expected=requirement(release)
            with self.lock:self._sdk_release={k:release[k] for k in ('version','checked_at','catalog_sha256','offline') if k in release}
            client=self.factory();stdin,stdout,stderr=client.exec_command(client.agent_command,timeout=25)
            def drain():
                try:
                    while stderr.read(4096):pass
                except (OSError,EOFError,socket.timeout):pass
            threading.Thread(target=drain,daemon=True).start()
            with self.lock:
                if session!=self.session:raise ValueError('Teleoperation session changed')
                self.client=client;self.stdin=stdin
            for line in stdout:
                with self.lock:
                    if session!=self.session:break
                if not line.startswith('WUJI_JSON:'):continue
                event=json.loads(line[len('WUJI_JSON:'):])
                if event.get('type')=='ready':
                    if ready:raise ValueError('Controller repeated startup')
                    if event.get('teleop_version')!=1:raise ValueError('控制端需更新到支持手套遥操作的版本 / Update controller for glove teleoperation')
                    verify_proof(event.get('sdk_proof'),expected)
                    select_wheel(release,event['sdk_proof'])
                    staged=None
                    if continuation is not None:
                        if event.get('calibration_continuation_version')!=1:
                            raise ValueError('控制端尚不支持沿用本次标定，请先更新控制端 / Update the controller to continue this calibration')
                        staged=self._stage_calibration(client,continuation)
                    with self.lock:
                        self.send(dict(name='disconnect') if self.stopping else dict(name='glove_session',sdk_requirement=expected,**({'continuation':staged} if staged else {})))
                    ready=True
                elif event.get('type')=='sdk_ready':
                    if not ready or sdk_ready:raise ValueError('Controller SDK proof is out of order')
                    proof=verify_proof(event.get('sdk_proof'),expected)
                    select_wheel(release,proof)
                    with self.lock:self._sdk_proof=proof
                    sdk_ready=True
                elif event.get('type')=='calibrated_ready' and continuation is not None:
                    if not ready or not sdk_ready or model_ready:raise ValueError('Controller calibration readiness is out of order')
                    model_ready=True
                    self._calibrated_ready(event,continuation)
                else:
                    if event.get('type')=='glove_state' and not sdk_ready:
                        raise ValueError('控制端尚未确认实际运行的 SDK，请修复组件 / Actual worker SDK was not confirmed; repair components')
                    self.receive(event,session=session)
            if not ready:raise ValueError('控制端未返回手套接口 / Controller did not expose glove interface')
        except Exception as e:
            # Credential/transport details are never copied to the public UI.
            text=str(e) if isinstance(e,ValueError) else '无法连接控制端，请核对连接设置 / Controller connection failed; check settings'
            self.receive(dict(type='glove_error',message=text),session=session)
        finally:
            if client:
                try:client.close()
                except Exception:pass
            with self.lock:
                if session!=self.session:return
                if self.lease or self._follow_unconfirmed:
                    self._mark_follow_unconfirmed('连接中断，启动或停用结果未确认 / Connection lost; follow or disable is unconfirmed')
                self.client=self.stdin=None;self.lease=None;self.state['stream']['fresh']=False
                if self.state['hardware'].get('active'):
                    self.state['hardware'].update(active=None,stop_confirmed=False,reason='连接中断，停用结果未确认 / Disable not confirmed')
                if self.state['connection'] not in ('disconnected','error'):
                    self.state.update(connection='error',message='控制端会话已结束，请手动重连 / Controller session ended')
                self.remember_termination()
