"""All-participant prepare / arm / common-start coordinator. No joint I/O here."""
from concurrent.futures import ThreadPoolExecutor
import copy
import secrets
import threading
import time
from group_timing import common_schedule


class GroupCoordinator:
    def __init__(self,read,post):
        self.read,self.post=read,post;self.lock=threading.RLock();self.pool=ThreadPoolExecutor(max_workers=8)
        self.state=dict(active=False,phase='idle',reason='',members=[])
        self.cancel=threading.Event();self.heartbeat=0.;self.token=None;self.thread=None

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
            self.state=dict(active=True,phase='preparing',reason='',mode=mode,kind=kind,action=command['action'],members=[dict(id=i,phase='preparing') for i in ids],elapsed_s=0.)
            self.thread=threading.Thread(target=self.run,args=(copy.deepcopy(command),self.token),daemon=True,name='Shared hand choreography');self.thread.start()
            return dict(group_lease=self.token)

    def beat(self,token):
        if not self.state['active'] or not secrets.compare_digest(str(token),str(self.token)):raise ValueError('Expired group lease')
        self.heartbeat=time.monotonic()

    def stop(self):self.cancel.set();return dict(stopping=True)

    def check(self):
        if self.cancel.is_set():raise ValueError('用户停止 / Stopped by user')
        if time.monotonic()-self.heartbeat>.8:raise ValueError('编组界面连接中断 / Group UI lease expired')

    def broadcast(self,ids,command):
        # Consume ALL results, even if a sibling fails; cleanup must reach every
        # possible reservation after partially successful concurrent operations.
        futures={i:self.pool.submit(self.post,i,command) for i in ids};errors=[];out={}
        for i,f in futures.items():
            try:out[i]=f.result()
            except Exception as e:errors.append(i+': '+str(e))
        if errors:raise ValueError('; '.join(errors))
        return out

    def run(self,spec,token):
        ids=spec['members'];phase='failed';reason='';cleanup=[]
        try:
            self.check();self.broadcast(ids,dict(spec,name='group_prepare',token=token))
            self.check();self.update(phase='arming');self.broadcast(ids,dict(name='group_arm',token=token))
            deadline=time.monotonic()+12
            while True:
                self.check();self.broadcast(ids,dict(name='group_beat',token=token))
                states=list(self.pool.map(self.read,ids));groups=[s.get('group',{}) for s in states]
                if all(g.get('token')==token and g.get('phase')=='ready' for g in groups):break
                if any(g.get('phase')=='failed' and g.get('token')==token for g in groups):raise ValueError('A hand failed preparation')
                if time.monotonic()>deadline:raise ValueError('部分手未完成准备 / Not all hands became ready')
                time.sleep(.05)
            command=dict(name='group_commit',token=token)
            if spec['mode']=='hardware':
                rows=[dict(g['timing'],clock_id=g.get('clock_id')) for g in groups]
                command.update(timing=common_schedule(rows),start_s=max(g['clock_s'] for g in groups)+1.5)
            else:command['start_s']=time.monotonic()+1.5
            self.check();self.broadcast(ids,command);self.update(phase='waiting')
            if spec['mode']=='hardware':duration=sum(command['timing'].values())
            else:
                from bimanual_program import resolve_action
                from performance_program import program
                duration=program(resolve_action(spec['kind'],spec['action'],'left'))['duration_s']/spec['speed']
            completion_deadline=time.monotonic()+1.5+duration*spec['cycles']+8
            while True:
                self.check();self.broadcast(ids,dict(name='group_beat',token=token))
                if time.monotonic()>completion_deadline:raise ValueError('编组未按时间轴完成 / Group did not finish its timeline')
                states=list(self.pool.map(self.read,ids));groups=[s.get('group',{}) for s in states]
                if any(g.get('token')!=token or g.get('phase') in ('failed','idle','preparing') or not g.get('active') and g.get('phase')!='completed' for g in groups):
                    raise ValueError(next((g.get('reason') for g in groups if g.get('reason')),None) or '编组成员中断 / A group member stopped')
                if spec['mode']=='hardware' and any(s['stale'] or s['connection']!='connected' for s in states):raise ValueError('编组反馈中断 / Group feedback expired')
                phases=[g['phase'] for g in groups]
                elapsed=[g.get('elapsed_s',0.) for g in groups]
                self.update(phase='playing' if any(p in ('playing','returning') for p in phases) else 'waiting',elapsed_s=min(elapsed),members=[dict(id=i,phase=g['phase'],elapsed_s=g.get('elapsed_s',0)) for i,g in zip(ids,groups)],sampled_phase_spread_ms=(max(elapsed)-min(elapsed))*1000,phase_samples_not_simultaneous=True)
                if all(p=='completed' for p in phases):phase='completed';reason='编组完成 / Group completed';break
                time.sleep(.12)
        except Exception as e:
            phase='stopped' if self.cancel.is_set() else 'failed';reason=str(e)
        finally:
            futures={i:self.pool.submit(self.post,i,dict(name='group_abort',token=token)) for i in ids}
            for i,f in futures.items():
                try:f.result()
                except Exception:cleanup.append(i)
            if spec['mode']=='hardware':
                deadline=time.monotonic()+2
                pending=set(ids)
                while pending and time.monotonic()<deadline:
                    for i in list(pending):
                        try:
                            h=self.read(i)['hardware']
                            if h.get('active') is False and h.get('stop_confirmed') is True:pending.remove(i)
                        except Exception:pass
                    if pending:time.sleep(.1)
                cleanup=sorted(set(cleanup)|pending)
            if cleanup:reason+='; 停止结果待确认 / Stop unconfirmed: '+', '.join(cleanup)
            previous=self.snapshot()['members']
            final_members=[dict(m,phase='stop_unconfirmed' if m['id'] in cleanup else 'completed' if phase=='completed' else 'stopped',phase_before_stop=m['phase']) for m in previous]
            self.update(active=False,phase=phase,reason=reason,stop_unconfirmed=cleanup,members=final_members)
