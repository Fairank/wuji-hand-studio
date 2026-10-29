"""Read official serial-specific firmware metadata; never firmware authority.

This module performs one explicit HTTPS metadata GET. It never discovers or
opens a device, runs a CLI, downloads a firmware payload, writes files, or retries.
An SN catalog listing does not prove hardware compatibility or authorize a write.
Only the captured Glove flat-manifest schema is supported until other device
families have a separately verified metadata contract.
"""
from datetime import datetime
import json
import re
import time
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode,urlsplit
from urllib.request import HTTPRedirectHandler,Request,build_opener


ORIGIN='https://api.wuji.tech'
PATH='/v2/firmwares'
MAX_METADATA=2*1024*1024
TIMEOUT=12
MAX_FILE_SIZE=512*1024*1024
_SERIAL=re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z')
_VERSION=re.compile(r'(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\Z')
_UUID=re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z')
_SHA=re.compile(r'[0-9a-f]{64}\Z')
_FAMILIES={'wuji_glove':('WG1',frozenset(('WG1J','WG1K')),'gboard')}
_ROW_FIELDS={'id','device_type_code','version','file_size','sha256','manifest','download_url',
    'has_elf','elf_size','elf_sha256','elf_download_url'}
_MANIFEST_FIELDS={'size','digest','version','device_type','component_id','release_date','release_notes','manifest_version'}


class CatalogError(ValueError):
    def __init__(self,message,*,code='invalid_catalog',http_status=None):
        super().__init__(message)
        self.code,self.http_status=code,http_status


def _require(condition,message):
    if not condition:raise CatalogError(message)


def _text(value,limit,*,multiline=False):
    return (type(value) is str and len(value)<=limit
        and all((ord(c)>=32 and ord(c)!=127 and not 0xD800<=ord(c)<=0xDFFF)
            or multiline and c in '\n\t\r' for c in value))


def _version(value):
    return type(value) is str and _VERSION.fullmatch(value) is not None


def _arguments(sn,expected_type,current_version,hardware_version):
    _require(type(sn) is str and _SERIAL.fullmatch(sn) is not None,'Invalid device serial')
    _require(type(expected_type) is str and expected_type in _FAMILIES,'Unsupported firmware catalog schema for this device family')
    _require(current_version is None or _version(current_version),'Invalid observed firmware version')
    _require(hardware_version is None or _version(hardware_version),'Invalid observed hardware version')


def _json(raw):
    _require(type(raw) in (bytes,str),'Firmware metadata must be UTF-8 JSON')
    _require(len(raw)<=MAX_METADATA,'Firmware metadata exceeds its limit')
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate metadata field')
            result[key]=value
        return result
    def nonfinite(value):raise ValueError('Non-finite metadata value')
    try:
        text=raw.decode('utf-8') if isinstance(raw,bytes) else raw
        _require(len(text.encode('utf-8'))<=MAX_METADATA,'Firmware metadata exceeds its limit')
        return json.loads(text,object_pairs_hook=unique,parse_constant=nonfinite)
    except (UnicodeError,ValueError,RecursionError) as error:
        if isinstance(error,CatalogError):raise
        raise CatalogError('Invalid official firmware JSON') from None


def _url(value,identifier,sn,leaf):
    _require(_text(value,2048) and value==value.strip(),'Invalid firmware URL')
    try:parsed=urlsplit(value)
    except ValueError:raise CatalogError('Invalid firmware URL') from None
    expected=PATH+'/'+identifier+'/'+leaf+'?'+urlencode({'sn':sn})
    _require(not parsed.scheme and not parsed.netloc and not parsed.fragment and value==expected,
        'Firmware URL must match its official route, entry and selected serial')
    return value


def _size(value):
    return type(value) is int and 0<value<=MAX_FILE_SIZE


def _manifest(value,version,family):
    _require(type(value) is dict and set(value)==_MANIFEST_FIELDS,'Unsupported firmware manifest schema')
    code,types,component=family
    _require(type(value['device_type']) is str and value['device_type'] in types,'Firmware manifest device family does not match')
    _require(value['component_id']==component,'Unsupported firmware component')
    _require(value['version']==version and _size(value['size']),'Firmware manifest version or size is invalid')
    _require(type(value['manifest_version']) is int and value['manifest_version']==1,'Unsupported firmware manifest version')
    digest=value['digest']
    _require(type(digest) is dict and set(digest)=={'algorithm','value'}
        and digest['algorithm']=='sha256' and type(digest['value']) is str and _SHA.fullmatch(digest['value']),
        'Invalid firmware component digest')
    date=value['release_date']
    _require(type(date) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',date) is not None,'Invalid firmware release date')
    try:datetime.strptime(date,'%Y-%m-%dT%H:%M:%SZ')
    except ValueError:raise CatalogError('Invalid firmware release date') from None
    notes=value['release_notes']
    _require(type(notes) is dict and 0<len(notes)<=12,'Invalid firmware release notes')
    for language,note in notes.items():
        _require(type(language) is str and len(language)<=35 and re.fullmatch(r'[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*',language) is not None,
            'Invalid firmware release-note language')
        _require(type(note) is dict and set(note)=={'description','update_description'}
            and _text(note['description'],1024,multiline=True) and _text(note['update_description'],65536,multiline=True),
            'Invalid firmware release-note content')
    return value


def parse_catalog(raw,sn,expected_type,current_version=None,*,hardware_version=None,http_status=200):
    """Validate metadata only. Success never means compatible or up to date."""
    _arguments(sn,expected_type,current_version,hardware_version)
    _require(type(http_status) is int,'Invalid firmware HTTP status')
    value=_json(raw)
    _require(type(value) is dict,'Invalid firmware response envelope')
    result=dict(source='official_firmware_catalog',serial=sn,device_type=expected_type,
        scope='hardware_filtered_catalog' if hardware_version is not None else 'serial_catalog',
        hardware_version=hardware_version,current_version=current_version,
        phase='empty',catalog_metadata=dict(entries=[],latest_listed=None,higher_listed=None if current_version is None else False),
        compatibility_verified=False,can_upgrade=False,up_to_date_authority=False,error=None)
    if (http_status==404 and set(value)=={'code','error_code','msg'}
            and type(value['code']) is int and value['code']==404
            and value['error_code']=='HARDWARE_VERSION_NOT_FOUND' and _text(value['msg'],2048) and value['msg']):
        result.update(phase='hardware_unavailable',error=dict(code='HARDWARE_VERSION_NOT_FOUND',message=value['msg'],http_status=404))
        return result
    if http_status!=200:
        raise CatalogError('Official firmware catalog request failed',code='http_error',http_status=http_status)
    _require(set(value)=={'code','msg','data'} and type(value['code']) is int and value['code']==0
        and _text(value['msg'],2048),'Unsupported firmware response envelope')
    rows=value['data']
    _require(type(rows) is list and len(rows)<=1000,'Invalid firmware entries')
    entries=[];seen=set();family=_FAMILIES[expected_type]
    for row in rows:
        _require(type(row) is dict and set(row)==_ROW_FIELDS,'Unsupported firmware entry schema')
        identifier=row['id'];version=row['version']
        _require(type(identifier) is str and _UUID.fullmatch(identifier) is not None and identifier not in seen,'Invalid or duplicate firmware entry ID')
        seen.add(identifier)
        _require(row['device_type_code']==family[0] and _version(version),'Firmware entry family or version does not match')
        _require(_size(row['file_size']) and type(row['sha256']) is str and _SHA.fullmatch(row['sha256']) is not None,'Invalid firmware archive size or digest')
        _manifest(row['manifest'],version,family)
        _url(row['download_url'],identifier,sn,'download')
        _require(type(row['has_elf']) is bool,'Invalid firmware ELF flag')
        if row['has_elf']:
            _require(_size(row['elf_size']) and type(row['elf_sha256']) is str and _SHA.fullmatch(row['elf_sha256']) is not None,'Invalid firmware ELF size or digest')
            _url(row['elf_download_url'],identifier,sn,'elf')
        else:_require(all(row[key] is None for key in ('elf_size','elf_sha256','elf_download_url')),'Unexpected firmware ELF metadata')
        entries.append(row)
    entries.sort(key=lambda row:tuple(map(int,row['version'].split('.'))),reverse=True)
    latest=entries[0]['version'] if entries else None
    higher=None if current_version is None else bool(latest and tuple(map(int,latest.split('.')))>tuple(map(int,current_version.split('.'))))
    result.update(phase='listed' if entries else 'empty',catalog_metadata=dict(entries=entries,latest_listed=latest,higher_listed=higher))
    return result


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def _read(response,url,deadline):
    _require(response.geturl()==url,'Official firmware metadata redirected')
    content_type=response.headers.get('Content-Type','').split(';',1)[0].strip().lower()
    _require(content_type=='application/json','Official firmware metadata is not JSON')
    length=response.headers.get('Content-Length')
    if length is not None:
        _require(type(length) is str and re.fullmatch(r'[0-9]{1,10}',length) is not None
            and int(length)<=MAX_METADATA,'Firmware metadata Content-Length exceeds its limit')
    body=bytearray();read=getattr(response,'read1',None) or response.read
    while True:
        if time.monotonic()>deadline:raise CatalogError('Official firmware catalog timed out',code='network_unavailable')
        chunk=read(min(16384,MAX_METADATA+1-len(body)))
        _require(type(chunk) is bytes,'Invalid firmware response body')
        if not chunk:break
        body.extend(chunk)
        _require(len(body)<=MAX_METADATA,'Firmware metadata exceeds its limit')
    if length is not None:_require(len(body)==int(length),'Firmware metadata length does not match')
    return bytes(body)


def fetch_catalog(sn,expected_type,current_version=None,*,hardware_version=None,opener=None):
    """One manual metadata GET; optional HW filter is only an explicit input.

    ``opener`` has urllib's (Request, timeout=seconds) signature for isolated
    testing. Redirects are disabled by the default opener and checked again on
    the returned response. No compatibility or firmware-write action occurs.
    """
    _arguments(sn,expected_type,current_version,hardware_version)
    _require(opener is None or callable(opener),'Invalid firmware metadata opener')
    query=dict(sn=sn)
    if hardware_version is not None:query['hw_version']=hardware_version
    url=ORIGIN+PATH+'?'+urlencode(query)
    request=Request(url,headers={'Accept':'application/json','Accept-Encoding':'identity','User-Agent':'HandWorkbench-Catalog/1'},method='GET')
    open_http=opener or build_opener(_NoRedirect()).open
    deadline=time.monotonic()+TIMEOUT
    try:
        try:response=open_http(request,timeout=TIMEOUT)
        except HTTPError as error:response=error
        with response:
            status=response.getcode()
            raw=_read(response,url,deadline)
        return parse_catalog(raw,sn,expected_type,current_version,hardware_version=hardware_version,http_status=status)
    except CatalogError:raise
    except (OSError,URLError,TimeoutError):
        raise CatalogError('Official firmware catalog is unavailable',code='network_unavailable') from None
