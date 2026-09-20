"""Hand 2 live retarget adapter. Runs beside feedback on the Linux controller.

Reuses existing identity, feedback, official fault and publisher handling.
No browser pose injection, recording replay, automatic resume or gain preset.
"""
import math
from hardware_showcase import HardwareShowcase,vector
from official_policy import LOWER_RAD,UPPER_RAD,CURRENT_LIMIT_A,KP,KD,settings
from motion_parameters import COMMAND_SPEED_RAD_S,PATH_SPEED_RAD_S

class GloveMotion(HardwareShowcase):
    def __init__(self,*args,source,**kwargs):
        super().__init__(*args,**kwargs)
        self.source=source;self.raw_target=None;self.reason='手套遥操作 · 等待明确开始'

    def target_from_glove(self,now):
        q=vector(self.source.target(now))
        if any(not lo<=x<=hi for x,lo,hi in zip(q,LOWER_RAD,UPPER_RAD)):
            raise ValueError('映射目标超出官方关节行程 / Retarget output outside documented limits')
        return q

    def start(self,c,row,diag,now):
        if self.active or self.owned:raise ValueError('已有动作或停用状态待确认')
        if c.get('workspace_clear') is not True or len(c.get('lease',''))!=32:
            raise ValueError('需要明确开始与有效会话')
        if not self.check_probe_ready(row,diag,now):raise ValueError(self.probe_reason)
        from hardware_trial import LABELS
        if [j.label for j in sorted(self.hand.joints(),key=lambda j:j.index)]!=LABELS:
            raise ValueError('SDK joint order does not match retarget firmware order')
        q=self.raw_position(row,now);self.target_from_glove(now)
        self.run_profile=dict(lower_rad=LOWER_RAD,upper_rad=UPPER_RAD,current_limit_A=CURRENT_LIMIT_A,
                              max_velocity_rad_s=min(COMMAND_SPEED_RAD_S,PATH_SPEED_RAD_S))
        self.action='glove_teleoperation';self.lease=c['lease'];self.elapsed=0.;self.last_beat=now
        self.probe=self.trial=None;self.enabled_indices=list(range(20));self.owned=True
        from motion_timing import PublishTiming
        self.publish_timing=PublishTiming()
        try:
            self.hand.effort_limit().set(CURRENT_LIMIT_A)
            self.hand.mit_params().set((KP,KD))
            gains=self.hand.mit_params().get();limits=self.hand.effort_limit().get()
            if len(gains)!=20 or any(p is None or not math.isfinite(p.kp) or not math.isfinite(p.kd)
                    or abs(p.kp-KP)>1e-5 or abs(p.kd-KD)>1e-5 for p in gains):raise ValueError('控制增益读回不符')
            if len(limits)!=20 or any(x is None or not math.isfinite(x) or abs(x-CURRENT_LIMIT_A)>1e-6 for x in limits):
                raise ValueError('电流上限读回不符')
            if self.refresh_before_enable:
                row,diag,now=self.refresh_before_enable()
                if not self.check_probe_ready(row,diag,now):raise ValueError(self.probe_reason)
                q=self.raw_position(row,now)
            self.target_from_glove(now)
            self.enable_verified(q,now)
            self.last_beat=now;self.reason='手套跟随 · 当前控制端参数'
        except Exception as e:
            self.stop('启动失败：'+str(e));raise

    def tick(self,row,diag,now):
        if not self.active:return
        try:
            if now-self.last_beat>.8:raise ValueError('网页控制连接中断 / Browser lease expired')
            q=self.raw_position(row,now);self.diagnostic(diag,now)
            if any(not a<=x<=b for x,a,b in zip(q,LOWER_RAD,UPPER_RAD)):
                raise ValueError('实际姿态超出官方文档行程')
            desired=self.target_from_glove(now);self.raw_target=desired
            if self.phase=='enabling':
                if all(j['state']==2 for j in diag['joints']):self.phase='following';self.last_tick=now
                elif now>self.enable_deadline:raise ValueError('关节启用超时')
                else:return
            self.diagnostic(diag,now,require_enabled=True)
            if not self.cadence.due(now):return
            dt=min(.03,max(0.,now-self.last_tick));self.last_tick=now;self.elapsed+=dt
            step=self.run_profile['max_velocity_rad_s']*dt
            self.target=[x+max(-step,min(step,y-x)) for x,y in zip(self.target,desired)]
            self.publish(self.target)
        except Exception as e:self.stop(str(e))

    def status(self):
        s=super().status()
        s.update(source='official_sdk_live_retarget',action_source='Wuji Glove → RetargetSession → Hand 2',
                 teleop_version=1,raw_target_rad=self.raw_target,applied_target_rad=getattr(self,'target',None),
                 commissioning_policy=settings(),max_velocity_rad_s=min(COMMAND_SPEED_RAD_S,PATH_SPEED_RAD_S))
        return s
