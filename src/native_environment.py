"""Environment for native official CLI children; no mutation or device I/O."""
import os
from pathlib import Path
import sys


def official_cli_environment(base=None):
    """Use one SDK home for profiles, calibration, viewing and firmware.

    Windows normally supplies USERPROFILE rather than HOME, while some CLI
    operations require HOME. An explicitly selected absolute HOME wins. Never
    substitute app data or an unrelated temporary software-check profile.
    Callers that verify software can supply their already isolated environment.
    """
    env=dict(os.environ if base is None else base)
    env.update(WUJI_NO_UPDATE_CHECK='1',WUJI_TELEMETRY_DISABLE='1')
    if sys.platform=='win32':
        home=env.get('HOME')
        if not home:
            home=env.get('USERPROFILE') or str(Path.home())
        if not isinstance(home,str) or '\x00' in home or not Path(home).is_absolute():
            raise ValueError('用户目录必须是有效绝对路径 / User home must be an absolute path')
        env['HOME']=home
    return env
