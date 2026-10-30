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


def prepare_permission(hand_thread,hand_state):
    """Retry only after the previous exact feedback worker released ownership."""
    if hand_thread is not None and hand_thread.is_alive():
        return False,'灵巧手反馈会话仍在运行 / Hand feedback session is still running'
    hardware=hand_state.get('hardware') or {}
    if 'active' in hardware and hardware['active'] is not False:
        return False,'灵巧手停用结果未确认 / Hand disable is unconfirmed'
    if hand_thread is not None and hand_state.get('cleanup_confirmed') is not True:
        return False,'上次灵巧手连接退出未确认，请先结束此会话 / Previous hand cleanup is unconfirmed; end this session first'
    return True,''


def scan_preview_hands(manager,glove,connection,solver_engine,source,now,
                       hand_thread,hand_state,hand_unpairing):
    """Refresh hand advertisements without reopening the existing glove."""
    if glove is None or connection!='receiving' or solver_engine!='sdk':
        raise ValueError('请先打开官方 SDK 手套预览 / Open the official SDK glove preview first')
    allowed,reason=prepare_permission(hand_thread,hand_state)
    if not allowed:raise ValueError(reason)
    if hand_unpairing or hand_state.get('device_id'):
        raise ValueError('先解除灵巧手配对并确认退出 / Unpair the hand and confirm cleanup first')
    if (hand_state.get('hardware') or {}).get('stop_confirmed') is False:
        raise ValueError('灵巧手停用结果未确认 / Hand disable is unconfirmed')
    source.target(now)
    from device_discovery import candidates
    return candidates(list(manager.scan()))


def paired_hand_worker(address, serial, selected, requests, events, factory, source=None):
    """Choose one fresh serial and verify side/model before creating a driver."""
    from device_discovery import connect_discovered, SelectionRequired
    from console_agent import worker
    hand=route=ownership=None;delegated=False;closed=None;cleanup=True
    class Forward:
        def put(self,event):
            nonlocal closed
            if event.get('type')=='closed':closed=copy.deepcopy(event)
            events.put(event)
    forward=Forward()
    try:
        from wuji_sdk import SdkManager
        hand,route,verified=connect_discovered(SdkManager.instance(),address,serial)
        if verified['id']!=selected['id']:
            raise ValueError('手套映射与灵巧手的代际或左右手不符 / Hand model/side differs from glove mapping')
        if sys.platform.startswith('linux'):
            from device_ownership import DeviceOwnership
            ownership=DeviceOwnership(hand.serial_number)
        # Publish the identity already verified by connect_discovered only
        # after acquiring ownership, before either prepared driver can start.
        events.put(dict(type='identity',profile=verified['id'],device_id=str(hand.serial_number)))
        owned_hand,owned_route=hand,route;hand=route=None
        delegated=True
        if selected['generation']=='hand1':
            from first_generation import worker_first
            try:worker_first(address,requests,forward,prepared_hand=owned_hand,source=source)
            finally:owned_route.close()
        else:
            worker(address,requests,forward,factory,prepared=(owned_hand,owned_route))
    except SelectionRequired as error:
        events.put(dict(type='selection',devices=error.devices,message=str(error)))
    except Exception as error:
        if delegated:cleanup=False
        events.put(dict(type='error',message=str(error)))
    finally:
        for resource in (hand,route,ownership):
            if resource is not None:
                try:resource.disconnect() if resource is hand else resource.close()
                except Exception:cleanup=False
        if delegated:
            hardware=(closed or {}).get('hardware') or {}
            cleanup=cleanup and closed is not None and hardware.get('active') is False
        events.put(dict(type='hand_session_closed',cleanup_confirmed=cleanup))

def worker_glove(requests,events,continuation=None,sdk_requirement=None):
    import collections
    from device_profiles import controller_profile
    selected=controller_profile();source=GloveSource();glove=sub=session=None
    hand_thread=None;hand_requests=queue.Queue();hand_events=queue.Queue()
    hand_state={};hand_identity={};hand_error='';manager=None;previous_user=None
    devices=[];hands=[];users=[];current=None;state='starting';message='正在加载官方SDK'
    glove_sn='';device_version=None;last_emit=0.;next_map=0.;last_source_seq=None
    from retarget_settings import OutputMapping, defaults
    mapping=OutputMapping(defaults())
    sdk_q=None;latest_kp=None;glove_ownership=None;profile_lease=None;solver_engine='sdk';selected_user_active=False
    model_receipt=None;model_provenance=None;opened_user_id='';selection_lease_released=True;selection_lease=None
    glove_disconnect_needs_retry=False
    hand_unpairing=False;hand_unpaired=False;hand_scan_error=''
    bootstrap_commands=collections.deque()
    class BootstrapCancelled(Exception):pass
    def bootstrap_not_cancelled():
        for _ in range(64):
            try:command=requests.get_nowait()
            except queue.Empty:return
            if command.get('name') in ('disconnect','glove_disconnect'):raise BootstrapCancelled()
            bootstrap_commands.append(command)
        raise ValueError('Continuation startup command queue exceeds its limit')
    try:
        # console_agent always supplies a verified requirement. Repeat in this
        # spawned interpreter before scanning, changing user, or opening devices.
        if sdk_requirement is not None:
            from sdk_runtime import probe_runtime,verify_proof
            proof=verify_proof(probe_runtime(),sdk_requirement)
            events.put(dict(type='sdk_ready',sdk_proof=proof))
        import numpy as np
        import wuji_sdk as sdk
        manager=sdk.SdkManager.instance();previous_user=manager.current_user();current=previous_user
        users=manager.list_users()
        if continuation is not None:
            from calibration_portability import validate_continuation,prepare_controller_model
            continuation=validate_continuation(continuation,path=True)
            if continuation['side']!=selected['side']:
                raise ValueError('本次标定侧别与遥操作目标不符 / Calibration side differs from the teleoperation target')
            bootstrap_not_cancelled()
            if sys.platform.startswith('linux'):
                from sdk_session import ProfileLease
                with ProfileLease(exclusive=True):model_receipt=prepare_controller_model(manager,continuation)
            else:model_receipt=prepare_controller_model(manager,continuation)
            if manager.current_user().get('user_id')!=previous_user.get('user_id'):
                raise ValueError('SDK user changed during calibration import; glove was not opened')
            bootstrap_not_cancelled()
            users=manager.list_users()
            from calibration_portability import model_provenance as provenance
            model_provenance=provenance(model_receipt,model_receipt['owner_user_id'],model_receipt['imported'])
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
            from glove_discovery import scan_gloves
            from zenoh_route import managed_wsl
            # Discovery must never open even a temporary device handle. A
            # direct-address connection belongs only to an explicit connect.
            return scan_gloves(manager,sdk,all_devices)
        # A verified continuation already carries the user's exact glove and
        # model choice. Let SDK connect(sn=...) perform its own resolution;
        # one preliminary broadcast snapshot cannot disprove that target.
        # Ordinary discovery/selection still never opens a device handle.
        devices=scan() if continuation is None else []
        devices_at=time.time();state='ready';message='选择手套和标定用户 / Select glove and SDK user'
        if continuation is not None:
            bootstrap_not_cancelled()
            events.put(dict(type='calibrated_ready',receipt=copy.deepcopy(model_provenance)))
        while True:
            now=time.monotonic()
            try:c=bootstrap_commands.popleft() if bootstrap_commands else requests.get_nowait()
            except queue.Empty:c=None
            if c:
                follow_dispatch_attempted=False
                open_acquire_started=False
                try:
                    name=c['name']
                    if name in ('disconnect','glove_disconnect'):break
                    if name=='glove_scan':
                        if glove is not None:raise ValueError('先断开手套再扫描 / Disconnect glove before scanning')
                        devices=scan();devices_at=time.time();message='扫描完成 / Scan completed'
                    elif name=='glove_hand_scan':
                        hands=scan_preview_hands(manager,glove,state,solver_engine,source,now,
                                                 hand_thread,hand_state,hand_unpairing)
                        hand_scan_error=''
                        message='灵巧手查找完成，请选择后连接 / Hand search completed; choose a hand to connect'
                    elif name=='glove_open':
                        if glove is not None:raise ValueError('手套已连接，修改配置请先断开')
                        if continuation is None and c['serial'] not in {d['serial'] for d in devices}:
                            raise ValueError('请先扫描并选择手套')
                        user_id=c.get('user_id',current['user_id'])
                        if user_id not in {u['user_id'] for u in users}:raise ValueError('Unknown SDK user')
                        if continuation is not None and (c['serial']!=continuation['serial'] or user_id!=model_receipt['owner_user_id']):
                            raise ValueError('Continuation device or SDK user differs from the saved calibration')
                        candidate=None;selection_lease=None;switched=False
                        open_acquire_started=True
                        try:
                            if sys.platform.startswith('linux'):
                                from sdk_session import ProfileLease
                                from device_ownership import DeviceOwnership
                                profile_lease=ProfileLease()
                                selection_lease_released=False
                                selection_lease=ProfileLease(selection=True)
                                glove_ownership=DeviceOwnership(c['serial'])
                            previous_user=manager.current_user()
                            manager.switch_user(user_id);switched=True;selected_user_active=True;current=manager.current_user()
                            if not isinstance(current,dict) or current.get('user_id')!=user_id:
                                raise ValueError('所选用户尚未确认，未打开手套 / Selected SDK user was not confirmed; glove was not opened')
                            if continuation is not None and (current.get('user_id')!=user_id or current.get('is_default') is not False
                                    or current.get('display_name')!=continuation['source_profile']):
                                raise ValueError('Controller did not confirm the calibrated SDK user')
                            from glove_discovery import connect_glove,GloveCleanupUnconfirmed
                            target=(dict(serial=continuation['serial'],source='calibration_continuation')
                                    if continuation is not None else next(d for d in devices if d['serial']==c['serial']))
                            if continuation is not None and continuation.get('glove_route') is not None:
                                from calibration_transport import validate_glove_route
                                route=validate_glove_route(continuation['glove_route'],continuation['serial'])
                                target=dict(serial=route['serial'],address=route['address'],source='official_address')
                            try:candidate=connect_glove(manager,target)
                            except Exception as error:
                                if isinstance(error,GloveCleanupUnconfirmed):
                                    candidate=error.handle;glove_disconnect_needs_retry=True
                                if continuation is None:raise
                                raise ValueError('未能接入这只手套，请确认电源和控制端网络后点击继续；标定资料已保留 / '
                                    'Check glove power and controller network, then retry; saved calibration is retained. SDK: '
                                    +str(error)[:300]) from error
                            if continuation is not None and not isinstance(candidate,sdk.WujiGlove):
                                raise ValueError('Selected calibration continuation is not a typed Wuji Glove')
                            actual=str(candidate.hand_side().get()).lower()
                            if actual not in (selected['side'],'handedness.'+selected['side']):
                                raise ValueError('手套左右与工作台所选型号不符 / Glove side differs from profile')
                            if model_receipt is not None:
                                from calibration_portability import verify_loaded_model
                                verify_loaded_model(candidate,model_receipt,apply_missing=True)
                            session=new_solver(c.get('solver',{}));solver_engine=c.get('solver',{}).get('engine','sdk')
                            sub=candidate.hand_skeleton().subscribe()
                        except Exception:
                            # Every acquired resource reaches the same confirmed
                            # cleanup loop. An early open failure must not drop
                            # a failed subscription/solver close or its candidate.
                            if candidate is not None:
                                glove=candidate;glove_sn=c['serial'];opened_user_id=user_id
                            raise
                        finally:
                            if selection_lease:
                                try:
                                    if selection_lease.close() is False:raise ValueError('SDK user selection release was not acknowledged')
                                    selection_lease_released=True;selection_lease=None
                                except Exception:
                                    # The open succeeded before the lease's
                                    # finalizer failed. Retain its actual handle
                                    # for outer cleanup rather than losing it.
                                    if candidate is not None:
                                        glove=candidate;glove_sn=c['serial'];opened_user_id=user_id
                                    raise
                        glove=candidate;glove_sn=c['serial'];opened_user_id=user_id;source=GloveSource(c['timeout_ms'])
                        from device_versions import read_handle_version
                        device_version=read_handle_version(glove,glove_sn)
                        mapping=OutputMapping(c.get('retarget',defaults()));last_source_seq=None
                        state='receiving';message='已连接手套，灵巧手未启用 / Glove connected; hand not enabled'
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
                        if solver_engine!='sdk':
                            raise ValueError('开源映射仅用于调参预览，请切回官方 SDK 后再接入灵巧手 / Open mapping is preview only; switch to the official SDK before preparing a hand')
                        source.target(now)
                        allowed,reason=prepare_permission(hand_thread,hand_state)
                        if not allowed:raise ValueError(reason)
                        if hand_unpairing:raise ValueError('Wait for hand unpair cleanup')
                        from glove_motion import GloveMotion
                        def factory(*args):return GloveMotion(*args,source=source)
                        hand_state={};hand_identity={};hand_error='';hand_unpaired=False;hand_requests=queue.Queue();hand_events=queue.Queue()
                        hand_scan_error=''
                        hand_thread=threading.Thread(target=paired_hand_worker,args=(c.get('address',''),c.get('serial',''),selected,hand_requests,hand_events,factory,source),daemon=True)
                        hand_thread.start();message='正在连接灵巧手，只读反馈 / Connecting hand feedback only'
                    elif name=='glove_retarget':
                        if glove is None:raise ValueError('Connect glove preview first')
                        if hand_thread and hand_thread.is_alive():raise ValueError('重新连接手套预览后应用映射 / Reconnect preview before applying mapping')
                        mapping=OutputMapping(c['retarget'])
                        source.invalidate('等待应用新映射后的骨架 / Waiting for a new mapped frame')
                        message='映射已应用，等待下一帧 / Mapping applied; awaiting new frame'
                    elif name=='glove_follow':
                        if hand_unpairing:raise ValueError('Hand cleanup is still in progress')
                        if solver_engine!='sdk':
                            raise ValueError('开源映射仅用于调参预览，请切回官方 SDK 后再启动灵巧手 / Open mapping is preview only; switch to the official SDK before following')
                        if not hand_thread or not hand_thread.is_alive():raise ValueError('先连接灵巧手反馈 / Prepare hand feedback first')
                        source.target(now)
                        follow_dispatch_attempted=True
                        hand_requests.put(dict(name='hardware_start',lease=c['lease'],workspace_clear=c['workspace_clear']))
                    elif name=='glove_keepalive':hand_requests.put(dict(name='hardware_keepalive',lease=c.get('lease')))
                    elif name=='glove_stop':hand_requests.put(dict(name='hardware_stop'))
                    elif name=='glove_unpair':
                        if (glove is None or hand_thread is None or not hand_thread.is_alive()
                                or hand_state.get('hardware',{}).get('active') is not False or hand_unpairing):
                            raise ValueError('Pause and confirm hand disable before unpairing')
                        hand_requests.put(dict(name='disconnect'));hand_unpairing=True
                        message='正在释放灵巧手，手套预览保持打开 / Releasing hand; glove preview remains open'
                except Exception as e:
                    if c.get('name')=='glove_hand_scan':hand_scan_error=str(e)[:400]
                    if c.get('name')=='glove_follow' and not follow_dispatch_attempted:
                        serial=hand_identity.get('serial');nonce=c.get('lease')
                        if (serial and hand_identity.get('source')=='sdk_identity'
                                and hand_state.get('device_id')==serial and isinstance(nonce,str)
                                and len(nonce)==32 and all(ch in '0123456789abcdef' for ch in nonce)):
                            # No hand-queue publication occurred. The bridge
                            # additionally binds this event to its reader epoch.
                            events.put(dict(type='glove_follow_rejected',device_id=serial,
                                lease=nonce,message=str(e)[:400]))
                    message=str(e);events.put(dict(type='glove_notice',message=message))
                    if c.get('name')=='glove_open' and open_acquire_started and any((glove is not None,sub is not None,session is not None,
                            selected_user_active,glove_ownership is not None,profile_lease is not None,selection_lease is not None)):
                        raise
                    if continuation is not None and c.get('name')=='glove_open' and glove is None:raise
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
                elif event['type']=='identity':
                    hand_identity=dict(serial=event.get('device_id'),profile=event.get('profile'),source='sdk_identity')
                elif event['type']=='selection':
                    hands=event['devices'];message=event['message']
                elif event['type'] in ('notice','error'):
                    hand_error=event['message'];message=hand_error
                    if event['type']=='error':hand_state['connection']='error'
                elif event['type']=='closed':
                    hand_state['connection']='disconnected'
                    if 'hardware' in event:hand_state['hardware']=event['hardware']
                elif event['type']=='hand_session_closed':
                    hand_state['cleanup_confirmed']=event.get('cleanup_confirmed') is True
            if hand_unpairing and hand_thread is not None and not hand_thread.is_alive():
                hand_unpairing=False
                if hand_state.get('cleanup_confirmed') is True and hand_state.get('hardware',{}).get('active') is False:
                    hand_thread=None;hand_state={};hand_identity={};hand_error='';hand_unpaired=True
                    message='已解除目标配对，手套预览保持打开 / Target unpaired; glove preview remains open'
                else:
                    hand_error='灵巧手释放尚未确认，请结束此会话并核对 / Hand cleanup unconfirmed; end and review this session'
                    message=hand_error
            now=time.monotonic()
            if now-last_emit>=.05:
                snapshot=source.snapshot(now)
                snapshot['sdk_q']=sdk_q
                snapshot['keypoints']=latest_kp if snapshot['fresh'] else None
                snapshot['retarget']=mapping.settings
                snapshot['solver']=dict(getattr(session,'info',{}) or {},engine=solver_engine,applied=bool(session and snapshot['fresh']))
                hw=copy.deepcopy(hand_state.get('hardware',{}))
                can_prepare,prepare_reason=prepare_permission(hand_thread,hand_state)
                if glove is None:can_prepare=False;prepare_reason='请先打开手套预览 / Open glove preview first'
                elif solver_engine!='sdk':can_prepare=False;prepare_reason='请切回官方 SDK 映射后再接入灵巧手 / Switch to official SDK mapping before preparing a hand'
                elif not snapshot['fresh']:can_prepare=False;prepare_reason='等待新鲜手套数据 / Wait for fresh glove data'
                feedback=copy.deepcopy({k:hand_state.get(k) for k in ('latest','device_id','device_version','joint_rates','metrics','connection')})
                feedback['identified_device']=copy.deepcopy(hand_identity)
                if feedback.get('latest'):
                    feedback['metrics']['age_ms']=max(0,now-feedback['latest']['host_s'])*1000
                events.put(dict(type='glove_state',state=dict(version=1,connection=state,message=message,
                    devices=devices,devices_at=devices_at,hands=hands,users=users,user=current,profile=selected['id'],glove_serial=glove_sn,device_version=device_version,
                    stream=snapshot,hardware=hw,feedback=feedback,hand_error=hand_error,hand_scan_error=hand_scan_error,
                    can_prepare=can_prepare,prepare_blocked_reason=prepare_reason,
                    hand_cleanup_confirmed=hand_state.get('cleanup_confirmed'),
                    hand_unpairing=hand_unpairing,hand_unpaired=hand_unpaired,
                    parameters=__import__('official_policy').settings(),
                    model_provenance=copy.deepcopy(model_provenance),
                    source='official_open_live_retarget' if solver_engine=='official_open' else 'official_sdk_live_retarget',hardware_validated=False)))
                last_emit=now
            time.sleep(.001)
    except BootstrapCancelled:events.put(dict(type='glove_notice',message='接续已取消，已保存的标定资料保留 / Continuation cancelled; saved calibration data is retained'))
    except Exception as e:events.put(dict(type='glove_error',message=str(e)))
    finally:
        source.invalidate('连接已结束 / Session closed')
        subscription_closed=sub is None
        solver_cleanup='not_open'
        ownership_released=glove_ownership is None
        profile_lease_released=profile_lease is None
        user_restored=not selected_user_active
        glove_disconnected=glove is None
        restore_selection_lease=None
        final_hardware=copy.deepcopy(hand_state.get('hardware',{}))
        final_device=hand_state.get('device_id') or hand_identity.get('serial')
        final_cleanup=hand_state.get('cleanup_confirmed') is True
        # An unsuccessful SDK close retains its original handle and leases.
        # No timed/background retry: another explicit disconnect is required.
        while True:
            if hand_thread and hand_thread.is_alive():
                hand_requests.put(dict(name='disconnect'));hand_thread.join(timeout=4)
            if not subscription_closed:
                try:
                    if sub.close() is False:raise ValueError('Subscription close was not acknowledged')
                    subscription_closed=True
                except Exception:pass
            if not glove_disconnected and not glove_disconnect_needs_retry:
                try:
                    if glove.disconnect() is False:raise ValueError('Glove disconnect was not acknowledged')
                    glove_disconnected=True
                except Exception:pass
            if session is not None:
                if callable(getattr(session,'close',None)):
                    try:
                        if session.close() is False:raise ValueError('Solver close was not acknowledged')
                        solver_cleanup='closed';session=None
                    except Exception:solver_cleanup='unconfirmed'
                else:
                    # Pinned SDK RetargetSession has step/reset but no close.
                    solver_cleanup='sdk_no_close_api' if solver_engine=='sdk' else 'unconfirmed'
                    if solver_cleanup!='unconfirmed':session=None
            if selection_lease is not None and not selection_lease_released:
                try:
                    if selection_lease.close() is False:raise ValueError('Selection lease release was not acknowledged')
                    selection_lease_released=True;selection_lease=None
                except Exception:pass
            if restore_selection_lease is not None and not selection_lease_released:
                try:
                    if restore_selection_lease.close() is False:raise ValueError('Selection lease release was not acknowledged')
                    selection_lease_released=True;restore_selection_lease=None
                except Exception:pass
            handles_closed=subscription_closed and glove_disconnected and solver_cleanup!='unconfirmed'
            hand_finished=hand_thread is None or not hand_thread.is_alive()
            if handles_closed and hand_finished and selection_lease_released:
                if not user_restored and manager is not None and previous_user is not None:
                    try:
                        if sys.platform.startswith('linux'):
                            from sdk_session import ProfileLease
                            restore_selection_lease=ProfileLease(selection=True);selection_lease_released=False
                        manager.switch_user(previous_user['user_id'])
                        user_restored=manager.current_user().get('user_id')==previous_user['user_id']
                    except Exception:pass
                    finally:
                        if restore_selection_lease is not None and not selection_lease_released:
                            try:
                                if restore_selection_lease.close() is False:raise ValueError('Selection lease release was not acknowledged')
                                selection_lease_released=True;restore_selection_lease=None
                            except Exception:pass
                if user_restored and selection_lease_released:
                    if not ownership_released:
                        try:
                            if glove_ownership.close() is False:raise ValueError('Ownership release was not acknowledged')
                            ownership_released=True
                        except Exception:pass
                    if ownership_released and not profile_lease_released:
                        try:
                            if profile_lease.close() is False:raise ValueError('Profile lease release was not acknowledged')
                            profile_lease_released=True
                        except Exception:pass
            while True:
                try:e=hand_events.get_nowait()
                except queue.Empty:break
                if e['type']=='closed':final_hardware=e.get('hardware',{})
                elif e['type']=='identity':final_device=e.get('device_id')
                elif e['type']=='state':final_device=e.get('device_id') or final_device
                elif e['type']=='hand_session_closed':final_cleanup=e.get('cleanup_confirmed') is True
            if hand_thread and (hand_thread.is_alive() or not final_hardware):
                final_hardware=dict(active=None,stop_confirmed=False,reason='停用结果未确认 / Disable not confirmed')
                final_cleanup=False
            retryable=not hand_finished or not all((handles_closed,user_restored,ownership_released,profile_lease_released,selection_lease_released))
            cleanup=dict(version=1,glove_serial=glove_sn,sdk_user_id=opened_user_id,profile=selected['id'],
                glove_was_open=glove is not None,subscription_closed=subscription_closed,
                glove_disconnected=glove_disconnected,solver_cleanup=solver_cleanup,
                user_restored=user_restored,ownership_released=ownership_released,
                profile_lease_released=profile_lease_released,selection_lease_released=selection_lease_released,
                hand_released=hand_finished and (hand_thread is None or final_cleanup
                    and final_hardware.get('active') is False and final_hardware.get('stop_confirmed') is True),
                retryable=retryable)
            events.put(dict(type='glove_closed',hardware=final_hardware,device_id=final_device,
                hand_cleanup_confirmed=final_cleanup,session_cleanup=cleanup))
            if not retryable:break
            # Retain the failed objects. Other queued commands cannot reopen a
            # glove, change users, map, or enable a hand in this cleanup phase.
            while True:
                command=requests.get()
                if command.get('name') in ('disconnect','glove_disconnect'):
                    glove_disconnect_needs_retry=False
                    break
                if command.get('name')=='glove_stop' and hand_thread is not None and hand_thread.is_alive():
                    hand_requests.put(command);continue
                events.put(dict(type='glove_notice',message='原手套资源仍在退出，请重试结束原会话 / Original glove cleanup is pending; retry ending its owning session'))
