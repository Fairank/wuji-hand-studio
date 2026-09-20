"""Linux glove session: SDK-only mapping, separate local feedback/control thread.

SSH carries status and operator commands, never the real-time target stream.
Scanning/preview opens no hand. Preparing a hand only subscribes to feedback.
"""
import collections
import copy
import math
import queue
import threading
import time

class GloveSource:
    def __init__(self,timeout_ms=250):
        self.lock=threading.Lock();self.timeout_s=timeout_ms/1000
        self.q=None;self.updated=None;self.seq=None;self.stamp=None;self.frames=0
        self.arrivals=collections.deque(maxlen=512);self.error='等待手套骨架 / Waiting for skeleton'
        from stream_rates import ArrivalRates
        self.rates=ArrivalRates()

    def update(self,q,seq,stamp,now):
        from hardware_showcase import vector
        q=vector(q)
        if type(seq) is not int or type(stamp) is not int:raise ValueError('Missing glove frame sequence/timestamp')
        with self.lock:
            if self.seq is not None and (seq<=self.seq or stamp<=self.stamp):return False
            self.q=q;self.seq=seq;self.stamp=stamp;self.updated=now;self.frames+=1;self.error=''
            self.arrivals.append(now)
            self.rates.add(now,seq)
        return True

    def invalidate(self,message):
        with self.lock:self.q=None;self.error=message

    def target(self,now):
        with self.lock:
            if self.q is None or self.updated is None or max(0,now-self.updated)>self.timeout_s:
                raise ValueError(self.error or '手套数据已过期；请手动重新开始 / Glove data expired')
            return self.q[:]

    def snapshot(self,now):
        with self.lock:
            age=None if self.updated is None else max(0,now-self.updated)*1000
            statistics=self.rates.snapshot(max(now,self.updated or now));hz=statistics['rate_hz']
            fresh=self.q is not None and age is not None and age<=self.timeout_s*1000
            return dict(q=self.q[:] if self.q is not None else None,seq=self.seq,frames=self.frames,
                        age_ms=age,retarget_hz=hz if fresh else None,fresh=fresh,error=self.error,
                        timeout_ms=round(self.timeout_s*1000))

def read_latest(sub):
    latest=None
    for _ in range(512):
        f=sub.recv()
        if f is None:return latest
        latest=f
    raise ValueError('手套队列积压；请重新连接 / Glove backlog was not drained')

def worker_glove(requests,events):
    from device_profiles import controller_profile
    selected=controller_profile();source=GloveSource();glove=sub=session=None
    hand_thread=None;hand_requests=queue.Queue();hand_events=queue.Queue()
    hand_state={};hand_error='';manager=None;previous_user=None
    devices=[];users=[];current=None;state='starting';message='正在加载官方SDK'
    glove_sn='';last_emit=0.;next_map=0.;last_source_seq=None
    try:
        import numpy as np
        import wuji_sdk as sdk
        manager=sdk.SdkManager.instance();previous_user=manager.current_user();current=previous_user
        users=manager.list_users()
        def scan():
            return [dict(serial=d.sn,address=str(d.address)) for d in manager.scan()
                    if d.device_type==sdk.DeviceType.WujiGlove]
        devices=scan();state='ready';message='选择手套和标定用户 / Select glove and SDK user'
        while True:
            now=time.monotonic()
            try:c=requests.get_nowait()
            except queue.Empty:c=None
            if c:
                try:
                    name=c['name']
                    if name in ('disconnect','glove_disconnect'):break
                    if name=='glove_scan':
                        if glove is not None:raise ValueError('先断开手套再扫描 / Disconnect glove before scanning')
                        devices=scan();message='扫描完成 / Scan completed'
                    elif name=='glove_open':
                        if glove is not None:raise ValueError('手套已连接，修改配置请先断开')
                        if c['serial'] not in {d['serial'] for d in devices}:raise ValueError('请先扫描并选择手套')
                        user_id=c.get('user_id') or current['user_id']
                        if user_id not in {u['user_id'] for u in users}:raise ValueError('Unknown SDK user')
                        manager.switch_user(user_id);current=manager.current_user()
                        candidate=manager.connect(sn=c['serial'],device_name='studio_glove')
                        try:
                            actual=str(candidate.hand_side().get()).lower()
                            if actual not in (selected['side'],'handedness.'+selected['side']):
                                raise ValueError('手套左右与工作台所选型号不符 / Glove side differs from profile')
                            model=sdk.HandModel.WujiHand2 if selected['generation']=='hand2' else sdk.HandModel.WujiHand
                            side=sdk.Handedness.Left if selected['side']=='left' else sdk.Handedness.Right
                            session=sdk.RetargetSession.for_hand(model,side=side)
                            sub=candidate.hand_skeleton().subscribe()
                        except Exception:
                            candidate.disconnect();raise
                        glove=candidate;glove_sn=c['serial'];source=GloveSource(c['timeout_ms'])
                        state='receiving';message='已连接手套，机械手未启用 / Glove connected; hand not enabled'
                    elif name=='glove_prepare':
                        if glove is None:raise ValueError('先连接手套 / Connect glove first')
                        source.target(now)
                        if selected['generation']!='hand2':raise ValueError('本版一代手套映射仅预览 / Hand 1 mapping preview only')
                        if hand_thread is not None:raise ValueError('已有机械手会话；请先断开全部再重试')
                        from console_agent import worker
                        from glove_motion import GloveMotion
                        def factory(*args):return GloveMotion(*args,source=source)
                        hand_thread=threading.Thread(target=worker,args=(c.get('address',''),hand_requests,hand_events,factory),daemon=True)
                        hand_thread.start();message='正在连接机械手，只读反馈 / Connecting hand feedback only'
                    elif name=='glove_follow':
                        if not hand_thread or not hand_thread.is_alive():raise ValueError('先连接机械手反馈 / Prepare hand feedback first')
                        source.target(now)
                        hand_requests.put(dict(name='hardware_start',lease=c['lease'],workspace_clear=c['workspace_clear']))
                    elif name=='glove_keepalive':hand_requests.put(dict(name='hardware_keepalive',lease=c.get('lease')))
                    elif name=='glove_stop':hand_requests.put(dict(name='hardware_stop'))
                except Exception as e:message=str(e);events.put(dict(type='glove_notice',message=message))
            if glove is not None and now>=next_map:
                next_map=now+1/120
                try:
                    frame=read_latest(sub)
                    if frame is not None:
                        seq=frame.header.seq;stamp=frame.header.timestamp_us
                        # No repeated SDK frame may renew the controller's freshness.
                        if last_source_seq is None or seq>last_source_seq:
                            kp=np.asarray([j.pose.position for j in frame.joints],dtype=np.float32)
                            if kp.shape!=(21,3) or not np.isfinite(kp).all():raise ValueError('Expected 21 finite keypoints')
                            q=np.asarray(session.step(kp),dtype=float).reshape(-1).tolist()
                            if source.update(q,seq,stamp,time.monotonic()):last_source_seq=seq
                except Exception as e:
                    source.invalidate(str(e));message=str(e)
            while True:
                try:event=hand_events.get_nowait()
                except queue.Empty:break
                if event['type']=='state':hand_state=event
                elif event['type'] in ('notice','error'):
                    hand_error=event['message'];message=hand_error
                    if event['type']=='error':hand_state['connection']='error'
                elif event['type']=='closed':
                    hand_state['connection']='disconnected'
                    if 'hardware' in event:hand_state['hardware']=event['hardware']
            now=time.monotonic()
            if now-last_emit>=.05:
                snapshot=source.snapshot(now)
                hw=copy.deepcopy(hand_state.get('hardware',{}))
                feedback=copy.deepcopy({k:hand_state.get(k) for k in ('latest','device_id','joint_rates','metrics','connection')})
                if feedback.get('latest'):
                    feedback['metrics']['age_ms']=max(0,now-feedback['latest']['host_s'])*1000
                events.put(dict(type='glove_state',state=dict(version=1,connection=state,message=message,
                    devices=devices,users=users,user=current,profile=selected['id'],glove_serial=glove_sn,
                    stream=snapshot,hardware=hw,feedback=feedback,hand_error=hand_error,
                    parameters=__import__('official_policy').settings(),
                    source='official_sdk_live_retarget',hardware_validated=False)))
                last_emit=now
            time.sleep(.001)
    except Exception as e:events.put(dict(type='glove_error',message=str(e)))
    finally:
        source.invalidate('连接已结束 / Session closed')
        if hand_thread:
            hand_requests.put(dict(name='disconnect'));hand_thread.join(timeout=4)
        if sub:
            try:sub.close()
            except Exception:pass
        if glove:
            try:glove.disconnect()
            except Exception:pass
        if manager and previous_user and (not hand_thread or not hand_thread.is_alive()):
            try:manager.switch_user(previous_user['user_id'])
            except Exception as e:events.put(dict(type='glove_notice',message='用户恢复未完成：'+str(e)))
        final_hardware=copy.deepcopy(hand_state.get('hardware',{}))
        while True:
            try:e=hand_events.get_nowait()
            except queue.Empty:break
            if e['type']=='closed':final_hardware=e.get('hardware',{})
        if hand_thread and (hand_thread.is_alive() or not final_hardware):
            final_hardware=dict(active=None,stop_confirmed=False,reason='停用结果未确认 / Disable not confirmed')
        events.put(dict(type='glove_closed',hardware=final_hardware))
