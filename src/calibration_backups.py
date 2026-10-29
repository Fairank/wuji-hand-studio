"""Owned official user-export archives; no reconstructed SDK files or auto restore."""
import copy
import hashlib
import json
from pathlib import Path
import re
import time
import uuid
import zipfile

from calibration_portability import MAX_BUNDLE,_private_directory,file_digest
from calibration_identity import require_sdk_user

ID=re.compile('[0-9a-f]{32}\\Z')

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
        if not isinstance(backup_id,str) or not ID.fullmatch(backup_id):raise ValueError('Invalid calibration backup identity')
        root=self._root();receipt=root/(backup_id+'.json')
        file_digest(receipt,1024*1024)
        value=json.loads(receipt.read_text(encoding='utf-8'))
        if (not isinstance(value,dict) or value.get('id')!=backup_id or value.get('verified') is not True
                or expected_sha256 is not None and value.get('sha256')!=expected_sha256):
            raise ValueError('标定备份尚未验证或已变化 / Calibration backup is unverified or changed')
        path=root/(backup_id+'.zip')
        if file_digest(path,MAX_BUNDLE)!=(value.get('sha256'),value.get('bytes')) or archive_signature(path)!=value.get('members'):
            raise ValueError('标定备份内容已变化，未启动恢复 / Calibration backup changed; restore was not started')
        if file_digest(path,MAX_BUNDLE)!=(value.get('sha256'),value.get('bytes')):
            raise ValueError('Calibration backup changed during verification')
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
