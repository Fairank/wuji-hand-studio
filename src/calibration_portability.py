"""Official calibration bundle transport; no device or motor command.

Windows exports with the pinned CLI. The configured controller validates and
imports with the already installed SDK, retaining its owner ID. ZIP contents
are never reconstructed into a substitute profile format.
"""
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import uuid
import zipfile

MAX_BUNDLE = 256 * 1024 * 1024
MAX_MODEL = 4 * 1024 * 1024
IDENT = re.compile(r'[A-Za-z0-9_.-]{1,100}\Z')
PROFILE = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,31}\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')
RUN = re.compile(r'[0-9a-f]{32}\Z')


def controller_transfer_directory(source_dir=None):
    base=PurePosixPath(str(source_dir or Path(__file__).resolve().parent).replace('\\','/'))
    if base.parent.name=='code' and re.fullmatch('[0-9a-f]{16}',base.name):base=base.parent.parent
    return (base/'calibration-transfer').as_posix()


def _private_directory(path):
    root=Path(path)
    if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (root,*root.parents)):
        raise ValueError('Calibration data directory is redirected')
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    if os.name=='posix':
        if root.stat().st_uid!=os.getuid():raise ValueError('Calibration data directory has another owner')
        root.chmod(0o700)
        if root.stat().st_mode & 0o077:raise ValueError('Calibration data directory is not private')
    return root


def prepare_transfer_directory(source_dir=None):
    """Called by the controller before advertising continuation support."""
    try:return str(_private_directory(controller_transfer_directory(source_dir)))
    except OSError:raise ValueError('Controller calibration transfer directory is unavailable') from None


def _check(check):
    if check is not None:check()


def file_digest(path, limit=MAX_MODEL):
    try:return _file_digest(path,limit)
    except (OSError,TypeError):raise ValueError('标定文件不存在或暂不可读 / Calibration artifact is missing or unreadable') from None


def _file_digest(path, limit):
    """Read one exact regular file, rejecting redirected or changing paths."""
    p=Path(path)
    if not p.is_absolute():raise ValueError('Calibration artifact path is not absolute')
    for part in (p,*p.parents):
        if part.is_symlink() or getattr(part,'is_junction',lambda:False)():
            raise ValueError('Calibration artifact path is redirected')
    before=p.stat()
    if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=limit:
        raise ValueError('Calibration artifact is missing or exceeds its limit')
    digest=hashlib.sha256();total=0
    with p.open('rb') as source:
        for block in iter(lambda:source.read(64*1024),b''):
            total+=len(block)
            if total>limit:raise ValueError('Calibration artifact exceeds its limit')
            digest.update(block)
    after=p.stat()
    key=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
    if key(before)!=key(after) or total!=before.st_size:
        raise ValueError('Calibration artifact changed while being checked')
    return digest.hexdigest(),total


def calibration_proof(snapshot, *, workspace='main'):
    """Accept only the current, strict, fully cleaned guided native result."""
    from native_cli import PINNED_VERSION
    run=snapshot.get('run') if isinstance(snapshot,dict) else None
    if (not isinstance(run,dict) or snapshot.get('cleanup_required') is True
            or run.get('running') is not False or run.get('status')!='completed'
            or type(run.get('exit_code')) is not int or run['exit_code']!=0
            or run.get('cleanup_confirmed') is not True or run.get('error')
            or run.get('source')!='official_jsonl_native'
            or not isinstance(run.get('cli_version'),str)
            or not isinstance(run.get('id'),str) or not RUN.fullmatch(run['id'])):
        raise ValueError('请先完成并确认本次引导标定 / Finish and verify this guided calibration first')
    # Retained successful runs keep their original component identity even
    # after a newer CLI becomes current. Legacy bundled-pin runs remain valid.
    if run['cli_version']!=PINNED_VERSION or run.get('cli_binary_sha256'):
        from native_cli_update import version_info
        component=version_info(run['cli_version'],strict=True)
        if (run.get('cli_binary_sha256')!=component['binary_sha256']
                or run.get('cli_archive_sha256')!=component['archive_sha256']):
            raise ValueError('Calibration component identity could not be verified')
    profile=run.get('user');serial=run.get('serial');side=run.get('side')
    event=run.get('result');result=event.get('result') if isinstance(event,dict) else None
    if (not isinstance(profile,str) or not PROFILE.fullmatch(profile) or profile.lower()=='default'
            or not isinstance(serial,str) or not IDENT.fullmatch(serial)
            or side not in ('left','right') or run.get('expected_side') not in (None,side)
            or not isinstance(result,dict) or event.get('event')!='result'
            or type(event.get('schema_version')) is not int or event['schema_version']!=2
            or event.get('calibration')!='hand_model' or event.get('operation')!='calibrate'
            or event.get('status')!='ok'
            or result.get('handedness')!=side or not isinstance(result.get('calibrated_urdf'),str)):
        raise ValueError('本次标定的设备、佩戴者或手型结果不一致 / Calibration device, wearer or model result differs')
    from calibration_guided import validate_result
    identity=validate_result(event,serial,side,profile,run.get('sdk_user'))
    for row in (event,result,event.get('device') or {},result.get('device') or {}):
        if not isinstance(row,dict):raise ValueError('Invalid calibration device context')
        if any(row.get(k) is not None and row[k]!=serial for k in ('sn','serial')):
            raise ValueError('Calibration result belongs to another glove')
    path=Path(result['calibrated_urdf'])
    digest,size=file_digest(path)
    if (not isinstance(run.get('model_sha256'),str) or not HASH.fullmatch(run['model_sha256'])
            or type(run.get('model_bytes')) is not int
            or (digest,size)!=(run['model_sha256'],run['model_bytes'])):
        raise ValueError('保存的手型与标定结束时不一致，请重新核对 / Saved hand model differs from the verified calibration output')
    proof=dict(source_run_id=run['id'],serial=serial,side=side,source_profile=profile,source_user_id=identity['user_id'],
        source_workspace=str(workspace),model_sha256=digest,model_bytes=size,model_path=str(path),source_cli_version=run['cli_version'])
    proof['expected_revision']=hashlib.sha256(json.dumps(proof,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return proof


def export_native_bundle(proof, folder, *, json_command=None, cancel_check=None):
    """Export the exact completed profile to an app-owned, unique local ZIP."""
    if json_command is None:
        from native_cli import json_command
    from calibration_guided import validate_profile
    from calibration_identity import read_current_sdk_user,require_sdk_user
    expected=dict(user_id=proof['source_user_id'],display_name=proof['source_profile'])
    def official(*args):
        try:return json_command(*args)
        except Exception:raise ValueError('官方标定资料读取或导出失败，未接入手套；请检查官方组件和佩戴者 / Official calibration data could not be read or exported; glove was not opened. Check the official component and wearer') from None
    _check(cancel_check)
    current=official('user','show')
    validate_profile(current,proof['source_profile'],proof['side'],True)
    require_sdk_user(read_current_sdk_user(proof['source_profile']),expected,proof['source_profile'])
    if file_digest(proof['model_path'])!=(proof['model_sha256'],proof['model_bytes']):
        raise ValueError('保存的手型已改变，请重新核对标定 / Saved hand model changed; review calibration')
    root=Path(folder).absolute()
    if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (root,*root.parents)):
        raise ValueError('Calibration transfer directory is redirected')
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    target=root/(uuid.uuid4().hex+'.zip')
    _check(cancel_check)
    official('user','export',str(target))
    _check(cancel_check)
    # An external CLI can change its current profile outside our application.
    validate_profile(official('user','show'),proof['source_profile'],proof['side'],True)
    require_sdk_user(read_current_sdk_user(proof['source_profile']),expected,proof['source_profile'])
    if file_digest(proof['model_path'])!=(proof['model_sha256'],proof['model_bytes']):
        raise ValueError('Hand model changed during export; no controller was opened')
    digest,size=file_digest(target,MAX_BUNDLE)
    if not zipfile.is_zipfile(target):raise ValueError('Official export did not produce a calibration ZIP')
    return dict(proof,bundle_path=str(target),bundle_sha256=digest,bundle_bytes=size)


CONTEXT_FIELDS={'source_run_id','source_workspace','serial','side','source_profile','source_user_id',
                'model_sha256','model_bytes','bundle_sha256','bundle_bytes'}


def continuation_source(value):
    """Keep saved-profile identity separate from a completed calibration run."""
    return ({key:value[key] for key in ('source_kind','transfer_id')}
            if value.get('source_kind')=='saved_profile' else {})


def model_provenance(context,owner_user_id,imported):
    return dict({key:context[key] for key in ('source_run_id','source_workspace','source_profile','serial','side','model_sha256')},
                owner_user_id=owner_user_id,imported=imported,**continuation_source(context))


def validate_continuation(value, *, path=False):
    required=CONTEXT_FIELDS|({'bundle_path'} if path else set())
    if not isinstance(value,dict) or not required<=set(value) or set(value)-required-{'glove_route','source_kind','transfer_id'}:raise ValueError('Invalid private calibration continuation')
    saved=value.get('source_kind')=='saved_profile'
    if (saved and (value['source_run_id'] is not None or not isinstance(value.get('transfer_id'),str) or not RUN.fullmatch(value['transfer_id']))
            or not saved and ('source_kind' in value or 'transfer_id' in value
                or not isinstance(value['source_run_id'],str) or not RUN.fullmatch(value['source_run_id']))):
        raise ValueError('Invalid private calibration source identity')
    if (not isinstance(value['serial'],str) or not IDENT.fullmatch(value['serial'])
            or not isinstance(value['source_workspace'],str) or not 1<=len(value['source_workspace'])<=100
            or value['side'] not in ('left','right') or not isinstance(value['source_profile'],str)
            or not PROFILE.fullmatch(value['source_profile']) or value['source_profile'].lower()=='default'
            or not isinstance(value['source_user_id'],str) or not IDENT.fullmatch(value['source_user_id'])
            or any(not isinstance(value[k],str) or not HASH.fullmatch(value[k]) for k in ('model_sha256','bundle_sha256'))
            or type(value['model_bytes']) is not int or not 0<value['model_bytes']<=MAX_MODEL
            or type(value['bundle_bytes']) is not int or not 0<value['bundle_bytes']<=MAX_BUNDLE):
        raise ValueError('Invalid private calibration identity')
    if path and (not isinstance(value['bundle_path'],str) or len(value['bundle_path'])>1024
                 or not Path(value['bundle_path']).is_absolute()):
        raise ValueError('Invalid private calibration bundle path')
    if 'glove_route' in value:
        from calibration_transport import validate_glove_route
        validate_glove_route(value['glove_route'],value['serial'])
    if len(json.dumps(value).encode())>4096:raise ValueError('Private calibration context exceeds its limit')
    return copy.deepcopy(value)


def _components(report):
    rows=report.get('components') if isinstance(report,dict) else None
    if not isinstance(rows,list) or len(rows)>512:raise ValueError('Official SDK returned incomplete calibration contents')
    present={}
    for row in rows:
        if not isinstance(row,dict) or row.get('state') not in ('present','missing','skipped'):
            raise ValueError('Invalid official SDK calibration component')
        name=row.get('name');sn=row.get('device_sn') or ''
        if not isinstance(name,str) or not isinstance(sn,str):raise ValueError('Invalid calibration component identity')
        if row['state']=='present':
            if not isinstance(row.get('path'),str) or not row['path']:
                raise ValueError('Official SDK did not identify a present calibration component path')
            key=name+'|'+sn
            if key in present:raise ValueError('Duplicate calibration component')
            present[key]=row
    return present


def _bundle_model(path, row):
    """Read the SDK-validated reported member; never guess a ZIP manifest."""
    member=row.get('path')
    if not isinstance(member,str):raise ValueError('SDK preview did not identify the hand model file')
    normalized=PurePosixPath(member)
    if normalized.is_absolute() or '..' in normalized.parts or '\\' in member:
        raise ValueError('SDK preview returned an invalid model member')
    with zipfile.ZipFile(path) as archive:
        names=archive.namelist()
        if len(names)>4096 or len(names)!=len(set(names)):raise ValueError('Invalid calibration ZIP entries')
        info=archive.getinfo(member)
        if info.is_dir() or not 0<info.file_size<=MAX_MODEL:raise ValueError('Calibration model exceeds its limit')
        with archive.open(info) as source:content=source.read(MAX_MODEL+1)
    if len(content)!=info.file_size:raise ValueError('Calibration model member is incomplete')
    return hashlib.sha256(content).hexdigest(),len(content)


def _bundle_signature(path,row):
    member=row.get('path')
    if not isinstance(member,str) or not member:raise ValueError('SDK preview did not identify a component path')
    relative=PurePosixPath(member)
    if relative.is_absolute() or '..' in relative.parts or '\\' in member:raise ValueError('Invalid SDK component path')
    with zipfile.ZipFile(path) as archive:
        names=archive.namelist()
        if len(names)>4096 or len(names)!=len(set(names)):raise ValueError('Invalid calibration ZIP entries')
        def digest(name):
            info=archive.getinfo(name)
            if not 0<=info.file_size<=MAX_BUNDLE:raise ValueError('Calibration component exceeds its limit')
            h=hashlib.sha256();size=0
            with archive.open(info) as source:
                for block in iter(lambda:source.read(64*1024),b''):
                    size+=len(block)
                    if size>MAX_BUNDLE:raise ValueError('Calibration component exceeds its limit')
                    h.update(block)
            if size!=info.file_size:raise ValueError('Calibration component is incomplete')
            return h.hexdigest(),size
        if member in names and not archive.getinfo(member).is_dir():
            return dict(kind='file',sha256=digest(member)[0])
        prefix=member.rstrip('/')+'/'
        files={};total=0
        for name in sorted(names):
            if not name.startswith(prefix) or archive.getinfo(name).is_dir():continue
            sha,size=digest(name);total+=size
            if total>MAX_BUNDLE:raise ValueError('Calibration component exceeds its limit')
            files[name[len(prefix):]]=sha
        return dict(kind='directory',files=files)


def _path_signature(path):
    p=Path(path)
    if not p.is_absolute():raise ValueError('Imported calibration component path is invalid')
    if p.is_file():return dict(kind='file',sha256=file_digest(p,MAX_BUNDLE)[0])
    if not p.is_dir() or p.is_symlink():raise ValueError('Imported calibration component is missing')
    files={};total=0
    for child in sorted(p.rglob('*')):
        if child.is_symlink():raise ValueError('Imported calibration component is redirected')
        if not child.is_file():continue
        if len(files)>=4096:raise ValueError('Imported calibration component exceeds its limit')
        digest,size=file_digest(child,MAX_BUNDLE);total+=size
        if total>MAX_BUNDLE:raise ValueError('Imported calibration component exceeds its limit')
        files[child.relative_to(p).as_posix()]=digest
    return dict(kind='directory',files=files)


def prepare_controller_model(manager, context, *, folder=None, receipt_folder=None):
    """Validate official data, refuse unmanaged overwrites, preserve current user.

    Caller holds an exclusive ProfileLease. This function never connects a
    device. SDK's own preview/import remains the only profile-format parser.
    """
    context=validate_continuation(context,path=True)
    root=Path(folder or controller_transfer_directory()).resolve()
    bundle=Path(context['bundle_path'])
    if not bundle.resolve().is_relative_to(root) or bundle.name!=context['bundle_sha256']+'.zip':
        raise ValueError('Calibration bundle is outside the owned controller transfer directory')
    if file_digest(bundle,MAX_BUNDLE)!=(context['bundle_sha256'],context['bundle_bytes']):
        raise ValueError('Calibration transfer checksum differs')
    try:preview=manager.preview_user_data(str(bundle))
    except Exception:raise ValueError('控制端未能验证官方标定资料包，未接入手套 / Controller could not validate the official calibration bundle; glove was not opened') from None
    owner=preview.get('owner_user_id') if isinstance(preview,dict) else None
    if (not isinstance(owner,str) or not IDENT.fullmatch(owner) or owner!=context['source_user_id']
            or preview.get('owner_display_name')!=context['source_profile']):
        raise ValueError('Official calibration bundle belongs to another SDK user')
    rows=_components(preview);model_key='hand_model.'+context['side']+'|'
    if model_key not in rows or _bundle_model(bundle,rows[model_key])!=(context['model_sha256'],context['model_bytes']):
        raise ValueError('Calibration bundle does not contain the saved hand model')
    receipt_root=Path(receipt_folder or Path.home()/'.local/share/hand-workbench/calibration-receipts')
    _private_directory(receipt_root)
    receipt_path=receipt_root/(hashlib.sha256(owner.encode()).hexdigest()+'.json')
    def receipt_digest():
        try:return file_digest(receipt_path,4*1024*1024)[0]
        except ValueError:
            if not receipt_path.exists():return None
            raise
    try:
        prior_digest=receipt_digest()
        prior=json.loads(receipt_path.read_text(encoding='utf-8')) if prior_digest is not None else None
    except (OSError,ValueError):raise ValueError('Managed calibration receipt is unverified; no data was imported') from None
    binding=dict(owner_user_id=owner,source_profile=context['source_profile'],source_run_id=context['source_run_id'],
        source_workspace=context['source_workspace'],bundle_sha256=context['bundle_sha256'],
        bundle_bytes=context['bundle_bytes'],model_sha256=context['model_sha256'],model_bytes=context['model_bytes'],
        side=context['side'],prior_receipt_sha256=prior_digest,**continuation_source(context))
    if context.get('source_kind')=='saved_profile':binding['serial']=context['serial']
    def saved_pending_matches(commit):
        # A new explicit saved-profile request gets a new transfer identity and
        # the exporter may stamp a new archive. Neither authorizes another
        # import or adoption of unrelated SDK data. Only a fully verified
        # candidate for this exact wearer/workspace/glove/side/model and prior
        # receipt may proceed to the complete SDK/content readback below.
        if context.get('source_kind')!='saved_profile' or not isinstance(commit,dict):return False
        try:
            old={key:context[key] for key in CONTEXT_FIELDS}
            old.update(source_kind='saved_profile',transfer_id=commit['transfer_id'],
                       bundle_sha256=commit['bundle_sha256'],bundle_bytes=commit['bundle_bytes'])
            validate_continuation(old)
            expected=dict(binding,state='verified',transfer_id=old['transfer_id'],
                          bundle_sha256=old['bundle_sha256'],bundle_bytes=old['bundle_bytes'])
            return commit==expected
        except (KeyError,TypeError,ValueError):return False
    def audit_current():
        audit=receipt_root/(uuid.uuid4().hex+'.zip')
        try:
            try:
                report=manager.export_user_data(owner,str(audit))
                file_digest(audit,MAX_BUNDLE)
                audit_preview=manager.preview_user_data(str(audit))
            except Exception:raise ValueError('Cannot verify existing controller calibration; no data was overwritten') from None
            if (not isinstance(report,dict) or not isinstance(audit_preview,dict)
                    or report.get('owner_user_id')!=owner or audit_preview.get('owner_user_id')!=owner
                    or audit_preview.get('owner_display_name')!=context['source_profile']):
                raise ValueError('Controller calibration owner changed; no data was overwritten')
            return {key:_bundle_signature(audit,row) for key,row in _components(audit_preview).items()}
        finally:
            try:audit.unlink(missing_ok=True)
            except OSError:raise ValueError('临时资料包清理未确认，请检查控制端本地存储 / Temporary calibration archive cleanup is unconfirmed; review controller storage') from None
    def valid_owned(value):
        try:
            return (isinstance(value,dict) and value.get('owner_user_id')==owner
                and value.get('source_profile')==context['source_profile'] and isinstance(value.get('components'),dict)
                and 1<=len(value['components'])<=512 and all(isinstance(key,str) and isinstance(row,dict)
                    and _path_signature(row.get('path',''))==row.get('signature') for key,row in value['components'].items()))
        except (OSError,ValueError,TypeError):return False
    users=manager.list_users()
    if not isinstance(users,list):raise ValueError('Cannot verify controller SDK users')
    exists=any(isinstance(u,dict) and u.get('user_id')==owner for u in users)
    preserved={};actual={}
    if exists:
        matches=[u for u in users if isinstance(u,dict) and u.get('user_id')==owner]
        if len(matches)!=1 or matches[0].get('display_name')!=context['source_profile'] or matches[0].get('is_default') is True:
            raise ValueError('控制端的佩戴者身份已改变，未覆盖 / Controller wearer identity changed; no data was overwritten')
        # A completed import whose receipt commit failed keeps its exact local
        # candidate. An explicit retry may verify it using the SDK, then commit
        # that same receipt. Preparing/failed imports never grant this proof.
        pending=list(receipt_root.glob(receipt_path.stem+'.*.pending'))
        if len(pending)>64:raise ValueError('待核对的标定保存记录过多，请先检查本地存储 / Too many pending calibration receipts; review local storage')
        candidates=[]
        for path in pending:
            try:
                file_digest(path,4*1024*1024)
                candidate=json.loads(path.read_text(encoding='utf8'))
            except (OSError,ValueError):continue
            commit=candidate.get('_commit') if isinstance(candidate,dict) else None
            if isinstance(commit,dict) and (commit==dict(binding,state='verified') or saved_pending_matches(commit)):
                candidates.append((path,candidate))
        if len(candidates)>1:raise ValueError('标定保存记录不唯一，请核对后重试 / Calibration receipt recovery is ambiguous; review it first')
        if candidates:
            path,candidate=candidates[0]
            if not valid_owned(candidate) or model_key not in candidate['components']:
                raise ValueError('待保存的标定资料已变化，未接入设备 / Pending calibration data changed; no device was opened')
            current=audit_current();owned=candidate['components']
            if (set(current)!=set(owned) or any(current[key]!=row['signature'] for key,row in owned.items())
                    or file_digest(owned[model_key]['path'])!=(context['model_sha256'],context['model_bytes'])
                    or receipt_digest()!=prior_digest):
                raise ValueError('待保存的标定资料与官方档案不一致 / Pending calibration receipt differs from official data')
            if context.get('source_kind')=='saved_profile':
                if (set(rows)!=set(owned)
                        or any(_bundle_signature(bundle,row)!=owned[key]['signature'] for key,row in rows.items())):
                    raise ValueError('本次资料与待保存的完整官方档案不一致，未重新导入 / This bundle differs from the complete pending official profile; nothing was reimported')
            try:path.replace(receipt_path)
            except OSError:raise ValueError('标定资料已验证但保存记录未完成，请检查存储后重试 / Calibration data verified; receipt save is incomplete. Check storage and retry') from None
            return dict(context,owner_user_id=owner,model_path=owned[model_key]['path'],imported=False)
        if not isinstance(prior,dict) or prior.get('owner_user_id')!=owner or prior.get('source_profile')!=context['source_profile']:
            raise ValueError('控制端已有未由工作台托管的佩戴者资料，未覆盖；请先核对资料来源 / Controller has unmanaged wearer data; no data was overwritten')
        owned=prior.get('components')
        if not isinstance(owned,dict) or not owned:raise ValueError('Managed hand model receipt is incomplete')
        if not valid_owned(prior):
            raise ValueError('控制端标定资料已在外部改变，未覆盖 / Controller calibration changed outside this task; no data was overwritten')
        actual=audit_current()
        if any(key not in actual or actual[key]!=row['signature'] for key,row in owned.items()):
            raise ValueError('Controller SDK model differs from its managed receipt; no data was overwritten')
        prior_model=owned.get(model_key)
        if prior_model and file_digest(prior_model['path'])==(context['model_sha256'],context['model_bytes']):
            return dict(context,owner_user_id=owner,model_path=prior_model['path'],imported=False)
        if set(actual)!=set(owned):
            raise ValueError('Controller contains additional unmanaged calibration data; no data was overwritten')
        preserved=copy.deepcopy(owned)
        for key,row in rows.items():
            if key!=model_key and key in actual and _bundle_signature(bundle,row)!=actual[key]:
                raise ValueError('资料包含有另一侧或其它标定的旧版本；请重新导出当前档案 / Bundle contains older calibration data for another side or component; export the current profile again')
    if file_digest(bundle,MAX_BUNDLE)!=(context['bundle_sha256'],context['bundle_bytes']):
        raise ValueError('Calibration transfer changed before import')
    pending=receipt_path.with_suffix('.'+uuid.uuid4().hex+'.pending')
    try:
        with pending.open('x',encoding='utf8') as output:json.dump(dict(_commit=dict(binding,state='preparing')),output,sort_keys=True);output.flush();os.fsync(output.fileno())
    except OSError:raise ValueError('无法保存标定接续记录，未导入资料；请检查存储后重试 / Cannot stage calibration receipt; nothing was imported. Check storage and retry') from None
    try:imported=manager.import_user_data(str(bundle))
    except Exception:raise ValueError('官方资料导入未确认，未接入手套；请先检查控制端资料 / Official import is unconfirmed; glove was not opened. Review controller data before retrying') from None
    if not isinstance(imported,dict) or imported.get('owner_user_id')!=owner:
        raise ValueError('Official SDK did not confirm the imported owner; glove was not opened')
    installed=_components(imported)
    if model_key not in installed:raise ValueError('Official SDK did not install the selected hand model')
    model_path=installed[model_key].get('path')
    if file_digest(model_path)!=(context['model_sha256'],context['model_bytes']):
        raise ValueError('Imported hand model differs from this calibration; glove was not opened')
    components=copy.deepcopy(preserved)
    for key,row in preserved.items():
        if key!=model_key and _path_signature(row['path'])!=row['signature']:
            raise ValueError('导入后其它标定资料已变化，未接入手套 / Other calibration data changed during import; glove was not opened')
    for key,row in installed.items():
        signature=_path_signature(row.get('path',''))
        expected=_bundle_signature(bundle,rows[key]) if key in rows else actual.get(key)
        if expected is None or signature!=expected:raise ValueError('Imported calibration component differs from the verified bundle')
        components[key]=dict(path=row.get('path'),signature=signature)
    if any(key not in installed for key in rows):raise ValueError('Official SDK did not confirm every imported calibration component')
    receipt=dict(schema=1,owner_user_id=owner,source_profile=context['source_profile'],
        source_workspace=context['source_workspace'],source_run_id=context['source_run_id'],**continuation_source(context),
        components=components,_commit=dict(binding,state='verified'))
    try:
        with pending.open('w',encoding='utf-8') as output:json.dump(receipt,output,sort_keys=True);output.flush();os.fsync(output.fileno())
        if receipt_digest()!=prior_digest:raise ValueError('Managed calibration receipt changed during import')
        pending.replace(receipt_path)
    except OSError:raise ValueError('官方资料已导入，但保存记录尚未完成；请检查存储后重试 / Official data imported; receipt save is incomplete. Check storage and retry') from None
    return dict(context,owner_user_id=owner,model_path=model_path,imported=True)


def verify_loaded_model(glove, receipt, *, sdk_root=None):
    """A custom online-IK override must never silently shadow the saved model."""
    expected=(receipt['model_sha256'],receipt['model_bytes'])
    if file_digest(receipt['model_path'])!=expected:raise ValueError('Controller hand model changed before connection')
    override=glove.hand_model_path().get()
    if not isinstance(override,str):raise ValueError('Cannot verify active glove hand model')
    # A directory managed by the SDK can contain older models or another
    # wearer's custom model. Only this exact model's bytes establish the match.
    if override:
        if file_digest(override)!=expected:
            raise ValueError('手套已有另一份自定义手型覆盖，未修改该设置 / A different custom glove hand model is active; it was not changed')
    return True
