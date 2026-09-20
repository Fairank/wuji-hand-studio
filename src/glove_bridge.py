"""Operator/status bridge over the configured local or SSH SDK transport."""
import copy
import json
import secrets
import socket
import threading
import time
from glove_protocol import validate

class GloveBridge:
    def __init__(self,factory):
        self.factory=factory;self.lock=threading.RLock();self.thread=None
        self.client=self.stdin=None;self.stopping=False;self.last_update=0.;self.lease=None
        self.state=dict(connection='disconnected',message='',devices=[],users=[],stream={},hardware={},feedback={})

    @property
    def busy(self):return bool(self.thread and self.thread.is_alive())

    def snapshot(self):
        with self.lock:
            s=copy.deepcopy(self.state);age=max(0,time.monotonic()-self.last_update)*1000
            s['busy']=self.busy;s['transport_age_ms']=age if self.last_update else None
            stream=s['stream'];remote=stream.get('age_ms')
            if remote is not None:stream['age_ms']=remote+age
            stream['fresh']=bool(stream.get('fresh') and remote is not None and remote+age<=stream.get('timeout_ms',250))
            if not stream['fresh']:stream['retarget_hz']=None
            feedback=s.get('feedback') or {};metrics=feedback.get('metrics') or {}
            if metrics.get('age_ms') is not None:metrics['age_ms']+=age
            feedback['stale']=metrics.get('age_ms') is None or metrics['age_ms']>100 or age>200
            s['feedback']=feedback
            return s

    def send(self,c):
        if not self.stdin:raise ValueError('控制端尚未就绪 / Controller not ready')
        self.stdin.write(json.dumps(c)+'\n');self.stdin.flush()

    def action(self,c):
        validate(c)
        with self.lock:
            name=c['name']
            if name=='glove_session':raise ValueError('Use scan to open a session')
            if name=='glove_scan' and not self.busy:
                self.stopping=False;self.lease=None;self.last_update=0.
                self.state=dict(connection='connecting',message='正在连接控制端 / Connecting controller',devices=[],users=[],stream={},hardware={},feedback={})
                self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start();return
            if name=='glove_disconnect':
                self.stopping=True
                if self.stdin:self.send(c)
                elif not self.busy:self.state['connection']='disconnected'
                return
            if not self.busy:raise ValueError('先扫描控制端 / Scan controller first')
            if name=='glove_follow':
                s=self.snapshot()
                if self.lease:raise ValueError('已有启动请求 / Start already requested')
                if not s['stream'].get('fresh'):raise ValueError('等待新鲜手套数据 / Wait for fresh glove data')
                if s['feedback']['stale'] or not s['hardware'].get('probe_ready') or s['hardware'].get('active') is not False:
                    raise ValueError('等待完整机械手反馈 / Wait for ready hand feedback')
                self.lease=secrets.token_hex(16);c=dict(c,lease=self.lease)
                self.requested=time.monotonic();self.seen_active=False
                try:self.send(c)
                except Exception:self.lease=None;raise
                return dict(lease=self.lease)
            if name=='glove_keepalive' and c.get('lease')!=self.lease:raise ValueError('Expired control lease')
            self.send(c)

    def receive(self,event):
        with self.lock:
            kind=event.get('type')
            if kind=='glove_state':
                self.state=event['state'];self.last_update=time.monotonic()
                active=self.state['hardware'].get('active')
                if self.lease:
                    if active:self.seen_active=True
                    elif active is False and (self.seen_active or time.monotonic()-self.requested>5):self.lease=None
            elif kind in ('glove_error','glove_notice','notice'):
                self.state['message']=str(event.get('message',''))[:400]
                if kind=='glove_error':self.state['connection']='error'
            elif kind=='glove_closed':
                self.state['connection']='disconnected';self.state['hardware']=event.get('hardware',{})
                self.state['stream']['fresh']=False;self.lease=None

    def run(self):
        client=None;ready=False
        try:
            client=self.factory();stdin,stdout,stderr=client.exec_command(client.agent_command,timeout=25)
            def drain():
                try:
                    while stderr.read(4096):pass
                except (OSError,EOFError,socket.timeout):pass
            threading.Thread(target=drain,daemon=True).start()
            with self.lock:self.client=client;self.stdin=stdin
            for line in stdout:
                if not line.startswith('WUJI_JSON:'):continue
                event=json.loads(line[len('WUJI_JSON:'):])
                if event.get('type')=='ready':
                    if event.get('teleop_version')!=1:raise ValueError('控制端需更新到支持手套遥操作的版本 / Update controller for glove teleoperation')
                    with self.lock:
                        self.send(dict(name='disconnect' if self.stopping else 'glove_session'))
                    ready=True
                else:self.receive(event)
            if not ready:raise ValueError('控制端未返回手套接口 / Controller did not expose glove interface')
        except Exception as e:
            # Credential/transport details are never copied to the public UI.
            text=str(e) if isinstance(e,ValueError) else '无法连接控制端，请核对连接设置 / Controller connection failed; check settings'
            self.receive(dict(type='glove_error',message=text))
        finally:
            if client:
                try:client.close()
                except Exception:pass
            with self.lock:
                self.client=self.stdin=None;self.lease=None;self.state['stream']['fresh']=False
                if self.state['hardware'].get('active'):
                    self.state['hardware'].update(active=None,stop_confirmed=False,reason='连接中断，停用结果未确认 / Disable not confirmed')
                if self.state['connection'] not in ('disconnected','error'):
                    self.state.update(connection='error',message='控制端会话已结束，请手动重连 / Controller session ended')
