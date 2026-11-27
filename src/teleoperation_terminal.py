"""Read-only teleoperation feedback in a Windows system console."""
import base64
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading


FEED_PATH = '/api/teleoperation-terminal'
KEY_HEADER = 'X-Wuji-Teleoperation-Key'
POWERSHELL_FEED = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
try { $Host.UI.RawUI.WindowTitle = 'Hand Workbench | Teleoperation' } catch {}
Add-Type -AssemblyName System.Net.Http
$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.UseProxy = $false
$handler.AllowAutoRedirect = $false
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds(2)
$client.DefaultRequestHeaders.Add('X-Wuji-Teleoperation-Key', $env:WUJI_TELEOP_TERMINAL_KEY)
$uri = $env:WUJI_TELEOP_TERMINAL_FEED
$last = ''
$misses = 0
try {
    while ($misses -lt 5) {
        try {
            $sample = $client.GetStringAsync($uri).GetAwaiter().GetResult() | ConvertFrom-Json
            if ($sample.key -ne $last) {
                [Console]::WriteLine('[{0:HH:mm:ss}] {1}', [DateTime]::Now, [string]$sample.line)
                $last = $sample.key
            }
            $misses = 0
        } catch {
            if ($misses -eq 0) { [Console]::WriteLine('[!] Workbench feedback unavailable') }
            $last = ''
            $misses++
        }
        Start-Sleep -Milliseconds 500
    }
} finally {
    $client.Dispose()
}
"""


def _serial(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', value) else ''


def _seq(value):
    return value if type(value) is int and value >= 0 else None


def _measure(value, unit, decimals=1):
    if type(value) in (int, float) and 0 <= value < math.inf:
        return f'{value:.{decimals}f} {unit}'
    return ''


def sample(glove, language):
    def label(zh, en):
        return en if language == 'en' else zh

    session = glove.get('session_id')
    receiving = glove.get('busy') is True and glove.get('connection') == 'receiving'
    stream = glove.get('stream') or {}
    feedback = glove.get('feedback') or {}
    hardware = glove.get('hardware') or {}
    identity = feedback.get('identified_device') or {}
    glove_serial = _serial(glove.get('glove_serial'))
    hand_serial = _serial(feedback.get('device_id'))
    glove_seq = _seq(stream.get('seq')) if receiving and glove_serial and stream.get('fresh') is True else None
    hand_verified = (feedback.get('connection') == 'connected' and identity.get('source') == 'sdk_identity'
                     and identity.get('serial') == hand_serial and identity.get('profile') == glove.get('profile'))
    hand_seq = (_seq((feedback.get('latest') or {}).get('seq')) if receiving and hand_serial
                and hand_verified and feedback.get('stale') is False else None)
    active = hardware.get('active') is True
    if glove.get('follow_unconfirmed') is True or ('active' in hardware and hardware['active'] is None):
        phase = label('停用待确认', 'Stop unconfirmed')
    elif active:
        phase = (label('正在跟随', 'Following') if glove_seq is not None and hand_seq is not None
                 else label('反馈中断', 'Feedback interrupted'))
    elif receiving:
        if glove_seq is None:
            phase = label('等待手套数据', 'Waiting for glove data')
        elif hand_seq is not None:
            phase = (label('跟随已暂停', 'Following paused') if hardware.get('phase') == 'stopped'
                     else label('灵巧手反馈已连接', 'Hand feedback connected'))
        else:
            phase = label('映射预览', 'Mapping preview')
    elif glove.get('connection') == 'connecting':
        phase = label('正在连接', 'Connecting')
    elif glove.get('connection') == 'error':
        phase = label('遥操作连接未完成', 'Teleoperation connection incomplete')
    else:
        phase = label('等待遥操作接入', 'Waiting for a teleoperation session')
    parts = [phase]
    if glove_seq is not None:
        age = _measure(stream.get('age_ms'), 'ms')
        parts.append(' · '.join(filter(None, (f"{label('手套', 'Glove')} {glove_serial} #{glove_seq}",
                                               label('龄期 ', 'age ') + age if age else ''))))
        solver = stream.get('solver') or {}
        engine = {'sdk':'SDK', 'official_open': label('官方开源', 'Official open solver')}.get(solver.get('engine'))
        if solver.get('applied') is True and engine:
            points, joints = stream.get('keypoints'), stream.get('q')
            topology = (label(f'{len(points)} 点 → {len(joints)} 关节', f'{len(points)} points → {len(joints)} joints')
                        if isinstance(points, list) and isinstance(joints, list) else '')
            elapsed = _measure(stream.get('mapping_ms'), 'ms', 2)
            parts.append(' · '.join(filter(None, (label('映射 ', 'Mapping ') + engine,
                        _measure(stream.get('retarget_hz'), 'Hz'), topology,
                        label('耗时 ', 'mapping time ') + elapsed if elapsed else ''))))
    if hand_seq is not None:
        metrics = feedback.get('metrics') or {}
        age = _measure(metrics.get('age_ms'), 'ms')
        parts.append(' · '.join(filter(None, (f"{label('灵巧手', 'Hand')} {hand_serial} #{hand_seq}",
                     _measure(metrics.get('host_hz'), 'Hz'), label('龄期 ', 'age ') + age if age else ''))))
    rate = ''
    if active and glove.get('follow_unconfirmed') is not True and glove_seq is not None and hand_seq is not None:
        rate = _measure((hardware.get('command_timing') or {}).get('host_publish_hz'), 'Hz')
    if rate:
        parts.append(label('指令发送 ', 'Command rate ') + rate)
    key = repr((_seq(session), phase, glove_serial if glove_seq is not None else '', glove_seq,
                hand_serial if hand_seq is not None else '', hand_seq, bool(rate)))
    return dict(key=key, line='  |  '.join(parts))


class Terminal:
    def __init__(self):
        self._lock = threading.Lock()
        self._process = None
        self._key = None

    def open(self, origin):
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return dict(ok=True, already_open=True)
            key = secrets.token_hex(24)
            env = dict(os.environ, WUJI_TELEOP_TERMINAL_KEY=key, WUJI_TELEOP_TERMINAL_FEED=origin + FEED_PATH)
            powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
            script = base64.b64encode(POWERSHELL_FEED.encode('utf-16le')).decode('ascii')
            self._process = subprocess.Popen(
                [str(powershell), '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', script],
                env=env, cwd=powershell.parent, creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
            self._key = key
            return dict(ok=True, already_open=False)

    def read(self, key, snapshot, language):
        with self._lock:
            if self._key is None or not key.isascii() or not secrets.compare_digest(key, self._key):
                return None
            if self._process.poll() is not None:
                return None
            return sample(snapshot(), language)

    def close(self):
        with self._lock:
            process = self._process
            self._process = self._key = None
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass  # The reader also leaves when the local feed goes away.
