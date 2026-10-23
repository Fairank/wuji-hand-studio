"""Background version checks, with no automatic install or device discovery.

Software checks read official metadata and import the configured SDK only.
Firmware checks require a previously explicit session, fresh exact-SN discovery
and the normal all-workspace idle/ownership gate. An unknown report is never
interpreted as up-to-date. Callbacks keep this scheduler independently testable.
"""
import copy
import hashlib
import json
import os
import re
import threading
import time

INTERVAL=6*60*60
SDK_INTERVAL=14*60
RETRY=30*60
FIRMWARE_INTERVAL=24*60*60
_SN=re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z')


def _serial(value):
    return value if isinstance(value,str) and _SN.fullmatch(value) else None


def _version(value):
    if isinstance(value,str) and re.fullmatch(r'\d+(?:\.\d+){1,3}',value):
        return tuple(map(int,value.split('.')))
    return None


def _newer(latest,current):
    left,right=_version(latest),_version(current)
    return left is not None and right is not None and left>right


def _type_matches(kind,device_type):
    return device_type in ({'wuji_glove'} if kind=='glove' else {'wuji_hand','wuji_hand_2'} if kind=='hand' else set())


class UpdateNotifications:
    def __init__(self,*,states,app,cli,sdk,firmware,devices,idle,clock=time.time):
        self.states,self.app,self.cli,self.sdk=states,app,cli,sdk
        self.firmware,self.devices,self.idle=firmware,devices,idle
        self.clock=clock;self.lock=threading.RLock();self.closed=threading.Event()
        self.dispatch_lock=threading.RLock()
        self.thread=None;self.next_check={};self.known={};self.reports={}
        self.pending_firmware=None
        self.idle_since=None;self.checked_at=None

    def startup(self):
        if (os.environ.get('WUJI_FLEET_ID') or os.environ.get('WUJI_FLEET_PARENT')
                or os.environ.get('WUJI_UPDATE_AUTO_CHECK')=='0'
                or os.environ.get('WUJI_APP_AUTO_CHECK')=='0' and os.environ.get('WUJI_CLI_AUTO_CHECK')=='0'):return
        with self.lock:
            if self.thread and self.thread.is_alive():return
            self.thread=threading.Thread(target=self._run,name='Version notifications',daemon=True)
            self.thread.start()

    def close(self):
        with self.dispatch_lock:self.closed.set()

    def _dispatch(self,callback):
        # Separate from the state lock: callbacks may read Controller.snapshot,
        # whose observer takes the state lock in the opposite call direction.
        with self.dispatch_lock:
            if self.closed.is_set():return False
            callback();return True

    def _run(self):
        # Let initial controls render and existing startup metadata checks finish.
        if self.closed.wait(20):return
        while not self.closed.is_set():
            try:self.tick()
            except Exception:pass  # Unavailable/offline is not an update notice.
            if self.closed.wait(15):return

    def _remember(self,sn,kind,version=None):
        sn=_serial(sn)
        if not sn or kind not in ('hand','glove'):return
        with self.lock:
            previous=self.known.get(sn,{})
            row=dict(serial=sn,kind=kind,last_connected=self.clock())
            if isinstance(version,dict) and version.get('source')=='sdk_handle_info' and version.get('serial')==sn:
                row['firmware_version']=version.get('firmware_version')
                report=self.reports.get(sn)
                if report and report.get('current')!=row['firmware_version']:
                    self.reports.pop(sn,None);self.next_check.pop('firmware:'+sn,None)
            elif previous.get('firmware_version'):row['firmware_version']=previous['firmware_version']
            self.known[sn]=row
            # Session-local, bounded memory; history alone never authorizes I/O.
            while len(self.known)>128:self.known.pop(next(iter(self.known)))

    def observe(self,states):
        for state in states:
            if not isinstance(state,dict):continue
            identity=state.get('identified_device') or {}
            if state.get('connection')=='connected' and identity.get('source')=='sdk_identity' and identity.get('serial')==state.get('device_id'):
                self._remember(identity.get('serial'),'hand',state.get('device_version'))
            glove=state.get('glove') or {}
            if glove.get('connection')=='receiving':
                self._remember(glove.get('glove_serial'),'glove',glove.get('device_version'))
            feedback=glove.get('feedback') or {};hand=feedback.get('identified_device') or {}
            if hand.get('source')=='sdk_identity' and hand.get('serial')==feedback.get('device_id'):
                self._remember(hand.get('serial'),'hand',feedback.get('device_version'))
            calibration=state.get('calibration') or {}
            if calibration.get('running') is True and calibration.get('id') and calibration.get('source') in ('official_tty','official_jsonl_native'):
                self._remember(calibration.get('serial'),'glove')
            viewer=state.get('official_viz') or {};device=viewer.get('reported_device') or {}
            if viewer.get('active') is True and viewer.get('phase')=='running' and viewer.get('session_id'):
                self._remember(device.get('serial') or device.get('sn'),device.get('kind'))

    def _due(self,key,now):
        return now>=self.next_check.get(key,0)

    def _software(self,now):
        for key,getter in (('workbench',self.app),('cli',self.cli)):
            if self.closed.is_set():return
            if os.environ.get('WUJI_APP_AUTO_CHECK' if key=='workbench' else 'WUJI_CLI_AUTO_CHECK')=='0':continue
            if not self._due(key,now):continue
            try:
                service=getter();state=service.snapshot()
                busy=state.get('busy')
                if busy:continue
                update=state.get('update',state)
                stamp=update.get('checked_at')
                if type(stamp) in (int,float) and 0<=now-stamp<INTERVAL:
                    self.next_check[key]=stamp+INTERVAL;continue
                if key=='cli':self._dispatch(lambda:service.start(check_only=True,automatic=True))
                else:self._dispatch(lambda:service.start('check'))
                self.next_check[key]=now+RETRY
            except Exception:self.next_check[key]=now+RETRY
        if self._due('sdk',now):
            if self.closed.is_set():return
            try:
                done=self.sdk().check_background(cancelled=self.closed.is_set)
                self.next_check['sdk']=now+(SDK_INTERVAL if done else 60)
            except Exception:self.next_check['sdk']=now+RETRY

    def _hardware(self,now):
        if self.closed.is_set():return
        try:self.idle()
        except Exception:self.idle_since=None;return
        if self.closed.is_set():return
        if self.idle_since is None:self.idle_since=now;return
        # Discovery authority expires after 30 seconds. A 5-second grace lets
        # the next 15-second tick use that evidence without starting a new scan.
        if now-self.idle_since<5:return
        service=self.firmware();state=service.snapshot()
        if state.get('busy') or state.get('locked') or state.get('can_upgrade'):return
        if self.pending_firmware:
            sn,started=self.pending_firmware;device=state.get('device') or {}
            finished=state.get('finished_at')
            if ((device.get('sn') or device.get('serial'))==sn and type(finished) in (int,float) and finished>=started):
                if not state.get('notification'):self.next_check['firmware:'+sn]=now+RETRY
                self.pending_firmware=None
        report=state.get('notification')
        if isinstance(report,dict) and _serial(report.get('serial')) in self.known:
            known=self.known[report['serial']];current=known.get('firmware_version')
            if _type_matches(known['kind'],report.get('device_type')) and (current is None or current==report.get('current')):
                report=copy.deepcopy(report);report['checked_at']=state.get('checked_at')
                with self.lock:self.reports[report['serial']]=report
        rows=self.devices()
        if self.closed.is_set():return
        for row in rows:
            sn=_serial(row.get('serial') or row.get('sn'))
            if (sn not in self.known or not self._due('firmware:'+sn,now)
                    or row.get('stale') is not False or row.get('selectable') is not True
                    or row.get('conflict') or row.get('in_use') or row.get('kind')!=self.known[sn]['kind']):continue
            # The service repeats freshness, exact identity and the global gate.
            # Only a finite read-only --check runs; no --list/write/yes action.
            try:
                if not self._dispatch(lambda:service.start_check(sn,notification_only=True)):return
                self.pending_firmware=(sn,now)
                self.next_check['firmware:'+sn]=now+FIRMWARE_INTERVAL
            except Exception:self.next_check['firmware:'+sn]=now+RETRY
            break

    def tick(self):
        now=self.clock()
        self.observe(self.states())
        self._software(now)
        if not self.closed.is_set():self._hardware(now)
        with self.lock:self.checked_at=now

    def snapshot(self):
        # Pure status: never starts a query from GET or from an embedded task.
        items=[]
        try:
            app=self.app().snapshot()
            if app.get('available') is True and _newer(app.get('latest_version'),app.get('current_version')):
                items.append(dict(kind='workbench',version=app['latest_version']))
        except Exception:pass
        try:
            cli=self.cli().snapshot();update=cli.get('update') or {};current=cli.get('current') or {}
            if (update.get('available') or update.get('newer_cli_available')) and _newer(update.get('latest_version'),current.get('version')):
                items.append(dict(kind='cli',version=update['latest_version']))
        except Exception:pass
        try:
            sdk=self.sdk().snapshot();release=sdk.get('release') or {};proof=sdk.get('proof') or {}
            from sdk_release import MAX_AGE
            stamp=release.get('checked_at');fresh=type(stamp) in (int,float) and 0<=self.clock()-stamp<MAX_AGE
            if fresh and sdk.get('phase') in ('available','external') and _newer(release.get('version'),proof.get('version')):
                items.append(dict(kind='sdk',version=release['version']))
        except Exception:pass
        with self.lock:
            reports=copy.deepcopy(self.reports);devices=copy.deepcopy(list(self.known.values()));stamp=self.checked_at
        for sn,row in sorted(reports.items()):
            age=self.clock()-(row.get('checked_at') or 0)
            known=next((d for d in devices if d['serial']==sn),{});current=known.get('firmware_version')
            if (_type_matches(known.get('kind'),row.get('device_type')) and (current is None or current==row.get('current'))
                    and 0<=age<FIRMWARE_INTERVAL and row.get('update_available') is True and _newer(row.get('latest'),row.get('current'))):
                model={'wuji_glove':'Wuji Glove','wuji_hand':'Wuji Hand','wuji_hand_2':'Wuji Hand 2'}[row['device_type']]
                items.append(dict(kind='firmware',serial=sn,version=row['latest'],current_version=row['current'],model=model))
        items=items[:40]
        for row in items:
            row['id']=hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()[:24]
        revision=hashlib.sha256(json.dumps(items,sort_keys=True).encode()).hexdigest()[:24]
        return dict(revision=revision,items=items,checked_at=stamp,devices=devices)
