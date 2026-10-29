"""Owned official user-export archives; no reconstructed SDK files or auto restore."""
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import time
import uuid
import zipfile

from calibration_portability import MAX_BUNDLE,_private_directory,file_digest
from calibration_identity import require_sdk_user,sdk_user_identity,PROFILE

ID=re.compile('[0-9a-f]{32}\\Z')
HASH=re.compile('[0-9a-f]{64}\\Z')
SERIAL=re.compile('[A-Za-z0-9][A-Za-z0-9_-]{0,79}\\Z')

def _timestamp(value):
    if type(value) not in (int,float):return False
    try:return math.isfinite(value) and value>0
    except OverflowError:return False

def _receipt(value,backup_id,expected_sha256):
    """Validate this app's ledger, without interpreting the vendor ZIP format."""
    required={'schema','id','run_id','profile','side','serial','sdk_user','created_at','cli_version',
              'verified','sha256','bytes','members','profile_metadata'}
    invalid='标定备份记录不完整或已损坏，未启动恢复 / Calibration backup receipt is incomplete or damaged; restore was not started'
    if (not isinstance(value,dict) or set(value)!=required or type(value.get('schema')) is not int or value['schema']!=1
            or value.get('id')!=backup_id or value.get('verified') is not True
            or not isinstance(value.get('run_id'),str) or not ID.fullmatch(value['run_id'])
            or not isinstance(value.get('profile'),str) or not PROFILE.fullmatch(value['profile']) or value['profile'].lower()=='default'
            or value.get('side') not in ('left','right')
            or not isinstance(value.get('serial'),str) or not SERIAL.fullmatch(value['serial'])
            or not _timestamp(value.get('created_at'))
            or value.get('cli_version') is not None and (not isinstance(value['cli_version'],str) or not 1<=len(value['cli_version'])<=100)
            or not isinstance(value.get('sha256'),str) or not HASH.fullmatch(value['sha256'])
            or type(value.get('bytes')) is not int or not 0<value['bytes']<=MAX_BUNDLE
            or expected_sha256 is not None and value['sha256']!=expected_sha256):
        raise ValueError(invalid)
    try:
        if sdk_user_identity(value['sdk_user'],value['profile'])!=value['sdk_user']:raise ValueError(invalid)
        from calibration_guided import validate_profile
        metadata=value['profile_metadata']
        for side in ('left','right'):validate_profile(metadata,value['profile'],side,True)
        if 'is_default' in metadata and metadata['is_default'] is not False:raise ValueError(invalid)
        if metadata[value['side']+'_hand']['calibrated'] is not True:raise ValueError(invalid)
    except (ValueError,TypeError,KeyError):raise ValueError(invalid) from None
    members=value['members']
    if not isinstance(members,dict) or not 1<=len(members)<=4096:raise ValueError(invalid)
    total=0
    for name,row in members.items():
        if (not isinstance(name,str) or not name or '\x00' in name or len(name)>65535
                or not isinstance(row,dict) or set(row)!={'sha256','bytes'}
                or not isinstance(row.get('sha256'),str) or not HASH.fullmatch(row['sha256'])
                or type(row.get('bytes')) is not int or not 0<=row['bytes']<=MAX_BUNDLE):raise ValueError(invalid)
        total+=row['bytes']
        if total>MAX_BUNDLE:raise ValueError(invalid)
    return value

def _unique_fields(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate calibration backup receipt field')
        result[key]=value
    return result

def _file_identity(path):
    info=path.stat()
    return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns

def root_folder():
    from cli_coordination import root_folder as cli_data
    return cli_data()/'calibration-backups'

def archive_signature(path):
    """Read original export members without extracting or guessing its schema."""
    try:return _archive_signature(path)
    except (zipfile.BadZipFile,RuntimeError,NotImplementedError,EOFError) as error:
        raise ValueError('Official calibration backup structure or CRC is unverified') from error

def _archive_signature(path):
    signature={};total=0
    with zipfile.ZipFile(path) as archive:
        rows=archive.infolist()
        if not 1<=len(rows)<=4096:raise ValueError('Official calibration backup has an invalid file count')
        for row in rows:
            if row.is_dir():continue
            if row.filename in signature or row.flag_bits&1 or row.file_size<0:
                raise ValueError('Official calibration backup member is unverified')
            total+=row.file_size
            if total>MAX_BUNDLE:raise ValueError('Official calibration backup exceeds its limit')
            digest=hashlib.sha256();size=0
            with archive.open(row) as stream:
                while block:=stream.read(65536):
                    size+=len(block)
                    if size>row.file_size:raise ValueError('Official calibration backup member changed')
                    digest.update(block)
            if size!=row.file_size:raise ValueError('Official calibration backup member is incomplete')
            signature[row.filename]=dict(sha256=digest.hexdigest(),bytes=size)
    if not signature:raise ValueError('Official calibration backup is empty')
    return signature

class CalibrationBackups:
    def __init__(self,folder=None):self.folder=Path(folder if folder is not None else root_folder())

    def _root(self):return _private_directory(self.folder)

    def _write(self,record):
        root=self._root();target=root/(record['id']+'.json');stage=root/(record['id']+'.'+uuid.uuid4().hex+'.tmp')
        with stage.open('x',encoding='utf-8') as output:
            json.dump(record,output,ensure_ascii=False,sort_keys=True);output.flush()
            __import__('os').fsync(output.fileno())
        if target.is_symlink() or getattr(target,'is_junction',lambda:False)():raise ValueError('Calibration backup receipt is redirected')
        stage.replace(target)

    def load(self,backup_id,expected_sha256=None):
        try:return self._load(backup_id,expected_sha256)
        except OSError as error:
            raise ValueError('标定备份暂不可读取，未启动恢复 / Calibration backup is unreadable; restore was not started') from error

    def _load(self,backup_id,expected_sha256=None):
        if not isinstance(backup_id,str) or not ID.fullmatch(backup_id):raise ValueError('Invalid calibration backup identity')
        root=self._root();receipt=root/(backup_id+'.json')
        identity=_file_identity(receipt);pinned=file_digest(receipt,1024*1024)
        with receipt.open('rb') as stream:raw=stream.read(1024*1024+1)
        if (len(raw)>1024*1024 or (hashlib.sha256(raw).hexdigest(),len(raw))!=pinned
                or _file_identity(receipt)!=identity or file_digest(receipt,1024*1024)!=pinned):
            raise ValueError('Calibration backup receipt changed during verification')
        try:value=json.loads(raw.decode('utf-8'),object_pairs_hook=_unique_fields)
        except (ValueError,UnicodeError):raise ValueError('标定备份记录已损坏，未启动恢复 / Calibration backup receipt is damaged; restore was not started') from None
        value=_receipt(value,backup_id,expected_sha256)
        path=root/(backup_id+'.zip')
        if file_digest(path,MAX_BUNDLE)!=(value.get('sha256'),value.get('bytes')) or archive_signature(path)!=value.get('members'):
            raise ValueError('标定备份内容已变化，未启动恢复 / Calibration backup changed; restore was not started')
        if file_digest(path,MAX_BUNDLE)!=(value.get('sha256'),value.get('bytes')):
            raise ValueError('Calibration backup changed during verification')
        if _file_identity(receipt)!=identity or file_digest(receipt,1024*1024)!=pinned:
            raise ValueError('Calibration backup receipt changed during verification')
        return value,path

    @staticmethod
    def summary(value):
        return {key:copy.deepcopy(value[key]) for key in ('id','run_id','profile','side','serial','created_at','bytes','sha256','verified')}

    def create(self,profile,side,serial,sdk_user,run_id,*,cli_version=None,command=None,cancel_check=None):
        from calibration_guided import validate_profile
        from calibration_identity import read_current_sdk_user
        from native_cli import json_command
        command=command or json_command
        if cancel_check:cancel_check()
        require_sdk_user(read_current_sdk_user(profile),sdk_user,profile)
        before=command('user','show');validate_profile(before,profile,side,True)
        if before[side+'_hand']['calibrated'] is not True:return None
        if not isinstance(run_id,str) or not ID.fullmatch(run_id):raise ValueError('Invalid backup run identity')
        backup_id=uuid.uuid4().hex;root=self._root();path=root/(backup_id+'.zip')
        value=dict(schema=1,id=backup_id,run_id=run_id,profile=profile,side=side,serial=serial,
                   sdk_user=copy.deepcopy(sdk_user),created_at=time.time(),cli_version=cli_version,verified=False)
        self._write(value)
        try:
            command('user','export',str(path))
            digest,size=file_digest(path,MAX_BUNDLE);members=archive_signature(path)
            require_sdk_user(read_current_sdk_user(profile),sdk_user,profile)
            after=command('user','show')
            if before!=after:raise ValueError('Wearer calibration changed during backup')
            value.update(sha256=digest,bytes=size,members=members,profile_metadata=copy.deepcopy(before),verified=True)
            self._write(value)
            self.load(backup_id,digest)
        except Exception as error:
            value.update(verified=False,error=str(error)[:400]);self._write(value)
            raise ValueError('无法完整保存覆盖前档案，未开始标定 / Could not preserve the previous profile; calibration was not started') from error
        return self.summary(value)

    def verify_current_owner(self,backup_id,expected_sha256=None,*,command=None):
        from calibration_identity import read_current_sdk_user
        from native_cli import json_command
        command=command or json_command;value,path=self.load(backup_id,expected_sha256)
        require_sdk_user(read_current_sdk_user(value['profile']),value['sdk_user'],value['profile'])
        current=command('user','show')
        if current.get('name')!=value['profile'] or current.get('is_default') is True:
            raise ValueError('请先切回备份对应的佩戴者，再恢复 / Select the backup wearer before restoring')
        return value,path

    def verify_restored(self,backup_id,expected_sha256=None,*,command=None):
        """Only exact local owner + official SDK readback establishes restoration.

        Export archives may include versioned metadata. Any mismatch stays
        unconfirmed rather than guessing which fields or artifacts to ignore.
        Both original and readback official archives remain available.
        """
        from native_cli import json_command
        command=command or json_command;value,_=self.verify_current_owner(backup_id,expected_sha256,command=command)
        before=command('user','show')
        for side in ('left','right'):
            old=value['profile_metadata'].get(side+'_hand') or {};now=before.get(side+'_hand') or {}
            if now.get('calibrated') is not old.get('calibrated'):return False
        readback=self._root()/(backup_id+'.readback-'+uuid.uuid4().hex+'.zip')
        command('user','export',str(readback))
        file_digest(readback,MAX_BUNDLE)
        self.verify_current_owner(backup_id,expected_sha256,command=command)
        if before!=command('user','show'):return False
        return archive_signature(readback)==value['members']
