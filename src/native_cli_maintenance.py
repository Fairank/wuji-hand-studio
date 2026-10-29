"""Idle-only native CLI maintenance, sharing the existing workspace reservation."""
import time
from cli_coordination import atomic,gate,reserve,release,reservation
from native_cli_update import selection,pointer,version_info,read_json


def preference(owner):
    try:return read_json(owner.folder/'update-preferences.json').get('native_auto_update') is True
    except (OSError,ValueError):return False


def snapshot(owner):
    from native_cli import current_info,PINNED_VERSION
    saved=selection(owner.native_store.folder)
    try:current=dict(current_info(folder=owner.native_store.folder),validated=True)
    except (OSError,ValueError) as error:
        current=dict(version=(saved.get('current') or {}).get('version',PINNED_VERSION),validated=False,binary=None,
            validation_error=str(error)[:900])
    current.update(mode='windows_native',source='managed_native' if saved.get('current') else 'workbench_native',update_method='managed_native')
    return current,saved.get('previous')


def set_preference(owner,enabled):
    if type(enabled) is not bool:raise ValueError('Automatic CLI update requires a boolean option')
    atomic(owner.folder/'update-preferences.json',dict(native_auto_update=enabled))
    owner._set(auto_update=enabled,auto_update_pending=enabled)
    if enabled:
        with owner.lock:busy=bool(owner.thread and owner.thread.is_alive())
        if not busy:owner.start(check_only=False,automatic=True)
    return owner.snapshot()


def run(owner,check_only,automatic):
    from native_cli import current_info,prepare,PINNED_VERSION
    from official_cli import version_order
    from bridge_config import load_config
    from official_device_discovery import use_native
    original_config=load_config()
    if not use_native(original_config):raise ValueError('Native CLI controller selection changed; retry')
    store=owner.native_store;document=store.fetch_release();value=document['tag_name'].removeprefix('v')
    saved=selection(store.folder)
    current_version=(saved.get('current') or {}).get('version',PINNED_VERSION)
    available=version_order(value)>version_order(current_version)
    enabled=preference(owner)
    if automatic or not check_only:owner._set(auto_update_pending=False)
    owner._set(latest_version=value,available=available,newer_cli_available=available,checked_at=time.time(),checking=False,
        auto_update=enabled,update_method='managed_native',progress=None)
    if (not available or check_only and not (automatic and enabled) or automatic and (not enabled or saved.get('hold_version')==value)):
        owner._set(phase='checked');return
    token=None
    while not owner.closed.is_set():
        if automatic and not preference(owner):owner._set(phase='checked',error=None);return
        try:
            with gate(owner.folder):
                if load_config()!=original_config:raise RuntimeError('Controller selection changed; retry CLI update')
                token=reserve('switch',owner.folder)
                try:owner.idle_check()
                except BaseException:release(token,owner.folder);token=None;raise
            break
        except ValueError as error:
            owner._set(phase='waiting_idle',preparing=False,error=str(error))
            if owner.closed.wait(2):return
    if token is None:return
    try:
        owner._set(phase='downloading',preparing=True,error=None,progress=dict(stage='downloading',received_bytes=0,total_bytes=None))
        # Keep an independently verified usable prior version for rollback.
        baseline=prepare(folder=store.folder,strict=True)
        candidate=store.prepare(value,release=document,strict=True)
        if owner.closed.is_set():return
        with gate(owner.folder):
            if reservation(owner.folder).get('token')!=token:raise ValueError('Native CLI maintenance ownership changed')
            if load_config()!=original_config:raise ValueError('Controller selection changed; retry CLI update')
            if automatic and not preference(owner):
                owner._set(phase='checked',error=None,progress=None);return
            owner.idle_check()
            checked=version_info(value,folder=store.folder,strict=True)
            if pointer(candidate)!=pointer(checked):raise ValueError('Prepared CLI changed before activation')
            if pointer(current_info(folder=store.folder,strict=True))!=pointer(baseline):raise ValueError('Active native CLI changed during update')
            atomic(store.folder/'selection.json',dict(current=pointer(checked),previous=pointer(baseline),updated_at=time.time()))
        owner._set(phase='ready',available=False,newer_cli_available=False,error=None,progress=dict(stage='ready',received_bytes=candidate['archive_size'],total_bytes=candidate['archive_size']))
    finally:release(token,owner.folder)


def rollback(owner):
    from native_cli import current_info
    with owner.lock:
        if owner.thread and owner.thread.is_alive():raise ValueError('Wait for the CLI update to finish')
    with gate(owner.folder):
        saved=selection(owner.native_store.folder);previous=saved.get('previous')
        if not previous:raise ValueError('No prior managed native CLI version')
        token=reserve('switch',owner.folder)
        try:
            owner.idle_check();current=current_info(folder=owner.native_store.folder,strict=True)
            checked=version_info(previous['version'],folder=owner.native_store.folder,strict=True)
            if pointer(checked)!=previous:raise ValueError('Previous native CLI changed; rollback refused')
            atomic(owner.native_store.folder/'selection.json',dict(current=pointer(checked),previous=pointer(current),hold_version=current['version'],updated_at=time.time()))
        finally:release(token,owner.folder)
    owner._set(phase='ready',error=None,progress=None,available=False,newer_cli_available=False)
    return owner.snapshot()
