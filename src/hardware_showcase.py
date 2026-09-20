"""Explicit commissioning trials and calibrated hardware playback.

Trajectory points are in SDK joint order/radians, never a provisional MJCF map.
The profile is provisioned locally after calibration, not accepted over HTTP.
Bounded commissioning is separate and never creates a fake reviewed profile.
"""
import json
import math
import time
from copy import deepcopy
from bisect import bisect_right,bisect_left
from pathlib import Path
from official_policy import CURRENT_LIMIT_A,KP,KD,LOWER_RAD,UPPER_RAD,classify_device_error,settings
from motion_parameters import PROBE_SPEED_RAD_S
from motion_timing import CommandCadence,PublishTiming,segment_position,COMMAND_HZ,EXECUTION_VERSION

PROFILE = Path(__file__).with_name('hardware_showcase_profile.json')
NIDS = [finger*5+joint+1 for finger in range(5) for joint in range(4)]
NID_INDEX = {nid:i for i,nid in enumerate(NIDS)}


def vector(values, length=20):
    if not isinstance(values,list) or len(values)!=length or any(
        type(x) not in {int,float} or not math.isfinite(x) for x in values):
        raise ValueError('需要完整有限数值关节数组')
    return [float(x) for x in values]


def validate_profile(p, device_id):
    if (not isinstance(p,dict) or p.get('schema')!=1 or p.get('device_id')!=device_id
        or p.get('side')!='left' or p.get('hardware_reviewed') is not True
        or not isinstance(p.get('calibration_record'),str) or not p['calibration_record'].strip()):
        raise ValueError('缺少与当前左手匹配的实机动作校准')
    lo,hi=vector(p.get('lower_rad')),vector(p.get('upper_rad'))
    if any(not -3. <= a < b <= 3. for a,b in zip(lo,hi)):
        raise ValueError('实机限位无效')
    rate=p.get('max_velocity_rad_s')
    cap=p.get('current_limit_A')
    if type(rate) not in {int,float} or not 0<rate<=.3 or type(cap) not in {int,float} or not 0<cap<=.5:
        raise ValueError('首版动作速度/电流上限无效')
    actions=p.get('actions')
    if not isinstance(actions,dict) or not 1<=len(actions)<=12:
        raise ValueError('没有已核验的实机动作')
    from demo_player import CATALOG
    result={}
    for name,action in actions.items():
        if name not in CATALOG or action.get('hardware_reviewed') is not True:
            raise ValueError('动作未单独核验')
        points=action.get('points')
        if not isinstance(points,list) or not 2<=len(points)<=1000:
            raise ValueError('动作轨迹长度无效')
        checked=[]
        for point in points:
            t=point.get('t'); q=vector(point.get('q'))
            if type(t) not in {int,float} or not math.isfinite(t) or not 0<=t<=180:
                raise ValueError('动作时间无效')
            if any(not a<=x<=b for x,a,b in zip(q,lo,hi)):
                raise ValueError('动作超出已核验限位')
            if checked:
                dt=t-checked[-1]['t']
                if dt<=0 or any(abs(x-y)/dt>rate+1e-9 for x,y in zip(q,checked[-1]['q'])):
                    raise ValueError('动作时间或速度超限')
            elif t!=0:raise ValueError('动作必须从零时刻开始')
            checked.append(dict(t=float(t),q=q))
        if max(abs(a-b) for a,b in zip(checked[0]['q'],checked[-1]['q']))>1e-6:
            raise ValueError('循环首尾必须连续')
        result[name]=checked
    return dict(device_id=device_id,lower_rad=lo,upper_rad=hi,max_velocity_rad_s=float(rate),
                current_limit_A=float(cap),actions=result)


def load_profile(device_id, path=PROFILE):
    try:
        if path.stat().st_size>1024*1024:raise ValueError('校准文件过大')
        return validate_profile(json.loads(path.read_text(encoding='utf-8')),device_id),''
    except FileNotFoundError:return None,'尚未完成实机动作校准'
    except (ValueError,KeyError,TypeError,OSError,AttributeError) as e:return None,str(e)


class HardwareShowcase:
    def __init__(self,hand,device_id,command_factory,profile_path=PROFILE):
        self.hand,self.command_factory=hand,command_factory
        self.profile,self.reason=load_profile(device_id,profile_path)
        self.active=False;self.owned=False;self.paused=False;self.publisher=None
        self.phase='idle';self.lease='';self.last_beat=0.;self.last_send=0.
        self.elapsed=0.;self.cycles=1;self.action='';self.error=None
        self.run_profile=self.profile;self.probe=None;self.probe_result=None
        self.enabled_indices=list(range(20));self.probe_ready=False
        self.probe_reason='等待完整反馈与诊断';self.pending_report=None
        self.comm_stable_since=None;self.comm_counters=None;self.comm_healthy=False
        self.last_diag_host=None
        self.warnings=[]
        self.trial=None;self.trial_result=None
        self.latest_observation=None
        self.refresh_before_enable=None
        self.publish_timing=PublishTiming()

    def status(self):
        return dict(ready=self.profile is not None,active=None if self.owned and not self.active else self.active,
            stop_confirmed=not self.owned,paused=self.paused,
            phase=self.phase,reason=self.reason,action=self.action,elapsed_s=self.elapsed,
            actions=list(self.profile['actions']) if self.profile else [],
            source='supervised_low_current_trial' if self.trial else 'bounded_single_joint_check' if self.probe else 'calibrated_sdk_trajectory',feedback_source='real_joint_states',
            current_limit_A=self.run_profile['current_limit_A'] if self.run_profile else None,
            probe_ready=self.probe_ready and not self.owned,probe_reason=self.probe_reason,
            probe_result=self.probe_result,probe_index=self.probe['index'] if self.probe else None,
            warnings=self.warnings,comm_stable=self.comm_healthy,
            trial_controls_version=3,gesture_library_version=1,warning_policy='official_sdk_severity',
            commissioning_policy=settings(),
            execution_version=EXECUTION_VERSION,command_timing=dict(self.publish_timing.snapshot(),
                missed_deadlines=getattr(getattr(self,'cadence',None),'missed',0)),
            speed_factor=self.run_profile.get('speed_factor',1.) if self.run_profile else None,
            trial_ready=self.probe_ready and not self.owned,trial_result=self.trial_result,
            trial_phase=self.trial['label'] if self.trial else None,
            trial_amplitude=self.trial['amplitude'] if self.trial else None,
            action_source=self.run_profile.get('source') if self.run_profile else None,
            planned_duration_s=self.period*self.cycles if self.trial else None,
            cycle=min(self.cycles,int(self.elapsed/self.period)+1) if self.trial else None,
            cycles=self.cycles)

    def start_trial(self,command,row,diag,now):
        from hardware_trial import make_trial,LABELS
        if self.active or self.owned:raise ValueError('已有实机动作或停用状态待确认')
        lease=command.get('lease')
        if command.get('workspace_clear') is not True or not isinstance(lease,str) or len(lease)!=32:
            raise ValueError('先确认底座固定且手指周围无人无物')
        if not self.check_probe_ready(row,diag,now):raise ValueError(self.probe_reason)
        if [j.label for j in sorted(self.hand.joints(),key=lambda j:j.index)]!=LABELS:
            raise ValueError('设备SDK关节标签与候选姿态不符')
        q=self.raw_position(row,now)
        plan=make_trial(q,command.get('action'),command.get('amplitude'),command.get('cycles'),speed=command.get('speed',1.),clock_at=command.get('clock_at'))
        self.probe=None;self.trial_result=None
        self.run_profile=plan;self.points=plan['points'];self.enabled_indices=list(range(20))
        self.action=plan['action'];self.speed=1.;self.cycles=plan['cycles'];self.lease=lease
        self.trial=dict(label=plan['label'],action=plan['action'],amplitude=plan['amplitude'],
            baseline=q[:],last=q[:],peak=[0.]*20,peak_current=[0.]*20,trace=[],commands_sent=0,
            started_unix=time.time(),started=now,deadline=now+plan['points'][-1]['t']*self.cycles*1.5+30,
            settled_since=None,pose_checks=[],checked_holds=set(),fault=None,peak_samples=[None]*20,
            tracking_max_error_deg=0.,tracking_samples=0)
        self.begin(q,now)

    def observe_diagnostics(self,diag,now):
        """Display-only communication statistics; never a Warning motion gate."""
        self.warnings=[dict(nid=j['nid'],code=j['error'],name=j.get('error_name'),
            severity=j.get('severity'),description=j.get('error_description')) for j in diag['joints'] if j['error']]
        c=diag.get('comm',{})
        if self.last_diag_host is not None and now-self.last_diag_host>.1:
            self.comm_stable_since=None;self.comm_counters=None
        self.last_diag_host=now
        keys=('e2e_lost','e2e_reordered','e2e_duplicates','rpc_timeouts','comm_get_failures','sdk_dropped')
        values=[c.get(k) for k in keys]+[j.get('bus_timeouts') for j in sorted(diag['joints'],key=lambda j:j['nid'])]
        valid=(0<=now-diag['host_s']<=.1 and len(diag['joints'])==20 and
            {j['nid'] for j in diag['joints']}==set(NIDS) and
            type(c.get('age_ms')) is int and 0<=c['age_ms']<=1500 and
            c.get('e2e_window_loss_x100')==0 and c.get('e2e_received',0)>0 and
            all(j.get('response_pct')==100 for j in diag['joints']) and
            all(type(v) is int and 0<=v<65535 for v in values))
        if not valid:
            self.comm_stable_since=None;self.comm_counters=None;self.comm_healthy=False;return
        counters=tuple(values)
        if counters!=self.comm_counters:
            self.comm_counters=counters;self.comm_stable_since=now
        self.comm_healthy=self.comm_stable_since is not None and now-self.comm_stable_since>=3.

    def check_probe_ready(self,row,diag,now):
        """Read-only preflight; never fabricates a reviewed trajectory profile."""
        try:
            q=self.raw_position(row,now);self.diagnostic(diag,now,allow_bus_warning=True)
            if any(j.get('state_name')!='Ready' for j in diag['joints']):
                raise ValueError('首次试动要求20关节均为Ready；请先停止其他控制程序')
            outside=[(i,x,a,b) for i,(x,a,b) in enumerate(zip(q,LOWER_RAD,UPPER_RAD)) if not a<=x<=b]
            if outside:
                i,x,a,b=outside[0];name=('拇指','食指','中指','无名指','小指')[i//4]
                raise ValueError(f'软件按文档行程检查：{name} S{i%4+1}反馈{math.degrees(x):.2f}°，范围{math.degrees(a):g}～{math.degrees(b):g}°；需核对姿态/零点（不是设备停机故障）')
            self.probe_ready=True;self.probe_reason='反馈就绪，可按官方初调参数播放；Warning仅记录'
        except (ValueError,KeyError,TypeError) as error:
            self.probe_ready=False;self.probe_reason=str(error)
        return self.probe_ready

    def raw_position(self,row,now):
        if row is None or not 0<=now-row['host_s']<=.1:raise ValueError('实机反馈过期，停止动作')
        entries={j['nid']:j for j in row['joints']}
        if set(entries)!=set(NIDS) or len(row['joints'])!=20:raise ValueError('20关节反馈不完整')
        vector([entries[n]['effort_A'] for n in NIDS])
        vector([entries[n].get('velocity_rad_s') for n in NIDS])
        return vector([entries[n]['position_rad'] for n in NIDS])

    def start_probe(self,command,row,diag,now):
        if self.active or self.owned:raise ValueError('已有实机动作或停用状态待确认')
        index=command.get('index');direction=command.get('direction');lease=command.get('lease')
        if (type(index) is not int or not 0<=index<20 or type(direction) is not int or direction not in {-1,1}
            or command.get('workspace_clear') is not True or not isinstance(lease,str) or len(lease)!=32):
            raise ValueError('请选择关节、方向，并确认手指周围没有人或物体')
        if not self.check_probe_ready(row,diag,now):raise ValueError(self.probe_reason)
        q=self.raw_position(row,now);goal=q[:];goal[index]+=direction*math.radians(3)
        if not LOWER_RAD[index]<=goal[index]<=UPPER_RAD[index]:
            raise ValueError('试动目标超出官方文档关节行程，请换方向')
        self.run_profile=dict(lower_rad=LOWER_RAD[:],upper_rad=UPPER_RAD[:],
            current_limit_A=CURRENT_LIMIT_A,max_velocity_rad_s=PROBE_SPEED_RAD_S,control_policy=settings())
        self.trial=None
        self.probe=dict(index=index,direction=direction,initial=q[:],peak=[0.]*20,last=q[:],
            directed_peak=0.,commands_sent=0,started_unix=time.time(),deadline=now+15.,settled_since=None,trace=[])
        self.probe_result=None;self.enabled_indices=[index]
        self.action='single_joint_check';self.speed=1.;self.cycles=1;self.lease=lease
        self.points=[dict(t=0.,q=q),dict(t=1.2,q=goal),dict(t=1.6,q=goal),dict(t=2.8,q=q),dict(t=3.4,q=q)]
        self.begin(q,now)

    def position(self,row,now):
        q=self.raw_position(row,now)
        entries={j['nid']:j for j in row['joints']}
        current=vector([entries[n]['effort_A'] for n in NIDS])
        if not (self.probe or self.trial) and any(abs(x)>self.run_profile['current_limit_A']+.05 for x in current):
            raise ValueError('反馈电流超过展示上限')
        if any(not a<=x<=b for x,a,b in zip(q,self.run_profile['lower_rad'],self.run_profile['upper_rad'])):
            raise ValueError('实际姿态超出官方文档行程' if self.probe or self.trial else '实际姿态超出已核验范围')
        return q

    def diagnostic(self,diag,now,require_enabled=False,allow_bus_warning=False):
        if diag is None or not 0<=now-diag['host_s']<=.1 or len(diag['joints'])!=20:
            raise ValueError('实机诊断未就绪或过期')
        if {j['nid'] for j in diag['joints']}!=set(NIDS):raise ValueError('关节诊断不完整')
        for joint in diag['joints']:
            if joint['error']:
                category=classify_device_error(joint['error'],joint.get('severity'),joint.get('error_name'))
                if category!='warning':
                    raise ValueError(f"节点{joint['nid']}：{joint.get('error_name') or '未知设备错误'} "
                        f"(0x{joint['error']:04X}, {joint.get('severity') or '未分类'})；按官方控制流程请求停用")
            if joint.get('position_limit_active'):raise ValueError('设备报告关节限位')
            index=NID_INDEX[joint['nid']]
            if require_enabled and index in self.enabled_indices and joint['state']!=2:raise ValueError('关节未处于已启用状态')
            if require_enabled and self.probe and index not in self.enabled_indices and joint.get('state_name')!='Ready':
                raise ValueError('非选定关节状态发生变化')

    def start(self,command,row,diag,now):
        if self.profile is None:raise ValueError(self.reason)
        if self.active or self.owned:raise ValueError('已有实机动作正在执行')
        action=command.get('action');speed=command.get('speed');cycles=command.get('cycles')
        lease=command.get('lease')
        if action not in self.profile['actions'] or type(speed) not in {int,float} or speed not in {.25,.5,1.}:
            raise ValueError('选择已校准动作与速度')
        if type(cycles) is not int or cycles not in {0,1,3,10} or not isinstance(lease,str) or len(lease)!=32:
            raise ValueError('循环或控制会话无效')
        self.run_profile=self.profile;self.probe=None;self.trial=None;self.enabled_indices=list(range(20))
        q=self.position(row,now);self.diagnostic(diag,now)
        self.action,self.speed,self.cycles,self.lease=action,speed,cycles,lease
        self.points=self.profile['actions'][action]
        self.begin(q,now)

    def index_path(self):
        self.period=self.points[-1]['t']
        self.point_times=[p['t'] for p in self.points]
        official=self.action=='official_opposition'
        self.holds=[(i,b) for i,(a,b) in enumerate(zip(self.points,self.points[1:]))
                    if a['q']==b['q'] and (not official or b.get('verify_hold'))]
        self.hold_ends=[b['t'] for _,b in self.holds]

    def rebase_commissioning(self,q,now):
        """Rebuild from the post-RPC feedback; no stale-pose jump or drift gate."""
        if self.trial:
            from hardware_trial import make_trial
            self.run_profile=make_trial(q,self.action,self.trial['amplitude'],self.cycles,
                speed=self.run_profile['speed_factor'],clock_at=self.run_profile.get('clock_at'))
            self.points=self.run_profile['points']
            self.trial.update(baseline=q[:],last=q[:],started=now,
                deadline=now+self.points[-1]['t']*self.cycles*1.5+30)
        elif self.probe:
            old=self.probe['initial']
            points=[dict(p,q=[x+a-b for x,a,b in zip(p['q'],q,old)]) for p in self.points]
            if any(not a<=x<=b for p in points for x,a,b in zip(p['q'],LOWER_RAD,UPPER_RAD)):
                raise ValueError('更新后的试动目标超出官方文档关节行程')
            self.points=points;self.probe.update(initial=q[:],last=q[:],deadline=now+15.)
        self.index_path()

    def begin(self,q,now):
        self.latest_observation=None
        self.last_trial_row=None
        self.publish_timing=PublishTiming()
        self.index_path()
        self.target=q[:];self.elapsed=0.;self.last_beat=now;self.last_tick=now;self.last_send=now
        self.pre_enable_seed=q[:]
        self.phase='enabling';self.paused=False;self.enable_deadline=now+5.;self.reason='正在启用低速展示'
        self.approach_deadline=now+30.
        self.owned=True
        try:
            self.hand.effort_limit().set(self.run_profile['current_limit_A'])
            expected_kp,expected_kd=(KP,KD) if self.probe or self.trial else (3.,.05)
            self.hand.mit_params().set((expected_kp,expected_kd))
            params=self.hand.mit_params().get()
            if len(params)!=20 or any(p is None or not math.isfinite(p.kp) or not math.isfinite(p.kd) or
                abs(p.kp-expected_kp)>1e-5 or abs(p.kd-expected_kd)>1e-5 for p in params):
                raise ValueError('控制增益读回不符')
            limits=self.hand.effort_limit().get()
            if len(limits)!=20 or any(x is None or not math.isfinite(x) or abs(x-self.run_profile['current_limit_A'])>1e-6 for x in limits):
                raise ValueError('电流上限读回不符')
            if self.refresh_before_enable is not None:
                row,diag,fresh_now=self.refresh_before_enable()
                self.capture_observation(row,diag,fresh_now)
                actual=self.position(row,fresh_now)
                self.diagnostic(diag,fresh_now,allow_bus_warning=bool(self.probe or self.trial))
                if self.probe or self.trial:
                    if not self.check_probe_ready(row,diag,fresh_now):
                        raise ValueError(self.probe_reason)
                    self.rebase_commissioning(actual,fresh_now)
                elif max(abs(a-b) for a,b in zip(actual,q))>.01:
                    raise ValueError('参数读回期间姿态发生变化，未启用电机；请重新检查起始姿态')
                self.target=actual[:];q=actual
                now=fresh_now
            self.enable_verified(q,now)
        except Exception as error:
            if self.trial:self.trial['fault']=deepcopy(self.latest_observation)
            self.stop('启动失败：'+str(error)+'；已请求停用')
            raise

    def enable_verified(self,q,now):
        """Seed the current measured pose before enabling the selected joints."""
        self.phase='enabling';self.enable_deadline=now+5.;self.approach_deadline=now+30.
        self.last_tick=now;self.last_send=now;self.target=q[:]
        self.cadence=CommandCadence(now)
        self.publisher=self.hand.joint_command().publish()
        self.publish(q)
        if self.probe:self.hand.enable(joints=[int(i in self.enabled_indices) for i in range(20)])
        else:self.hand.enable()
        self.active=True;self.reason='正在启用低速展示'

    def publish(self,q):
        # 【SDK命令字段】position=目标关节角(rad)，velocity=目标速度(rad/s)。
        # 当前velocity=0使Kd项提供阻尼；不是把实际关节速度强制设为0。
        # effort=前馈电流(A)，当前0；闭环Kp/Kd仍会根据误差产生电流。
        # 常用参数请改motion_parameters.py；不要把effort误作N或Nm。
        commands=[self.command_factory(position=x,velocity=0.,effort=0.) for x in q]
        send_start=time.perf_counter()
        self.publisher.send(commands)
        self.publish_timing.add(send_start,time.perf_counter())
        if self.probe:self.probe['commands_sent']+=1
        if self.trial:self.trial['commands_sent']+=1

    def command(self,c,row,diag,now):
        name=c['name']
        if name=='hardware_start':return self.start(c,row,diag,now)
        if name=='hardware_probe':return self.start_probe(c,row,diag,now)
        if name=='hardware_trial':return self.start_trial(c,row,diag,now)
        if name=='hardware_stop':return self.stop('用户停止实机展示')
        if name=='hardware_keepalive':
            if self.active and c.get('lease')==self.lease:self.last_beat=now
            return
        if not self.active or c.get('lease')!=self.lease:raise ValueError('实机控制会话已过期')
        if name=='hardware_pause':
            self.target=self.position(row,now)
            self.paused=True;self.reason='已暂停，低电流保持当前位置'
        elif name=='hardware_resume':self.paused=False;self.reason='继续实机展示'
        else:raise ValueError('未知实机操作')

    def capture_observation(self,row,diag,now):
        # Preserve the actual frame BEFORE any guard can terminate this trial.
        # This includes diagnostic faults and over-current, formerly absent from traces.
        if self.trial:
            # Agent creates new row/diagnostic dictionaries per frame and never
            # mutates them. Copy only when retaining a fault/final report; a
            # deepcopy of all 20 joints on every control poll wastes 1 kHz time.
            self.latest_observation=dict(host_s=now,feedback=row,diagnostics=diag,
                target_rad=self.target[:],phase=self.phase,action_phase=self.trial['label'],
                elapsed_s=self.elapsed)

    def tick(self,row,diag,now):
        if not self.active:return
        self.capture_observation(row,diag,now)
        try:
            if now-self.last_beat>.8:raise ValueError('网页控制连接中断')
            if self.probe and now>self.probe['deadline']:raise ValueError('单关节试动达到15秒上限')
            if self.trial and now>self.trial['deadline']:raise ValueError('整套试运行达到时间上限')
            q=self.position(row,now);self.diagnostic(diag,now)
            if self.trial and row is not self.last_trial_row:
                self.observe_trial(row,q,now)
                self.last_trial_row=row
            if self.probe:
                self.probe['last']=q[:]
                self.probe['peak']=[max(p,abs(x-y)) for p,x,y in zip(self.probe['peak'],q,self.probe['initial'])]
                i=self.probe['index']
                self.probe['directed_peak']=max(self.probe['directed_peak'],self.probe['direction']*(q[i]-self.probe['initial'][i]))
                trace=self.probe['trace']
                if not trace or self.elapsed-trace[-1]['t']>=.05:
                    joint=next(j for j in row['joints'] if j['nid']==NIDS[i])
                    trace.append(dict(t=self.elapsed,q=q[i],target=self.target[i],effort_A=joint['effort_A']))
            if self.phase=='enabling':
                if all(j['state']==2 for j in diag['joints'] if NID_INDEX[j['nid']] in self.enabled_indices):
                    self.phase='approach';self.last_tick=now
                elif now>self.enable_deadline:raise ValueError('关节启用超时')
                else:return
            self.diagnostic(diag,now,require_enabled=True)
            if not (self.probe or self.trial) and max(abs(a-b) for a,b in zip(q,self.target))>.20:
                raise ValueError('实际姿态与指令偏差过大')
            if not self.cadence.due(now):return
            dt=min(.03,max(0.,now-self.last_tick));self.last_tick=now;self.last_send=now
            if self.paused:desired=self.target
            elif self.phase=='approach':
                if now>self.approach_deadline:raise ValueError('起始姿态过渡超时')
                desired=self.points[0]['q']
                if max(abs(a-b) for a,b in zip(q,desired))<.03:self.phase='playing'
            else:
                self.elapsed+=dt*self.speed
                if self.cycles and self.elapsed>=self.period*self.cycles:
                    if not self.probe and not self.trial:
                        self.stop('循环完成，已请求停用电机',completed=True);return
                    self.phase='returning'
                    if self.probe:
                        record=self.probe;i=record['index']
                        joint=next(j for j in row['joints'] if j['nid']==NIDS[i])
                        settled=abs(q[i]-record['initial'][i])<=math.radians(1) and abs(joint['velocity_rad_s'])<.03
                    else:
                        record=self.trial
                        settled=max(abs(x-y) for x,y in zip(q,record['baseline']))<=math.radians(2) and max(abs(j['velocity_rad_s']) for j in row['joints'])<.05
                    if not settled:record['settled_since']=None
                    elif record['settled_since'] is None:record['settled_since']=now
                    elif now-record['settled_since']>=.2:
                        self.stop('实测返回完成，已请求停用电机',completed=True);return
                    if self.elapsed>=self.period*self.cycles+2.:
                        self.stop('实际返回未在2秒内达到要求，已请求停用');return
                    t=self.period
                else:t=self.elapsed%self.period
                index=min(len(self.points)-2,max(0,bisect_right(self.point_times,t)-1))
                a,b=self.points[index:index+2]
                desired=segment_position(a,b,t)
                if self.trial:self.trial['label']=b['label']
            step=self.run_profile['max_velocity_rad_s']*dt
            self.target=[x+max(-step,min(step,y-x)) for x,y in zip(self.target,desired)]
            self.publish(self.target)
        except Exception as error:
            if self.trial:self.trial['fault']=deepcopy(self.latest_observation)
            self.stop(str(error))

    def observe_trial(self,row,q,now):
        p=self.trial;p['last']=q[:]
        entries={j['nid']:j for j in row['joints']};currents=[abs(entries[n]['effort_A']) for n in NIDS]
        if self.phase=='playing':
            p['tracking_samples']+=1
            p['tracking_max_error_deg']=max(p['tracking_max_error_deg'],
                math.degrees(max(abs(a-b) for a,b in zip(q,self.target))))
        for i,(x,start) in enumerate(zip(q,p['baseline'])):
            if abs(x-start)>p['peak'][i]:
                p['peak_samples'][i]=dict(nid=NIDS[i],t=now-p['started'],q=x,target=self.target[i],
                    effort_A=entries[NIDS[i]]['effort_A'],seq=row.get('seq'),device_timestamp_us=row.get('device_timestamp_us'))
        p['peak']=[max(a,abs(x-y)) for a,x,y in zip(p['peak'],q,p['baseline'])]
        p['peak_current']=[max(a,b) for a,b in zip(p['peak_current'],currents)]
        trace=p['trace']
        if not trace or now-p['started']-trace[-1]['t']>=.1:
            trace.append(dict(t=now-p['started'],q=q[:],target=self.target[:],current_A=currents,
                effort_A=[entries[n]['effort_A'] for n in NIDS],
                seq=row.get('seq'),device_timestamp_us=row.get('device_timestamp_us'),
                feedback_age_ms=(now-row['host_s'])*1000,phase=self.phase,label=p['label']))
        # One check near the end of every held pose, not a claim of contact.
        cycle=min(self.cycles-1,int(self.elapsed/self.period))
        t=self.elapsed-cycle*self.period
        k=bisect_left(self.hold_ends,t)
        if k<len(self.holds):
            i,b=self.holds[k]
            key=(cycle,i)
            hold_start=self.points[i]['t']
            check_start=b['t']-min(.1,(b['t']-hold_start)/2)
            if check_start<=t<=b['t'] and key not in p['checked_holds']:
                error=math.degrees(max(abs(x-y) for x,y in zip(q,b['q'])))
                p['pose_checks'].append(dict(cycle=cycle+1,label=b['label'],max_error_deg=error,within_3deg=error<=3.))
                p['checked_holds'].add(key)

    def stop(self,reason,completed=False):
        was_active=self.active or self.owned
        stopped_phase=self.phase
        if self.trial and was_active:
            self.trial['stop_observation']=deepcopy(self.latest_observation)
        self.active=False;self.paused=False;self.phase='stopped';self.reason=reason
        if self.owned:
            try:self.hand.disable()
            except Exception as error:
                self.reason += '；停用未确认：'+str(error)
                self.phase='stop_unconfirmed'
                # Keep ownership so final cleanup retries instead of reporting success.
            else:self.owned=False
        if self.publisher:
            try:self.publisher.close()
            except Exception as error:self.reason+='；指令通道关闭失败：'+str(error)
            finally:self.publisher=None
        if self.probe and was_active:
            p=self.probe;i=p['index'];peak=math.degrees(p['peak'][i])
            returned=math.degrees(abs(p['last'][i]-p['initial'][i]))
            directed=math.degrees(p['directed_peak'])
            other=math.degrees(max(x for j,x in enumerate(p['peak']) if j!=i))
            self.probe_result=dict(index=i,nid=NIDS[i],command_delta_deg=p['direction']*3,
                peak_actual_delta_deg=peak,directed_peak_deg=directed,other_joints_peak_deg=other,
                return_error_deg=returned,commands_sent=p['commands_sent'],
                completed=completed,stop_confirmed=not self.owned,
                feedback_motion_observed=peak>=1.5,returned_to_start=returned<=1.,
                accepted=bool(completed and not self.owned and directed>=1.5 and returned<=1. and other<=3.),
                full_action_calibrated=False,reason=self.reason,started_unix=p['started_unix'])
            self.pending_report=dict(self.probe_result)
            self.pending_report['trace']=p['trace']
            self.pending_report['control']=dict(kp=KP,kd=KD,current_limit_A=CURRENT_LIMIT_A,max_velocity_rad_s=PROBE_SPEED_RAD_S)
            self.pending_report['control_policy']=settings()
            self.pending_report['acceptance_basis']='project_motion_measurement_not_factory_certification'
        if self.trial and was_active:
            p=self.trial
            error=math.degrees(max(abs(x-y) for x,y in zip(p['last'],p['baseline'])))
            expected_checks=len(self.holds)*self.cycles
            poses_ok=len(p['pose_checks'])==expected_checks and all(c['within_3deg'] for c in p['pose_checks'])
            tracking_ok=self.action!='official_opposition' or (p['tracking_samples']>0 and p['tracking_max_error_deg']<=3.)
            self.trial_result=dict(kind='low_current_showcase_trial',action=p['action'],amplitude=p['amplitude'],
                requested_cycles=self.cycles,speed_factor=self.run_profile.get('speed_factor',1.),finished_cycles=min(self.cycles,int(self.elapsed/self.period)),
                schema=3,execution_version=EXECUTION_VERSION,peak_actual_delta_deg=math.degrees(max(p['peak'])),
                displacement_at_stop_deg=error,return_error_deg=error if completed or stopped_phase=='returning' else None,
                return_evaluated=bool(completed or stopped_phase=='returning'),stopped_phase=stopped_phase,stopped_action_phase=p['label'],
                peak_current_A=max(p['peak_current']),commands_sent=p['commands_sent'],
                completed=completed,stop_confirmed=not self.owned,pose_checks=p['pose_checks'],
                accepted=bool(completed and not self.owned and error<=2. and poses_ok and tracking_ok),
                action_source=self.run_profile.get('source'),source_url=self.run_profile.get('source_url'),
                source_commit=self.run_profile.get('source_commit'),source_sha256=self.run_profile.get('source_sha256'),
                planned_duration_s=self.period*self.cycles,tracking_max_error_deg=p['tracking_max_error_deg'],
                tracking_samples=p['tracking_samples'],
                physical_contact_verified=False,full_action_calibrated=False,
                control_policy=settings(),acceptance_basis='project_motion_measurement_not_factory_certification',
                reason=self.reason,started_unix=p['started_unix'])
            self.pending_report=dict(self.trial_result,trace=p['trace'],
                clock_at=self.run_profile.get('clock_at'),
                initial_q=p['baseline'],fault=p['fault'],stop_observation=p.get('stop_observation'),
                peak_samples=p['peak_samples'],
                control=dict(kp=KP,kd=KD,current_limit_A=CURRENT_LIMIT_A,max_velocity_rad_s=self.run_profile['max_velocity_rad_s']))
        if was_active and self.pending_report is not None:
            timing=self.publish_timing.report()
            timing['missed_deadlines']=getattr(getattr(self,'cadence',None),'missed',0)
            self.pending_report['command_timing']=timing
