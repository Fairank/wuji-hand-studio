"""Official host-network discovery; no synthetic device identities."""
import json
import os
import subprocess
import sys
import time


def use_native(config):
    return (sys.platform=='win32' and config.get('mode')=='wsl'
            and config.get('cli','wuji') in ('wuji','/usr/local/bin/wuji'))


class DiscoveryDeferred(ValueError):
    """A finite read yielded to an explicitly requested device operation."""


class DiscoveryCleanupUnconfirmed(ValueError):
    """Keep ownership when the discovery child has not certainly exited."""


def _cancellable_read(command,cancelled):
    from windows_process_job import OwnedProcess
    owned=None
    try:
        if cancelled():raise DiscoveryDeferred('Device discovery paused for your operation')
        try:
            owned=OwnedProcess(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                text=True,encoding='utf-8',errors='replace',
                env={**os.environ,'WUJI_NO_UPDATE_CHECK':'1','WUJI_TELEMETRY_DISABLE':'1'})
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
        if owned is not None and not owned.close(timeout=2):
            raise DiscoveryCleanupUnconfirmed('Discovery process exit is unconfirmed')


def native_devices(*,cancelled=None):
    from native_cli import prepare
    component=prepare(strict=True)
    # A just-closed UDP preview can yield one empty discovery interval. Retry
    # once, never invent an identity or keep polling without an operator action.
    for attempt in range(2):
        command=[component['binary'],'--json','devices']
        if cancelled is None:
            result=subprocess.run(command,capture_output=True,
                text=True,encoding='utf-8',errors='replace',timeout=20,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
                env={**os.environ,'WUJI_NO_UPDATE_CHECK':'1','WUJI_TELEMETRY_DISABLE':'1'})
        else:result=_cancellable_read(command,cancelled)
        if result.returncode:raise ValueError((result.stderr or result.stdout or 'Official discovery failed')[:700])
        value=json.loads(result.stdout)
        if not isinstance(value,dict) or not isinstance(value.get('devices'),list):raise ValueError('Invalid official device list')
        if value['devices'] or attempt:return value
        time.sleep(.25)
