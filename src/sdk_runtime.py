"""Software-only evidence from the actual SDK interpreter, never SdkManager.instance()."""
import hashlib
import importlib
import importlib.metadata
from pathlib import Path
import platform
import re
import sys

PROOF_VERSION=1
_APIS={'SdkManager':('instance','scan','connect','list_users','current_user','switch_user','preview_user_data','import_user_data','export_user_data'),
       'RetargetSession':('for_hand','step'),'HandModel':('WujiHand','WujiHand2'),
       'Handedness':('Left','Right'),'WujiGlove':('hand_skeleton','hand_side','hand_model_path'),
       'HandModelPathResource':('get','set'),'WujiException':(),
       'WujiHand2':('joint_states','joint_command','joint_diagnostics','enable','disable')}


def interpreter_facts():
    return dict(executable=sys.executable,platform=sys.platform,implementation=sys.implementation.name,
                python_tag=f'cp{sys.version_info.major}{sys.version_info.minor}',
                machine=platform.machine().lower(),libc=list(platform.libc_ver()))


def probe_runtime():
    """Import only. Do not construct retargeters, managers, subscribers or devices."""
    result=dict(proof_version=PROOF_VERSION,**interpreter_facts(),available=False,api_compatible=False)
    try:
        sdk=importlib.import_module('wuji_sdk');distribution=importlib.metadata.distribution('wuji-sdk')
        importlib.import_module('numpy')
        # Existing controller transport also requires zenoh; import verifies ABI,
        # but opening a zenoh session is expressly outside this probe.
        importlib.import_module('zenoh')
        missing=[]
        for owner,members in _APIS.items():
            cls=getattr(sdk,owner,None)
            if cls is None:missing.append(owner);continue
            if owner=='WujiException':
                if not isinstance(cls,type) or not issubclass(cls,Exception):missing.append(owner)
                continue
            # Read class descriptors only; probing must never construct a
            # manager, model resource or retargeter. Enum values are data;
            # every other required member is an actual callable operation.
            missing.extend(owner+'.'+name for name in members if not hasattr(cls,name)
                or owner not in ('HandModel','Handedness') and not callable(getattr(cls,name)))
        filename=Path(sdk.__file__).resolve()
        if not filename.is_file():raise ValueError('SDK module has no importable file')
        if filename.parent!=Path(distribution.locate_file('wuji_sdk')).resolve():
            raise ValueError('SDK import path differs from installed distribution')
        native={}
        for relative in distribution.files or []:
            if relative.parts[0] in ('wuji_sdk','wuji_sdk.libs') and '.so' in relative.name:
                path=Path(distribution.locate_file(relative)).resolve()
                if not path.is_relative_to(filename.parent.parent) or not path.is_file():
                    raise ValueError('SDK native library path is invalid')
                native[str(relative)]=hashlib.sha256(path.read_bytes()).hexdigest()
        if not any(name.startswith('wuji_sdk/') for name in native):raise ValueError('SDK native library is missing')
        result.update(available=True,version=distribution.version,import_path=str(filename),
                      module_sha256=hashlib.sha256(filename.read_bytes()).hexdigest(),
                      native_sha256=native,api_compatible=not missing,missing_api=missing)
    except Exception:
        result['error']='SDK 或依赖无法加载，请在设置中修复官方组件 / Repair official SDK components in Settings'
    return result


def verify_proof(proof,expected):
    from sdk_release import validate_requirement
    expected=validate_requirement(expected)
    if (not isinstance(proof,dict) or type(proof.get('proof_version')) is not int or proof.get('proof_version')!=PROOF_VERSION
            or proof.get('available') is not True or proof.get('api_compatible') is not True
            or proof.get('platform')!='linux' or proof.get('implementation')!='cpython'
            or not isinstance(proof.get('module_sha256'),str) or not re.fullmatch(r'[0-9a-f]{64}',proof['module_sha256'])
            or not isinstance(proof.get('native_sha256'),dict) or not proof['native_sha256']
            or any(not isinstance(key,str) or not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{64}',value) for key,value in proof.get('native_sha256',{}).items())
            or not proof.get('executable') or not proof.get('import_path')):
        raise ValueError('控制端未通过 SDK 检查，请在设置中修复组件；外部控制端需更新官方 SDK / Repair SDK components in Settings or update the external controller SDK')
    if proof.get('version')!=expected['version']:
        raise ValueError('控制端 SDK 不是已核定的官方稳定版 '+expected['version']+'，请在设置中更新组件；外部控制端需更新对应 Python 环境 / Update the actual controller SDK environment to '+expected['version'])
    return dict(proof)
