"""Bounded, local code interface for inspecting this app and preparing updates."""
import hashlib
import math
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from device_termination import unconfirmed
from desktop_lifecycle import calibration_cleanup_required

PAGES = ('home', 'library', 'feedback', 'parameters', 'connection', 'glove', 'doctor',
         'interaction', 'capture', 'records', 'settings', 'devices', 'control-tools', 'official-viz','official-viz-device')
OPERATIONS = ('inspect', 'capture', 'page', 'resize', 'appearance', 'menu',
              'viewer', 'close_idle', 'prepare_update', 'import_model', 'preview', 'picker', 'connection_panel', 'reload_ui')


def update_remaining(deadline, clock=time.monotonic):
    """One absolute budget, shared by the update request and its close steps."""
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError('Invalid application update deadline')
    remaining = deadline-clock()
    if not math.isfinite(remaining) or remaining <= 0:
        raise ValueError('更新请求已超时，请重试 / Update request timed out; retry')
    return remaining


def owned_update_scan(controller):
    """Pure status: only this process's ordinary discovery may yield on apply."""
    from cli_coordination import identity, reservation, root_folder
    from official_visualization import OfficialVisualization
    viewer = getattr(controller, 'official_viz', None)
    if not isinstance(viewer, OfficialVisualization):return None
    with viewer.lock:
        state = viewer.snapshot()
        token = viewer.scan_token
        if (state.get('active') is not False or state.get('busy') is not True or state.get('scanning') is not True
                or state.get('scan_cleanup_required') or state.get('error') or state.get('session_id')
                or viewer.token is not None or viewer.scan_job is not None or viewer.scan_needs_close
                or type(token) is not str or re.fullmatch('[a-f0-9]{32}', token) is None
                or Path(viewer.folder).resolve() != root_folder().resolve()):return None
        marker = reservation(viewer.folder)
        if (marker.get('kind') != 'scan' or marker.get('token') != token
                or marker.get('pid') != os.getpid() or marker.get('start') != identity(os.getpid())):return None
        return token


@contextmanager
def update_discovery_priority(controller, *, deadline, clock=time.monotonic):
    """Explicit requests yield only local discovery; enter before the CLI gate."""
    from official_visualization import OfficialVisualization
    viewer = getattr(controller, 'official_viz', None)
    remaining = update_remaining(deadline, clock)
    if not isinstance(viewer, OfficialVisualization):
        yield
        return
    with viewer.discovery_priority(timeout=min(2., remaining)):
        update_remaining(deadline, clock)
        yield


def idle(state, doctor):
    from connection_switch import unsettled
    if unsettled(state):return False
    if state.get('maintenance',{}).get('busy') is True or state.get('maintenance',{}).get('catalog_busy') is True:return False
    return (state.get('connection') == 'disconnected'
            and not state.get('glove', {}).get('busy')
            and state.get('hardware', {}).get('active') is False
            and not state.get('recording', {}).get('active')
            and not state.get('parameter_sync', {}).get('busy')
            and not state.get('calibration', {}).get('running')
            and not calibration_cleanup_required(state)
            and not state.get('calibration_connection', {}).get('busy')
            and not state.get('teleoperation_connection', {}).get('busy')
            and not state.get('calibrated_teleoperation', {}).get('busy')
            and not state.get('official_studio', {}).get('reserved')
            and not state.get('official_studio', {}).get('active')
            and not state.get('official_studio', {}).get('busy')
            and not state.get('official_viz', {}).get('active')
            and not state.get('official_viz', {}).get('busy')
            and not state.get('official_viz', {}).get('reserved')
            and not doctor.get('running') and not unconfirmed(state))


def version_tuple(version):
    if not isinstance(version, str) or not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}', version):
        raise ValueError('Expected a release version such as 0.1.4')
    return tuple(map(int, version.split('.')))


def verify_update(data_dir, edition, version, digest):
    """Only a named installer in the owned updates folder can be prepared."""
    if version_tuple(version) < version_tuple(edition['version']):
        raise ValueError('Older releases cannot be applied through this interface')
    if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
        raise ValueError('Expected SHA-256 from a trusted release')
    prefix = 'HandWorkbench'
    root = Path(data_dir).resolve()
    updates = root / 'updates'
    # Windows can virtualize LocalAppData for a packaged parent process; that
    # changes resolve() even for an ordinary directory. Reject redirects only.
    if updates.parent.resolve() != root or updates.is_symlink() or updates.is_junction():
        raise ValueError('Updates folder must be inside application data')
    path = updates / f'{prefix}-{version}-windows-x64-setup.exe'
    if path.parent != updates or path.is_symlink() or path.is_junction() or not path.is_file():
        raise ValueError('The installer has not been staged in application data')
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError('Expected a Windows installer')
        stream.seek(0)
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(block)
    if checksum.hexdigest() != digest:
        raise ValueError('Installer SHA-256 does not match')
    return dict(ok=True, version=version, edition=edition['name'], sha256=digest,
                installer=str(path), restart_required=True)


def dispatch(host, payload, *, clock=time.monotonic):
    if not isinstance(payload, dict):
        raise ValueError('Expected a JSON object')
    op = payload.get('operation')
    if op not in OPERATIONS:
        raise ValueError('Unsupported desktop operation')
    if op in ('prepare_update', 'close_idle'):
        deadline = clock()+10.
        with update_discovery_priority(host.server.controller, deadline=deadline, clock=clock):
            update_remaining(deadline, clock)
            if not idle(host.server.controller.snapshot(), host.server.controller.doctor.snapshot()):
                raise ValueError('Disconnect devices and finish active work before closing or updating by code')
            if op == 'close_idle':
                return host.stop_and_close(require_idle=True, deadline=deadline, clock=clock)
            result = verify_update(host.data_dir, host.edition, payload.get('version'), payload.get('sha256'))
            update_remaining(deadline, clock)
            return result
    if op == 'inspect':
        return host.inspect()
    if op == 'connection_panel':
        if payload.get('state') not in ('open','closed'):raise ValueError('Expected open or closed')
        return host.connection_panel(payload['state'])
    if op == 'reload_ui':
        if not idle(host.server.controller.snapshot(),host.server.controller.doctor.snapshot()):raise ValueError('UI reload requires an idle disconnected app')
        return host.reload_ui()
    if op == 'capture':
        return host.capture()
    if op == 'page':
        if payload.get('page') not in PAGES:
            raise ValueError('Unsupported page')
        return host.navigate(payload['page'])
    if op == 'resize':
        width, height = payload.get('width'), payload.get('height')
        if type(width) is not int or type(height) is not int or not 960 <= width <= 2560 or not 680 <= height <= 1600:
            raise ValueError('Size must be 960–2560 by 680–1600')
        host.window.resize(width, height)
        return dict(ok=True)
    if op == 'appearance':
        key, value = payload.get('key'), payload.get('value')
        if key not in ('reduceTransparency', 'reduceMotion', 'language') or (
                value not in ('zh', 'en') if key == 'language' else type(value) is not bool):
            raise ValueError('Unsupported appearance setting')
        return host.appearance(key, value)
    if op == 'menu':
        if type(payload.get('open')) is not bool:
            raise ValueError('Expected open: true or false')
        return host.menu(payload['open'])
    if op == 'viewer':
        return host.open_viewer()
    if op == 'picker':
        if payload.get('kind') not in ('letters','numbers','playlist','closed'):
            raise ValueError('Unsupported picker')
        return host.picker(payload['kind'])
    if op == 'preview':
        from demo_player import CATALOG
        from playback_rates import PLAYBACK_SPEEDS
        action=payload.get('action');speed=payload.get('speed',1.)
        if action not in CATALOG or type(speed) not in (int,float) or speed not in PLAYBACK_SPEEDS:
            raise ValueError('Unsupported preview action or speed')
        # Visual QA must never change or obscure an active hardware session.
        if not idle(host.server.controller.snapshot(),host.server.controller.doctor.snapshot()):
            raise ValueError('Code preview requires an idle, disconnected workbench')
        return host.preview(action,speed)
    if op == 'import_model':
        from model_pack import install
        digest=payload.get('sha256')
        if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Invalid model pack hash')
        root=Path(host.data_dir).resolve();path=root/'imports'/('model-'+digest+'.zip')
        if path.resolve()!=path or not path.is_file() or path.stat().st_size>6_000_000:raise ValueError('Invalid staged model pack')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Model pack hash mismatch')
        return install(path,root)
    raise ValueError('Unsupported desktop operation')
