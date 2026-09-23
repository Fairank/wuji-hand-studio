"""One app, independent local service processes and data directories per hand.

No hardware is connected on creation. Child pages keep their existing leases;
switching the visible workspace does not restart a controller or move a hand.
"""
import json
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen


class DeviceFleet:
    def __init__(self, data, resource, origin):
        self.data, self.resource, self.origin = Path(data), Path(resource), origin
        self.lock = threading.RLock()
        self.children = {}
        self.records=self.data/'device_workspaces.json'
        try:
            saved=json.loads(self.records.read_text(encoding='utf-8'))
            if not isinstance(saved,list) or len(saved)>7:raise ValueError('Invalid device workspace catalog')
        except FileNotFoundError:saved=[]
        for item in saved:
            if (isinstance(item,dict) and isinstance(item.get('id'),str) and len(item['id'])==16
                and all(c in '0123456789abcdef' for c in item['id'])):
                try:self.create(item['label'],item['profile'],_ident=item['id'])
                except (OSError,ValueError):pass

    def create(self, label, profile, _ident=None):
        from device_profiles import PROFILES
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 40:
            raise ValueError('设备工作区名称需为1–40字 / Workspace name: 1–40 characters')
        if profile not in PROFILES:
            raise ValueError('Unknown device profile')
        with self.lock:
            if len(self.children) >= 7:
                raise ValueError('最多8个同时打开的设备工作区 / Up to eight device workspaces')
            ident = _ident or secrets.token_hex(8)
            folder = self.data/'devices'/ident
            if not _ident:
                folder.mkdir(parents=True)
                for name in ('connection.json', 'motion_parameters.py'):
                    if (self.data/name).is_file():
                        shutil.copyfile(self.data/name, folder/name)
                (folder/'device_profile.json').write_text(json.dumps({'id': profile}), encoding='utf-8')
            elif not folder.is_dir():raise ValueError('Device workspace data is missing')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            env = dict(os.environ, WUJI_STUDIO_DATA=str(folder), WUJI_STUDIO_PORT=str(port),
                       WUJI_FLEET_PARENT=self.origin, WUJI_FLEET_ID=ident)
            command = [sys.executable, '--serve'] if getattr(sys, 'frozen', False) else [sys.executable, str(self.resource/'desktop.py'), '--serve']
            with (folder/'console.log').open('ab') as log:
                child = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                                         stdout=log, stderr=log,
                                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            record = dict(id=ident, label=label.strip(), port=port, profile=profile, process=child)
            self.children[ident] = record
        for _ in range(100):
            if child.poll() is not None:
                raise ValueError('设备工作区启动失败 / Device workspace failed to start')
            try:
                self.get(record, '/api/state')
                if not _ident:
                    rows=json.loads(self.records.read_text(encoding='utf-8')) if self.records.exists() else []
                    rows.append({k:record[k] for k in ('id','label','profile')})
                    staged=self.records.with_suffix('.pending')
                    staged.write_text(json.dumps(rows,ensure_ascii=False),encoding='utf-8')
                    staged.replace(self.records)
                return self.public(record)
            except (OSError, ValueError):
                time.sleep(.1)
        raise ValueError('设备工作区启动超时，请查看设备列表 / Workspace startup timed out; check device list')

    @staticmethod
    def public(r):
        return {k: r[k] for k in ('id', 'label', 'port', 'profile')}

    @staticmethod
    def get(r, path):
        with urlopen(f'http://127.0.0.1:{r["port"]}'+path, timeout=1) as response:
            return json.load(response)

    def post(self, r, command):
        state = self.get(r, '/api/state')
        origin = f'http://127.0.0.1:{r["port"]}'
        request = Request(origin+'/api/action', data=json.dumps(command).encode(),
                          headers={'Content-Type': 'application/json', 'Origin': origin,
                                   'X-Console-Token': state['csrf']})
        with urlopen(request, timeout=15 if command.get('name')=='session_close' else 4) as response:
            return json.load(response)

    def heartbeat(self):
        with self.lock:records=list(self.children.values())
        def beat(r):
            if r['process'].poll() is not None:return None
            try:self.post(r, {'name':'session_keepalive'})
            except (OSError, ValueError) as error:return dict(id=r['id'],error=str(error))
            return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            return [x for x in pool.map(beat,records) if x]

    def snapshot(self):
        with self.lock:
            records = list(self.children.values())
        def describe(r):
            row = self.public(r)
            try:
                s = self.get(r, '/api/state')
                row.update(connection=s['connection'], device_id=s['device_id'],
                           profile=s['device_profile']['id'], hardware=s['hardware'].get('active'),
                           glove=s.get('glove', {}).get('connection'), program=s.get('program', {}))
            except (OSError, ValueError):
                row.update(connection='offline', hardware=None)
            return row
        with ThreadPoolExecutor(max_workers=8) as pool:
            return list(pool.map(describe,records))

    def stop_all(self):
        errors = []
        for r in list(self.children.values()):
            try:glove_busy=self.get(r,'/api/state').get('glove',{}).get('busy',False)
            except (OSError, ValueError):glove_busy=False
            for name in ('program_stop','glove_stop' if glove_busy else 'hardware_stop'):
                try:self.post(r, {'name': name})
                except (OSError, ValueError) as error:
                    errors.append(dict(id=r['id'],action=name,error=str(error)))
        return errors

    def remove(self, ident):
        if not isinstance(ident,str) or ident not in self.children:raise ValueError('Unknown workspace')
        r=self.children[ident]
        response=self.post(r,{'name':'session_close'})
        if not response.get('ok'):return response
        if r['process'].poll() is None:
            r['process'].terminate()
            try:r['process'].wait(timeout=5)
            except subprocess.TimeoutExpired:r['process'].kill()
        with self.lock:
            self.children.pop(ident,None)
            rows=json.loads(self.records.read_text(encoding='utf-8')) if self.records.exists() else []
            staged=self.records.with_suffix('.pending')
            staged.write_text(json.dumps([x for x in rows if x.get('id')!=ident],ensure_ascii=False),encoding='utf-8')
            staged.replace(self.records)
        return dict(removed=True)

    def close(self):
        for r in list(self.children.values()):
            try:
                response=self.post(r, {'name': 'session_close'})
                if not response.get('ok'):
                    return response
            except (OSError, ValueError) as error:
                if r['process'].poll() is None:
                    return dict(ok=False,error='设备工作区尚未确认断开 / Workspace disconnect unconfirmed: '+str(error))
        for r in list(self.children.values()):
            if r['process'].poll() is None:
                r['process'].terminate()
                try:r['process'].wait(timeout=5)
                except subprocess.TimeoutExpired:r['process'].kill()
        self.children.clear()
        return dict(ok=True)
