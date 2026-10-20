"""Observed stop acknowledgements, independent of transport/thread lifetime.

This records uncertainty, not inferred motor state. A new read-only connection
or a different device's acknowledgement cannot erase an earlier uncertainty.
"""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading


def unconfirmed(state):
    rows = copy.deepcopy(state.get('unconfirmed_devices', []))
    for source, device, hw in (
        ('hand', state.get('device_id'), state.get('hardware', {})),
        ('glove', state.get('glove', {}).get('feedback', {}).get('device_id'),
         state.get('glove', {}).get('hardware', {})),
    ):
        if 'active' in hw and hw['active'] is not False:
            row = dict(source=source, device_id=device, reason=hw.get('reason') or '停用结果未确认 / Disable not confirmed')
            if not any(r.get('source') == source and r.get('device_id') == device for r in rows):rows.append(row)
    return rows


class TerminationJournal:
    def __init__(self, path):
        self.path = Path(path); self.lock = threading.RLock(); self.rows = []; self.next_session=1;self.confirmed={};self.dirty=False
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding='utf-8'))
                rows = saved.get('rows') if isinstance(saved,dict) else saved
                if isinstance(saved,dict):
                    self.next_session=saved['next_session']
                    if type(self.next_session) is not int or self.next_session<1:raise ValueError('Invalid session counter')
                    self.confirmed=saved.get('confirmed',{})
                    if not isinstance(self.confirmed,dict) or not all(isinstance(k,str) and type(v) is int and v>=0 for k,v in self.confirmed.items()):raise ValueError('Invalid acknowledgement epochs')
                if not isinstance(rows, list) or not all(isinstance(r, dict) and isinstance(r.get('source'), str) for r in rows):raise ValueError('Invalid stop journal')
                if not all(type(r.get('session',0)) is int and r.get('session',0)>=0 for r in rows):raise ValueError('Invalid session epochs')
                self.rows = rows
                self.next_session=max(self.next_session,1+max((r.get('session',0) for r in rows),default=0),1+max(self.confirmed.values(),default=0))
            except (OSError, ValueError,KeyError,TypeError):
                self.confirmed={};self.next_session=1
                self.rows = [dict(source='journal', device_id=None, reason='停用确认记录无法读取 / Stop acknowledgement record could not be read')]

    def new_session(self):
        with self.lock:
            session=self.next_session;self.next_session+=1;self.dirty=True;self.save();return session

    def observe(self, source, device, hardware, session=0):
        if 'active' not in hardware:return
        key = str(device) if device else 'session:' + str(session)
        with self.lock:
            before = copy.deepcopy(self.rows)
            old_confirmed=dict(self.confirmed)
            if hardware.get('active') is False and hardware.get('stop_confirmed') is True:
                # Acknowledgement belongs to this exact device/session only.
                self.rows = [r for r in self.rows if not (r.get('key') == key and r.get('session',0)<=session and (device or r.get('source') == source))]
                if device:self.confirmed[key]=max(self.confirmed.get(key,0),session)
            elif hardware.get('active') is not False:
                if device and session<self.confirmed.get(key,-1):return
                self.rows = [r for r in self.rows if not (r.get('key') == key and r.get('source') == source and r.get('session',0)==session)]
                self.rows.append(dict(source=source, key=key, session=session,device_id=str(device) if device else None,
                                      reason='停用结果未确认 / Disable not confirmed'))
            if self.rows != before or self.confirmed!=old_confirmed:self.dirty=True
            if self.dirty:self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=self.path.name + '.', suffix='.partial', dir=self.path.parent)
        stage = Path(name)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(dict(version=1,rows=self.rows,next_session=self.next_session,confirmed=self.confirmed), stream, ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
            os.replace(stage, self.path)
            self.dirty=False
        finally:stage.unlink(missing_ok=True)

    def snapshot(self):
        with self.lock:
            if self.dirty:
                try:self.save()
                except OSError:pass  # Keep the in-memory uncertainty visible.
            return copy.deepcopy(self.rows)
