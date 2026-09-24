"""Linux glove session: SDK-only mapping, separate local feedback/control thread.

SSH carries status and operator commands, never the real-time target stream.
Scanning/preview opens no hand. Preparing a hand only subscribes to feedback.
"""
import collections
import copy
import math
import queue
import sys
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


def paired_hand_worker(address, serial, selected, requests, events, factory, source=None):
    """Choose one fresh serial and verify side/model before creating a driver."""
    from device_discovery import connect_discovered, SelectionRequired
    from console_agent import worker
    hand=route=ownership=None
    try:
        from wuji_sdk import SdkManager
        hand,route,verified=connect_discovered(SdkManager.instance(),address,serial)
        if verified['id']!=selected['id']:
            raise ValueError('手套映射与机械手的代际或左右手不符 / Hand model/side differs from glove mapping')
        if sys.platform.startswith('linux'):
            from device_ownership import DeviceOwnership
            ownership=DeviceOwnership(hand.serial_number)
        owned_hand,owned_route=hand,route;hand=route=None
        if selected['generation']=='hand1':
            from first_generation import worker_first
            try:worker_first(address,requests,events,prepared_hand=owned_hand,source=source)
            finally:owned_route.close()
        else:
            worker(address,requests,events,factory,prepared=(owned_hand,owned_route))
    except SelectionRequired as error:
        events.put(dict(type='selection',devices=error.devices,message=str(error)))
    except Exception as error:
        events.put(dict(type='error',message=str(error)))
    finally:
        if hand is not None:hand.disconnect()
        if route is not None:route.close()
        if ownership is not None:ownership.close()

def worker_glove(requests,events):
    from device_profiles import controller_profile
    selected=controller_profile();source=GloveSource();glove=sub=session=None
    hand_thread=None;hand_requests=queue.Queue();hand_events=queue.Queue()
    hand_state={};hand_error='';manager=None;previous_user=None
    devices=[];hands=[];users=[];current=None;state='starting';message='正在加载官方SDK'
    glove_sn='';last_emit=0.;next_map=0.;last_source_seq=None
    from retarget_settings import OutputMapping, defaults
    mapping=OutputMapping(defaults())
    sdk_q=None;latest_kp=None;glove_ownership=None;profile_lease=None;solver_engine='sdk'
    try:
        import numpy as np
        import wuji_sdk as sdk
        manager=sdk.SdkManager.instance();previous_user=manager.current_user();current=previous_user
        users=manager.list_users()
        def new_solver(settings):
            if settings.get('engine','sdk')=='official_open':
                from solver_session import SolverSession
                return SolverSession(selected['id'],settings['values'])
            model=sdk.HandModel.WujiHand2 if selected['generation']=='hand2' else sdk.HandModel.WujiHand
            side=sdk.Handedness.Left if selected['side']=='left' else sdk.Handedness.Right
            return sdk.RetargetSession.for_hand(model,side=side)
        def scan():
            nonlocal hands
            from device_discovery import candidates
            all_devices=list(manager.scan());hands=candidates(all_devices)
            return [dict(serial=d.sn,address=str(d.address)) for d in all_devices
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
                        candidate=None;selection_lease=None;switched=False
                        try:
                            if sys.platform.startswith('linux'):
                                from sdk_session import ProfileLease
                                from device_ownership import DeviceOwnership
                                profile_lease=ProfileLease()
                                selection_lease=ProfileLease(selection=True)
                                glove_ownership=DeviceOwnership(c['serial'])
                            previous_user=manager.current_user()
                            manager.switch_user(user_id);switched=True;current=manager.current_user()
                            candidate=manager.connect(sn=c['serial'],device_name='studio_glove')
                            actual=str(candidate.hand_side().get()).lower()
                            if actual not in (selected['side'],'handedness.'+selected['side']):
                                raise ValueError('手套左右与工作台所选型号不符 / Glove side differs from profile')
                            session=new_solver(c.get('solver',{}));solver_engine=c.get('solver',{}).get('engine','sdk')
                            sub=candidate.hand_skeleton().subscribe()
                        except Exception:
                            if hasattr(session,'close'):session.close()
                            session=None
                            if glove_ownership:glove_ownership.close();glove_ownership=None
                            if candidate:candidate.disconnect()
                            if profile_lease:profile_lease.close();profile_lease=None
                            raise
                        finally:
                            try:
                                if switched and previous_user:manager.switch_user(previous_user['user_id'])
                            except Exception:
                                if sub:sub.close();sub=None
                                if hasattr(session,'close'):session.close()
                                session=None
                                if candidate:candidate.disconnect()
                                if glove_ownership:glove_ownership.close();glove_ownership=None
                                if profile_lease:profile_lease.close();profile_lease=None
                                raise
                            finally:
                                if selection_lease:selection_lease.close()
                        glove=candidate;glove_sn=c['serial'];source=GloveSource(c['timeout_ms'])
                        mapping=OutputMapping(c.get('retarget',defaults()));last_source_seq=None
                        state='receiving';message='已连接手套，机械手未启用 / Glove connected; hand not enabled'
                    elif name=='glove_solver':
                        if glove is None:raise ValueError('Connect glove preview first')
                        if hand_thread and hand_thread.is_alive():raise ValueError('Disconnect hand feedback before changing solver')
                        replacement=new_solver(c['solver'])
                        old=session;session=replacement;solver_engine=c['solver']['engine']
                        if hasattr(old,'close'):old.close()
                        mapping=OutputMapping(mapping.settings);sdk_q=None
                        source.invalidate('等待新求解器的下一帧 / Waiting for a new solver frame')
                        message='求解器已切换，等待新骨架 / Solver switched; waiting for fresh skeleton'
                    elif name=='glove_prepare':
                        if glove is None:raise ValueError('先连接手套 / Connect glove first')
                        source.target(now)
                        if hand_thread is not None and hand_thread.is_alive():raise ValueError('已有机械手会话；请先断开全部再重试')
                        from glove_motion import GloveMotion
                        def factory(*args):return GloveMotion(*args,source=source)
                        hand_state={};hand_error='';hand_requests=queue.Queue();hand_events=queue.Queue()
                        hand_thread=threading.Thread(target=paired_hand_worker,args=(c.get('address',''),c.get('serial',''),selected,hand_requests,hand_events,factory,source),daemon=True)
                        hand_thread.start();message='正在连接机械手，只读反馈 / Connecting hand feedback only'
                    elif name=='glove_retarget':
                        if glove is None:raise ValueError('Connect glove preview first')
                        if hand_thread and hand_thread.is_alive():raise ValueError('重新连接手套预览后应用映射 / Reconnect preview before applying mapping')
                        mapping=OutputMapping(c['retarget'])
                        source.invalidate('等待应用新映射后的骨架 / Waiting for a new mapped frame')
                        message='映射已应用，等待下一帧 / Mapping applied; awaiting new frame'
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
                        arrival=time.monotonic()
                        seq=frame.header.seq;stamp=frame.header.timestamp_us
                        # No repeated SDK frame may renew the controller's freshness.
                        if last_source_seq is None or seq>last_source_seq:
                            kp=np.asarray([j.pose.position for j in frame.joints],dtype=np.float32)
                            if kp.shape!=(21,3) or not np.isfinite(kp).all():raise ValueError('Expected 21 finite keypoints')
                            latest_kp=kp.tolist()
                            sdk_q=np.asarray(session.step(kp),dtype=float).reshape(-1).tolist()
                            q=mapping.apply(sdk_q,time.monotonic())
                            if source.update(q,seq,stamp,arrival):last_source_seq=seq
                except Exception as e:
                    source.invalidate(str(e));message=str(e)
            while True:
                try:event=hand_events.get_nowait()
                except queue.Empty:break
                if event['type']=='state':hand_state=event
                elif event['type']=='selection':
                    hands=event['devices'];message=event['message']
                elif event['type'] in ('notice','error'):
                    hand_error=event['message'];message=hand_error
                    if event['type']=='error':hand_state['connection']='error'
                elif event['type']=='closed':
                    hand_state['connection']='disconnected'
                    if 'hardware' in event:hand_state['hardware']=event['hardware']
            now=time.monotonic()
            if now-last_emit>=.05:
                snapshot=source.snapshot(now)
                snapshot['sdk_q']=sdk_q
                snapshot['keypoints']=latest_kp if snapshot['fresh'] else None
                snapshot['retarget']=mapping.settings
                snapshot['solver']=dict(getattr(session,'info',{}) or {},engine=solver_engine,applied=bool(session and snapshot['fresh']))
                hw=copy.deepcopy(hand_state.get('hardware',{}))
                feedback=copy.deepcopy({k:hand_state.get(k) for k in ('latest','device_id','joint_rates','metrics','connection')})
                if feedback.get('latest'):
                    feedback['metrics']['age_ms']=max(0,now-feedback['latest']['host_s'])*1000
                events.put(dict(type='glove_state',state=dict(version=1,connection=state,message=message,
                    devices=devices,hands=hands,users=users,user=current,profile=selected['id'],glove_serial=glove_sn,
                    stream=snapshot,hardware=hw,feedback=feedback,hand_error=hand_error,
                    parameters=__import__('official_policy').settings(),
                    source='official_open_live_retarget' if solver_engine=='official_open' else 'official_sdk_live_retarget',hardware_validated=False)))
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
        if session is not None and hasattr(session,'close'):session.close()
        if glove_ownership:glove_ownership.close()
        if profile_lease:profile_lease.close()
        final_hardware=copy.deepcopy(hand_state.get('hardware',{}))
        while True:
            try:e=hand_events.get_nowait()
            except queue.Empty:break
            if e['type']=='closed':final_hardware=e.get('hardware',{})
        if hand_thread and (hand_thread.is_alive() or not final_hardware):
            final_hardware=dict(active=None,stop_confirmed=False,reason='停用结果未确认 / Disable not confirmed')
        events.put(dict(type='glove_closed',hardware=final_hardware))
