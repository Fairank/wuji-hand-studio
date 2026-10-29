"""One controller's reservation in a multi-hand group. Browser leases stay separate."""
import copy
import secrets
import threading
import time


class GroupParticipant:
    def __init__(self,controller):
        self.c=controller;self.token=None;self.mode=None;self.active=False;self.internal=False
        self.heartbeat=0.;self.start_s=None;self.reason='';self.spec={};self.watch=None
        self.control_epoch=0
        self.prepare_cleanup_required=False;self._preview_run=None

    def snapshot(self):
        result=dict(active=self.active,mode=self.mode,phase='idle',reason=self.reason,token=self.token)
        if self.mode=='hardware':
            h=self.c.state['hardware'];g=h.get('group_sync') or {}
            result.update(g)
            # A previous completed run can never acknowledge a new reservation.
            if g.get('token')!=self.token:result.update(phase='preparing',token=self.token,active=self.active)
            elif self.active and not h.get('active'):
                result.update(phase='completed' if g.get('completed') and h.get('stop_confirmed') else 'failed',reason=h.get('reason'))
        elif self.active:
            p=self.c.player.snapshot()
            result.update(phase='ready' if self.start_s is None else 'waiting' if time.monotonic()<self.start_s else 'paused' if p['group_paused'] else 'playing' if p['running'] else 'completed',elapsed_s=p['elapsed_s'],clock_s=time.monotonic(),control_version=1,control_epoch=self.control_epoch,applied_epoch=p['applied_epoch'],pending_control=p['pending_control'])
        result['cleanup_required']=self.prepare_cleanup_required
        if self.prepare_cleanup_required:result.update(phase='cleanup_required',reason=self.reason)
        return copy.deepcopy(result)

    def call(self,command):
        self.internal=True
        try:return self.c._action(command)
        finally:self.internal=False

    def prepare(self,command):
        from bimanual_program import resolve_action
        from playback_rates import PLAYBACK_SPEEDS
        s=self.c.snapshot();token=command.get('token')
        if self.active or s['program']['active'] or s['hardware'].get('active') is not False or self.c.hardware_lease or s['glove'].get('busy') or s['calibration']['running'] or self.c.doctor.snapshot()['running']:
            raise ValueError('先结束当前会话再编组 / Finish current sessions before grouping')
        if not isinstance(token,str) or len(token)!=32:raise ValueError('Invalid group token')
        mode=command.get('mode')
        if mode not in ('preview','hardware'):raise ValueError('Invalid group mode')
        if type(command.get('speed')) not in (int,float) or command['speed'] not in PLAYBACK_SPEEDS:raise ValueError('Invalid speed')
        if type(command.get('cycles')) is not int or command['cycles'] not in (1,3):raise ValueError('Choose one or three cycles')
        if type(command.get('amplitude')) not in (int,float) or command['amplitude'] not in (.25,.5,.75,1.):raise ValueError('Invalid amplitude')
        action=resolve_action(command.get('kind'),command.get('action'),s['device_profile']['side'])
        if mode=='hardware' and (s['connection']!='connected' or s['stale'] or not s['hardware'].get('trial_ready') or s['hardware'].get('group_sync_version')!=1 or s['device_profile']['generation']!='hand2' or command.get('workspace_clear') is not True):
            raise ValueError('需连接二代手、更新控制端并确认空间已清空 / Connect Hand 2, update controller, and confirm clear space')
        self.token=token;self.mode=mode;self.heartbeat=time.monotonic();self.start_s=None;self.reason=''
        self.control_epoch=0
        self.prepare_cleanup_required=False;self._preview_run=None
        self.spec=dict(command,action=action);self.active=True
        try:
            if mode=='preview':
                self.call(dict(name='demo_start',action=action,speed=command['speed'],cycles=command['cycles']))
                with self.c.player.lock:self._preview_run=self.c.player.run_id
                self.c.player.command(dict(name='demo_pause'));self.c.player.elapsed=0.
                self.c.player.amplitude=command['amplitude']
            # Hardware prepare is deliberately read-only. Enabling is a separate barrier.
        except Exception:
            self.active=False;raise
        try:self.watch=threading.Thread(target=self._watch,args=(token,),daemon=True,name='Group session lease');self.watch.start()
        except Exception as error:
            if self.token==token:
                self.watch=None
                if mode=='preview':
                    try:self.c.player.command(dict(name='demo_stop',expected_run=self._preview_run))
                    except Exception as cleanup:
                        self.prepare_cleanup_required=True
                        self.reason='编组预览退出未确认 / Group preview cleanup is unconfirmed: '+str(cleanup)[:400]
                        raise ValueError(self.reason) from error
                # Hardware prepare has not armed anything. Keep only the old
                # token for an idempotent late abort, with no live reservation.
                self.active=False;self.mode=None;self.spec={};self.heartbeat=0.;self.reason=str(error)[:700]
            raise
        return self.snapshot()

    def handle(self,command):
        name=command['name']
        if name=='group_prepare':return self.prepare(command)
        if name=='group_abort':
            if command.get('token') and command['token']!=self.token:raise ValueError('Expired group token')
            return self.stop('已停止编组 / Group stopped')
        if self.prepare_cleanup_required:raise ValueError('Group preview cleanup is unconfirmed; review the original preview')
        if not self.active or not secrets.compare_digest(str(command.get('token','')),str(self.token)):
            raise ValueError('Expired group reservation')
        self.heartbeat=time.monotonic()
        if name=='group_beat':
            if self.mode=='hardware' and self.c.hardware_lease:
                self.call(dict(name='hardware_keepalive',lease=self.c.hardware_lease))
        elif name=='group_arm':
            if self.mode=='hardware':
                if self.c.hardware_lease:raise ValueError('Group already armed')
                self.call(dict(name='hardware_trial',action=self.spec['action'],speed=self.spec['speed'],cycles=self.spec['cycles'],amplitude=self.spec['amplitude'],workspace_clear=True))
        elif name=='group_commit':
            if self.mode=='preview':
                from group_timing import number
                start=number(command.get('start_s'),time.monotonic()+.1,time.monotonic()+5)
                self.start_s=start;self.c.player.schedule(start)
            else:self.c.send(dict(name='hardware_group_commit',lease=self.c.hardware_lease,token=self.token,start_s=command.get('start_s'),timing=command.get('timing')))
        elif name in ('group_pause','group_resume'):
            if self.mode=='preview':
                from group_timing import number
                epoch=command.get('epoch')
                if type(epoch) is not int or epoch!=self.control_epoch+1:raise ValueError('Expired group control')
                at=number(command.get('at_s'),time.monotonic()+.1,time.monotonic()+5)
                self.c.player.schedule_control(name.removeprefix('group_'),at,epoch)
                self.control_epoch=epoch
            else:self.c.send(dict(name='hardware_'+name,lease=self.c.hardware_lease,token=self.token,epoch=command.get('epoch'),at_s=command.get('at_s')))
        else:raise ValueError('Unknown group operation')
        return self.snapshot()

    def stop(self,reason):
        if self.prepare_cleanup_required:
            # A failed watchdog startup may only stop its exact local run.
            # A replacement preview is never cleared by this old reservation.
            self.c.player.command(dict(name='demo_stop',expected_run=self._preview_run))
            self.prepare_cleanup_required=False;self.active=False;self.mode=None;self.spec={};self.reason=reason
            return dict(stopping=True)
        self.active=False;self.reason=reason
        if self.mode=='hardware':self.call(dict(name='hardware_stop'))
        elif self.mode=='preview':self.call(dict(name='demo_stop'))
        return dict(stopping=True)

    def _watch(self,token):
        while self.active and self.token==token:
            time.sleep(.1)
            with self.c.lock:
                if self.active and self.token==token and time.monotonic()-self.heartbeat>.8:
                    try:self.stop('编组控制连接中断 / Group lease expired')
                    except Exception:self.active=False
