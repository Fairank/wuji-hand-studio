"""Official Wuji CLI hand-model calibration on the configured SDK controller.

This process never sends robot-hand motor commands. Human TTY uses the original
CLI's user and overwrite prompts; machine-mode controllers retain strict host
preflight. Every path requires an explicitly selected glove.
"""
import copy
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
import uuid

from bridge_config import load_config
from doctor import local_run, remote_run

_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,31}\Z")
_SERIAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")


def is_glove_device(device):
    if not isinstance(device, dict):
        return False
    kind = ' '.join(str(device.get(key) or '') for key in ('model', 'type', 'device_type', 'device_name')).lower()
    serial = str(device.get('sn') or device.get('serial') or '')
    return 'glove' in kind or (not kind.strip() and serial.upper().startswith('WG'))


def controller_args(config, args):
    """Use the same Linux user/configuration as the teleoperation bridge."""
    mode = config['mode']
    if mode == 'wsl':
        from managed_runtime import args as runtime_args, check_ready
        check_ready()
        return runtime_args(args)
    if mode == 'macvm':
        from macos_runtime import args as runtime_args, ensure_running, env
        ensure_running()
        return ['/usr/bin/env', 'LIMA_HOME=' + env()['LIMA_HOME'], *runtime_args(args)]
    if mode == 'local':
        return args
    raise ValueError('Remote calibration needs the official CLI on that Linux controller / 请在远程 Linux 控制端运行官方标定')


def cli_json(config, *args):
    from official_device_discovery import use_native
    if use_native(config):
        if args==('devices',):
            from official_device_discovery import native_devices
            return native_devices()
        from native_cli import json_command
        return json_command(*args)
    if args==('devices',):
        from official_device_discovery import use_native,native_devices
        if use_native(config):return native_devices()
    from official_cli import cli_path
    command = [cli_path(config), '--json', *args]
    if len(args)>1 and args[0]=='user' and args[1] in ('create','switch'):
        command = runner_command(config, 'profile', command)
        code, out, err = local_run(command)
    elif config['mode'] == 'ssh':
        code, out, err = remote_run(config, command)
    else:
        code, out, err = local_run(controller_args(config, command))
    if code:
        raise ValueError((err or out or 'Official CLI failed').strip()[:400])
    try:
        value = json.loads(out)
    except (ValueError, TypeError) as error:
        raise ValueError('Official CLI did not return JSON / 官方 CLI 未返回有效数据') from error
    if not isinstance(value, dict):
        raise ValueError('Unexpected official CLI response')
    return value


def runner_command(config, mode, args, expected_user=None, replace=False, expected_device=None, expected_side=None):
    """Deploy the same verified controller bundle used by the device sessions."""
    if config['mode']=='wsl':
        from managed_runtime import WslController, PYTHON
        bridge=WslController(config, 'hand2_left')
        directory=bridge.agent_directory;python=PYTHON
    elif config['mode']=='local':
        directory=config.get('agent_directory') or str(Path(__file__).parent)
        python=config.get('python') or 'python3'
    elif config['mode']=='macvm':
        from macos_runtime import MacController
        bridge=MacController(config, 'hand2_left')
        directory=bridge.agent_directory;python=config.get('python') or 'python3'
    else:
        raise ValueError('Guided calibration requires the built-in or local controller')
    prefix=[mode]
    if mode in ('calibrate','calibrate_address'):
        if not isinstance(expected_user,str) or not expected_user:raise ValueError('Expected calibration user required')
        prefix.extend([expected_user,'replace' if replace else 'keep'])
        if mode=='calibrate_address':prefix.extend([expected_device,expected_side])
    return controller_args(config,[python,'-u',directory+'/calibration_runner.py',*prefix,*args])


def progress_fields(event):
    """Normalize the public SDK callback, retaining unknown CLI data verbatim.

    SDK example 5.calibration.py documents zero-based step_index/step_total,
    step_name, progress, variance_ok, constraints_ok, and metric diagnostics.
    A step index is only mapped to our six references when step_total is six.
    Pose names take precedence and a conflicting/unknown name stays unknown.
    No local pass thresholds, estimated elapsed time, or fabricated completion.
    """
    payload=event
    for key in ('progress','feedback'):
        if isinstance(event.get(key),dict):payload=event[key];break
    names=('pinch_index','pinch_middle','pinch_ring','pinch_pinky','four_finger_bend_90','flat_open')
    name=payload.get('step_name')
    index=payload.get('pose_index')
    if type(index) is not int or not 0<=index<6:index=None
    if isinstance(name,str) and name:
        index=names.index(name) if name in names else None
    elif index is None and type(payload.get('step_total')) is int and payload['step_total']==6:
        step=payload.get('step_index')
        if type(step) is int and 0<=step<6:index=step
    value=payload.get('fraction',payload.get('progress'))
    if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1:value=None
    phase=payload.get('phase',payload.get('state',''))
    checks={key:payload.get(key) if type(payload.get(key)) is bool else None
            for key in ('variance_ok','constraints_ok')}
    checks['metrics']=copy.deepcopy(payload['metrics']) if isinstance(payload.get('metrics'),list) else []
    timing={key:payload[key] for key in ('hold_elapsed','hold_target','collect_elapsed','collect_target','frames_collected')
            if type(payload.get(key)) in (int,float) and math.isfinite(payload[key]) and payload[key]>=0}
    return dict(step=payload.get('step_index',payload.get('step')),pose_index=index,
                step_name=name if isinstance(name,str) else '',step_total=payload.get('step_total'),
                phase=phase if isinstance(phase,str) else '',progress=value,checks=checks,timing=timing)

def record_progress(run,event):
    fields=progress_fields(event)
    captured=list(run.get('captured_poses',[]))
    if fields['phase']=='done' and fields['pose_index'] is not None and fields['pose_index'] not in captured:
        captured.append(fields['pose_index'])
    run.update(fields,captured_poses=sorted(captured))


class CalibrationCLI:
    def __init__(self, reports=None, device_provider=None, metadata_check=None):
        self.lock = threading.RLock()
        self.refresh_lock = threading.Lock()
        self.device_provider=device_provider
        self.metadata_check=metadata_check or (lambda:None)
        self.profile_read_check=self.metadata_check
        self.process = None
        self.native = None
        self.thread = None
        self.cache = None
        self.cached_at = 0.0
        self.reports=Path(reports) if reports else None
        self.run = dict(running=False, status='idle', step=None, progress=None,
                        event=None, error=None, exit_code=None, started=None,pose_index=None,
                        phase='',result=None,event_count=0)

    def snapshot(self, refresh=False):
        with self.lock:
            run = copy.deepcopy(self.run)
            cache = copy.deepcopy(self.cache)
            stale = time.monotonic() - self.cached_at > 8
        metadata_error=None
        refreshing=False
        if not run['running'] and (refresh or cache is None or stale):
            # Do not wait while holding self.lock: start() may already hold it,
            # while another metadata reader needs it to publish its result.
            if not self.refresh_lock.acquire(blocking=False):
                refreshing=True;metadata_error='官方用户信息正在刷新，请稍后重试 / Official user information is refreshing; retry shortly'
            else:
                config=None
                try:
                    config=load_config()
                    from official_device_discovery import use_native
                    native=use_native(config)
                    if native:
                        # These commands read local SDK user files only. They
                        # neither discover nor connect to a device.
                        self.profile_read_check()
                        from native_cli import prepare
                        component=prepare()
                        users=cli_json(config,'user','list').get('users',[])
                        current=cli_json(config,'user','show')
                        if not isinstance(users,list) or len(users)>128:raise ValueError('Invalid SDK user list')
                        detailed=[]
                        for user in users:
                            if not isinstance(user,dict) or not isinstance(user.get('name'),str) or not _NAME.fullmatch(user['name']):
                                raise ValueError('Invalid SDK user identity')
                            user=copy.deepcopy(user)
                            calibrated=user.get('calibrated')
                            if isinstance(calibrated,dict) and all(type(calibrated.get(s)) is bool for s in ('left','right')):
                                for s in ('left','right'):user[s+'_hand']=dict(calibrated=calibrated[s])
                            if not all(isinstance(user.get(s+'_hand'),dict) and type(user[s+'_hand'].get('calibrated')) is bool for s in ('left','right')):
                                detail=cli_json(config,'user','show',user['name'])
                                if detail.get('name')!=user['name']:raise ValueError('SDK user identity changed')
                                user.update(detail)
                            detailed.append(user)
                        users=detailed
                    else:
                        self.metadata_check()
                        users=cli_json(config,'user','list').get('users',[])
                        current=cli_json(config,'user','show')
                    devices=[] if self.device_provider is not None else cli_json(config,'devices').get('devices',[])
                    if not isinstance(users,list) or (not native and not isinstance(current,dict)) or not isinstance(devices,list):
                        raise ValueError('Unexpected official CLI list')
                    cache=dict(available=True,calibration_supported=config['mode']!='ssh',mode=config['mode'],users=users,
                               current=current,devices=[d for d in devices if is_glove_device(d)],error=None,
                               metadata_checked_at=time.time())
                    if native:
                        cache.update(runtime=dict(backend='windows_native',label='Windows CLI · Windows SDK',version=component['version']),guide='official_jsonl',human_tty=False,guided_native=True)
                    else:cache.update(runtime=dict(backend=config['mode'],label=config['mode']+' CLI / SDK'),guide='official_jsonl',human_tty=False)
                    with self.lock:
                        self.cache=copy.deepcopy(cache);self.cached_at=time.monotonic()
                    stale=False
                except (OSError,ValueError,RuntimeError,TimeoutError) as error:
                    metadata_error=str(error)[:400]
                finally:self.refresh_lock.release()
        if cache is None:
            cache=dict(available=False,calibration_supported=False,mode=None,users=[],current=None,devices=[],error=None,metadata_checked_at=None)
        cache.update(metadata_current=bool(cache.get('available') and not stale and not metadata_error),
                     metadata_error=metadata_error,metadata_refreshing=refreshing)
        if metadata_error:cache.update(available=False,error=metadata_error)
        if self.device_provider is not None:
            try:
                devices=self.device_provider(False)
                if not isinstance(devices,list):raise ValueError('Unexpected shared device inventory')
                cache.update(devices=[copy.deepcopy(d) for d in devices if is_glove_device(d)],devices_error=None)
            except (OSError,ValueError,RuntimeError,TimeoutError) as error:
                cache.update(devices=[],devices_error=str(error)[:400])
        with self.lock:run=copy.deepcopy(self.run)
        from cli_coordination import reservation,identity
        marker=reservation()
        cleanup=bool(marker.get('kind')=='calibration' and identity(marker.get('pid'))!=marker.get('start'))
        return dict(**(cache or {}), run=run,cleanup_required=cleanup,
                    cleanup_error='上次原版 CLI 退出尚未核对，请结束会话以恢复 / Previous CLI cleanup is unverified; end the session to recover' if cleanup else None)

    def profile(self, operation, name):
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError('Profile name must use 1–32 letters, digits, _ or -')
        with self.lock:
            if self.run['running']:
                raise ValueError('Finish calibration before changing SDK user')
            config = load_config()
            if config['mode']=='ssh':raise ValueError('Use built-in or local controller to change calibration profiles')
            self.metadata_check()
            users = cli_json(config, 'user', 'list').get('users', [])
            if operation == 'create':
                if any(isinstance(u, dict) and u.get('name') == name for u in users):
                    raise ValueError('SDK user already exists')
                cli_json(config, 'user', 'create', name, '--switch')
            elif operation == 'switch':
                if not any(isinstance(u, dict) and u.get('name') == name for u in users):
                    raise ValueError('Unknown SDK user')
                cli_json(config, 'user', 'switch', name)
            else:
                raise ValueError('Unsupported SDK user operation')
            self.cache = None
        return self.snapshot(refresh=True)

    def preflight_guided(self, profile, side, replace=False, *, create_profile=False):
        """Read-only preflight may run while this workspace owns the Viewer."""
        from calibration_guided import validate_profile
        if (not isinstance(profile,str) or not _NAME.fullmatch(profile) or profile.lower()=='default'
                or type(create_profile) is not bool or type(replace) is not bool):
            raise ValueError('请选择或填写命名佩戴者 / Select or create a named SDK user')
        if side not in ('left','right'):raise ValueError('请先确认手套左右手 / Verify the selected glove side')
        self.profile_read_check()
        config=load_config()
        users=cli_json(config,'user','list').get('users')
        if not isinstance(users,list):raise ValueError('Invalid SDK user list')
        exists=any(isinstance(u,dict) and u.get('name')==profile for u in users)
        if create_profile:
            if exists:raise ValueError('佩戴者名称已存在 / SDK user already exists')
            return dict(name=profile,left_hand=dict(calibrated=False),right_hand=dict(calibrated=False))
        if not exists:raise ValueError('未找到佩戴者 / Unknown SDK user')
        return validate_profile(cli_json(config,'user','show',profile),profile,side,replace)

    def start(self, side, serial, replace=False, timeout_s=900, *, interface='guided', connected=None, profile=None, create_profile=False, cancel_check=None):
        config=load_config()
        from official_device_discovery import use_native
        if use_native(config):
            if not isinstance(serial,str) or not _SERIAL.fullmatch(serial):
                raise ValueError('Select a discovered glove / 请选择已发现的手套')
            if type(replace) is not bool or type(timeout_s) is not int or not 60<=timeout_s<=1800:
                raise ValueError('Invalid calibration options')
            if interface not in ('guided','terminal') or type(create_profile) is not bool:raise ValueError('Invalid calibration interface')
            # Only the server handover supplies this fact after matching and
            # stopping its own live Viewer. Browser discovery is not a connection.
            from cli_coordination import identity,gate
            if (not isinstance(connected,dict) or connected.get('source')!='native_viewer'
                    or connected.get('serial')!=serial or connected.get('owner_pid')!=os.getpid()
                    or identity(os.getpid())!=connected.get('owner_start')
                    or not isinstance(connected.get('viewer_token'),str)
                    or type(connected.get('checked_at')) not in (int,float)
                    or not math.isfinite(connected['checked_at']) or not 0<=time.time()-connected['checked_at']<=60):
                raise ValueError('请先手动接入并查看，再切换到标定 / Connect the selected Viewer before calibration')
            if side not in ('left','right') or connected.get('side') not in (None,side):raise ValueError('Glove side changed during handover')
            with gate():
                # Metadata checks read Controller.snapshot (controller -> run
                # lock). Never hold the run lock while calling back into it.
                with self.lock:
                    if self.run['running']:raise ValueError('Calibration is already running')
                self.metadata_check()
                if cancel_check is not None:cancel_check()
                if interface=='guided':
                    self.preflight_guided(profile,side,replace,create_profile=create_profile)
                    current=cli_json(config,'user','show')
                    if cancel_check is not None:cancel_check()
                    if create_profile:cli_json(config,'user','create',profile,'--switch')
                    elif current.get('name')!=profile:cli_json(config,'user','switch',profile)
                    if cancel_check is not None:cancel_check()
                    from calibration_guided import validate_profile
                    validate_profile(cli_json(config,'user','show'),profile,side,replace)
                elif create_profile:raise ValueError('Original terminal manages its own user creation')
                from native_calibration import NativeCalibration
                native=NativeCalibration(self)
                with self.lock:self.native=native
                options=dict(cancel_check=cancel_check) if cancel_check is not None else {}
                if interface=='terminal':return native.start_human(serial,expected_side=side,timeout=timeout_s,**options)
                return native.start_guided(serial,side,profile,replace,timeout_s,**options)
        if side not in ('left', 'right') or not isinstance(serial, str) or not _SERIAL.fullmatch(serial):
            raise ValueError('Select a discovered glove and its hand side')
        if type(replace) is not bool or type(timeout_s) is not int or not 60 <= timeout_s <= 1800:
            raise ValueError('Invalid calibration options')
        with self.lock:
            if self.run['running']:
                raise ValueError('Calibration is already running')
            state = self.snapshot(refresh=True)
            if not state['available'] or state.get('metadata_current') is not True:
                raise ValueError(state.get('metadata_error') or state.get('error') or 'Refresh official user status before calibration / 请先刷新官方标定用户状态')
            if not state['calibration_supported']:
                raise ValueError('Streaming calibration requires the local managed controller / 流式标定需使用本机内置控制端')
            current = state['current'] or {}
            if (not isinstance(current.get('name'), str) or not current['name'].strip()
                    or current['name'].lower() == 'default' or current.get('is_default')
                    or any(isinstance(u, dict) and u.get('name') == current['name'] and u.get('is_default') for u in state['users'])):
                raise ValueError('Create and switch to a named SDK user first / 请先建立并切换到命名用户')
            model = current.get(side + '_hand')
            if not isinstance(model, dict) or type(model.get('calibrated')) is not bool:
                raise ValueError('Official calibration state is unknown; refresh first / 无法确认此侧已有标定，请先刷新用户状态')
            if model['calibrated'] and not replace:
                raise ValueError('This side is already calibrated; confirm replacement / 已有标定，请明确确认覆盖')
            if self.device_provider is not None:
                devices=self.device_provider(True)
                if not isinstance(devices,list):raise ValueError('Unexpected shared device inventory')
                state['devices']=[copy.deepcopy(d) for d in devices if is_glove_device(d)]
            if not any(isinstance(d, dict) and (d.get('sn') or d.get('serial')) == serial for d in state['devices']):
                raise ValueError('Selected glove is not in the latest official scan / 手套不在最新扫描结果中')
            device=next(d for d in state['devices'] if (d.get('sn') or d.get('serial'))==serial)
            found_side=str(device.get('handedness') or device.get('side') or '').lower().removeprefix('handedness.')
            if found_side in ('left','right') and found_side!=side:
                raise ValueError('Selected glove side does not match calibration side / 手套与标定左右手不符')
            config = load_config()
            from official_cli import cli_path
            self.native=None
            args = [cli_path(config), '--jsonl', 'calib', 'hand-model',
                    '--sn', serial, '--handedness', side, '--timeout-s', str(timeout_s)]
            command = runner_command(config, 'calibrate', args, current['name'], replace)
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
            process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       creationflags=flags, env={**os.environ, 'WUJI_NO_UPDATE_CHECK': '1'},
                                       text=True, encoding='utf-8', errors='replace', bufsize=1)
            self.process = process
            self.run = dict(running=True, status='starting', step=None, progress=None,
                            event=None, error=None, exit_code=None, started=time.time(),
                            side=side, serial=serial, user=current['name'],pose_index=None,phase='',
                            result=None,event_count=0,captured_poses=[],id=uuid.uuid4().hex)
            self._config=config
            self.thread = threading.Thread(target=self._collect, args=(process,), daemon=True)
            self.thread.start()
            return self.snapshot(refresh=False)

    def _collect(self, process):
        stderr_parts = []
        terminal=None;trace=None;trace_size=0
        def drain():
            for line in process.stderr:
                if sum(map(len, stderr_parts)) < 4000:
                    stderr_parts.append(line[:500])
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            if self.reports:
                self.reports.mkdir(parents=True,exist_ok=True)
                trace=(self.reports/(self.run['id']+'.jsonl')).open('x',encoding='utf-8')
            for line in process.stdout:
                if len(line) > 65536:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict) or event.get('schema_version') != 2 or event.get('calibration') != 'hand_model':
                    continue
                kind = event.get('event') or event.get('type') or 'progress'
                if kind not in ('progress','result','error','cancelled'):continue
                if trace and trace_size<8*1024*1024:
                    trace.write(line);trace.flush();trace_size+=len(line.encode('utf-8'))
                with self.lock:
                    self.run['event'] = event
                    self.run['event_count']+=1
                    if kind=='progress':
                        record_progress(self.run,event)
                        if self.run['status']!='cancelling':self.run['status']='solving' if self.run['phase']=='solving' else 'collecting'
                    else:
                        terminal=kind
                        if kind=='result':self.run['result']=event
                        elif kind=='error':self.run['error']=str(event.get('error') or 'Official calibration failed')[:800]
            code = process.wait()
            reader.join(timeout=1)
            with self.lock:
                self.run['running'] = False
                self.run['exit_code'] = code
                if code == 0 and terminal=='result':
                    self.run['status'] = 'completed'
                elif terminal=='error':
                    self.run['status'] = 'error'
                    self.run['error'] = self.run['error'] or ''.join(stderr_parts)[-400:] or 'Official calibration failed'
                elif code == 9 or terminal=='cancelled':
                    self.run['status'] = 'cancelled'
                elif code == 0:
                    self.run.update(status='unconfirmed',error='CLI exited without an official result / 官方流程结束但未返回成功结果')
                else:
                    self.run['status'] = 'error'
                    self.run['error'] = self.run['error'] or ''.join(stderr_parts)[-400:] or 'Official calibration failed'
                self.process = None
                self.cache = None
        except (OSError, ValueError) as error:
            if process.poll() is None:
                try:process.stdin.write('cancel\n');process.stdin.flush();process.wait(timeout=10)
                except (OSError,ValueError,subprocess.TimeoutExpired):process.terminate()
            with self.lock:
                self.run.update(running=False, status='error', error=str(error)[:400])
                self.process = None
                self.cache = None
        finally:
            if trace:trace.close()
            if process.stdin:
                try:process.stdin.close()
                except OSError:pass
            if self.reports:
                with self.lock:record=copy.deepcopy(self.run)
                try:(self.reports/(record['id']+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
                except OSError:pass

    def cancel(self):
        if self.native is not None and (self.run['running'] or self.native.token):return self.native.cancel()
        from cli_coordination import reservation,gate
        if reservation().get('kind')=='calibration':
            from native_calibration import NativeCalibration
            with gate():NativeCalibration(self).recover()
            return self.snapshot(refresh=False)
        with self.lock:
            process = self.process
            if not process or process.poll() is not None:
                return self.snapshot(refresh=False)
            self.run['status'] = 'cancelling'
            try:
                process.stdin.write('cancel\n');process.stdin.flush()
            except (OSError, ValueError):
                process.terminate()
        return self.snapshot(refresh=False)
