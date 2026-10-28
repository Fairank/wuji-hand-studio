"""Official host-network discovery; no synthetic device identities."""
import json
import os
import subprocess
import sys
import time


def use_native(config):
    return (sys.platform=='win32' and config.get('mode')=='wsl'
            and config.get('cli','wuji') in ('wuji','/usr/local/bin/wuji'))


def native_devices():
    from native_cli import prepare
    component=prepare(strict=True)
    # A just-closed UDP preview can yield one empty discovery interval. Retry
    # once, never invent an identity or keep polling without an operator action.
    for attempt in range(2):
        result=subprocess.run([component['binary'],'--json','devices'],capture_output=True,
            text=True,encoding='utf-8',errors='replace',timeout=20,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
            env={**os.environ,'WUJI_NO_UPDATE_CHECK':'1','WUJI_TELEMETRY_DISABLE':'1'})
        if result.returncode:raise ValueError((result.stderr or result.stdout or 'Official discovery failed')[:700])
        value=json.loads(result.stdout)
        if not isinstance(value,dict) or not isinstance(value.get('devices'),list):raise ValueError('Invalid official device list')
        if value['devices'] or attempt:return value
        time.sleep(.25)
