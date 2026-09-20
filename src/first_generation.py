"""First-generation adapter based on official subscribe/publish/grasp examples.

The first generation has different messages and a LowPass controller. It must
never be passed to the Hand 2 diagnostic/MIT-parameter code. Only open/curl
motions are enabled here; other shapes remain preview-only candidates.
"""
import collections,math,queue,time
from device_profiles import controller_profile,require_identity
from motion_parameters import CURRENT_LIMIT_A

SOURCE='https://github.com/wuji-technology/wuji-sdk/blob/b0e48652dd94f4bc33df61cdc23a6d5dc598f93d/examples/python/wuji_hand/2.grasp_loop.py'
PUB_HZ=200;CUTOFF_HZ=5.

def serialize_first(frame,now,seq):
    values=list(frame.position)
    if len(values)!=20 or not all(math.isfinite(float(x)) for x in values):raise ValueError('Invalid first-generation positions')
    # Array channels are display indices, not invented hardware CAN node IDs.
    header=getattr(frame,'header',None);stamp=getattr(header,'timestamp_us',None)
    velocity=getattr(frame,'velocity',None)
    return dict(seq=seq,device_timestamp_us=stamp if type(stamp) is int else None,host_s=now,
        frame_id='hand1-finger-major',channel_id_source='array_index',effort_unit='unavailable',
        joints=[dict(nid=i,position_rad=float(v),velocity_rad_s=float(velocity[i]) if velocity is not None and len(velocity)==20 else None,effort_A=None) for i,v in enumerate(values)])

def target_at(start,action,amplitude,speed,cycles,elapsed):
    transition=1.5/speed;cycle_time=2./speed
    duration=transition*2+cycle_time*cycles
    def smooth(u):return u*u*u*(10+u*(-15+6*u))
    if elapsed<transition:
        blend=smooth(max(0,elapsed/transition));return [v*(1-blend) for v in start],duration
    if elapsed<transition+cycles*cycle_time:
        q=[0.]*20
        if action=='fist':
            curl=(1-math.cos(math.pi*(elapsed-transition)*speed))*.8*amplitude
            for f in (1,2,3,4):
                for j in (0,2,3):q[f*4+j]=curl
        return q,duration
    blend=smooth(min(1,(elapsed-transition-cycles*cycle_time)/transition))
    return [v*blend for v in start],duration

def worker_first(address,requests,events):
    from wuji_sdk import SdkManager,DeviceType,JointCommand,LowPass
    from console_agent import Recorder
    selected=controller_profile();manager=SdkManager.instance();hand=sub=publisher=lowpass=None
    latest=None;seq=0;recent=collections.deque(maxlen=500);recorder=Recorder();last=time.monotonic();last_emit=0.
    active=False;paused=False;owned=False;lease='';last_beat=0.;elapsed=0.;tick=0.;next_send=0.;sent=0
    action='';reason='一代手仅开放官方张开/握拳适配，其他动作可预览';plan=None;duration=0.
    def stop(message):
        nonlocal active,paused,owned,publisher,lowpass,reason
        active=False;paused=False;reason=message
        if publisher:
            try:publisher.close()
            except Exception as error:reason+='; publisher: '+str(error)
            publisher=None
        if lowpass:
            try:lowpass.__exit__(None,None,None)
            except Exception as error:reason+='; controller: '+str(error)
            lowpass=None
        if owned and hand:
            try:hand.disable();owned=False
            except Exception as error:reason+='; disable 未确认: '+str(error)
    def finish_record(message):
        report=recorder.finish(message,time.monotonic())
        if report:events.put(dict(type='report',report=report))
    try:
        devices=[d for d in manager.scan() if d.device_type==DeviceType.WujiHand and (not address or str(d.address)==address)]
        if not devices:raise RuntimeError('未发现一代手 / No first-generation hand found')
        # Handedness is checked before subscribing or enabling anything.
        if len(devices)>1 and not address:raise ValueError('检测到多个一代手，请填写设备地址')
        hand=manager.connect(sn=devices[0].sn,device_name='wuji_hand');require_identity(hand,selected)
        device_id=str(hand.serial_number);sub=hand.joint_states().subscribe()
        while True:
            now=time.monotonic()
            for _ in range(256):
                frame=sub.recv()
                if frame is None:break
                seq+=1;latest=serialize_first(frame,time.monotonic(),seq);last=latest['host_s'];recent.append(latest);recorder.add(latest)
            try:command=requests.get_nowait()
            except queue.Empty:command=None
            if command:
                name=command['name']
                if name=='disconnect':stop('已断开');break
                if name=='hardware_stop':stop('用户停止')
                elif name=='hardware_keepalive' and command.get('lease')==lease:last_beat=now
                elif name=='hardware_pause' and command.get('lease')==lease:paused=True
                elif name=='hardware_resume' and command.get('lease')==lease:paused=False;tick=now
                elif name in {'hardware_start','hardware_probe','hardware_trial'}:
                    try:
                        if name!='hardware_trial' or command.get('action') not in {'open','fist'}:raise ValueError('一代实机只支持官方张开/握拳适配；其余动作仅预览')
                        if active or owned or recorder.active or latest is None or now-last>.5:raise ValueError('请等待反馈并停止当前任务')
                        if command.get('workspace_clear') is not True or len(command.get('lease',''))!=32:raise ValueError('需要明确开始与有效控制会话')
                        if command.get('amplitude') not in (.25,.5,.75,1.) or command.get('speed') not in (.25,.5,1.) or command.get('cycles') not in (1,3):raise ValueError('Invalid motion selection')
                        plan=dict(start=[j['position_rad'] for j in latest['joints']],action=command['action'],amplitude=command['amplitude'],speed=command['speed'],cycles=command['cycles'])
                        hand.set_all_effort_limit(CURRENT_LIMIT_A)
                        # Record ownership before enable so a partial failure still disables.
                        owned=True;hand.enable();lowpass=hand.realtime_controller(LowPass(cutoff_hz=CUTOFF_HZ));lowpass.__enter__();publisher=hand.joint_command().publish()
                        lease=command['lease'];last_beat=now;tick=now;next_send=now;elapsed=0.;sent=0;active=True;paused=False;action=plan['action'];reason='一代官方LowPass示例适配'
                    except Exception as error:stop(str(error));events.put(dict(type='notice',message=reason))
                elif name=='record':
                    if active or owned:events.put(dict(type='notice',message='先停止动作再采集'))
                    else:recorder.start(command['label'],command['seconds'],now)
                elif name=='stop':finish_record('user_stop')
            if active:
                if now-last>.5 or now-last_beat>2.5:stop('反馈或控制会话已过期')
                elif now>=next_send:
                    if not paused:elapsed+=max(0,now-tick)
                    tick=now;q,duration=target_at(**plan,elapsed=elapsed)
                    publisher.send([JointCommand(v,0.,0.) for v in q]);sent+=1;next_send=now+1/PUB_HZ
                    if elapsed>=duration:stop('动作完成，已请求停用')
            if recorder.active and now-recorder.started>=recorder.seconds:finish_record('completed')
            if now-last>3:raise RuntimeError('连续3秒无反馈')
            if now-last_emit>=.05:
                span=recent[-1]['host_s']-recent[0]['host_s'] if len(recent)>1 else 0.
                hz=(len(recent)-1)/span if span>.2 else None
                rates=[dict(nid=i,host_hz=hz,device_hz=None,age_ms=(now-last)*1000,status='fresh' if now-last<=.1 else 'stale',missing_in_received_frames=0,missing_stream_slots=None,samples=len(recent)) for i in range(20)]
                hw=dict(active=None if owned and not active else active,ready=False,trial_ready=latest is not None and not owned,probe_ready=False,probe_reason=reason,reason=reason,actions=['open','fist'],trial_controls_version=3,gesture_library_version=1,paused=paused,elapsed_s=elapsed,action=action,trial_phase='LowPass · '+action,trial_amplitude=plan['amplitude'] if plan else None,planned_duration_s=duration,cycles=plan['cycles'] if plan else 1,cycle=1,stop_confirmed=not owned,warnings=[],command_timing=dict(requested_hz=PUB_HZ),source='official_hand1_lowpass_adaptation',action_source=SOURCE,diagnostics_available=False,effort_unit='unavailable',kp_kd_applicable=False)
                events.put(dict(type='state',connection='connected' if latest else 'connecting',message='一代手反馈 · 设备频率/电流未由此接口提供，显示—',latest=latest,device_id=device_id,joint_rates=rates,metrics=dict(host_hz=hz,device_hz=None,age_ms=(now-last)*1000),recording=recorder.state(),hardware=hw));last_emit=now
            time.sleep(.0005)
    except Exception as error:events.put(dict(type='error',message=str(error)))
    finally:
        stop('连接结束');finish_record('disconnected')
        if sub:sub.close()
        manager.disconnect_all();events.put(dict(type='closed'))
