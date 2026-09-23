"""Official Wuji CLI hand-model calibration on the configured SDK controller.

This process never sends robot-hand motor commands. A named SDK user and an
explicitly selected glove are required before the official six-pose flow.
"""
import copy
import json
import os
import re
import signal
import subprocess
import threading
import time

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
    if config['mode'] == 'ssh':
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


class CalibrationCLI:
    def __init__(self):
        self.lock = threading.RLock()
        self.process = None
        self.thread = None
        self.cache = None
        self.cached_at = 0.0
        self.run = dict(running=False, status='idle', step=None, progress=None,
                        event=None, error=None, exit_code=None, started=None)

    def snapshot(self, refresh=False):
        with self.lock:
            run = copy.deepcopy(self.run)
            cache = copy.deepcopy(self.cache)
            stale = time.monotonic() - self.cached_at > 8
        if refresh or cache is None or (stale and not run['running']):
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
        return dict(**cache, run=run)

    def profile(self, operation, name):
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError('Profile name must use 1–32 letters, digits, _ or -')
        with self.lock:
            if self.run['running']:
                raise ValueError('Finish calibration before changing SDK user')
            config = load_config()
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
            config = load_config()
            args = [config['cli'] or 'wuji', '--jsonl', 'calib', 'hand-model',
                    '--sn', serial, '--handedness', side, '--timeout-s', str(timeout_s)]
            command = controller_args(config, args)
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       creationflags=flags, env={**os.environ, 'WUJI_NO_UPDATE_CHECK': '1'},
                                       text=True, encoding='utf-8', errors='replace', bufsize=1)
            self.process = process
            self.run = dict(running=True, status='collecting', step=None, progress=None,
                            event=None, error=None, exit_code=None, started=time.time(),
                            side=side, serial=serial, user=current['name'])
            self.thread = threading.Thread(target=self._collect, args=(process,), daemon=True)
            self.thread.start()
            return self.snapshot(refresh=False)

    def _collect(self, process):
        stderr_parts = []
        def drain():
            for line in process.stderr:
                if sum(map(len, stderr_parts)) < 4000:
                    stderr_parts.append(line[:500])
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
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
                with self.lock:
                    self.run['event'] = event
                    self.run['status'] = kind
                    self.run['step'] = event.get('step_index', event.get('step'))
                    self.run['progress'] = event.get('progress')
            code = process.wait()
            reader.join(timeout=1)
            with self.lock:
                self.run['running'] = False
                self.run['exit_code'] = code
                if code == 0:
                    self.run['status'] = 'completed'
                elif code == 9 or self.run['status'] == 'cancelling':
                    self.run['status'] = 'cancelled'
                else:
                    self.run['status'] = 'error'
                    self.run['error'] = ''.join(stderr_parts)[-400:] or 'Official calibration failed'
                self.process = None
                self.cache = None
        except (OSError, ValueError) as error:
            with self.lock:
                self.run.update(running=False, status='error', error=str(error)[:400])
                self.process = None
                self.cache = None

    def cancel(self):
        with self.lock:
            process = self.process
            if not process or process.poll() is not None:
                return self.snapshot(refresh=False)
            self.run['status'] = 'cancelling'
            try:
                if os.name == 'nt':
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    process.send_signal(signal.SIGINT)
            except (OSError, ValueError):
                process.terminate()
        return self.snapshot(refresh=False)
