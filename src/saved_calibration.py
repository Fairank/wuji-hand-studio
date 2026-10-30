"""Transfer an existing official wearer without inventing a calibration result.

Only official local user reads/exports are used. The CLI's reported file name
identifies the model in the unmodified export; SDK preview/import subsequently
verifies its exact owner, component and bytes before any glove is opened.
"""
import copy
import hashlib
import json
from pathlib import Path,PurePosixPath
import re
import uuid

from calibration_identity import read_current_sdk_user,require_sdk_user,PROFILE
from calibration_portability import MAX_BUNDLE,MAX_MODEL,_private_directory,file_digest

SERIAL=re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z')


class SavedCalibrationUnavailable(ValueError):
    """A named wearer/side needs calibration; no source owner was released."""


def _check(callback):
    if callback is not None:callback()


def _command(command):
    if command is None:
        from native_cli import json_command
        return json_command
    return command


def _metadata(command,profile,side):
    from calibration_guided import validate_profile
    value=command('user','show')
    if profile is None:profile=value.get('name') if isinstance(value,dict) else None
    if not isinstance(profile,str) or not PROFILE.fullmatch(profile) or profile.lower()=='default':
        raise SavedCalibrationUnavailable('请先选择已标定的命名佩戴者，或进行标定 / Select a calibrated named wearer or calibrate first')
    validate_profile(value,profile,side,True)
    model=value[side+'_hand']
    if model['calibrated'] is not True:
        raise SavedCalibrationUnavailable('这位佩戴者尚无此侧标定，请先标定 / This wearer has no calibration for this side; calibrate first')
    name=model.get('file_name');size=model.get('size_bytes')
    if (not isinstance(name,str) or not name or len(name)>255 or '/' in name or '\\' in name
            or name in ('.','..') or any(ord(c)<32 for c in name)
            or type(size) is not int or not 0<size<=MAX_MODEL):
        raise ValueError('官方未提供可核对的已存手型文件，请刷新佩戴者资料 / Official saved model file metadata is unverified; refresh wearer data')
    return value,profile,name,size


def _archive(path,name,size):
    from calibration_backups import archive_signature
    digest,length=file_digest(path,MAX_BUNDLE);members=archive_signature(path)
    matches=[key for key in members if PurePosixPath(key).name==name]
    if len(matches)!=1 or members[matches[0]]['bytes']!=size:
        raise ValueError('官方导出未唯一包含所选已存手型 / Official export does not uniquely contain the selected saved model')
    member=matches[0];row=members[member]
    # The original ZIP remains intact. No vendor metadata is reconstructed.
    if file_digest(path,MAX_BUNDLE)!=(digest,length):raise ValueError('Saved calibration archive changed during verification')
    return dict(bundle_path=str(path),bundle_sha256=digest,bundle_bytes=length,
                model_sha256=row['sha256'],model_bytes=row['bytes'],_model_member=member,_members=members)


def _identity(proof):
    keys=('source_kind','transfer_id','source_run_id','source_workspace','serial','side','source_profile',
          'source_user_id','model_sha256','model_bytes','bundle_sha256','bundle_bytes','_model_member','_members','_metadata')
    try:return {k:proof[k] for k in keys}
    except (KeyError,TypeError):raise ValueError('Saved calibration transfer proof is incomplete') from None


def _revision(proof):
    return hashlib.sha256(json.dumps(_identity(proof),sort_keys=True,separators=(',',':')).encode()).hexdigest()


def preflight_saved_profile(serial,side,profile=None,folder=None,*,json_command=None,registry=None,workspace='main',cancel_check=None):
    """Pin a real saved profile while the caller retains the original owner.

    The shared workflow/file gate is held by the caller. Returned paths and
    proof remain server private and are never accepted from a browser.
    """
    if (not isinstance(serial,str) or not SERIAL.fullmatch(serial) or side not in ('left','right')
            or not isinstance(workspace,str) or not 1<=len(workspace)<=100):raise ValueError('Invalid saved calibration target')
    command=_command(json_command);_check(cancel_check)
    metadata,profile,name,size=_metadata(command,profile,side)
    identity=read_current_sdk_user(profile,registry=registry)
    if folder is None:
        from cli_coordination import root_folder
        folder=root_folder()/'saved-calibration'
    root=_private_directory(folder).absolute();target=root/(uuid.uuid4().hex+'.zip')
    _check(cancel_check);command('user','export',str(target));_check(cancel_check)
    archive=_archive(target,name,size)
    require_sdk_user(read_current_sdk_user(profile,registry=registry),identity,profile)
    after,_,_,_=_metadata(command,profile,side)
    if after!=metadata:raise ValueError('已存佩戴者资料在导出时已改变，请重试 / Saved wearer changed during export; retry')
    _check(cancel_check)
    proof=dict(source_kind='saved_profile',transfer_id=uuid.uuid4().hex,source_run_id=None,
               serial=serial,side=side,source_profile=profile,source_user_id=identity['user_id'],
               source_workspace=workspace,_metadata=copy.deepcopy(metadata),**archive)
    proof['expected_revision']=_revision(proof)
    return proof


def verify_saved_snapshot(proof,*,json_command=None,registry=None,cancel_check=None):
    """Re-export current official data to detect changes, including same size.

    This does not create a new calibration proof, mutate any SDK user, connect
    a device or grant write/follow permission. Readback exports are temporary.
    """
    from calibration_portability import CONTEXT_FIELDS,validate_continuation
    if not isinstance(proof,dict) or proof.get('source_kind')!='saved_profile' or proof.get('expected_revision')!=_revision(proof):
        raise ValueError('Saved calibration proof changed; select the wearer again')
    context={key:proof[key] for key in CONTEXT_FIELDS|{'source_kind','transfer_id','bundle_path'}}
    validate_continuation(context,path=True)
    command=_command(json_command);_check(cancel_check)
    expected=dict(user_id=proof['source_user_id'],display_name=proof['source_profile'])
    require_sdk_user(read_current_sdk_user(proof['source_profile'],registry=registry),expected,proof['source_profile'])
    metadata,_,name,size=_metadata(command,proof['source_profile'],proof['side'])
    original=_archive(Path(proof['bundle_path']),name,size)
    if (metadata!=proof['_metadata'] or any(original[key]!=proof[key] for key in original)):
        raise ValueError('已存标定资料已改变，请重新选择 / Saved calibration changed; select again')
    path=Path(proof['bundle_path']).parent/(uuid.uuid4().hex+'.readback.zip')
    try:
        _check(cancel_check);command('user','export',str(path));_check(cancel_check)
        current=_archive(path,name,size)
        require_sdk_user(read_current_sdk_user(proof['source_profile'],registry=registry),expected,proof['source_profile'])
        after,_,_,_=_metadata(command,proof['source_profile'],proof['side'])
        # Fresh exports may change opaque packaging metadata (for example an
        # export timestamp). The original archive is still pinned in full;
        # only the actual selected model identifies this readback. Existing
        # controller components retain prepare_controller_model's own checks.
        if (after!=metadata or current['_model_member']!=proof['_model_member']
                or (current['model_sha256'],current['model_bytes'])!=(proof['model_sha256'],proof['model_bytes'])):
            raise ValueError('已存标定内容已改变，请重新选择 / Saved calibration content changed; select again')
        _check(cancel_check)
    finally:
        try:path.unlink(missing_ok=True)
        except OSError:raise ValueError('标定资料核对临时文件清理未确认 / Saved calibration readback cleanup is unconfirmed') from None
    return True


def prepare_saved_profile(proof,*,json_command=None,registry=None,cancel_check=None):
    verify_saved_snapshot(proof,json_command=json_command,registry=registry,cancel_check=cancel_check)
    return copy.deepcopy(proof)
