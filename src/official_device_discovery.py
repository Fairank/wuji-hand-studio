"""Official host-network discovery; no synthetic device identities."""
import json
import os
import subprocess
import sys
import time
import copy
import threading


def use_native(config):
    return (sys.platform=='win32' and config.get('mode')=='wsl'
            and config.get('cli','wuji') in ('wuji','/usr/local/bin/wuji'))


class DiscoveryDeferred(ValueError):
    """A finite read yielded to an explicitly requested device operation."""


class DiscoveryCleanupUnconfirmed(ValueError):
    """Keep ownership when the discovery child has not certainly exited."""

    def __init__(self,message,*,owned=None):
        super().__init__(message)
        # A failed close retains its private Job handle. Keep that exact owner
        # available for an explicit retry; never rediscover a process by PID.
        self.owned=owned


def _sdk_rows(rows):
    """Only typed SDK advertisements, never serial-number identity guesses."""
    from device_inventory import normalize_devices
    if not isinstance(rows,list) or len(rows)>512:raise ValueError('Invalid SDK discovery list')
    result=normalize_devices(rows,source='controller_discovery',observed_at=time.time())
    if (result['rejected'] or result['conflicts'] or len(result['devices'])!=len(rows)
            or any(row['kind_source']!='device' or row.get('conflict')
                   or row['kind'] not in ('hand','glove')
                   or row['kind']=='hand' and row['generation'] not in ('hand1','hand2')
                   for row in result['devices'])):
        raise ValueError('SDK discovery device identity was not typed and consistent')
    return copy.deepcopy(rows)


class _SDKDiscoveryRead:
    """One finite existing agent, retained on unconfirmed transport cleanup."""
    def __init__(self,factory,cancelled):
        self.factory=factory;self.cancelled=cancelled;self.stopped=threading.Event()
        self.finished=threading.Event();self.entered=threading.Event()
        self.lock=threading.RLock();self.close_lock=threading.Lock()
        self.client=None;self.thread=None;self.diagnostics=None
        self.transport_closed=True;self.error=None;self.result=None

    def _close_client(self):
        with self.close_lock:
            with self.lock:client=self.client
            try:closed=client is None or client.close() is not False
            except Exception:closed=False
            with self.lock:self.transport_closed=closed
            return closed

    def _read(self):
        self.entered.set()
        try:
            client=self.factory()
            with self.lock:self.client=client;self.transport_closed=False
            if self.stopped.is_set() or self.cancelled():raise DiscoveryDeferred('SDK discovery was cancelled')
            stdin,stdout,stderr=client.exec_command(client.agent_command,timeout=12)
            def drain():
                try:
                    while stderr.read(4096):pass
                except (OSError,ValueError,EOFError):pass
            self.diagnostics=threading.Thread(target=drain,name='SDK discovery diagnostics',daemon=True)
            self.diagnostics.start()
            stdin.write('{"name":"discover","include_gloves":true}\n');stdin.flush()
            total=0
            for line in stdout:
                if self.stopped.is_set() or self.cancelled():raise DiscoveryDeferred('SDK discovery was cancelled')
                total+=len(line)
                if total>2*1024*1024 or len(line)>262144:raise ValueError('SDK discovery output exceeded its limit')
                if not line.startswith('WUJI_JSON:'):continue
                def unique(pairs):
                    value={}
                    for key,item in pairs:
                        if key in value:raise ValueError('Duplicate SDK discovery field')
                        value[key]=item
                    return value
                event=json.loads(line[10:],object_pairs_hook=unique)
                if not isinstance(event,dict):raise ValueError('Invalid SDK discovery event')
                if event.get('type')=='discovery':
                    if self.result is not None:raise ValueError('Duplicate SDK discovery result')
                    self.result=dict(devices=_sdk_rows(event.get('devices')),observed_at=time.time())
                elif event.get('type') in ('error','notice'):
                    raise ValueError(str(event.get('message') or 'SDK discovery failed')[:500])
            if self.result is None:raise ValueError('SDK controller did not return a complete device directory')
        except Exception as error:self.error=error
        finally:
            self._close_client()
            if self.diagnostics is not None and self.diagnostics.ident is not None:self.diagnostics.join(.2)
            self.finished.set()

    def start(self):
        try:self.thread=threading.Thread(target=self._read,name='SDK read-only discovery',daemon=True)
        except Exception:
            self.finished.set()
            raise
        try:self.thread.start()
        except Exception:
            if not self.entered.is_set() and self.thread.ident is None and not self.thread.is_alive():
                self.finished.set()
            raise

    def close(self,timeout=2):
        self.stopped.set();closed=self._close_client()
        self.finished.wait(timeout)
        thread=self.thread
        if thread is not None and thread.ident is not None:thread.join(.1)
        diagnostics=self.diagnostics
        return bool(closed and self.finished.is_set() and (thread is None or not thread.is_alive())
                    and (diagnostics is None or not diagnostics.is_alive()) and self.transport_closed)


def sdk_devices(factory,*,cancelled=None,timeout=15):
    """Invoke original finite SDK discover; never create a connected workspace."""
    cancelled=cancelled or (lambda:False)
    if cancelled():raise DiscoveryDeferred('SDK discovery was cancelled')
    owned=_SDKDiscoveryRead(factory,cancelled)
    try:
        owned.start();deadline=time.monotonic()+timeout
        while not owned.finished.wait(.05):
            if cancelled():raise DiscoveryDeferred('SDK discovery was cancelled')
            if time.monotonic()>=deadline:raise ValueError('SDK discovery timed out')
        if cancelled():raise DiscoveryDeferred('SDK discovery was cancelled')
        if owned.error is not None:raise owned.error
        return copy.deepcopy(owned.result)
    finally:
        if not owned.close(timeout=2):
            raise DiscoveryCleanupUnconfirmed('SDK discovery transport exit is unconfirmed',owned=owned)


def sdk_preview_scan_allowed(state):
    """Pure eligibility only; the existing Bridge rechecks before dispatch."""
    from connection_switch import owner
    try:actual=owner(state)
    except (ValueError,TypeError,AttributeError):return False
    if not actual or actual['mode']!='teleoperation':return False
    glove=state.get('glove') or {};stream=glove.get('stream') or {};solver=stream.get('solver') or {}
    proof=glove.get('sdk_proof') or {};hardware=glove.get('hardware') or {}
    return bool(glove.get('busy') is True and glove.get('connection')=='receiving'
        and glove.get('can_prepare') is True and stream.get('fresh') is True
        and solver.get('engine')=='sdk' and solver.get('applied') is True
        and proof.get('available') is True and proof.get('api_compatible') is True
        and not glove.get('open_pending') and not glove.get('hand_unpairing')
        and not glove.get('follow_pending') and not glove.get('follow_unconfirmed')
        and not (glove.get('feedback') or {}).get('device_id')
        and hardware.get('active',False) is False and hardware.get('stop_confirmed') is not False)


def _cancellable_read(command,cancelled):
    from windows_process_job import OwnedProcess
    from native_environment import official_cli_environment
    owned=None
    try:
        if cancelled():raise DiscoveryDeferred('Device discovery paused for your operation')
        try:
            owned=OwnedProcess(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                text=True,encoding='utf-8',errors='replace',
                env=official_cli_environment())
        except RuntimeError as error:
            raise DiscoveryCleanupUnconfirmed('Discovery process startup exit is unconfirmed') from error
        deadline=time.monotonic()+20
        while True:
            if cancelled():raise DiscoveryDeferred('Device discovery paused for your operation')
            if time.monotonic()>=deadline:raise ValueError('Official discovery timed out')
            try:
                out,err=owned.process.communicate(timeout=.1)
                return subprocess.CompletedProcess(command,owned.process.returncode,out,err)
            except subprocess.TimeoutExpired:continue
    finally:
        # This private job contains only `devices`, never a Viewer or firmware
        # writer. A user action may cancel it, but must prove the whole read
        # tree exited before another owner is admitted.
        if owned is not None:
            try:closed=owned.close(timeout=2)
            except Exception as error:
                raise DiscoveryCleanupUnconfirmed('Discovery process exit is unconfirmed: '+str(error)[:400],owned=owned) from error
            if not closed:
                raise DiscoveryCleanupUnconfirmed('Discovery process exit is unconfirmed',owned=owned)


def native_devices(*,cancelled=None):
    from native_cli import prepare
    from native_environment import official_cli_environment
    component=prepare(strict=True)
    # A just-closed UDP preview can yield one empty discovery interval. Retry
    # once, never invent an identity or keep polling without an operator action.
    for attempt in range(2):
        command=[component['binary'],'--json','devices']
        if cancelled is None:
            result=subprocess.run(command,capture_output=True,
                text=True,encoding='utf-8',errors='replace',timeout=20,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
                env=official_cli_environment())
        else:result=_cancellable_read(command,cancelled)
        if result.returncode:raise ValueError((result.stderr or result.stdout or 'Official discovery failed')[:700])
        value=json.loads(result.stdout)
        if not isinstance(value,dict) or not isinstance(value.get('devices'),list):raise ValueError('Invalid official device list')
        if value['devices'] or attempt:return value
        time.sleep(.25)
