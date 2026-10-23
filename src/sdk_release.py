"""Official stable SDK catalog. No device operations and no package installation."""
import copy
import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
import time
import urllib.parse
import urllib.request

INDEX='https://pypi.org/pypi/wuji-sdk/json'
MAX_BYTES=4*1024*1024
MAX_AGE=15*60
_VERSION=re.compile(r'20\d{2}\.\d{1,2}\.\d{1,2}')
_SHA=re.compile(r'[0-9a-f]{64}')


def stable_version(value):
    if not isinstance(value,str) or not _VERSION.fullmatch(value):
        raise ValueError('SDK 稳定版本格式无效 / Invalid SDK stable version')
    try:datetime.date(*map(int,value.split('.')))
    except ValueError:raise ValueError('Invalid SDK release date') from None
    return value


def validate_release(value,now=None,require_fresh=True):
    now=time.time() if now is None else now
    if not isinstance(value,dict) or value.get('source')!=INDEX:
        raise ValueError('SDK 官方发布来源无效 / Invalid official SDK source')
    stable_version(value.get('version'))
    stamp=value.get('checked_at')
    if type(stamp) not in (int,float) or not math.isfinite(stamp) or stamp>now+30 or stamp<=0:
        raise ValueError('Invalid SDK catalog timestamp')
    if require_fresh and now-stamp>MAX_AGE:
        raise ValueError('SDK 版本目录已过期，请联网刷新 / Refresh the SDK release catalog online')
    if not isinstance(value.get('catalog_sha256'),str) or not _SHA.fullmatch(value['catalog_sha256']):
        raise ValueError('Invalid SDK catalog digest')
    files=value.get('files')
    if not isinstance(files,list) or not 1<=len(files)<=100:
        raise ValueError('SDK 稳定版没有可验证的安装包 / No verified stable SDK wheel')
    for row in files:
        if not isinstance(row,dict):raise ValueError('Invalid SDK wheel metadata')
        filename=row.get('filename','');url=urllib.parse.urlsplit(row.get('url',''))
        if (not re.fullmatch(r'wuji_sdk-'+re.escape(value['version'])+r'-cp\d+-cp\d+-[A-Za-z0-9_.]+\.whl',filename)
                or url.scheme!='https' or url.hostname!='files.pythonhosted.org' or url.port not in (None,443)
                or url.username or url.password or url.fragment or url.query
                or Path(url.path).name!=filename or row.get('yanked') is not False
                or type(row.get('size')) is not int or not 1<=row['size']<=80*1024*1024
                or not isinstance(row.get('sha256'),str) or not _SHA.fullmatch(row['sha256'])):
            raise ValueError('SDK 安装包来源或校验值无效 / Invalid official SDK wheel provenance')
    if not isinstance(value.get('requires_dist',[]),list):raise ValueError('Invalid SDK dependency metadata')
    return copy.deepcopy(value)


def parse_catalog(raw,now=None):
    if not isinstance(raw,bytes) or len(raw)>MAX_BYTES:raise ValueError('SDK catalog exceeds limit')
    data=json.loads(raw);version=stable_version(data['info']['version'])
    # PyPI is the official binary distribution. Never select a prerelease,
    # yanked upload, source archive, main branch or an inferred platform build.
    files=[dict(filename=x['filename'],url=x['url'],sha256=x['digests']['sha256'],
                size=x['size'],yanked=False,requires_python=x.get('requires_python'))
           for x in data.get('urls',[]) if x.get('packagetype')=='bdist_wheel' and x.get('yanked') is False]
    value=dict(source=INDEX,version=version,checked_at=time.time() if now is None else now,
               catalog_sha256=hashlib.sha256(raw).hexdigest(),files=files,
               requires_python=data['info'].get('requires_python'),requires_dist=data['info'].get('requires_dist') or [])
    return validate_release(value,now=now)


def _read_official():
    def read(url):
        request=urllib.request.Request(url,headers={'User-Agent':'WujiHandWorkbench/SDK-updater','Accept':'application/json'})
        with urllib.request.urlopen(request,timeout=15) as response:
            if response.url!=url:raise ValueError('Official SDK catalog redirected unexpectedly')
            raw=response.read(MAX_BYTES+1)
            if len(raw)>MAX_BYTES:raise ValueError('SDK catalog exceeds limit')
            return raw
    raw=read(INDEX);document=json.loads(raw);stable=[]
    for version,files in document.get('releases',{}).items():
        try:stable_version(version)
        except ValueError:continue
        if any(row.get('packagetype')=='bdist_wheel' and row.get('yanked') is False for row in files):stable.append(version)
    if stable:
        selected=max(stable,key=lambda value:tuple(map(int,value.split('.'))))
        if document.get('info',{}).get('version')!=selected:
            raw=read('https://pypi.org/pypi/wuji-sdk/'+selected+'/json')
            if json.loads(raw).get('info',{}).get('version')!=selected:raise ValueError('SDK stable metadata version mismatch')
    return raw


def cache_path():
    from runtime_paths import DATA
    return DATA/'components'/'sdk-stable.json'


def resolve_release(path=None,*,force=False,fetch=None,now=None):
    """15 minute snapshot; a failed refresh never extends the verified timestamp."""
    path=Path(path) if path is not None else cache_path()
    now=time.time() if now is None else now
    cached=None
    try:cached=validate_release(json.loads(path.read_text(encoding='utf-8')),now=now)
    except (OSError,ValueError,TypeError,KeyError):pass
    if cached is not None and not force:return dict(cached,offline=False)
    try:
        value=parse_catalog((fetch or _read_official)(),now=now)
    except Exception:
        if cached is not None:return dict(cached,offline=True)
        raise ValueError('无法核对官方稳定 SDK，请联网后在设置中刷新组件 / Connect to the internet and refresh official SDK components in Settings') from None
    path.parent.mkdir(parents=True,exist_ok=True)
    pending=path.with_name(path.name+'.'+secrets.token_hex(8)+'.pending')
    try:
        pending.write_text(json.dumps(value,sort_keys=True),encoding='utf-8');pending.replace(path)
    finally:pending.unlink(missing_ok=True)
    return dict(value,offline=False)


def requirement(release):
    value=validate_release(release)
    return {key:value[key] for key in ('version','catalog_sha256','checked_at')}


def validate_requirement(value):
    # The controller uses the host's bounded, immutable launch snapshot.
    # Controller and host clocks need not agree; host enforces catalog age.
    if (not isinstance(value,dict) or set(value)!={'version','catalog_sha256','checked_at'}
            or not isinstance(value.get('catalog_sha256'),str) or not _SHA.fullmatch(value['catalog_sha256'])
            or type(value.get('checked_at')) not in (int,float) or not math.isfinite(value['checked_at']) or value['checked_at']<=0):
        raise ValueError('Missing verified stable SDK requirement')
    stable_version(value.get('version'))
    return copy.deepcopy(value)


def select_wheel(release,proof):
    release=validate_release(release)
    if proof.get('platform')!='linux' or proof.get('implementation')!='cpython':
        raise ValueError('官方 SDK 需要受支持的 Linux 控制环境 / Use a supported Linux controller for the official SDK')
    abi=proof.get('python_tag');machine=proof.get('machine')
    if not isinstance(abi,str) or not re.fullmatch(r'cp3\d{1,2}',abi) or machine not in ('x86_64','aarch64'):
        raise ValueError('此控制环境没有匹配的官方 SDK 安装包 / No official SDK wheel matches this controller')
    libc=proof.get('libc') or []
    if (len(libc)!=2 or libc[0]!='glibc' or not re.fullmatch(r'\d+\.\d+(?:\.\d+)?',libc[1])
            or tuple(map(int,libc[1].split('.')[:2]))<(2,17)):
        raise ValueError('Official SDK requires glibc 2.17 or later')
    pattern=f'-{abi}-{abi}-manylinux_2_17_{machine}.manylinux2014_{machine}.whl'
    matches=[row for row in release['files'] if row['filename'].endswith(pattern)]
    if len(matches)!=1:raise ValueError('此 Python 版本没有匹配的官方 SDK 安装包 / No official wheel matches this Python version')
    return copy.deepcopy(matches[0])
