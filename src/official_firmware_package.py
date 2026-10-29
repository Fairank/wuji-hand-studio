"""Prepare verified official firmware ZIP files; never flash or open a device.

The official CLI remains responsible for its supported file-mode validation and
human confirmation. ZIP/component integrity is not hardware compatibility.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from urllib.error import HTTPError,URLError
from urllib.request import Request,build_opener
import zipfile

from official_firmware_catalog import CatalogError,ORIGIN,_NoRedirect,_json,parse_catalog


TIMEOUT=30
MAX_UNCOMPRESSED=64*1024*1024
MAX_MANIFEST=256*1024
MAX_RATIO=200


class PackageError(ValueError):
    def __init__(self,message,*,code='invalid_package',http_status=None):
        super().__init__(message)
        self.code,self.http_status=code,http_status


@dataclass(frozen=True)
class FirmwarePackage:
    path:str
    serial:str
    device_type:str
    entry_id:str
    version:str
    archive_sha256:str
    archive_size:int
    component_sha256:str
    component_size:int
    manifest_device_type:str
    component_id:str
    package_integrity_verified:bool=True
    hardware_compatibility_verified:bool=False


def _require(condition,message):
    if not condition:raise PackageError(message)


def _entry(entry,sn,expected_type):
    try:
        raw=json.dumps(dict(code=0,msg='success',data=[entry]),allow_nan=False).encode('utf8')
        return parse_catalog(raw,sn,expected_type)['catalog_metadata']['entries'][0]
    except (CatalogError,ValueError,TypeError,UnicodeError):
        raise PackageError('Firmware metadata did not pass the official catalog contract') from None


def _plain(path):
    try:value=Path(path)
    except (TypeError,ValueError):raise PackageError('Invalid firmware cache path') from None
    _require(value.is_absolute() and len(value.parts)>1,'Firmware cache path must be absolute')
    _require(not any(part.is_symlink() or getattr(part,'is_junction',lambda:False)()
        for part in (value,*value.parents)),'Firmware cache path is redirected')
    return value


def _file_stat(value):
    _require(stat.S_ISREG(value.st_mode) and value.st_nlink==1,'Firmware package must be a regular file with one link')
    return (value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns)


def _hash(stream):
    digest=hashlib.sha256()
    while chunk:=stream.read(65536):digest.update(chunk)
    return digest.hexdigest()


def _member_digest(archive,info,expected_size,expected_digest,*,elf=False):
    _require(info.file_size==expected_size,'Firmware member size does not match its manifest')
    digest=hashlib.sha256();size=0;head=b''
    with archive.open(info,'r') as source:
        while chunk:=source.read(65536):
            if not head:head=chunk[:4]
            size+=len(chunk)
            _require(size<=expected_size,'Firmware member exceeds its declared size')
            digest.update(chunk)
    _require(size==expected_size and digest.hexdigest()==expected_digest,'Firmware member digest does not match its manifest')
    if elf:_require(head==b'\x7fELF','Debug member is not an ELF file')


def _inspect(stream,row,sn,expected_type):
    stream.seek(0)
    _require(stream.read(4)==b'PK\x03\x04','Firmware package is not a plain ZIP archive')
    _require(row['file_size']>=22,'Firmware ZIP is truncated')
    stream.seek(-22,os.SEEK_END);end=stream.read(22)
    _require(end[:4]==b'PK\x05\x06' and end[-2:]==b'\x00\x00','Firmware ZIP has unsupported trailing data or comments')
    stream.seek(0)
    manifest=row['manifest'];base=manifest['component_id']+'-v'+row['version']
    binary=base+'.signed.bin';elf=base+'.defmt.elf'
    expected={'manifest.json',binary}|({elf} if row['has_elf'] else set())
    with zipfile.ZipFile(stream,'r') as archive:
        infos=archive.infolist();names=[info.filename for info in infos]
        _require(len(names)==len(set(names)) and set(names)==expected,'Firmware ZIP members do not match the supported package layout')
        _require(not archive.comment,'Firmware ZIP comments are unsupported')
        total=0
        for info in infos:
            mode=info.external_attr>>16
            _require(not info.is_dir() and stat.S_IFMT(mode) in (0,stat.S_IFREG),'Firmware ZIP contains a special file')
            _require(not info.flag_bits&1 and info.compress_type in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED),'Firmware ZIP encryption or compression is unsupported')
            _require(0<info.file_size<=MAX_UNCOMPRESSED and info.file_size<=max(1,info.compress_size)*MAX_RATIO,'Firmware ZIP exceeds its expansion limit')
            total+=info.file_size
        _require(total<=MAX_UNCOMPRESSED,'Firmware ZIP total expansion exceeds its limit')
        manifest_info=archive.getinfo('manifest.json')
        _require(manifest_info.file_size<=MAX_MANIFEST,'Firmware ZIP manifest is too large')
        actual=_json(archive.read(manifest_info))
        # Equality alone treats JSON true/1 and 1.0/1 as equal in Python.
        # Apply the same exact-type manifest contract before comparing values.
        checked=_entry(dict(row,manifest=actual),sn,expected_type)['manifest']
        _require(checked==manifest,'Firmware ZIP manifest differs from official catalog metadata')
        _member_digest(archive,archive.getinfo(binary),manifest['size'],manifest['digest']['value'])
        if row['has_elf']:_member_digest(archive,archive.getinfo(elf),row['elf_size'],row['elf_sha256'],elf=True)


def validate_package(entry,sn,expected_type,path):
    """Recheck a private prepared ZIP immediately before official CLI launch.

    No path is accepted from the frontend. The coordinator supplies its retained
    catalog entry and private path; this function performs only local reads.
    """
    row=_entry(entry,sn,expected_type);path=_plain(path)
    try:
        before=_file_stat(path.stat(follow_symlinks=False))
        flags=os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)
        descriptor=os.open(path,flags)
        with os.fdopen(descriptor,'rb') as source:
            _require(_file_stat(os.fstat(source.fileno()))==before,'Firmware package changed before validation')
            _require(before[2]==row['file_size'] and _hash(source)==row['sha256'],'Firmware ZIP size or digest does not match official metadata')
            _inspect(source,row,sn,expected_type)
            _require(_file_stat(os.fstat(source.fileno()))==before,'Firmware package changed while validating')
        _plain(path)
        _require(_file_stat(path.stat(follow_symlinks=False))==before,'Firmware package was replaced while validating')
    except PackageError:raise
    except (OSError,ValueError,zipfile.BadZipFile,RuntimeError,EOFError,CatalogError):
        raise PackageError('Firmware package could not be validated') from None
    manifest=row['manifest']
    return FirmwarePackage(str(path),sn,expected_type,row['id'],row['version'],row['sha256'],row['file_size'],
        manifest['digest']['value'],manifest['size'],manifest['device_type'],manifest['component_id'])


def _progress(callback,stage,received,total):
    if callback is not None:callback(dict(stage=stage,received_bytes=received,total_bytes=total))


def _publish(temporary,target):
    if os.name=='nt':
        # Windows rename is atomic and refuses an existing destination. Avoid
        # a two-link publication window that could survive a process crash.
        os.rename(temporary,target)
    else:
        # POSIX rename replaces a destination. Hard-link publication instead
        # provides the same create-only property for non-Windows test hosts.
        os.link(temporary,target)
        temporary.unlink()


def prepare_package(entry,sn,expected_type,cache_dir,*,opener=None,on_progress=None):
    """One explicit official ZIP download, atomically published after validation.

    A cache hit is revalidated. A damaged cache is preserved and reported rather
    than silently overwritten. Only this invocation's temporary file is removed
    on failure; neither metadata checks nor firmware operations are retried.
    """
    row=_entry(entry,sn,expected_type);root=_plain(cache_dir)
    _require(opener is None or callable(opener),'Invalid firmware package opener')
    _require(on_progress is None or callable(on_progress),'Invalid firmware progress callback')
    try:root.mkdir(parents=True,exist_ok=True)
    except OSError:raise PackageError('Firmware cache directory is unavailable',code='cache_unavailable') from None
    _plain(root)
    target=root/(row['id']+'-'+row['sha256']+'.zip')
    if target.exists() or target.is_symlink() or getattr(target,'is_junction',lambda:False)():
        record=validate_package(row,sn,expected_type,target)
        _progress(on_progress,'cache_verified',0,row['file_size'])
        return record
    url=ORIGIN+row['download_url']
    request=Request(url,headers={'Accept':'application/zip, application/octet-stream','Accept-Encoding':'identity','User-Agent':'HandWorkbench-Package/1'},method='GET')
    open_http=opener or build_opener(_NoRedirect()).open
    temporary=None
    try:
        _progress(on_progress,'downloading',0,row['file_size'])
        deadline=time.monotonic()+TIMEOUT
        try:response=open_http(request,timeout=TIMEOUT)
        except HTTPError as error:
            error.close()
            raise PackageError('Official firmware package request failed',code='http_error',http_status=error.code) from None
        with response:
            _require(response.geturl()==url and response.getcode()==200,'Official firmware package redirected or returned a non-success status')
            content_type=response.headers.get('Content-Type','').split(';',1)[0].strip().lower()
            _require(content_type in ('application/zip','application/octet-stream','application/x-zip-compressed'),'Official firmware response is not a ZIP payload')
            length=response.headers.get('Content-Length')
            if length is not None:_require(length==str(row['file_size']),'Firmware download Content-Length differs from official metadata')
            descriptor,name=tempfile.mkstemp(prefix='.firmware-',suffix='.part',dir=root)
            temporary=Path(name);received=0;digest=hashlib.sha256()
            with os.fdopen(descriptor,'wb') as output:
                read=getattr(response,'read1',None) or response.read
                while True:
                    if time.monotonic()>deadline:raise PackageError('Official firmware package download timed out',code='network_unavailable')
                    chunk=read(min(65536,row['file_size']+1-received))
                    _require(type(chunk) is bytes,'Invalid firmware download body')
                    if not chunk:break
                    received+=len(chunk)
                    _require(received<=row['file_size'],'Firmware download exceeds official size')
                    output.write(chunk);digest.update(chunk)
                    _progress(on_progress,'downloading',received,row['file_size'])
                _require(received==row['file_size'] and digest.hexdigest()==row['sha256'],'Firmware download size or digest differs from official metadata')
                output.flush();os.fsync(output.fileno())
        _progress(on_progress,'verifying',received,row['file_size'])
        validate_package(row,sn,expected_type,temporary)
        _plain(root)
        try:_publish(temporary,target)
        except FileExistsError:
            # Another invocation may have completed the same immutable entry.
            # Revalidate its file; never replace it or delete its evidence.
            temporary.unlink();temporary=None
            record=validate_package(row,sn,expected_type,target)
        else:
            temporary=None
            record=validate_package(row,sn,expected_type,target)
        _progress(on_progress,'ready',received,row['file_size'])
        return record
    except PackageError:raise
    except (OSError,URLError,TimeoutError):
        raise PackageError('Official firmware package is unavailable',code='package_unavailable') from None
    finally:
        if temporary is not None:
            try:temporary.unlink()
            except OSError:pass
