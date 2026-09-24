"""Bounded, local code interface for inspecting this app and preparing updates."""
import hashlib
import re
from pathlib import Path

PAGES = ('library', 'feedback', 'parameters', 'connection', 'glove', 'doctor',
         'interaction', 'capture', 'records', 'settings', 'devices')
OPERATIONS = ('inspect', 'capture', 'page', 'resize', 'appearance', 'menu',
              'viewer', 'close_idle', 'prepare_update', 'import_model', 'preview', 'picker', 'connection_panel', 'reload_ui')


def idle(state, doctor):
    return (state.get('connection') == 'disconnected'
            and not state.get('glove', {}).get('busy')
            and state.get('hardware', {}).get('active') is False
            and not state.get('recording', {}).get('active')
            and not state.get('parameter_sync', {}).get('busy')
            and not doctor.get('running'))


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


def dispatch(host, payload):
    if not isinstance(payload, dict):
        raise ValueError('Expected a JSON object')
    op = payload.get('operation')
    if op not in OPERATIONS:
        raise ValueError('Unsupported desktop operation')
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
        if payload.get('kind') not in ('letters','numbers','closed'):
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
    if not idle(host.server.controller.snapshot(), host.server.controller.doctor.snapshot()):
        raise ValueError('Disconnect devices and finish active work before closing or updating by code')
    if op == 'close_idle':
        return host.stop_and_close(require_idle=True)
    return verify_update(host.data_dir, host.edition, payload.get('version'), payload.get('sha256'))
