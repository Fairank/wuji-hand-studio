"""Run a selected playlist through the existing preview / hardware command path.

An explicit start and a renewable UI lease are required. An interrupted or failed
entry ends the playlist; it never silently skips a failed physical movement.
"""
import copy
import secrets
import threading
import time


class ProgramRunner:
    def __init__(self, controller):
        self.controller=controller
        self.thread=None
        self.cancel=threading.Event()
        self.lock=threading.RLock()
        self.state=dict(active=False, paused=False, mode=None, current=None, completed=0, reason='')
        self.lease=None
        self.heartbeat=0.

    def snapshot(self):
        with self.lock:return copy.deepcopy(self.state)

    def update(self, **values):
        with self.lock:self.state.update(values)

    def start(self, command):
        from playlist_plan import validate_plan
        from gesture_library import CATALOG
        allowed={x['id'] for x in CATALOG if x['group']=='dance'}
        plan=validate_plan(command.get('plan'),allowed)
        mode=command.get('mode')
        if mode not in ('preview','hardware'):raise ValueError('Choose preview or real hand')
        amplitude=command.get('amplitude',1.)
        if type(amplitude) not in (int,float) or amplitude not in (.25,.5,.75,1.):raise ValueError('Invalid amplitude')
        s=self.controller.snapshot()
        if self.thread and self.thread.is_alive():raise ValueError('节目正在播放 / Playlist already running')
        if s['hardware'].get('active') is not False or s.get('glove',{}).get('busy'):
            raise ValueError('先结束当前动作 / Finish current motion first')
        if mode=='hardware':
            if command.get('workspace_clear') is not True:raise ValueError('Confirm the workspace is clear')
            if s['connection']!='connected' or s['stale']:raise ValueError('先连接机械手 / Connect the hand first')
            if s['device_profile']['generation']!='hand2':raise ValueError('一代手指舞当前仅可预览 / Hand 1 dances are preview-only')
            if s['hardware'].get('gesture_library_version',0)<4:raise ValueError('先升级控制端动作库 / Update controller dance library')
        self.cancel.clear();self.heartbeat=time.monotonic();self.lease=secrets.token_hex(16)
        self.update(active=True,paused=False,mode=mode,current=None,completed=0,reason='',plan=plan)
        self.thread=threading.Thread(target=self.run,args=(plan,mode,amplitude),daemon=True,name='Selected dance playlist')
        self.thread.start()
        return dict(program_lease=self.lease)

    def keepalive(self, lease):
        if not self.lease or not secrets.compare_digest(str(lease),self.lease):raise ValueError('Expired playlist lease')
        self.heartbeat=time.monotonic()

    def stop(self):
        self.cancel.set()

    def check(self):
        if self.cancel.is_set():raise ValueError('用户停止 / Stopped by user')
        if time.monotonic()-self.heartbeat>.8:raise ValueError('节目控制连接中断 / Playlist control connection expired')

    def pause(self, paused):
        if not self.state['active']:raise ValueError('No active playlist')
        self.update(paused=paused)
        mode=self.state['mode']
        if mode=='hardware' and self.controller.hardware_lease:
            self.controller._action(dict(name='hardware_pause' if paused else 'hardware_resume',lease=self.controller.hardware_lease))
        elif mode=='preview':
            self.controller.player.command(dict(name='demo_pause' if paused else 'demo_resume'))

    def run(self, plan, mode, amplitude):
        from playlist_plan import iter_plan
        complete=0
        try:
            for entry in iter_plan(plan):
                self.check()
                while self.state['paused']:
                    self.check();time.sleep(.05)
                self.update(current=entry)
                # Execute a single cycle at a time so existing per-action completion
                # and disable confirmation stay authoritative for every entry.
                for cycle in range(entry['cycles']):
                    self.check()
                    while self.snapshot()['paused']:
                        self.check();time.sleep(.05)
                    if mode=='preview':
                        self.controller.action(dict(name='demo_start',action=entry['action'],speed=entry['speed'],cycles=1))
                        while True:
                            self.check()
                            p=self.controller.player.snapshot()
                            if not p['active']:raise ValueError('预览已中断 / Preview interrupted')
                            if not self.state['paused'] and p['elapsed_s']>=p['duration_s']:break
                            time.sleep(.05)
                    else:
                        started=time.time()
                        result=self.controller.action(dict(name='hardware_trial',action=entry['action'],
                            speed=entry['speed'],amplitude=amplitude,cycles=1,workspace_clear=True))
                        lease=result['lease'];seen=False;deadline=time.monotonic()+8
                        while True:
                            self.check()
                            self.controller.action(dict(name='hardware_keepalive',lease=lease))
                            s=self.controller.snapshot();h=s['hardware']
                            if s['stale']:raise ValueError('机械手反馈中断 / Hand feedback expired')
                            seen=seen or h.get('active') is True
                            if h.get('active') is False and seen:
                                r=h.get('trial_result') or {}
                                if r.get('started_unix',0)<started-1 or not r.get('completed') or not h.get('stop_confirmed'):
                                    raise ValueError(r.get('reason') or '本段未正常完成 / Entry did not complete')
                                break
                            if not seen and time.monotonic()>deadline:raise ValueError('动作未启动 / Motion did not start')
                            time.sleep(.1)
                complete+=1;self.update(completed=complete)
            self.update(reason='节目完成 / Playlist completed')
        except Exception as error:
            self.update(reason=str(error))
        finally:
            try:self.controller.action(dict(name='hardware_stop' if mode=='hardware' else 'demo_stop'))
            except Exception as error:self.update(reason=self.state['reason']+'; '+str(error))
            self.lease=None;self.update(active=False,paused=False)
