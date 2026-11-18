"""All-participant prepare / arm / common-start coordinator. No joint I/O here."""
from concurrent.futures import ThreadPoolExecutor
import copy
import secrets
import threading
import time
from group_timing import common_schedule

_MEMBER_RENEWAL_EXIT_WAIT=6.5  # Fleet read <=1s; post does GET <=1s then POST <=4s.


class GroupCoordinator:
    def __init__(self,read,post):
        self.read,self.post=read,post;self.lock=threading.RLock();self.pool=ThreadPoolExecutor(max_workers=8)
        self.state=dict(active=False,phase='idle',reason='',members=[])
        self.cancel=threading.Event();self.heartbeat=0.;self.token=None;self.thread=None
        self.control_request=None;self.control_epoch=0
        self._member_threads=[]

    def snapshot(self):
        with self.lock:return copy.deepcopy(self.state)

    def update(self,**values):
        with self.lock:self.state.update(values)

    def start(self,command):
        from bimanual_program import catalog
        from playback_rates import PLAYBACK_SPEEDS
        ids=command.get('members');mode=command.get('mode');kind=command.get('kind')
        if not isinstance(ids,list) or not 2<=len(ids)<=8 or any(not isinstance(x,str) for x in ids) or len(set(ids))!=len(ids):raise ValueError('Choose 2–8 different hands / 选择2–8只不同的手')
        if not any(x['id']==command.get('action') and x['kind']==kind for x in catalog()):raise ValueError('Unknown group action')
        if mode not in ('preview','hardware'):raise ValueError('Invalid group mode')
        if type(command.get('speed')) not in (int,float) or command['speed'] not in PLAYBACK_SPEEDS:raise ValueError('Invalid speed')
        if type(command.get('cycles')) is not int or command['cycles'] not in (1,3):raise ValueError('Invalid cycles')
        if type(command.get('amplitude')) not in (int,float) or command['amplitude'] not in (.25,.5,.75,1.):raise ValueError('Invalid amplitude')
        if mode=='hardware' and command.get('workspace_clear') is not True:raise ValueError('Confirm clear space / 请确认周围空间已清空')
        with self.lock:
            if self.state['active']:raise ValueError('A group is already active / 编组正在运行')
            if any(thread.is_alive() for _,thread in self._member_threads):
                raise ValueError('编组续约任务尚未退出，请稍后重试 / Group renewal cleanup is unconfirmed')
            self._member_threads=[]
            # Read all identities BEFORE reserving any participant.
            states=list(self.pool.map(self.read,ids));sides=[s['device_profile']['side'] for s in states]
            if kind=='pair' and (len(ids)!=2 or sorted(sides)!=['left','right']):raise ValueError('双手编排需要一左一右 / Pair choreography requires one left and one right')
            if mode=='hardware':
                serials=[s.get('device_id') for s in states]
                if not all(serials) or len(set(serials))!=len(ids):raise ValueError('Each member must own a different connected hand')
                if any(s['connection']!='connected' or s['stale'] or s['device_profile']['generation']!='hand2' or s['hardware'].get('group_sync_version')!=1 for s in states):raise ValueError('连接所有二代手并更新控制端 / Connect all Hand 2 devices and update controllers')
                clocks={s['hardware'].get('controller_clock_id') for s in states}
                if len(clocks)!=1 or not next(iter(clocks)):raise ValueError('同步实机需使用同一 Linux 控制端 / Use one Linux controller for synchronized playback')
            self.cancel.clear();self.token=secrets.token_hex(16);self.heartbeat=time.monotonic()
            self.control_request=None;self.control_epoch=0
            self.state=dict(active=True,phase='preparing',reason='',mode=mode,kind=kind,action=command['action'],members=[dict(id=i,phase='preparing') for i in ids],elapsed_s=0.)
            try:
                self.thread=threading.Thread(target=self.run,args=(copy.deepcopy(command),self.token),daemon=True,name='Shared hand choreography');self.thread.start()
            except Exception as error:
                # Participant preparation has not run. Drop only the new
                # local lease instead of broadcasting an unrelated abort.
                self.thread=None;self.token=None;self.heartbeat=0.
                self.state.update(active=False,phase='failed',reason=str(error)[:700],can_pause=False,can_resume=False)
                raise
            return dict(group_lease=self.token)

    def beat(self,token):
        with self.lock:
            if not self.state['active'] or not secrets.compare_digest(str(token),str(self.token)):raise ValueError('Expired group lease')
            self.heartbeat=time.monotonic()

    def stop(self):self.cancel.set();return dict(stopping=True)

    def request_control(self,action,token):
        with self.lock:
            if (action not in ('pause','resume') or not self.state['active']
                    or not secrets.compare_digest(str(token),str(self.token))
                    or self.cancel.is_set() or self.control_request
                    or self.state.get('can_'+action) is not True):
                raise ValueError('当前编组不能暂停或继续 / Group pause state changed')
            self.control_epoch+=1
            self.control_request=dict(action=action,epoch=self.control_epoch)
            self.state.update(phase='pausing' if action=='pause' else 'resuming',can_pause=False,can_resume=False)
            return dict(requested=action,epoch=self.control_epoch)

    def dispatch_control(self,ids,token,groups):
        with self.lock:
            self.check(token)
            request=self.control_request;self.control_request=None
        if not request:return None
        expected='playing' if request['action']=='pause' else 'paused'
        if any(g.get('phase')!=expected or g.get('control_version')!=1 for g in groups):
            raise ValueError('部分手无法切换暂停状态 / A hand cannot change pause state')
        at=max(g['clock_s'] for g in groups)+1.5
        self.check(token)
        self.broadcast(ids,dict(name='group_'+request['action'],token=token,epoch=request['epoch'],at_s=at))
        return dict(request,at_s=at,acknowledged=False)

    @staticmethod
    def control_complete(groups,transition):
        """Transport acceptance is insufficient: require exact worker evidence."""
        epoch=transition['epoch'];at=transition['at_s'];action=transition['action']
        phase='paused' if action=='pause' else 'playing'
        if any(g.get('control_epoch',0)>epoch or g.get('applied_epoch',0)>epoch for g in groups):
            raise ValueError('Unexpected group control epoch')
        clock=max(g['clock_s'] for g in groups)
        if not transition['acknowledged']:
            if clock>=at-.15:raise ValueError('整组切换未及时确认 / Group control acknowledgement expired')
            acknowledged=[]
            for g in groups:
                p=g.get('pending_control') or {}
                acknowledged.append(g.get('control_epoch')==epoch and p.get('epoch')==epoch
                    and p.get('action')==action and p.get('at_s')==at)
            transition['acknowledged']=all(acknowledged)
        complete=transition['acknowledged'] and all(g.get('applied_epoch')==epoch and g.get('phase')==phase and not g.get('pending_control') for g in groups)
        if not complete and clock>at+.5:raise ValueError('部分手未完成整组切换 / A hand did not complete group control')
        return complete

    def check(self,token=None):
        if self.cancel.is_set():raise ValueError('用户停止 / Stopped by user')
        if time.monotonic()-self.heartbeat>.8:raise ValueError('编组界面连接中断 / Group UI lease expired')
        if token is not None and (token!=self.token or self.state.get('active') is not True):raise ValueError('Expired group lease')

    def broadcast(self,ids,command):
        # Consume ALL results, even if a sibling fails; cleanup must reach every
        # possible reservation after partially successful concurrent operations.
        futures={i:self.pool.submit(self.post,i,command) for i in ids};errors=[];out={}
        for i,f in futures.items():
            try:out[i]=f.result()
            except Exception as e:errors.append(i+': '+str(e))
        if errors:raise ValueError('; '.join(errors))
        return out

    @staticmethod
    def completed_stop(state,token):
        group=state.get('group') or {};hardware=state.get('hardware') or {}
        return (group.get('token')==token and group.get('mode')=='hardware'
            and group.get('phase')=='completed' and group.get('completed') is True
            and group.get('active') is False and group.get('stop_confirmed') is True
            and hardware.get('active') is False and hardware.get('stop_confirmed') is True
            and hardware.get('start_pending',False) is False)

    def run(self,spec,token):
        ids=spec['members'];phase='failed';reason='';cleanup=[]
        renewal_stop=threading.Event();renewal_lock=threading.Lock();renewal_errors=[]
        prepared={i:threading.Event() for i in ids};threads=[];attempted=False
        def check_run():
            self.check(token)
            with renewal_lock:
                if renewal_errors:raise ValueError('; '.join(renewal_errors))
        def renew(member):
            # Independent lanes cannot starve behind eight prepare/arm RPCs,
            # nor let one slow heartbeat response delay its siblings.
            while not renewal_stop.wait(.12):
                try:
                    check_run()
                    if not prepared[member].is_set():
                        group=(self.read(member).get('group') or {})
                        if renewal_stop.is_set():return
                        check_run()
                        if group.get('token')!=token:continue
                        if (group.get('mode')!=spec['mode'] or group.get('active') is not True
                                or group.get('cleanup_required') or group.get('phase') in ('failed','idle','cleanup_required','stopped')):
                            raise ValueError('Group reservation is unavailable for renewal')
                        # prepare may have applied while its response is held.
                        # Only this actual reservation, not an inferred ACK,
                        # authorizes its exact-token heartbeat.
                        prepared[member].set()
                    if renewal_stop.is_set():return
                    check_run();self.post(member,dict(name='group_beat',token=token))
                except Exception as error:
                    with renewal_lock:
                        if not renewal_stop.is_set():renewal_errors.append(member+': '+str(error))
                        renewal_stop.set()
                    return
        try:
            with self.lock:
                check_run();self._member_threads=threads
            for member in ids:
                thread=threading.Thread(target=renew,args=(member,),daemon=True,name='Group member renewal')
                threads.append((member,thread));thread.start()
            check_run();attempted=True
            self.broadcast(ids,dict(spec,name='group_prepare',token=token))
            for event in prepared.values():event.set()
            with self.lock:check_run();self.update(phase='arming')
            self.broadcast(ids,dict(name='group_arm',token=token))
            deadline=time.monotonic()+12
            while True:
                check_run()
                states=list(self.pool.map(self.read,ids));groups=[s.get('group',{}) for s in states]
                check_run()
                if all(g.get('token')==token and g.get('phase')=='ready' for g in groups):break
                if any(g.get('phase')=='failed' and g.get('token')==token for g in groups):raise ValueError('A hand failed preparation')
                if time.monotonic()>deadline:raise ValueError('部分手未完成准备 / Not all hands became ready')
                time.sleep(.05)
            command=dict(name='group_commit',token=token)
            if spec['mode']=='hardware':
                rows=[dict(g['timing'],clock_id=g.get('clock_id')) for g in groups]
                command.update(timing=common_schedule(rows),start_s=max(g['clock_s'] for g in groups)+1.5)
            else:command['start_s']=time.monotonic()+1.5
            check_run();self.broadcast(ids,command)
            with self.lock:check_run();self.update(phase='waiting')
            if spec['mode']=='hardware':duration=sum(command['timing'].values())
            else:
                from bimanual_program import resolve_action
                from performance_program import program
                duration=program(resolve_action(spec['kind'],spec['action'],'left'))['duration_s']/spec['speed']
            completion_deadline=time.monotonic()+1.5+duration*spec['cycles']+8
            transition=None;paused=False;previous_poll=time.monotonic()
            while True:
                check_run()
                now=time.monotonic()
                if paused:completion_deadline+=max(0.,now-previous_poll)
                previous_poll=now
                if now>completion_deadline:raise ValueError('编组未按时间轴完成 / Group did not finish its timeline')
                states=list(self.pool.map(self.read,ids));groups=[s.get('group',{}) for s in states]
                check_run()
                if any(g.get('token')!=token or g.get('phase') in ('failed','idle','preparing') or not g.get('active') and g.get('phase')!='completed' for g in groups):
                    raise ValueError(next((g.get('reason') for g in groups if g.get('reason')),None) or '编组成员中断 / A group member stopped')
                # A current, acknowledged completion is independent of report
                # I/O delaying later telemetry. Live or uncertain members still
                # require the unchanged fresh-feedback checks.
                if spec['mode']=='hardware' and any((s['stale'] or s['connection']!='connected')
                        and not self.completed_stop(s,token) for s in states):raise ValueError('编组反馈中断 / Group feedback expired')
                phases=[g['phase'] for g in groups]
                elapsed=[g.get('elapsed_s',0.) for g in groups]
                if transition and self.control_complete(groups,transition):
                    paused=transition['action']=='pause';transition=None
                if not transition:transition=self.dispatch_control(ids,token,groups)
                run_phase=('pausing' if transition['action']=='pause' else 'resuming') if transition else 'paused' if paused else 'playing' if any(p in ('playing','returning') for p in phases) else 'waiting'
                supports_control=all(g.get('control_version')==1 for g in groups)
                with self.lock:
                    check_run()
                    # A user can queue a request while member reads are in flight.
                    # Keep that newer pending phase instead of briefly re-enabling.
                    queued=self.control_request
                    if queued:run_phase='pausing' if queued['action']=='pause' else 'resuming'
                    self.update(phase=run_phase,can_pause=not transition and not queued and supports_control and all(p=='playing' for p in phases),can_resume=not transition and not queued and supports_control and all(p=='paused' for p in phases),elapsed_s=min(elapsed),members=[dict(id=i,phase=g['phase'],elapsed_s=g.get('elapsed_s',0)) for i,g in zip(ids,groups)],sampled_phase_spread_ms=(max(elapsed)-min(elapsed))*1000,phase_samples_not_simultaneous=True)
                if all(p=='completed' for p in phases):phase='completed';reason='编组完成 / Group completed';break
                time.sleep(.12)
        except Exception as e:
            phase='stopped' if self.cancel.is_set() else 'failed';reason=str(e)
        finally:
            renewal_stop.set()
            join_deadline=time.monotonic()+_MEMBER_RENEWAL_EXIT_WAIT
            for member,thread in threads:
                try:
                    if thread.ident is not None:thread.join(max(0.,join_deadline-time.monotonic()))
                    if thread.is_alive():raise ValueError('Member renewal has not exited')
                except Exception:
                    cleanup.append(member)
            if cleanup:reason+='; 编组续约退出未确认 / Member renewal cleanup unconfirmed'
            futures={i:self.pool.submit(self.post,i,dict(name='group_abort',token=token)) for i in ids} if attempted else {}
            read_only_released=set()
            for i,f in futures.items():
                try:
                    result=f.result()
                    receipt=result.get('group') if isinstance(result,dict) else None
                    if (isinstance(receipt,dict) and receipt.get('token')==token
                            and receipt.get('mode')=='hardware' and receipt.get('stopping') is True
                            and receipt.get('read_only_released') is True):read_only_released.add(i)
                except Exception:cleanup.append(i)
            if spec['mode']=='hardware' and attempted:
                deadline=time.monotonic()+2
                # This receipt releases only a reservation that never owned a
                # hardware start. It does not change any motor/stop state.
                pending=set(ids)-read_only_released
                while pending and time.monotonic()<deadline:
                    for i in list(pending):
                        try:
                            h=self.read(i)['hardware']
                            if h.get('active') is False and h.get('stop_confirmed') is True:pending.remove(i)
                        except Exception:pass
                    if pending:time.sleep(.1)
                cleanup=sorted(set(cleanup)|pending)
            if cleanup:reason+='; 停止结果待确认 / Stop unconfirmed: '+', '.join(cleanup)
            with self.lock:
                if self.token==token:
                    previous=self.snapshot()['members']
                    final_members=[dict(m,phase='stop_unconfirmed' if m['id'] in cleanup else 'completed' if phase=='completed' else 'stopped',phase_before_stop=m['phase']) for m in previous]
                    self.update(active=False,phase=phase,reason=reason,stop_unconfirmed=cleanup,members=final_members,can_pause=False,can_resume=False)
