"""VM SDK process, read-only until explicit commissioning or playback.

All motion requires a live browser lease. Commissioning never implies a
reviewed full-action profile. stdin EOF ends the session; no auto reconnect.
"""
import collections
import ipaddress
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
import queue
import select
import sys
import time
import uuid

if os.environ.get('WUJI_PARAMETERS_JSON'):
    from agent_bootstrap import install_parameters
    install_parameters(os.environ['WUJI_PARAMETERS_JSON'])

from capture import require_left, serialize_frame
from feedback_stream import drain_available
from timing_stats import summarize_timing
from joint_stats import JointRates
from hardware_showcase import HardwareShowcase

LABELS = {'baseline', 'thumb', 'index', 'middle', 'ring', 'little', 'withdrawal'}
HARDWARE_COMMANDS = {'hardware_start','hardware_probe','hardware_trial','hardware_stop','hardware_pause','hardware_resume','hardware_keepalive'}
_session=os.environ.get('WUJI_SESSION_ID','')
if _session and (len(_session)!=16 or any(c not in '0123456789abcdef' for c in _session)):
    raise ValueError('Invalid controller session ID')
ROOT = Path(__file__).resolve().parent / 'sessions'
if _session:ROOT=ROOT/_session


def validate_command(command):
    if not isinstance(command, dict):
        raise ValueError('Invalid request')
    name = command.get('name')
    if isinstance(name,str) and name.startswith('glove_'):
        from glove_protocol import validate
        return validate(command)
    if name not in {'connect', 'disconnect', 'record', 'stop'} | HARDWARE_COMMANDS:
        raise ValueError('Unsupported console operation')
    if name == 'connect':
        if type(command.get('auto_detect', False)) is not bool:
            raise ValueError('Invalid discovery mode')
        serial = command.get('serial', '')
        if not isinstance(serial, str) or len(serial)>80 or (serial and not all(c.isascii() and (c.isalnum() or c in '-_') for c in serial)):
            raise ValueError('Invalid device serial')
        address = command.get('address', '')
        if not isinstance(address, str):
            raise ValueError('Address must be IPv4 with an optional port')
        if address:
            host,separator,port=address.partition(':')
            ipaddress.IPv4Address(host)
            if separator and (not port.isascii() or not port.isdecimal() or not 1<=int(port)<=65535):
                raise ValueError('Invalid device port')
    if name == 'record':
        seconds = command.get('seconds')
        if type(seconds) is not int or seconds not in {10, 30, 60}:
            raise ValueError('Choose 10, 30 or 60 seconds')
        if command.get('label') not in LABELS:
            raise ValueError('Unknown human annotation')
    return command


def rate(rows, field):
    if len(rows) < 2:
        return None
    duration = rows[-1][field] - rows[0][field]
    if field == 'device_timestamp_us':
        duration /= 1e6
    return (len(rows)-1)/duration if duration > 0 else None


class Recorder:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.active = False
        self.frames = 0
        self.seconds = 0
        self.label = 'baseline'
        self.elapsed_s = 0.

    def start(self, label, seconds, now):
        if self.active:
            raise ValueError('Recording already active')
        validate_command(dict(name='record', label=label, seconds=seconds))
        self.id = uuid.uuid4().hex
        self.path = self.root/self.id
        self.path.mkdir(parents=True, exist_ok=False)
        self.label, self.seconds, self.started = label, seconds, now
        self.frames = 0
        self.elapsed_s = 0.
        self.host, self.device = [], []
        self.file = (self.path/'feedback.jsonl').open('x', encoding='utf-8', buffering=1024*1024)
        self.active = True

    def add(self, row):
        if not self.active:
            return
        self.file.write(json.dumps(row, separators=(',', ':'), allow_nan=False)+'\n')
        self.host.append(dict(seq=row['seq'], host_s=row['host_s']))
        stamp = row.get('device_timestamp_us')
        if isinstance(stamp, (int, float)) and math.isfinite(stamp):
            self.device.append(dict(seq=row['seq'], host_s=stamp/1e6))
        self.frames += 1
        self.elapsed_s = row['host_s']-self.started

    def finish(self, reason, now):
        if not self.active:
            return None
        self.file.close()
        self.active = False
        self.elapsed_s = now-self.started
        from device_profiles import controller_profile
        selected = controller_profile()
        result = dict(id=self.id, label=self.label, seconds=self.elapsed_s,
            requested_seconds=self.seconds, frames=self.frames, reason=reason,
            side=selected['side'], generation=selected['generation'], read_only=True, recognition_enabled=False, motion_commands_sent=False,
            annotation_source='human_selection_not_model_prediction',
            units=dict(position='rad', velocity='rad/s', effort='A' if selected['generation']=='hand2' else 'unavailable'),
            host_arrival=summarize_timing(self.host),
            device_timestamp_intervals=summarize_timing(self.device),
            raw_feedback_location=str(self.path/'feedback.jsonl'))
        (self.path/'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        self.host, self.device = [], []
        return result

    def state(self):
        return dict(active=self.active, frames=self.frames, elapsed_s=self.elapsed_s,
                    label=self.label, seconds=self.seconds)


def worker_auto(address, serial, requests, events):
    from device_discovery import connect_discovered, SelectionRequired
    hand = route = ownership = None
    try:
        from wuji_sdk import SdkManager
        hand, route, selected = connect_discovered(SdkManager.instance(), address, serial)
        if sys.platform.startswith('linux'):
            from device_ownership import DeviceOwnership
            ownership = DeviceOwnership(hand.serial_number)
        os.environ['WUJI_HAND_PROFILE'] = selected['id']
        events.put(dict(type='identity', profile=selected['id'], device_id=str(hand.serial_number)))
        if selected['generation'] == 'hand1':
            from first_generation import worker_first
            owned_hand = hand; hand = None
            worker_first(address, requests, events, prepared_hand=owned_hand)
        else:
            owned_hand, owned_route = hand, route;hand = route = ownership = None
            worker(address, requests, events, prepared=(owned_hand, owned_route))
    except SelectionRequired as error:
        events.put(dict(type='selection', devices=error.devices, message=str(error)))
    except Exception as error:
        events.put(dict(type='error', message=str(error)))
    finally:
        if hand is not None:hand.disconnect()
        if route is not None:route.close()
        if ownership is not None:ownership.close()


def worker(address, requests, events, motion_factory=HardwareShowcase, prepared=None):
    from device_profiles import controller_profile
    if controller_profile()["generation"]=="hand1":
        from first_generation import worker_first
        return worker_first(address,requests,events)
    hand = sub = diagnostics = motion = protocol_route = ownership = None
    recorder = Recorder()
    def finish(reason):
        report = recorder.finish(reason, time.monotonic())
        if report:
            events.put(dict(type='report', report=report))
    try:
        from wuji_sdk import SdkManager, Handedness, WujiHand2
        from device_profiles import controller_profile
        selected=controller_profile()
        kwargs = dict(device_name='wuji_hand_2')
        if address:
            kwargs['address'] = address
        else:
            kwargs['handedness'] = Handedness.Left if selected['side']=='left' else Handedness.Right
        from zenoh_route import managed_wsl, connect_managed_hand
        if prepared is not None:
            hand, protocol_route = prepared
        elif managed_wsl():
            hand, protocol_route = connect_managed_hand(SdkManager.instance(), address, selected['side'])
        else:
            hand = SdkManager.instance().connect(**kwargs)
        require_left(hand)
        device_id = str(hand.serial_number)
        if prepared is None and sys.platform.startswith('linux'):
            from device_ownership import DeviceOwnership
            ownership=DeviceOwnership(device_id)
        command_type=None
        def make_command(**kwargs):
            nonlocal command_type
            if command_type is None:
                from wuji_sdk import JointCommand
                command_type=JointCommand
            return command_type(**kwargs)
        motion = motion_factory(hand,device_id,make_command)
        # Parse/validate public data before subscribing, not inside an active
        # 1000 Hz feedback loop where disk work could make diagnostics stale.
        try:
            from hardware_trial import make_trial
            make_trial([0.]*20,'official_opposition',.25,1)
        except (ValueError,OSError) as error:
            events.put(dict(type='notice',message='官方动作文件未就绪：'+str(error)))
        diagnostics = hand.joint_diagnostics().subscribe()
        diagnostic = None
        error_info={0:{}}
        sub = hand.joint_states().subscribe()
        recent = collections.deque(maxlen=2000)
        joint_rates = JointRates()
        last = time.monotonic()
        last_emit = 0.
        def receive():
            nonlocal last,diagnostic
            frames,state_empty=drain_available(sub)
            for frame,received in frames:
                row=serialize_frame(frame,received)
                row['received_host_s']=received
                row['queue_batch_frames']=len(frames)
                recent.append(row);joint_rates.add(row);recorder.add(row);last=received
            diag_frames,diag_empty=drain_available(diagnostics)
            for diag,received in diag_frames:
                for j in diag.joints:
                    if j.error_code_current not in error_info:
                        error_info[j.error_code_current]=WujiHand2.describe_error(j.error_code_current) or {}
                diagnostic=dict(host_s=received,seq=diag.header.seq,device_timestamp_us=diag.header.timestamp_us,
                    queue_batch_frames=len(diag_frames),joints=[dict(nid=j.nid,error=j.error_code_current,
                    state=j.status_word.ext_state,state_name=j.status_word.ext_state_name,
                    error_name=error_info[j.error_code_current].get('name'),
                    severity=error_info[j.error_code_current].get('severity'),
                    error_description=error_info[j.error_code_current].get('desc'),
                    current_A=j.current,vbus_V=j.vbus_v_fb,temperature_C=j.mcu_temp_c_fb,
                    response_pct=j.comm_response_rate_pct,bus_timeouts=j.comm_timeout_total,
                    position_limit_active=j.status_word.position_limit_active) for j in diag.joints],
                    comm={key:getattr(diag.comm,key,None) for key in ('age_ms','e2e_received','e2e_lost',
                        'e2e_reordered','e2e_duplicates','e2e_window_loss_x100','rpc_timeouts','comm_get_failures','sdk_dropped')})
                motion.observe_diagnostics(diagnostic,received)
                # Do not silently drop a fault that appears in an intermediate
                # diagnostic frame while selecting the newest control feedback.
                if motion.active:
                    try:motion.diagnostic(diagnostic,received)
                    except ValueError as error:
                        motion.capture_observation(recent[-1] if recent else None,diagnostic,received)
                        if motion.trial:motion.trial['fault']=motion.latest_observation
                        motion.stop(str(error))
            return state_empty and diag_empty
        def refresh_before_enable():
            if not receive():raise ValueError('启用前反馈积压，未启用电机')
            return recent[-1] if recent else None,diagnostic,time.monotonic()
        motion.refresh_before_enable=refresh_before_enable
        while True:
            now = time.monotonic()
            drained=receive()
            now=time.monotonic()
            if not drained and motion.active:
                motion.capture_observation(recent[-1] if recent else None,diagnostic,now)
                if motion.trial:motion.trial['fault']=motion.latest_observation
                motion.stop('反馈队列未及时排空，已停止，不能用积压旧帧继续动作')
            try:
                command = requests.get_nowait()
            except queue.Empty:
                command = None
            if command:
                name = command['name']
                if name == 'disconnect':
                    motion.stop('反馈连接断开')
                    finish('disconnected')
                    break
                if name in HARDWARE_COMMANDS:
                    try:
                        if name in {'hardware_start','hardware_probe','hardware_trial'} and recorder.active:
                            raise ValueError('先结束只读采集，再启动实机展示')
                        if name in {'hardware_start','hardware_probe','hardware_trial'} and not drained:
                            raise ValueError('反馈队列尚未排空，等待实时反馈后再启动')
                        motion.command(command,recent[-1] if recent else None,diagnostic,now)
                    except (ValueError,RuntimeError) as error:
                        events.put(dict(type='notice',message=str(error)))
                if name == 'record' and motion.active:
                    events.put(dict(type='notice',message='请先停止实机展示，再进行只读采集'))
                    continue
                if name == 'stop':
                    finish('user_stop')
                if name == 'record':
                    if not recent or now-last > .5:
                        events.put(dict(type='notice', message='反馈尚未稳定，未开始采集'))
                    elif recorder.active:
                        events.put(dict(type='notice', message='已有采集正在进行'))
                    else:
                        recorder.start(command['label'], command['seconds'], now)
            # Parameter writes and enable RPCs can block; drain AGAIN before
            # control instead of stamping an old queued frame as fresh.
            if command:drained=receive()
            now = time.monotonic()
            if drained:motion.tick(recent[-1] if recent else None,diagnostic,now)
            elif motion.active:
                motion.capture_observation(recent[-1] if recent else None,diagnostic,now)
                if motion.trial:motion.trial['fault']=motion.latest_observation
                motion.stop('反馈队列未及时排空，已停止，不能用积压旧帧继续动作')
            if motion.pending_report:
                report=motion.pending_report;motion.pending_report=None
                if report.get('kind')=='low_current_showcase_trial':report['feedback_tail']=list(recent)[-300:]
                report.update(id=uuid.uuid4().hex,device_id=device_id,side=selected['side'],generation=selected['generation'])
                report.setdefault('kind','single_joint_check')
                report_dir=Path(__file__).resolve().parent/'motion_reports'
                report_dir.mkdir(exist_ok=True)
                (report_dir/(report['id']+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                events.put(dict(type='motion_report',report=report))
            if recorder.active and now-recorder.started >= recorder.seconds:
                finish('completed')
            if now-last > 3:
                raise RuntimeError('连续3秒没有收到设备反馈，请检查连接')
            if now-last_emit >= .05:
                if not motion.active:motion.check_probe_ready(recent[-1] if recent else None,diagnostic,now)
                events.put(dict(type='state', connection='connected' if recent else 'connecting',
                    message=('正在执行实机动作，画面跟随反馈' if motion.active else '已核对设备身份，正在接收反馈') if recent else '已连接，等待反馈',
                    latest=recent[-1] if recent else None,
                    device_id=device_id, joint_rates=joint_rates.snapshot(now),
                    metrics=dict(host_hz=rate(recent, 'host_s'), device_hz=rate(recent, 'device_timestamp_us'), age_ms=(now-last)*1000 if recent else None),
                    recording=recorder.state(),hardware=motion.status()))
                last_emit = now
            if motion.active:
                # Reserve the final 150 us for the next command deadline. This
                # improves host cadence but is not a real-time OS guarantee.
                wait=min(.0003,max(0.,motion.cadence.next_due-time.monotonic()-.00015))
                if wait:time.sleep(wait)
            elif now-last>.0003:time.sleep(.0003)
    except Exception as error:
        finish('feedback_error')
        events.put(dict(type='error', message=str(error)))
    finally:
        finish('disconnected')
        if motion is not None:motion.stop('连接结束' if motion.active else motion.reason)
        if diagnostics is not None:diagnostics.close()
        if sub is not None:
            sub.close()
        try:
            if hand is not None:
                hand.disconnect()
        finally:
            if protocol_route is not None:
                protocol_route.close()
            if ownership is not None:
                ownership.close()
        events.put(dict(type='closed',hardware=motion.status() if motion is not None else {}))


def emit(event):
    print('WUJI_JSON:'+json.dumps(event, ensure_ascii=False, allow_nan=False), flush=True)


def main():
    ctx = mp.get_context('spawn')
    requests, events = ctx.Queue(), ctx.Queue()
    process = None
    started = 0.
    received_feedback = False
    glove_mode = False
    def halt():
        nonlocal process
        if process:
            requests.put(dict(name='disconnect'))
            process.join(timeout=6. if glove_mode else 1.)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2.)
            process = None
    emit(dict(type='ready',teleop_version=1))
    try:
        while True:
            if select.select([sys.stdin], [], [], .04)[0]:
                line = sys.stdin.readline()
                if not line:
                    break
                try:
                    command = validate_command(json.loads(line))
                    name = command['name']
                    if name == 'glove_session':
                        if process:raise ValueError('Already connected')
                        from glove_agent import worker_glove
                        process=ctx.Process(target=worker_glove,args=(requests,events))
                        process.start();received_feedback=True;started=time.monotonic();glove_mode=True
                    elif name == 'connect':
                        if process:
                            raise ValueError('Already connecting or connected')
                        if command.get('auto_detect', False):
                            process = ctx.Process(target=worker_auto, args=(command.get('address', ''), command.get('serial', ''), requests, events))
                        else:
                            process = ctx.Process(target=worker, args=(command.get('address', ''), requests, events))
                        process.start()
                        started = time.monotonic()
                        received_feedback = False
                    elif name == 'disconnect':
                        halt()
                        while True:
                            try:
                                emit(events.get_nowait())
                            except queue.Empty:
                                break
                        emit(dict(type='closed'))
                        break
                    elif process and process.is_alive():
                        requests.put(command)
                    else:
                        raise ValueError('Connect the left hand first')
                except (ValueError, TypeError) as error:
                    emit(dict(type='notice', message=str(error)))
            while True:
                try:
                    event = events.get_nowait()
                except queue.Empty:
                    break
                if event['type'] == 'state' and event.get('latest'):
                    received_feedback = True
                emit(event)
            if process and not process.is_alive():
                process.join()
                break
            if process and not received_feedback and time.monotonic()-started > 15:
                halt()
                emit(dict(type='error', message='15秒内未收到机械手反馈；请检查电源、网线和设备地址'))
                break
    finally:
        halt()


if __name__ == '__main__':
    main()
