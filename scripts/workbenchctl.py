"""Code-only desktop maintenance. No mouse, keyboard, desktop capture or arbitrary JS.

Use --port from the app's native-window.json. All commands target this app only.
"""
import argparse
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


class Client:
    def __init__(self, port):
        if not 1024 <= port <= 65535:raise ValueError('Invalid local port')
        self.origin=f'http://127.0.0.1:{port}'

    def get(self, route):
        with urlopen(self.origin+route, timeout=15) as response:return json.load(response)

    def call(self, operation, **values):
        token=self.get('/api/state')['csrf']
        request=Request(self.origin+'/api/desktop',json.dumps(dict(operation=operation,**values)).encode(),
                        {'Content-Type':'application/json','X-Console-Token':token,'Origin':self.origin})
        try:
            with urlopen(request, timeout=30) as response:result=json.load(response)
        except HTTPError as error:
            detail=json.load(error)
            raise RuntimeError(detail.get('error',str(error))) from None
        if not result.get('ok'):raise RuntimeError(result.get('error','Desktop request failed'))
        return result


def checksum(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def upgrade(client, source, digest, apply):
    """Stage a locally supplied release; verify before and after orderly app exit."""
    if sys.platform!='win32':raise ValueError('Windows installer required')
    info=client.get('/api/desktop')
    if not info.get('native'):raise ValueError('Start the Windows desktop app first')
    prefix='HandWorkbench'
    match=re.fullmatch(re.escape(prefix)+r'-(\d{1,4}\.\d{1,4}\.\d{1,4})-windows-x64-setup\.exe',source.name)
    if not match or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Invalid installer name or SHA-256')
    if checksum(source)!=digest:raise ValueError('Installer hash does not match the trusted release')
    root=Path(info['data_dir']).resolve();folder=root/'updates'
    # Packaged Windows hosts can virtualize LocalAppData after the final path
    # component. Reject reparse-point redirects, not that OS path translation.
    if folder.parent.resolve()!=root or folder.is_symlink() or folder.is_junction():raise ValueError('Invalid updates directory')
    folder.mkdir(exist_ok=True)
    destination=folder/source.name
    if destination.is_symlink() or destination.is_junction():raise ValueError('Invalid installer destination')
    if not destination.exists():
        # Exclusive creation avoids overwriting an earlier staged installer.
        with source.open('rb') as src,destination.open('xb') as dst:shutil.copyfileobj(src,dst)
    result=client.call('prepare_update',version=match[1],sha256=digest)
    if not apply:return result
    client.call('close_idle')
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        try:client.get('/api/desktop')
        except (URLError,ConnectionError,http.client.RemoteDisconnected):break
        time.sleep(.25)
    else:raise RuntimeError('Application did not exit; installer was not started')
    if checksum(destination)!=digest:raise ValueError('Staged installer changed')
    # Installer AppMutex is the final check that the desktop process has exited.
    args=[str(destination),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER',
          '/NOCLOSEAPPLICATIONS','/NORESTARTAPPLICATIONS']
    completed=subprocess.run(args,check=False)
    if completed.returncode:raise RuntimeError(f'Installer exited with code {completed.returncode}')
    return dict(ok=True,installed_version=match[1],edition=info['edition'],app_restarted=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,required=True)
    sub=parser.add_subparsers(dest='command',required=True)
    for command in ('info','inspect','close-idle','viewer'):sub.add_parser(command)
    capture=sub.add_parser('capture');capture.add_argument('output',type=Path)
    page=sub.add_parser('page');page.add_argument('page')
    resize=sub.add_parser('resize');resize.add_argument('width',type=int);resize.add_argument('height',type=int)
    appearance=sub.add_parser('appearance');appearance.add_argument('key');appearance.add_argument('value')
    menu=sub.add_parser('menu');menu.add_argument('state',choices=('open','closed'))
    update=sub.add_parser('upgrade');update.add_argument('installer',type=Path);update.add_argument('--sha256',required=True);update.add_argument('--apply',action='store_true')
    model=sub.add_parser('import-model');model.add_argument('pack',type=Path)
    args=parser.parse_args();client=Client(args.port)
    if args.command=='info':result=client.get('/api/desktop')
    elif args.command=='capture':
        result=client.call('capture');raw=base64.b64decode(result.pop('image'),validate=True)
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_bytes(raw);result['path']=str(args.output.resolve())
    elif args.command=='page':result=client.call('page',page=args.page)
    elif args.command=='resize':result=client.call('resize',width=args.width,height=args.height)
    elif args.command=='appearance':result=client.call('appearance',key=args.key,value={'true':True,'false':False}.get(args.value,args.value))
    elif args.command=='menu':result=client.call('menu',open=args.state=='open')
    elif args.command=='upgrade':result=upgrade(client,args.installer.resolve(),args.sha256,args.apply)
    elif args.command=='import-model':
        if args.pack.stat().st_size>6_000_000:raise ValueError('Model pack too large')
        digest=checksum(args.pack);root=Path(client.get('/api/desktop')['data_dir']).resolve();folder=root/'imports'
        if folder.resolve()!=folder:raise ValueError('Invalid imports directory')
        folder.mkdir(exist_ok=True);target=folder/('model-'+digest+'.zip')
        if target.is_symlink():raise ValueError('Invalid staged path')
        if not target.exists():
            with target.open('xb') as dst,args.pack.open('rb') as src:shutil.copyfileobj(src,dst)
        result=client.call('import_model',sha256=digest)
    else:result=client.call(args.command.replace('-','_'))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
