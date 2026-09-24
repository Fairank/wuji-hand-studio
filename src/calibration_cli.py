"""Official Wuji CLI hand-model calibration on the configured SDK controller.

This process never sends robot-hand motor commands. A named SDK user and an
explicitly selected glove are required before the official six-pose flow.
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
    command = [config['cli'] or 'wuji', '--json', *args]
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


def runner_command(config, mode, args, expected_user=None, replace=False):
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
    if mode=='calibrate':
        if not isinstance(expected_user,str) or not expected_user:raise ValueError('Expected calibration user required')
        prefix.extend([expected_user,'replace' if replace else 'keep'])
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
    def __init__(self, reports=None):
        self.lock = threading.RLock()
        self.process = None
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
        if not run['running'] and (refresh or cache is None or stale):
            config = None
            try:
                config = load_config()
                users = cli_json(config, 'user', 'list').get('users', [])
                current = cli_json(config, 'user', 'show')
                devices = cli_json(config, 'devices').get('devices', [])
                if not isinstance(users, list) or not isinstance(devices, list):
                    raise ValueError('Unexpected official CLI list')
                cache = dict(available=True, calibration_supported=config['mode']!='ssh', mode=config['mode'], users=users,
                             current=current, devices=[d for d in devices if is_glove_device(d)], error=None)
            except (OSError, ValueError, RuntimeError, TimeoutError) as error:
                cache = dict(available=False, calibration_supported=False, mode=(config or {}).get('mode'),
                             users=[], current=None, devices=[], error=str(error)[:400])
            with self.lock:
                self.cache = cache
                self.cached_at = time.monotonic()
        with self.lock:run=copy.deepcopy(self.run)
        return dict(**(cache or {}), run=run)

    def profile(self, operation, name):
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError('Profile name must use 1–32 letters, digits, _ or -')
        with self.lock:
            if self.run['running']:
                raise ValueError('Finish calibration before changing SDK user')
            config = load_config()
            if config['mode']=='ssh':raise ValueError('Use built-in or local controller to change calibration profiles')
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

    def start(self, side, serial, replace=False, timeout_s=900):
        if side not in ('left', 'right') or not isinstance(serial, str) or not _SERIAL.fullmatch(serial):
            raise ValueError('Select a discovered glove and its hand side')
        if type(replace) is not bool or type(timeout_s) is not int or not 60 <= timeout_s <= 1800:
            raise ValueError('Invalid calibration options')
        with self.lock:
            if self.run['running']:
                raise ValueError('Calibration is already running')
            state = self.snapshot(refresh=True)
            if not state['available']:
                raise ValueError(state['error'] or 'Official CLI unavailable')
            if not state['calibration_supported']:
                raise ValueError('Streaming calibration requires the local managed controller / 流式标定需使用本机内置控制端')
            current = state['current'] or {}
            if not current.get('name') or current['name'].lower() == 'default' or any(isinstance(u, dict) and u.get('name') == current['name'] and u.get('is_default') for u in state['users']):
                raise ValueError('Create and switch to a named SDK user first / 请先建立并切换到命名用户')
            model = current.get(side + '_hand') or {}
            if model.get('calibrated') and not replace:
                raise ValueError('This side is already calibrated; confirm replacement / 已有标定，请明确确认覆盖')
            if not any(isinstance(d, dict) and (d.get('sn') or d.get('serial')) == serial for d in state['devices']):
                raise ValueError('Selected glove is not in the latest official scan / 手套不在最新扫描结果中')
            device=next(d for d in state['devices'] if (d.get('sn') or d.get('serial'))==serial)
            found_side=str(device.get('handedness') or device.get('side') or '').lower().removeprefix('handedness.')
            if found_side in ('left','right') and found_side!=side:
                raise ValueError('Selected glove side does not match calibration side / 手套与标定左右手不符')
            config = load_config()
            args = [config['cli'] or 'wuji', '--jsonl', 'calib', 'hand-model',
                    '--sn', serial, '--handedness', side, '--timeout-s', str(timeout_s)]
            command = runner_command(config, 'calibrate', args, current['name'], replace)
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
            process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       creationflags=flags, env={**os.environ, 'WUJI_NO_UPDATE_CHECK': '1'},
                                       text=True, encoding='utf-8', errors='replace', bufsize=1)
            self.process = process
            self.run = dict(running=True, status='collecting', step=None, progress=None,
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
                elif code == 0:
                    self.run.update(status='unconfirmed',error='CLI exited without an official result / 官方流程结束但未返回成功结果')
                elif code == 9 or terminal=='cancelled':
                    self.run['status'] = 'cancelled'
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
