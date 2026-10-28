"""Inspect bundled official CLI/SDK software without opening a device.

Installation uses managed_runtime's verified controller image. A saved install
marker is never presented as this check's actual version/import result.
"""
import copy
import json
import re
import subprocess
import sys
import threading
import time

SDK_CHECK = "import importlib.metadata,json,wuji_sdk;print(json.dumps({'sdk_version':importlib.metadata.version('wuji-sdk'),'sdk_imported':True}))"


def target_versions():
    from managed_runtime import image_manifest
    from official_cli import selected
    if sys.platform=='darwin':
        from macos_runtime import manifest as mac_manifest
        try:manifest=mac_manifest()
        except (OSError,ValueError):manifest={}
        # The original Mac payload pins this CLI archive but omitted its label.
        # Limit the legacy fallback to that exact payload, not future releases.
        if manifest.get('payload_id')=='mac-linux-1.1.0' and not manifest.get('cli_version'):
            manifest={**manifest,'cli_version':'2026.10.12'}
    else:manifest=image_manifest() or {}
    current=selected().get('current') or {}
    target={key:manifest.get(key) for key in ('cli_version','sdk_version')}
    if sys.platform!='darwin' and current.get('validated') and current.get('mode')=='wsl':target['cli_version']=current.get('version')
    return target


class OfficialTools:
    def __init__(self):
        self.lock=threading.RLock()
        self.check=dict(busy=False,status='unchecked',cli_version=None,sdk_version=None,
                        error=None,checked_at=None,available=False,compatible=False)

    def snapshot(self):
        from managed_runtime import status
        target=target_versions()
        with self.lock:
            return dict(target=target,
                        runtime=status(),check=copy.deepcopy(self.check))

    def start_check(self):
        from managed_runtime import status
        runtime=status()
        if runtime.get('busy'):raise ValueError('请等待内置组件安装完成 / Wait for the built-in component installation')
        if not runtime.get('supported') or not runtime.get('installed'):
            raise ValueError('请先安装内置官方组件 / Install the bundled official components first')
        with self.lock:
            if self.check['busy']:raise ValueError('组件检查正在进行 / Component check already running')
            self.check.update(busy=True,status='checking',error=None,available=False,compatible=False,
                              cli_version=None,sdk_version=None,checked_at=None)
        threading.Thread(target=self.run,name='Official CLI SDK software check',daemon=True).start()
        return self.snapshot()

    def run(self):
        from managed_runtime import args,PYTHON,CLI,_decode
        flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
        versions={}
        try:
            mode='macvm' if sys.platform=='darwin' else 'wsl'
            config=dict(mode=mode,cli=CLI)
            if mode=='macvm':
                from calibration_cli import controller_args
                runtime_args=lambda command:controller_args(config,command)
            else:runtime_args=args
            # Neither command scans, connects, subscribes or sends motor data.
            sdk=subprocess.run(runtime_args([PYTHON,'-c',SDK_CHECK]),capture_output=True,timeout=30,creationflags=flags)
            if sdk.returncode:raise ValueError('SDK 导入检查失败 / SDK import check failed')
            parsed=json.loads(_decode(sdk.stdout))
            sdk_version=parsed.get('sdk_version')
            if parsed.get('sdk_imported') is not True or not isinstance(sdk_version,str) or not re.fullmatch(r'[0-9A-Za-z.+_-]{1,80}',sdk_version):
                raise ValueError('SDK 版本输出无效 / Invalid SDK version result')
            versions['sdk_version']=sdk_version
            from official_cli import cli_path
            actual_cli=cli_path(config)
            cli=subprocess.run(runtime_args(['/usr/bin/env','WUJI_NO_UPDATE_CHECK=1',actual_cli,'--version']),capture_output=True,timeout=20,creationflags=flags)
            if cli.returncode:raise ValueError('CLI 版本检查失败 / CLI version check failed')
            match=re.search(r'\b(20\d{2}\.\d{1,2}\.\d{1,2}(?:[.+-][\w.-]+)?)\b',_decode(cli.stdout))
            if not match:raise ValueError('CLI 版本输出无效 / Invalid CLI version result')
            versions['cli_version']=match[1]
            target=target_versions()
            # This legacy control-image check is a baseline/repair check, not
            # the public stable SDK selector. Never offer a bundled downgrade
            # merely because the verified SDK maintenance installed a newer
            # stable release. Session startup still checks the exact selected
            # stable SDK and actual worker API through sdk_runtime.
            from official_cli import version_order
            compatible=(versions.get('cli_version')==target.get('cli_version')
                and version_order(versions.get('sdk_version'))>=version_order(target.get('sdk_version')))
            with self.lock:self.check.update(**versions,status='ready',available=True,compatible=compatible,error=None)
        except Exception as error:
            detail=str(error) if isinstance(error,ValueError) else '软件检查未完成，请重试 / Software check did not complete; retry'
            with self.lock:self.check.update(**versions,status='failed',available=False,compatible=False,error=detail)
        finally:
            with self.lock:self.check.update(busy=False,checked_at=time.time())
