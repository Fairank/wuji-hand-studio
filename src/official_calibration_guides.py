"""Read pinned public pose references into a private, verified user cache.

Only source URLs and integrity metadata ship with the application. Images stay
copyright their official publisher and are not part of the installer payload.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid

_lock = threading.Lock()
_MAX = 4 * 1024 * 1024


def _url(value):
    parsed=urlsplit(value)
    if (parsed.scheme!='https' or parsed.hostname!='mintcdn.com' or parsed.port not in (None,443)
            or parsed.username or parsed.password or parsed.fragment):
        raise ValueError('Invalid official guide source')
    return value


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req,fp,code,msg,headers,_url(newurl))


def _verify(data, item):
    if len(data)!=item['size'] or hashlib.sha256(data).hexdigest()!=item['sha256']:
        raise ValueError('Official guide integrity verification failed')
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        if (image.size!=(item['width'],item['height'])
                or {'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}.get(image.format)!=item['mime']):
            raise ValueError('Invalid official guide image')
        image.verify()
    return data


def read_guide(step):
    from runtime_paths import RESOURCE,DATA
    if type(step) is not int or not 0<=step<6:raise ValueError('Unknown official calibration pose')
    manifest=json.loads((RESOURCE/'official_calibration_guides.json').read_text(encoding='utf-8'))
    items=manifest.get('poses')
    if not isinstance(items,list) or len(items)!=6:raise ValueError('Official guide metadata is unavailable')
    item=items[step]
    if (not isinstance(item,dict) or type(item.get('size')) is not int or not 0<item['size']<=_MAX
            or item.get('mime') not in ('image/png','image/jpeg','image/webp')
            or not isinstance(item.get('sha256'),str) or len(item['sha256'])!=64
            or any(type(item.get(key)) is not int or not 0<item[key]<=4096 for key in ('width','height'))):
        raise ValueError('Invalid official guide metadata')
    url=_url(item['url'])
    folder=DATA/'official-calibration-guides'
    path=folder/(item['sha256']+'.image')
    with _lock:
        folder.mkdir(parents=True,exist_ok=True)
        for parent in (folder,*folder.parents):
            if parent.is_symlink() or getattr(parent,'is_junction',lambda:False)():
                raise ValueError('Guide cache cannot traverse a redirected folder')
        if path.exists():
            if path.is_symlink() or not path.is_file() or path.stat().st_size>_MAX:
                raise ValueError('Invalid guide cache entry')
            return _verify(path.read_bytes(),item),item['mime']
        request=urllib.request.Request(url,headers={'User-Agent':'HandWorkbench official-reference-cache','Accept':item['mime']})
        try:
            with urllib.request.build_opener(_Redirect()).open(request,timeout=8) as response:
                _url(response.geturl())
                if response.headers.get_content_type()!=item['mime']:raise ValueError('Unexpected official guide content type')
                data=response.read(_MAX+1)
        except urllib.error.URLError as error:
            raise OSError('官方参考图暂不可用，请按动作文字或打开官方说明 / Official pose reference is temporarily unavailable') from error
        _verify(data,item)
        temporary=folder/(uuid.uuid4().hex+'.tmp')
        try:
            with temporary.open('xb') as stream:stream.write(data)
            os.replace(temporary,path)
        finally:
            try:temporary.unlink(missing_ok=True)
            except OSError:pass
        return data,item['mime']
