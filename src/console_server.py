"""Loopback left-hand console; explicit bounded commissioning and playback."""
import collections
import base64
import copy
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import socket
import sys
import threading
import time
import traceback
from urllib.parse import parse_qs, urlsplit

from console_agent import validate_command, HARDWARE_COMMANDS
from pose_view import PoseView, validate_mapping
from demo_player import DemoPlayer
from motion_history import summarize as summarize_motion
from view_camera import DEFAULT as DEFAULT_CAMERA, validate_camera
from parameter_store import ParameterStore
from gesture_library import catalog,CATALOG,CUSTOM_IDS
from performance_program import PROGRAM_IDS, NEW_DANCE_IDS, DANCES
from playback_rates import PLAYBACK_SPEEDS

from runtime_paths import RESOURCE, DATA, initialize
HERE = RESOURCE
PROJECT = HERE.parents[1]
REPORTS = DATA/'console_reports'
from runtime_paths import PORT
ORIGIN = f'http://127.0.0.1:{PORT}'


def vm_client():
    from bridge_config import bridge_client
    return bridge_client()


class Controller:
    def __init__(self, factory=vm_client, reports=REPORTS,parameter_path=None):
        self.factory, self.reports = factory, Path(reports)
        if self.reports == REPORTS: initialize()
        self.lock = threading.RLock()
        self.parameters=ParameterStore(parameter_path or self.reports.parent/'motion_parameters.py')
        self.parameter_worker=None
        self.player = DemoPlayer()
        from doctor import Doctor
        self.doctor=Doctor()
        from glove_bridge import GloveBridge
        self.glove=GloveBridge(factory)
        self.client = self.stdin = None
        self.generation = 0
        self.last_update = 0.
        self.csrf = secrets.token_urlsafe(32)
        self.state = dict(connection='disconnected', message='接好机械手后，点击“自动连接”', devices=[],
            latest=None, metrics=dict(host_hz=None, device_hz=None, age_ms=None),
            device_id=None, joint_rates=[], mapping=[], mapping_device_id=None, display_selected=None,
            recording=dict(active=False, frames=0, elapsed_s=0, label='baseline', seconds=30),
            sessions=[], log=[], read_only=True, recognition_enabled=False,
            motion_enabled=False, instance='wuji-studio-v1',motion_history=[],
            camera=copy.deepcopy(DEFAULT_CAMERA),camera_revision=0,view_source='feedback')
        from device_profiles import selected_profile
        self.state['device_profile']=selected_profile()
        self.state['hardware'] = dict(ready=False,active=False,actions=[],probe_ready=False,reason='先连接左手，进行单关节试动')
        self.hardware_lease = None
        motion_dir=self.reports.parent/'motion_reports'
        for path in sorted(motion_dir.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:20]:
            try:self.state['motion_history'].append(summarize_motion(json.loads(path.read_text(encoding='utf-8'))))
            except (OSError,ValueError,TypeError):pass
        if self.reports.exists():
            for path in sorted(self.reports.glob('*.json'), key=lambda p:p.stat().st_mtime, reverse=True)[:20]:
                try:
                    self.state['sessions'].append(json.loads(path.read_text(encoding='utf-8')))
                except (OSError, ValueError):
                    pass
        self.log('控制台已启动，等待连接；没有自动连接设备')
        mapping_path = self.reports.parent/'display_mapping.json'
        if mapping_path.exists():
            try:
                config = json.loads(mapping_path.read_text(encoding='utf-8'))
                self.state['mapping'] = validate_mapping(config['entries'])
                self.state['mapping_device_id'] = config['device_id']
            except (ValueError, KeyError, TypeError):
                self.log('已有显示映射无效，请重新核对')

    def log(self, text):
        self.state['log'].append(dict(time=datetime.now().strftime('%H:%M:%S'), text=text))
        self.state['log'] = self.state['log'][-60:]

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.state)
            # The user explicitly requested a live visual preview before full
            # calibration. This observed node-group profile is provisional and
            # display-only; it must never authorize hardware command mapping.
            expected = list(range(20)) if result['device_profile']['generation']=='hand1' else [f*5+j+1 for f in range(5) for j in range(4)]
            observed = sorted(r['nid'] for r in result['joint_rates'])
            verified = bool(result['mapping']) and result['mapping_device_id']==result['device_id']
            result['mapping_verified'] = verified
            if not verified and observed == expected and result['device_id']:
                result['mapping'] = [dict(index=i,nid=nid,sign=1,offset=0.,verified=False) for i,nid in enumerate(expected)]
                result['mapping_device_id'] = result['device_id']
                result['mapping_source'] = 'provisional_observed_node_groups_display_only'
            age = result['metrics'].get('age_ms')
            if age is not None:
                age += max(0, time.monotonic()-self.last_update)*1000
            stale = age is None or age > 500 or result['connection'] != 'connected'
            result['stale'] = stale
            result['metrics']['age_ms'] = age
            if stale:
                result['latest'] = None
                result['metrics']['host_hz'] = result['metrics']['device_hz'] = None
                for joint in result['joint_rates']:
                    joint.update(host_hz=None, device_hz=None, status='stale')
            result['csrf'] = self.csrf
            result['playback'] = self.player.snapshot()
            result['parameter_sync']=dict(self.parameters.sync_status)
            result['glove']=self.glove.snapshot()
            return result

    def parameter_snapshot(self):
        with self.lock:return self.parameters.snapshot()

    def synchronize_parameters(self,source):
        client=None;error=None
        try:
            client=self.factory();self.parameters.transfer(client,source)
        except Exception:
            error='请检查控制端连接配置；保留本机参数'
        finally:
            if client:
                try:client.close()
                except Exception:pass
            with self.lock:
                self.parameters.finish_sync(source,error)
                self.log(self.parameters.sync_status['message'])

    def clear_live(self):
        self.state['hardware']['probe_ready']=False
        self.state['hardware']['trial_ready']=False
        self.state['latest'] = None
        self.state['metrics'] = dict(host_hz=None, device_hz=None, age_ms=None)
        self.state['joint_rates'] = []
        if self.state['hardware'].get('active'):
            self.state['hardware'].update(ready=False,active=None,reason='连接已结束，电机停用结果待确认')
            self.state['motion_enabled']=None
        else:
            self.state['hardware'].update(ready=False,actions=[])

    def send(self, command):
        if self.stdin is None:
            raise ValueError('尚未建立反馈连接')
        self.stdin.write(json.dumps(command)+'\n')
        self.stdin.flush()

    def action(self, command):
        if isinstance(command,dict) and command.get('name') in {'connect','runtime_select','bridge_config_save','bridge_password_save','bridge_password_forget','parameters_sync','doctor_version','doctor_run','device_profile_select','glove_connect'}:
            from managed_runtime import status as runtime_status
            if runtime_status()['busy']:
                raise ValueError('内置控制环境正在安装，请等待完成 / Wait for controller setup to finish')
        if isinstance(command,dict) and command.get('name') in {'runtime_install','runtime_select'}:
            from managed_runtime import start_install,select,status
            with self.lock:
                if self.state['connection']!='disconnected' or self.state['hardware'].get('active') is not False or self.glove.busy or self.doctor.snapshot()['running'] or self.parameters.sync_status['busy']:
                    raise ValueError('Disconnect devices before setting up the control environment / 请先断开设备再配置控制环境')
                if command['name']=='runtime_select':select();return dict(runtime=status())
                return dict(runtime=start_install())
        # Serialize starts and configuration changes with diagnostics. RLock
        # also covers existing command branches that take the same lock.
        with self.lock:return self._action(command)

    def _action(self, command):
        if not isinstance(command,dict):raise ValueError('Invalid request')
        name=command.get('name','')
        if getattr(self,'desktop_closing',False) and name not in {'disconnect','hardware_stop','glove_stop','glove_disconnect','glove_keepalive','hardware_keepalive','demo_stop'}:
            raise ValueError('工作台正在退出 / Workbench is closing')
        if name=='hardware_stop' and self.glove.busy:
            return self.glove.action(dict(name='glove_stop'))
        if isinstance(name,str) and name.startswith('glove_'):
            if name not in {'glove_stop','glove_disconnect','glove_keepalive'} and (self.state['connection']!='disconnected' or self.state['hardware'].get('active') is not False or self.doctor.snapshot()['running'] or self.parameters.sync_status['busy']):
                raise ValueError('先结束其他设备会话或诊断 / Finish the other device session or diagnostics first')
            result=self.glove.action(command)
            if name in {'glove_scan','glove_open','glove_follow','glove_prepare'}:
                self.player.command(dict(name='demo_stop'));self.state['view_source']='glove'
            return result
        if self.glove.busy and name in {'connect','device_profile_select','bridge_config_save','parameters_sync','doctor_version','doctor_run','demo_start','hardware_start','hardware_probe','hardware_trial'}:
            raise ValueError('先断开手套遥操作会话 / Disconnect the glove session first')
        if command.get('name') in {'doctor_version','doctor_run'}:
            with self.lock:
                if self.state['connection']!='disconnected' or self.state['hardware'].get('active') is not False or self.parameters.sync_status['busy']:
                    raise ValueError('先断开设备连接，再运行独立诊断 / Disconnect the SDK session before diagnostics')
                return self.doctor.start('version' if command['name']=='doctor_version' else 'diagnose',command.get('serial',''))
        if self.doctor.snapshot()['running'] and command.get('name') in {'connect','bridge_config_save','parameters_sync','device_profile_select'}:
            raise ValueError('官方诊断正在运行，请稍候 / Official diagnostic in progress')
        if isinstance(command,dict) and command.get('name') == 'device_profile_select':
            with self.lock:
                if self.state['connection']!='disconnected' or self.state['hardware'].get('active') is not False:
                    raise ValueError('Disconnect before changing hand generation/side')
                from device_profiles import save_profile
                self.player.command(dict(name='demo_stop'))
                self.state['device_profile']=save_profile(command.get('profile'))
                self.state['mapping']=[];self.state['mapping_device_id']=None
                return dict(device_profile=self.state['device_profile'])
        if isinstance(command,dict) and command.get('name') in {'bridge_config_save','bridge_password_save','bridge_password_forget'}:
            if self.state['connection'] != 'disconnected' or self.state['hardware'].get('active') is not False:
                raise ValueError('Disconnect before editing controller settings / 先断开再修改控制端')
            if self.glove.busy or self.doctor.snapshot()['running'] or self.parameters.sync_status['busy']:
                raise ValueError('Wait for the controller session to finish / 请先结束控制端会话')
            from bridge_config import save_config,load_config
            if command['name']=='bridge_config_save':save_config(command.get('values'))
            else:
                from controller_credentials import save,forget
                if command['name']=='bridge_password_save':save(load_config(),command.get('password'))
                else:forget(load_config())
            return dict(saved=True)
        if isinstance(command,dict) and command.get('name') in {'desktop_viewer','desktop_open'}:
            from desktop import open_app
            return dict(native_opened=open_app(viewer=command['name']=='desktop_viewer'))
        if isinstance(command,dict) and command.get('name') in {'parameters_save','parameters_sync'}:
            with self.lock:
                if command['name']=='parameters_save':
                    result=self.parameters.save(command.get('values'),command.get('revision'))
                    self.log('参数已保存到本机；未改变当前动作或启动电机')
                    return dict(parameters=result)
                if (self.state['connection']!='disconnected' or self.state['hardware'].get('active') is not False
                    or self.hardware_lease or self.state['recording']['active']):
                    raise ValueError('先停止动作并断开左手，再同步参数；保存本机参数不受此影响')
                source=self.parameters.begin_sync(command.get('revision'))
                self.parameter_worker=threading.Thread(target=self.synchronize_parameters,args=(source,),daemon=True)
                self.parameter_worker.start()
                return dict(parameters=self.parameters.snapshot())
        if isinstance(command,dict) and command.get('name') in HARDWARE_COMMANDS:
            with self.lock:
                if command['name']=='hardware_stop':
                    if self.stdin:self.send(command)
                    elif self.state['hardware'].get('active') is None:
                        raise ValueError('反馈连接已断，无法确认电机停用状态')
                    return
                if self.state['connection']!='connected' or self.snapshot()['stale']:
                    raise ValueError('先连接左手并等待实时反馈')
                name=command['name']
                if name in {'hardware_start','hardware_probe','hardware_trial'}:
                    hardware=self.state['hardware']
                    if hardware.get('active') is not False:raise ValueError('实机动作进行中或停用状态待确认')
                    if self.hardware_lease:raise ValueError('已有实机启动请求，请等待或先停止')
                    if self.state['recording']['active']:raise ValueError('先停止采集')
                    if name in {'hardware_probe','hardware_trial'} and hardware.get('trial_controls_version')!=3:
                        raise ValueError('请断开后重新连接，加载官方参数版本的动作程序')
                    if name=='hardware_start':
                        if not hardware['ready']:raise ValueError(hardware['reason'])
                        if command.get('action') not in hardware['actions']:raise ValueError('该动作尚未实机核验')
                        outgoing={k:command.get(k) for k in ('name','action','speed','cycles')}
                    elif name=='hardware_probe':
                        if not hardware.get('probe_ready'):raise ValueError(hardware.get('probe_reason','等待完整反馈与诊断'))
                        if (type(command.get('index')) is not int or not 0<=command['index']<20 or
                            type(command.get('direction')) is not int or command['direction'] not in {-1,1} or
                            command.get('workspace_clear') is not True):raise ValueError('请选择关节和方向，确认周围已清空')
                        outgoing={k:command.get(k) for k in ('name','index','direction','workspace_clear')}
                    else:
                        if not hardware.get('trial_ready'):raise ValueError(hardware.get('probe_reason','等待完整反馈与诊断'))
                        if self.state['device_profile']['generation']=='hand1' and command.get('action') not in {'open','fist'}:
                            raise ValueError('一代实机当前支持官方张开/握拳适配，其余动作仅预览')
                        fast=type(command.get('speed',1.)) in {int,float} and command.get('speed',1.)>1
                        if fast and self.state['device_profile']['generation']=='hand1':
                            raise ValueError('一代实机暂不支持超过1倍；可用画面预览 / Hand 1 supports up to 1x on hardware')
                        action_id=command.get('action','')
                        revised=action_id in DANCES or action_id in {'count_digits','clock','text_sequence','splay'} or str(action_id).startswith('digit_')
                        required=4 if revised else 3 if fast else 2 if action_id in PROGRAM_IDS else 1
                        version=hardware.get('gesture_library_version',0)
                        if (fast or command.get('action') in CUSTOM_IDS) and (type(version) is not int or version<required):
                            raise ValueError('控制端需升级到对应动作库版本，再重新连接 / Update the controller gesture library, then reconnect')
                        if (command.get('action') not in {x['id'] for x in CATALOG} or
                            type(command.get('amplitude')) not in {int,float} or command['amplitude'] not in {.25,.5,.75,1.} or
                            type(command.get('speed',1.)) not in {int,float} or command.get('speed',1.) not in PLAYBACK_SPEEDS or
                            type(command.get('cycles')) is not int or command['cycles'] not in {1,3} or
                            command.get('workspace_clear') is not True):raise ValueError('选择试运行幅度和1/3轮，并确认周围清空')
                        outgoing={k:command.get(k) for k in ('name','action','amplitude','cycles','workspace_clear')}
                        outgoing['speed']=command.get('speed',1.)
                        if command.get('action')=='text_sequence':
                            from phrase_text import normalize_phrase
                            outgoing['text']=normalize_phrase(command.get('text'))
                        if command.get('action')=='clock':outgoing['clock_at']=datetime.now().astimezone().isoformat()
                    self.hardware_lease=secrets.token_hex(16)
                    outgoing['lease']=self.hardware_lease
                    self.player.command(dict(name='demo_stop'))
                    self.state['view_source']='feedback'
                    try:self.send(outgoing)
                    except Exception:
                        self.hardware_lease=None
                        raise
                    self.hardware_requested_at=time.monotonic()
                    self.hardware_seen_active=False
                    self.log('请求低力度整套试运行，候选姿态尚未实机验收' if name=='hardware_trial' else '请求实机单关节3°试动' if name=='hardware_probe' else '请求实机展示；画面将使用实际关节反馈')
                    return dict(lease=self.hardware_lease)
                if name!='hardware_stop' and command.get('lease')!=self.hardware_lease:
                    raise ValueError('实机控制会话已过期')
                self.send(command)
                return
        if isinstance(command, dict) and command.get('name') == 'view_camera':
            camera = validate_camera(command.get('camera'))
            with self.lock:
                self.state['camera'] = camera
                self.state['camera_revision'] += 1
                return dict(camera=copy.deepcopy(camera),camera_revision=self.state['camera_revision'])
        if isinstance(command, dict) and command.get('name') == 'view_source':
            if self.glove.busy:raise ValueError('手套会话期间显示映射或实际反馈 / Glove session owns the view source')
            source = command.get('source')
            if source not in {'feedback','preview'}:
                raise ValueError('请选择实机反馈或动作预览')
            with self.lock:
                if source=='preview' and self.state['hardware'].get('active') is not False:
                    raise ValueError('实机展示期间画面保持跟随实际反馈')
                self.state['view_source'] = source
                if source=='feedback':self.player.command(dict(name='demo_stop'))
            return
        if isinstance(command, dict) and str(command.get('name','')).startswith('demo_'):
            if command['name']=='demo_start' and self.state['hardware'].get('active') is not False:
                raise ValueError('先停止实机展示，再播放仿真预览')
            self.player.command(command)
            with self.lock:
                if command['name']=='demo_start':self.state['view_source']='preview'
                elif command['name']=='demo_stop':self.state['view_source']='feedback'
            return
        if isinstance(command, dict) and command.get('name') == 'display_selected':
            index = command.get('index')
            if type(index) is not int or not 0<=index<20:
                raise ValueError('无效的显示关节')
            with self.lock:
                self.state['display_selected'] = index
            return
        if isinstance(command, dict) and command.get('name') == 'display_mapping':
            with self.lock:
                if self.state['connection'] != 'connected' or not self.state['device_id']:
                    raise ValueError('连接左手后再核对显示映射')
                entries = validate_mapping(command.get('entries'))
                observed = {r['nid'] for r in self.state['joint_rates']}
                if any(e['nid'] not in observed for e in entries):
                    raise ValueError('只能映射已收到反馈的设备关节')
                config = dict(device_id=self.state['device_id'], entries=entries,
                              source='user_verified_display_only', no_hardware_commands=True)
                self.reports.parent.mkdir(parents=True, exist_ok=True)
                (self.reports.parent/'display_mapping.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
                self.state['mapping'], self.state['mapping_device_id'] = entries, config['device_id']
                self.log(f'保存{len(entries)}个显示关节对应；没有向机械手发送命令')
            return
        validate_command(command)
        with self.lock:
            name = command['name']
            if name == 'connect':
                command = dict(command)
                command.setdefault('auto_detect', True)
                if self.parameters.sync_status['busy']:raise ValueError('参数同步中，请完成后连接')
                if self.state['connection'] in {'connected', 'connecting'}:
                    raise ValueError('已有连接正在进行')
                self.generation += 1
                generation = self.generation
                self.state['connection'] = 'connecting'
                self.state['message'] = '正在重新发现设备并核对型号、左右手'
                self.clear_live()
                self.state['device_id'] = None
                self.state['devices'] = []
                self.state['hardware'] = dict(ready=False, active=False, actions=[], probe_ready=False, reason='正在识别新设备')
                self.hardware_lease = None
                self.log('用户请求连接所选设备，仅接收反馈')
                threading.Thread(target=self.read_session, args=(command, generation), daemon=True).start()
            elif name == 'disconnect':
                self.generation += 1
                # Ask the worker to flush recordings; its reader may still save
                # final reports, but stale states cannot revive the connection.
                client, stream = self.client, self.stdin
                if stream:
                    try:
                        stream.write('{"name":"disconnect"}\n'); stream.flush()
                    except Exception:
                        pass
                self.client = self.stdin = None
                self.state['connection'] = 'disconnected'
                self.state['message'] = '已请求断开；不会自动重连'
                self.state['recording']['active'] = False
                self.clear_live()
                self.log('用户断开反馈连接')
                if client:
                    threading.Timer(3., client.close).start()
            elif name == 'record':
                if self.snapshot()['stale'] or self.state['connection'] != 'connected':
                    raise ValueError('请先连接左手并等待新鲜反馈')
                if self.state['recording']['active']:
                    raise ValueError('已有采集正在进行')
                self.send(command)
                self.state['recording'] = dict(active=True, frames=0, elapsed_s=0,
                                              seconds=command['seconds'], label=command['label'])
                self.log('已请求采集；标签来自你的选择，不是模型判断')
            else:
                if self.stdin:
                    self.send(command)
                self.log('已请求停止采集并保存记录；此操作不是硬件急停')

    def event(self, event, generation):
        with self.lock:
            kind = event.get('type')
            if kind=='motion_report':
                report=event['report']
                if not re.fullmatch('[a-f0-9]{32}',report.get('id','')):raise ValueError('Invalid motion report id')
                destination=self.reports.parent/'motion_reports'
                destination.mkdir(parents=True,exist_ok=True)
                (destination/(report['id']+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                summary=summarize_motion(report)
                self.state['motion_history'].insert(0,summary)
                self.state['motion_history']=self.state['motion_history'][:20]
                returned=summary['return_error_deg']
                result=(f'实际回位误差{returned:.2f}°' if returned is not None else
                    f"未完成回位验收，中止时偏离起点{summary['displacement_at_stop_deg']:.2f}°")
                self.log(f"试动结束：实际最大变化{report['peak_actual_delta_deg']:.2f}°，{result}；{report['reason']}")
            if kind == 'report':
                report = event['report']
                if not re.fullmatch('[a-f0-9]{32}', report.get('id', '')):
                    raise ValueError('Invalid report id')
                self.reports.mkdir(parents=True, exist_ok=True)
                (self.reports/(report['id']+'.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                self.state['sessions'].insert(0, report)
                self.state['sessions'] = self.state['sessions'][:20]
                self.log(f"采集已保存：{report['frames']}帧，结束原因 {report['reason']}")
            if generation != self.generation:
                return
            if kind == 'identity':
                from device_profiles import save_profile
                self.state['device_profile'] = save_profile(event['profile'])
                self.state['device_id'] = event['device_id']
                self.state['mapping'] = []
                self.state['mapping_device_id'] = None
                self.state['display_selected'] = None
                self.player.command(dict(name='demo_stop'))
                self.log('自动识别：'+self.state['device_profile']['zh']+' · '+event['device_id'])
            elif kind == 'selection':
                self.state['devices'] = event['devices']
                self.state['connection'] = 'error'
                self.state['message'] = event['message']
                self.clear_live()
            elif kind == 'state':
                self.last_update = time.monotonic()
                for key in ('connection', 'message', 'latest', 'metrics', 'recording', 'device_id', 'joint_rates'):
                    self.state[key] = event[key]
                if 'hardware' in event:self.state['hardware']=event['hardware']
                if self.state['hardware'].get('active'):self.hardware_seen_active=True
                elif self.hardware_lease and (self.hardware_seen_active or time.monotonic()-self.hardware_requested_at>3):
                    self.hardware_lease=None
                self.state['motion_enabled']=self.state['hardware']['active']
                self.state['read_only']=self.state['motion_enabled'] is False
            elif kind == 'error':
                self.state['connection'] = 'error'
                message = str(event.get('message', '连接失败'))[:300]
                self.state['message'] = ('未发现左手，请检查电源与网线后重新连接'
                                         if message == 'Device not found' else message)
                self.state['recording']['active'] = False
                self.clear_live()
                self.log(self.state['message'])
            elif kind == 'notice':
                self.log(str(event.get('message', ''))[:300])

    def read_session(self, command, generation):
        client = None
        try:
            client = self.factory()
            stdin, stdout, stderr = client.exec_command(client.agent_command, timeout=25)
            # Drain SDK diagnostics locally without placing them in the web UI.
            def drain_diagnostics():
                while not stdout.channel.closed:
                    try:
                        if not stderr.read(4096):break
                    except socket.timeout:continue
                    except (OSError,EOFError):break
            threading.Thread(target=drain_diagnostics, daemon=True).start()
            with self.lock:
                if generation != self.generation:
                    return
                self.client, self.stdin = client, stdin
                self.send(command)
            for line in stdout:
                if line.startswith('WUJI_JSON:'):
                    self.event(json.loads(line[len('WUJI_JSON:'):]), generation)
            with self.lock:
                if generation == self.generation and self.state['connection'] != 'error':
                    self.event(dict(type='error', message='反馈连接已结束，请手动重新连接'), generation)
        except Exception:
            # Never expose credential/configuration tracebacks through HTTP.
            (DATA/'bridge_error.log').write_text(traceback.format_exc(), encoding='utf-8')
            self.event(dict(type='error', message='未建立稳定反馈连接，请检查电源、设备地址及控制端设置'), generation)
        finally:
            if client:
                client.close()
            with self.lock:
                if generation == self.generation:
                    self.client = self.stdin = None
                    self.state['recording']['active'] = False
                    self.clear_live()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, body, mime='application/json; charset=utf-8', filename=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def allowed_host(self):
        return self.headers.get('Host') in {f'127.0.0.1:{PORT}', f'localhost:{PORT}'}

    def do_GET(self):
        if not self.allowed_host():
            return self.reply(403, dict(error='Local host required'))
        url = urlsplit(self.path)
        if url.path == '/api/desktop':
            host=getattr(self.server,'desktop',None)
            return self.reply(200,host.info() if host else dict(native=False,api_version=1,operations=[]))
        if url.path == '/api/installation':
            from bridge_config import load_config
            from controller_credentials import status as credential_status
            from device_profiles import PROFILES
            from runtime_paths import EDITION
            from model_pack import status as model_status
            config=load_config()
            from managed_runtime import status as runtime_status
            runtime=runtime_status()
            required=('agent_directory','python')+(('host','username') if config['mode']=='ssh' else ())
            missing=[key for key in required if not config[key]]
            return self.reply(200,dict(edition=EDITION,version=EDITION['version'],optional_model=model_status(),local_controller_supported=sys.platform.startswith('linux'),bridge=config,controller_configured=runtime['ready'] if config['mode'] in ('wsl','macvm') else not missing,missing_controller_fields=missing,credential=credential_status(config),runtime=runtime,profiles=list(PROFILES.values()),selected_profile=self.server.controller.state['device_profile']['id'],unofficial=True))
        if url.path in ('/api/doctor','/api/doctor/report'):
            state=self.server.controller.doctor.snapshot()
            if url.path.endswith('/report'):
                if not state.get('report'):return self.reply(404,dict(error='No completed diagnostic report'))
                return self.reply(200,state['report'],filename='wuji-doctor-report.json')
            return self.reply(200,state)
        if url.path == '/api/state':
            return self.reply(200, self.server.controller.snapshot())
        if url.path == '/api/parameters':
            return self.reply(200,self.server.controller.parameter_snapshot())
        if url.path == '/api/catalog':
            return self.reply(200,catalog())
        if url.path == '/api/performance':
            from performance_export import export_program
            query=parse_qs(url.query)
            try:
                action=query.get('action',[''])[0];text=query.get('text',['WUJI TECH'])[0];fmt=query.get('format',['json'])[0]
                profile_id=self.server.controller.state['device_profile']['id']
                body=export_program(action,text,profile_id,fmt)
                return self.reply(200,body,'text/csv; charset=utf-8' if fmt=='csv' else 'application/json; charset=utf-8',filename=f'{profile_id}-{action}-nominal.{fmt}')
            except (ValueError,TypeError) as error:return self.reply(400,dict(error=str(error)))
        if url.path == '/api/view':
            jpeg, meta = self.server.pose.get()
            return self.reply(200, dict(meta=meta, image='data:image/jpeg;base64,'+base64.b64encode(jpeg).decode('ascii') if jpeg else None))
        if url.path == '/api/report':
            ident = parse_qs(url.query).get('id', [''])[0]
            if not re.fullmatch('[a-f0-9]{32}', ident):
                return self.reply(400, dict(error='Invalid report'))
            path = self.server.controller.reports/(ident+'.json')
            if path.exists():
                return self.reply(200, path.read_bytes(), filename='left-hand-'+ident+'.json')
            return self.reply(404, dict(error='Report not found'))
        assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8')}
        assets.update({'/visual.js': ('visual.js', 'text/javascript; charset=utf-8'),
                       '/playback.js': ('playback.js', 'text/javascript; charset=utf-8'),
                       '/camera.js': ('camera.js', 'text/javascript; charset=utf-8'),
                       '/camera_gesture.mjs': ('camera_gesture.mjs', 'text/javascript; charset=utf-8'),
                       '/visual.css': ('visual.css', 'text/css; charset=utf-8')})
        assets.update({'/workspace.js':('workspace.js','text/javascript; charset=utf-8'),
            '/workspace.css':('workspace.css','text/css; charset=utf-8'),
            '/parameters.js':('parameters.js','text/javascript; charset=utf-8')})
        for name in ('studio.js','locale.js','floating_panel.js','viewer.js','action_picker.js','brand.js','installation.js','profiles.js','doctor.js','glove.js','desktop_shell.js','external_refraction.js','device_network.js','connection_toolbar.js'):
            assets['/'+name]=(name,'text/javascript; charset=utf-8')
        for name in ('studio.css','floating_panel.css','glass.css','glove.css','desktop_glass.css','desktop_refinement.css','glass_refresh.css'):
            assets['/'+name]=(name,'text/css; charset=utf-8')
        assets['/viewer']=('viewer.html','text/html; charset=utf-8')
        assets['/favicon.ico']=('favicon.ico','image/x-icon')
        assets['/app-icon.png']=('app-icon.png','image/png')
        assets['/wuji-logo.png']=('wuji-logo.png','image/png')
        if url.path not in assets:
            return self.reply(404, dict(error='Not found'))
        name, mime = assets[url.path]
        return self.reply(200, (HERE/'web'/name).read_bytes(), mime)

    def do_POST(self):
        origin = self.headers.get('Origin')
        if (not self.allowed_host() or origin not in {None, ORIGIN, f'http://localhost:{PORT}'}
                or not secrets.compare_digest(self.headers.get('X-Console-Token', ''), self.server.controller.csrf)):
            return self.reply(403, dict(ok=False, error='本机控制会话校验失败，请刷新页面'))
        if self.path not in ('/api/action','/api/desktop'):
            return self.reply(404, dict(ok=False, error='Unsupported endpoint'))
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096:
                raise ValueError('Invalid request length')
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('JSON required')
            payload=json.loads(self.rfile.read(length))
            if not isinstance(payload,dict):raise ValueError('JSON object required')
            if self.path == '/api/desktop':
                from desktop_tools import dispatch
                host=getattr(self.server,'desktop',None)
                if not host:return self.reply(409,dict(ok=False,error='Open the Windows desktop application to use this interface'))
                result=dispatch(host,payload)
            elif payload.get('name') in ('desktop_open','desktop_viewer') and getattr(self.server,'desktop',None):
                result=self.server.desktop.open_viewer() if payload['name']=='desktop_viewer' else dict(ok=True,native_opened=True)
            else:result = self.server.controller.action(payload)
            self.reply(200, {'ok':True, **(result or {})})
        except (ValueError, TypeError) as error:
            self.reply(400, dict(ok=False, error=str(error)))
        except Exception:
            if self.path == '/api/desktop':
                import logging
                logging.exception('Desktop code interface failed')
                return self.reply(503,dict(ok=False,error='Desktop operation unavailable; check desktop.log'))
            self.reply(503, dict(ok=False, error='反馈通道暂不可用，请检查连接状态'))


class ConsoleHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False
    allow_reuse_port = False

    def server_bind(self):
        if sys.platform == 'win32':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main():
    server = ConsoleHTTPServer(('127.0.0.1', PORT), Handler)
    server.controller = Controller()
    server.pose = PoseView(server.controller)
    try:
        server.serve_forever()
    finally:
        server.controller.action(dict(name='disconnect'))
        server.server_close()


if __name__ == '__main__':
    main()
