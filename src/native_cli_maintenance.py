"""Idle-only native CLI maintenance, sharing the existing workspace reservation."""
import copy
import threading
import time
from cli_coordination import atomic,gate,reserve,release,reservation
from native_cli_update import selection,pointer,version_info,read_json


class CLIReleaseUnconfirmed(RuntimeError):
    """This completed software operation still owns its exact switch lease."""


def switch_marker(owner):
    # reservation() intentionally hides dead generic switch markers. Recovery
    # must compare the original bytes' object, including a changed PID/start.
    try:return read_json(owner.folder/'session.json')
    except FileNotFoundError:return {}


def reserve_switch(owner,operation='update'):
    if getattr(owner,'_maintenance_marker',None) is not None:
        raise CLIReleaseUnconfirmed('CLI 占用释放尚未确认，请明确重试 / CLI reservation release is unconfirmed; retry explicitly')
    token=reserve('switch',owner.folder)
    with owner.lock:
        owner._maintenance_marker=copy.deepcopy(reservation(owner.folder))
        owner._maintenance_outcome=dict(operation=operation,applied=None)
    return token


def switch_applied(owner,target):
    with owner.lock:owner._maintenance_outcome['applied']=copy.deepcopy(target)


def release_switch(owner,token):
    """Retain the exact receipt on failure; never clear a replacement owner."""
    try:
        with gate(owner.folder):
            with owner.lock:expected=copy.deepcopy(owner._maintenance_marker)
            if not expected or expected.get('token')!=token or expected.get('kind')!='switch':
                raise ValueError('CLI cleanup owner is unverified')
            current=switch_marker(owner)
            if current and current!=expected:raise ValueError('CLI cleanup owner changed')
            if current:release(token,owner.folder)
            if switch_marker(owner):raise ValueError('CLI reservation remains unconfirmed')
            with owner.lock:
                if owner._maintenance_marker!=expected:raise ValueError('CLI cleanup owner changed')
                owner._maintenance_marker=None
                owner._maintenance_outcome=None
                recovering=owner.state.get('recovery_required') is True
                owner.state['recovery_required']=False
                if recovering:
                    owner.state['error']=None
                    if owner.closed.is_set():owner.state['phase']='cancelled'
    except Exception as error:
        detail='CLI 更新已结束，但占用尚未释放；请检查或重试 / CLI maintenance ended but reservation release is unconfirmed; check or retry: '+str(error)[:350]
        owner._set(phase='error',recovery_required=True,error=detail)
        raise CLIReleaseUnconfirmed(detail) from error


def retry_release(owner,*,automatic=False):
    with owner.lock:
        pending=copy.deepcopy(getattr(owner,'_maintenance_marker',None))
        worker=owner.thread
        required=owner.state.get('recovery_required') is True
        outcome=copy.deepcopy(getattr(owner,'_maintenance_outcome',None))
    if pending is None:return
    if not required or automatic:
        raise CLIReleaseUnconfirmed('CLI 占用释放尚未确认，请明确重试 / CLI reservation release is unconfirmed; retry explicitly')
    if worker is not None and worker is not threading.current_thread() and worker.is_alive():
        raise ValueError('Wait for the original CLI maintenance worker to finish')
    release_switch(owner,pending.get('token'))
    return outcome


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
        with owner.lock:busy=bool(owner.thread and owner.thread.is_alive()) or owner.state.get('recovery_required') is True
        if not busy:owner.start(check_only=False,automatic=True)
    return owner.snapshot()


def run(owner,check_only,automatic):
    from native_cli import current_info,prepare,PINNED_VERSION
    from official_cli import version_order
    from bridge_config import load_config
    from official_device_discovery import use_native
    retry_release(owner,automatic=automatic)
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
                token=reserve_switch(owner)
                try:owner.idle_check()
                except BaseException:release_switch(owner,token);token=None;raise
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
            switch_applied(owner,pointer(checked))
        owner._set(phase='ready',available=False,newer_cli_available=False,error=None,progress=dict(stage='ready',received_bytes=candidate['archive_size'],total_bytes=candidate['archive_size']))
    finally:release_switch(owner,token)


def rollback(owner):
    from native_cli import current_info
    with owner.lock:
        if owner.thread and owner.thread.is_alive():raise ValueError('Wait for the CLI update to finish')
    recovered=retry_release(owner)
    if recovered and recovered.get('operation')=='rollback' and recovered.get('applied') is not None:
        if pointer(current_info(folder=owner.native_store.folder,strict=True))!=recovered['applied']:
            raise ValueError('CLI rollback target changed; check the current version before retrying')
        owner._set(phase='ready',error=None,progress=None,available=False,newer_cli_available=False)
        return owner.snapshot()
    with gate(owner.folder):
        saved=selection(owner.native_store.folder);previous=saved.get('previous')
        if not previous:raise ValueError('No prior managed native CLI version')
        token=reserve_switch(owner,'rollback')
        try:
            owner.idle_check();current=current_info(folder=owner.native_store.folder,strict=True)
            checked=version_info(previous['version'],folder=owner.native_store.folder,strict=True)
            if pointer(checked)!=previous:raise ValueError('Previous native CLI changed; rollback refused')
            atomic(owner.native_store.folder/'selection.json',dict(current=pointer(checked),previous=pointer(current),hold_version=current['version'],updated_at=time.time()))
            switch_applied(owner,pointer(checked))
        finally:release_switch(owner,token)
    owner._set(phase='ready',error=None,progress=None,available=False,newer_cli_available=False)
    return owner.snapshot()
