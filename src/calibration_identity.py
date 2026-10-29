"""Exact SDK wearer identity from its existing local registry; no SDK/device calls."""
import copy
import os
from pathlib import Path
import re
import stat
import tomllib

IDENT=re.compile(r'[A-Za-z0-9_.-]{1,100}\Z')
PROFILE=re.compile(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,31}\Z')


def sdk_user_identity(value, profile=None):
    if (not isinstance(value,dict) or not isinstance(value.get('user_id'),str)
            or not IDENT.fullmatch(value['user_id']) or not isinstance(value.get('display_name'),str)
            or not PROFILE.fullmatch(value['display_name']) or value['display_name'].lower()=='default'
            or 'is_default' in value and (type(value['is_default']) is not bool or value['is_default'])
            or profile is not None and value['display_name']!=profile):
        raise ValueError('佩戴者身份不一致，请重新选择并开始标定 / Calibration wearer identity differs; select the wearer and start again')
    return {key:value[key] for key in ('user_id','display_name')}


def require_sdk_user(value, expected, profile=None):
    actual=sdk_user_identity(value,profile)
    if actual!=sdk_user_identity(expected,profile):
        raise ValueError('佩戴者身份已改变，请重新开始标定 / Calibration wearer identity changed; start again')
    return copy.deepcopy(actual)


def read_current_sdk_user(profile, *, registry=None):
    """Snapshot the SDK's exact current ID after CLI profile selection.

    This versioned registry shape is checked strictly; it is never edited and
    names or directories are never substituted for the SDK-issued user ID.
    """
    if not isinstance(profile,str) or not PROFILE.fullmatch(profile) or profile.lower()=='default':
        raise ValueError('请选择命名佩戴者 / Select a named wearer')
    if registry is None:
        from native_environment import official_cli_environment
        environment=official_cli_environment()
        home=environment.get('HOME') or environment.get('USERPROFILE') or str(Path.home())
        registry=Path(home)/'.wuji/sdk/users/registry.toml'
    path=Path(registry)
    try:
        if not path.is_absolute() or any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (path,*path.parents)):
            raise ValueError('佩戴者注册资料路径不可核对 / SDK wearer registry path is unverified')
        before=path.stat()
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=65536:
            raise ValueError('佩戴者注册资料无效 / SDK wearer registry is invalid')
        with path.open('rb') as source:raw=source.read(65537)
        after=path.stat()
        key=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
        if key(before)!=key(after) or len(raw)!=before.st_size:
            raise ValueError('佩戴者注册资料已改变，请重新核对 / SDK wearer registry changed; review it again')
        value=tomllib.loads(raw.decode('utf8'))
        if not isinstance(value,dict) or set(value)!={'current_user_id','users'} or not isinstance(value['users'],list) or not 1<=len(value['users'])<=128:
            raise ValueError('佩戴者注册资料格式不可核对 / SDK wearer registry format is unverified')
        ids=set();names=set();selected=[]
        for user in value['users']:
            if (not isinstance(user,dict) or not isinstance(user.get('user_id'),str)
                    or not isinstance(user.get('display_name'),str) or type(user.get('is_default')) is not bool
                    or user['user_id'] in ids or user['display_name'] in names
                    or user['is_default']!=(user['user_id']=='')
                    or user['is_default'] and user['display_name']!='Default'):
                raise ValueError('佩戴者注册身份重复或无效 / SDK wearer registry identities are duplicated or invalid')
            ids.add(user['user_id']);names.add(user['display_name'])
            if not user['is_default']:
                if not IDENT.fullmatch(user['user_id']) or not 1<=len(user['display_name'])<=128 or any(ord(c)<32 for c in user['display_name']):
                    raise ValueError('佩戴者注册身份无效 / SDK wearer registry identity is invalid')
                if user['display_name']==profile:selected.append(sdk_user_identity(user,profile))
        if (not isinstance(value['current_user_id'],str) or value['current_user_id'] not in ids
                or len(selected)!=1 or selected[0]['user_id']!=value['current_user_id']):
            raise ValueError('当前佩戴者与所选档案不一致，请重新选择 / Current SDK wearer differs from the selected profile')
        return copy.deepcopy(selected[0])
    except (OSError,UnicodeError,tomllib.TOMLDecodeError) as error:
        raise ValueError('无法核对佩戴者注册身份，请刷新官方用户资料 / Cannot verify the SDK wearer registry; refresh official profiles') from error
