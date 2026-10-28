"""Background version checks, with no automatic install or device discovery.

Software checks read official metadata and import the configured SDK only.
Hardware reminders consume already sampled handle metadata and explicit check
results. They never reopen a disconnected device or start a firmware query.
An unknown report is never interpreted as up-to-date.
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


def _device_kind(device):
    if not isinstance(device,dict):return None
    kind=device.get('device_type')
    return {'wuji_glove':'glove','wuji_hand':'hand','wuji_hand_2':'hand'}.get(kind) if isinstance(kind,str) else None


class UpdateNotifications:
    def __init__(self,*,states,app,cli,sdk,firmware,devices,idle,clock=time.time):
        self.states,self.app,self.cli,self.sdk=states,app,cli,sdk
        self.firmware,self.devices,self.idle=firmware,devices,idle
        self.clock=clock;self.lock=threading.RLock();self.closed=threading.Event()
        self.dispatch_lock=threading.RLock()
        self.thread=None;self.next_check={};self.known={};self.reports={}
        self.pending_firmware=None
        self.idle_since=None;self.checked_at=None
        self.user_priority_until=0

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

    def _remember(self,sn,kind,version=None,observation=None):
        sn=_serial(sn)
        if not sn or kind not in ('hand','glove'):return
        with self.lock:
            previous=self.known.get(sn,{})
            now=self.clock();row=copy.deepcopy(previous)
            row.update(serial=sn,kind=kind,last_connected=now)
            if isinstance(version,dict) and version.get('source')=='sdk_handle_info' and version.get('serial')==sn:
                value=version.get('firmware_version')
                # Handle metadata is sampled once per connection. Reading an
                # unchanged snapshot again is not a new device observation and
                # must not roll back a later exact official --check result.
                new=(value!=previous.get('_sdk_firmware_version') or observation!=previous.get('_sdk_observation'))
                if new:
                    row.update(firmware_version=value,firmware_observed_at=now,firmware_version_source='sdk_handle_info')
                row.update(_sdk_firmware_version=value,_sdk_observation=observation)
                report=self.reports.get(sn)
                if report and report.get('current')!=row.get('firmware_version'):
                    row['_rejected_check']=(report.get('current'),report.get('latest'),report.get('checked_at'))
                    self.reports.pop(sn,None);self.next_check.pop('firmware:'+sn,None)
            self.known[sn]=row
            # Session-local, bounded memory; history alone never authorizes I/O.
            while len(self.known)>128:self.known.pop(next(iter(self.known)))

    def observe(self,states):
        for state in states:
            if not isinstance(state,dict):continue
            identity=state.get('identified_device') or {}
            if state.get('connection')=='connected' and identity.get('source')=='sdk_identity' and identity.get('serial')==state.get('device_id'):
                self._remember(identity.get('serial'),'hand',state.get('device_version'),('hand',state.get('session_generation')))
            glove=state.get('glove') or {}
            if glove.get('connection')=='receiving':
                self._remember(glove.get('glove_serial'),'glove',glove.get('device_version'),('glove',glove.get('session_id')))
            feedback=glove.get('feedback') or {};hand=feedback.get('identified_device') or {}
            if hand.get('source')=='sdk_identity' and hand.get('serial')==feedback.get('device_id'):
                self._remember(hand.get('serial'),'hand',feedback.get('device_version'),('paired',glove.get('session_id')))
            calibration=state.get('calibration') or {}
            if calibration.get('running') is True and calibration.get('id') and calibration.get('source') in ('official_tty','official_jsonl_native'):
                self._remember(calibration.get('serial'),'glove')
            viewer=state.get('official_viz') or {};device=viewer.get('reported_device') or {}
            if viewer.get('active') is True and viewer.get('phase')=='running' and viewer.get('session_id'):
                sn=device.get('serial') or device.get('sn')
                selected=viewer.get('device') or {};selected=selected if isinstance(selected,dict) else {}
                selected_sn=selected.get('serial') or selected.get('sn')
                # The real native Viewer reports sn/handedness, while its
                # selected device contains the authoritative device_type. Both
                # must agree exactly; never guess a family from an SN prefix.
                kind=_device_kind(selected) if selected_sn==sn else None
                if not selected and kind is None and device.get('kind') in ('hand','glove'):
                    kind=device['kind']
                self._remember(sn,kind)

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

    def _ingest_firmware(self,state,now):
        # Consume facts already obtained by an explicit check even while the
        # user's Viewer is active. This never opens a second CLI or device.
        if self.pending_firmware:
            sn,started=self.pending_firmware;device=state.get('device') or {}
            finished=state.get('finished_at')
            if ((device.get('sn') or device.get('serial'))==sn and type(finished) in (int,float) and finished>=started):
                if not state.get('notification'):self.next_check['firmware:'+sn]=now+RETRY
                self.pending_firmware=None
        recovery=state.get('result') or {};device=state.get('device') or {}
        recovered_sn=_serial(device.get('sn') or device.get('serial'))
        recovered_at=state.get('checked_at')
        recovered=(state.get('phase')=='verified' and state.get('locked') is False and state.get('busy') is False
            and recovery.get('verdict')=='recovery_verified' and recovery.get('sn')==recovered_sn
            and _version(recovery.get('firmware_version')) is not None
            and state.get('current_version')==recovery.get('firmware_version')
            and type(recovered_at) in (int,float) and 0<=now-recovered_at<FIRMWARE_INTERVAL)
        if recovered:
            with self.lock:
                known=self.known.get(recovered_sn)
                if known is not None and recovered_at>=known.get('firmware_observed_at',0):
                    known.update(firmware_version=recovery['firmware_version'],firmware_observed_at=recovered_at,
                        firmware_version_source='official_cli_recovery')
        report=state.get('notification')
        if not isinstance(report,dict):
            # A host may prove the actual version without a current catalog.
            # Its old preflight is retained as history, never re-timestamped.
            if recovered:return
            check=state.get('check_report') or {};device=state.get('device') or {}
            sn=_serial(device.get('sn') or device.get('serial'));known=self.known.get(sn)
            if known and check.get('returncode')==0:
                from device_versions import parse_official_check_report
                expected='wuji_glove' if known['kind']=='glove' else 'wuji_hand_2' if device.get('generation')=='hand2' else 'wuji_hand' if device.get('generation')=='hand1' else None
                candidate=parse_official_check_report(check.get('stdout'),sn,expected)
                if candidate and _type_matches(known['kind'],candidate.get('device_type')):report=candidate
        if isinstance(report,dict) and _serial(report.get('serial')) in self.known:
            known=self.known[report['serial']];current=known.get('firmware_version')
            checked=state.get('checked_at');observed=known.get('firmware_observed_at',0)
            valid_time=type(checked) in (int,float) and 0<=now-checked<FIRMWARE_INTERVAL
            rejected=known.get('_rejected_check')==(report.get('current'),report.get('latest'),checked)
            if (valid_time and _type_matches(known['kind'],report.get('device_type'))
                    and not (rejected and current!=report.get('current'))
                    and (current is None or current==report.get('current') or checked>=observed)):
                report=copy.deepcopy(report);report['checked_at']=checked
                with self.lock:
                    previous=self.reports.get(report['serial']) or {}
                    if checked>=previous.get('checked_at',0):
                        self.reports[report['serial']]=report
                        if checked>=observed:
                            known.update(firmware_version=report['current'],firmware_observed_at=checked,firmware_version_source='official_cli_check')

    def give_user_priority(self,timeout=3):
        """Defer new background checks and finish any existing finite read.

        Controller calls this before an explicit connection or task start. A
        false return preserves normal maintenance refusal; no write is stopped.
        """
        with self.dispatch_lock:
            self.user_priority_until=self.clock()+60
            service=self.firmware()
            return service.cancel_notification_check(timeout=timeout)

    def _hardware(self,now):
        if self.closed.is_set():return
        self._ingest_firmware(self.firmware().snapshot(),now)

    def tick(self):
        now=self.clock()
        self.observe(self.states())
        self._software(now)
        if not self.closed.is_set():self._hardware(now)
        with self.lock:self.checked_at=now

    def snapshot(self):
        # Pure status: never starts a query from GET or from an embedded task.
        # A just-connected Viewer can expose an already completed exact-SN
        # preflight report immediately, without waiting for the next scheduler
        # tick or starting a second query against the connected device.
        try:self._ingest_firmware(self.firmware().snapshot(),self.clock())
        except Exception:pass
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
            reports=copy.deepcopy(self.reports)
            devices=[{key:copy.deepcopy(value) for key,value in device.items() if not key.startswith('_')}
                     for device in self.known.values()]
            stamp=self.checked_at
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
