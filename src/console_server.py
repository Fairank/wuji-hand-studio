"""Loopback left-hand console; explicit bounded commissioning and playback."""
import collections
import base64
import os
import copy
import errno
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


def local_file_error(error):
    """Describe known local file failures without publishing arbitrary exceptions."""
    number=getattr(error,'errno',None);windows=getattr(error,'winerror',None)
    if isinstance(error,PermissionError) or number in (errno.EACCES,errno.EPERM):
        reason='没有文件访问权限 / File access denied'
    elif number==errno.ENOSPC or windows==112:
        reason='本机存储空间不足 / Local storage is full'
    elif number==errno.EROFS:
        reason='文件位置只读 / File location is read-only'
    elif isinstance(error,FileNotFoundError):
        reason='文件或文件夹不存在 / File or folder is missing'
    elif isinstance(error,NotADirectoryError):
        reason='需要文件夹，但该位置是文件 / Expected a folder at this location'
    elif isinstance(error,IsADirectoryError):
        reason='需要文件，但该位置是文件夹 / Expected a file at this location'
    elif isinstance(error,FileExistsError):
        reason='该位置已存在文件 / A file already exists at this location'
    elif number==errno.ENAMETOOLONG:
        reason='文件路径过长 / File path is too long'
    else:
        reason='本机文件操作未完成 / Local file operation did not complete'
    code=windows if type(windows) is int else number if type(number) is int else None
    if code is not None:reason+=' (OS '+str(code)+')'
    path=getattr(error,'filename',None)
    if isinstance(path,(str,bytes)):
        if isinstance(path,bytes):path=os.fsdecode(path)
        path=''.join(c for c in path if c.isprintable())[:512]
        if path:reason+=': '+path
    return reason


def vm_client():
    from bridge_config import bridge_client
    return bridge_client()


class Controller:
    def __init__(self, factory=vm_client, reports=REPORTS,parameter_path=None):
        self.factory, self.reports = factory, Path(reports)
        if self.reports == REPORTS: initialize()
        self.lock = threading.RLock()
        from device_termination import TerminationJournal
        self.termination=TerminationJournal(self.reports.parent/'device_termination.json')
        self.session_devices={}
        self.session_epochs={0:self.termination.new_session()};self.hardware_generation=0
        self.parameters=ParameterStore(parameter_path or self.reports.parent/'motion_parameters.py')
        self.parameter_worker=None
        self.player = DemoPlayer()
        from doctor import Doctor
        self.doctor=Doctor()
        from glove_bridge import GloveBridge
        self.glove=GloveBridge(factory,self.termination)
        from calibration_cli import CalibrationCLI
        self.calibration=CalibrationCLI(self.reports.parent/'calibration_reports')
        from retarget_workspace import RetargetWorkspace
        self.retarget=RetargetWorkspace(self.reports.parent)
        from retarget_tuning import TuningStore
        self.tuning=TuningStore(self.retarget.folder/'official_tuning')
        from solver_runtime import SolverRuntime
        self.solver_runtime=SolverRuntime()
        from official_tools import OfficialTools
        self.official_tools=OfficialTools()
        from studio_component import StudioComponent
        self.studio=StudioComponent()
        from official_cli import OfficialCLI
        from official_visualization import OfficialVisualization
        self.official_cli=OfficialCLI()
        self.official_viz=OfficialVisualization()
        from calibration_handover import CalibrationHandover
        self.calibration_handover=CalibrationHandover(self)
        from teleoperation_handover import TeleoperationHandover
        self.teleoperation_handover=TeleoperationHandover(self)
        from connection_switch import ConnectionSwitch
        self.connection_switch=ConnectionSwitch(self)
        self.feedback_sessions={}
        self.sdk_session_configs={}
        from program_runner import ProgramRunner
        self.program=ProgramRunner(self)
        from group_participant import GroupParticipant
        self.group=GroupParticipant(self)
        self.client = self.stdin = None
        self.generation = 0
        self.last_update = 0.
        self.csrf = secrets.token_urlsafe(32)
        self.state = dict(connection='disconnected', message='选择设备后，点击“连接”', devices=[],
            latest=None, metrics=dict(host_hz=None, device_hz=None, age_ms=None),
            device_id=None, identified_device=None, joint_rates=[], mapping=[], mapping_device_id=None, display_selected=None,
            recording=dict(active=False, frames=0, elapsed_s=0, label='baseline', seconds=30),
            sessions=[], log=[], read_only=True, recognition_enabled=False,
            motion_enabled=False, instance='wuji-studio-v1',motion_history=[],
            camera=copy.deepcopy(DEFAULT_CAMERA),camera_revision=0,view_source='feedback',teleoperation_view_mode='automatic')
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
        from calibrated_teleoperation import CalibratedTeleoperation
        from calibration_portability import export_native_bundle
        self._continuation_profile=None
        self.calibrated_teleoperation=CalibratedTeleoperation(
            self._continuation_calibration,
            self._start_calibrated_session,
            self.glove.disconnect_session,self.glove.snapshot,self._continuation_idle,
            lambda proof,**kw:export_native_bundle(proof,self.reports.parent/'calibration_transfer',**kw),
            owner_workspace=os.environ.get('WUJI_FLEET_ID','main'),
            workflow_lock=self.calibration_handover.workflow_lock,
            owner_check=self._continuation_owner_check)

    def _start_calibrated_session(self,bundle):
        config=self.connection_switch._config()
        session=self.glove.start_calibrated(bundle,self._continuation_model_ready,expected_profile=self._continuation_profile)
        self.sdk_session_configs[session]=config
        return session

    def _continuation_calibration(self):
        # No discovery, user refresh or SDK access in this read-only view.
        with self.calibration.lock:
            run=copy.deepcopy(self.calibration.run)
        return dict(run=run,cleanup_required=(run.get('source') in ('official_tty','official_jsonl_native')
            and run.get('running') is False and run.get('id') is not None and run.get('cleanup_confirmed') is not True))

    def _continuation_idle(self):
        from studio_component import require_idle
        from cli_coordination import reservation
        from managed_runtime import status as runtime_status
        if getattr(self,'desktop_closing',False):raise ValueError('Session is closing')
        if runtime_status().get('busy'):raise ValueError('Wait for controller installation')
        state=self.snapshot()
        switch=getattr(self,'connection_switch',None)
        if switch is not None and switch.internal:state['connection_switch']={}
        if (state['calibration_connection'].get('busy') or state['teleoperation_connection'].get('busy')):
            raise ValueError('Another device task is switching')
        continuation=state.get('calibrated_teleoperation') or {}
        if continuation.get('busy') and not self.calibrated_teleoperation.internal:
            raise ValueError('Calibration continuation is still in progress')
        if reservation(self.official_viz.folder) or any(state['official_viz'].get(k) for k in ('active','busy','reserved','scanning')):
            raise ValueError('Finish the official CLI device session first')
        if any(state['official_studio'].get(k) for k in ('active','busy','reserved')):
            raise ValueError('Finish the Studio session first')
        require_idle(state,self.doctor.snapshot())

    def _continuation_owner_check(self,proof):
        from studio_component import enforce_session_owner
        from cli_coordination import enforce
        # This private entry bypasses the ordinary action dispatcher, so it
        # must retain both existing global native/component owner gates.
        enforce_session_owner(dict(name='glove_scan'))
        enforce(dict(name='glove_scan'))
        with self.lock:
            selected=self.state['device_profile']
            if (getattr(self,'desktop_closing',False) or self._continuation_profile!=selected['id']
                    or selected['side']!=proof['side']
                    or proof['source_workspace']!=os.environ.get('WUJI_FLEET_ID','main')):
                raise ValueError('工作区或目标型号已变化，请重新核对 / Workspace or target profile changed; review it first')

    def _continuation_model_ready(self,receipt):
        from calibration_portability import file_digest,IDENT
        with self.lock:
            proof=self.calibrated_teleoperation._current_proof()
            self._continuation_owner_check(proof)
            expected={k:proof[k] for k in ('source_run_id','source_workspace','source_profile','serial','side','model_sha256')}
            if (not isinstance(receipt,dict) or any(receipt.get(k)!=v for k,v in expected.items())
                    or not isinstance(receipt.get('owner_user_id'),str) or not IDENT.fullmatch(receipt['owner_user_id'])
                    or type(receipt.get('imported')) is not bool
                    or file_digest(proof['model_path'])!=(proof['model_sha256'],proof['model_bytes'])):
                raise ValueError('标定接续证明已变化，未打开手套 / Calibration continuation identity changed; glove was not opened')
            with self.glove.lock:
                context=self.glove._continuation or {}
                if self.glove.stopping or any(context.get(k)!=v for k,v in expected.items()):
                    raise ValueError('Calibration controller session changed')
            profile=self.state['device_profile']
            binding=self.retarget.context(profile)['binding']
            binding=dict(binding,glove_serial=proof['serial'],sdk_user=receipt['owner_user_id'])
            context=self.retarget.select(binding,profile)
            tuning=self.tuning.context(context['binding'])
            if tuning['engine']!='sdk':self.tuning.select_engine(context['binding'],'sdk')
            # The human URDF stays in the official SDK user store. It is never
            # assigned to the robot model or a custom hand_model_path override.
            self.state['view_source']='glove'
            return dict(timeout_ms=250,retarget=context['settings'],
                solver=dict(engine='sdk',values=tuning['values']))

    def task_context_action(self,expected_context,action,payload=None):
        from task_control import execute
        return execute(self,expected_context,action,payload)

    def _task_context_dispatch(self,action,payload,state):
        glove=state.get('glove') or {}
        if action=='continue_teleoperation':
            offered=self.calibrated_teleoperation.snapshot()
            return self.action(dict(name='calibrated_teleoperation_continue',
                run_id=offered.get('run_id'),expected_revision=offered.get('expected_revision')))
        if action=='pair_prepare':
            profile=state['device_profile'];binding=self.retarget.context(profile)['binding']
            if (payload['profile']!=profile['id'] or payload['sdk_user']!=binding['sdk_user']
                    or payload['sdk_user']!=(glove.get('user') or {}).get('user_id')
                    or glove.get('connection')!='receiving' or not (glove.get('stream') or {}).get('fresh')
                    or (glove.get('feedback') or {}).get('device_id')):
                raise ValueError('Pairing source, user or target profile changed')
            from retarget_workspace import MappingSwitchRequired
            try:self.action(dict(name='retarget_binding_save',binding=dict(binding,hand_serial=payload['hand_serial'])))
            except MappingSwitchRequired:
                from task_sessions import context_digest
                return self.connection_switch.start(dict(name='connection_switch',request_id=secrets.token_hex(16),
                    expected_owner=context_digest(state),target=dict(mode='teleoperation',serial=binding['glove_serial'],
                        user_id=binding['sdk_user'],profile=profile['id'],hand_serial=payload['hand_serial'])),pair_target=payload)
            return self.action(dict(name='glove_prepare',serial=payload['hand_serial']))
        if action=='unpair':
            session=glove.get('session_id')
            old=self.retarget.context(state['device_profile'])['binding']
            result=self.action(dict(name='glove_unpair'))
            deadline=time.monotonic()+5.
            while time.monotonic()<deadline:
                current=self.glove.snapshot()
                if current.get('session_id')!=session:raise ValueError('Glove session changed during unpair')
                if current.get('hand_unpaired') is True and not (current.get('feedback') or {}).get('device_id'):
                    with self.lock:
                        binding=self.retarget.context(self.state['device_profile'])['binding']
                        if binding!=old:raise ValueError('Pairing changed during hand cleanup')
                        self.retarget.select(dict(binding,hand_serial=''),self.state['device_profile'])
                    return dict(unpaired=True,glove=current)
                if not current.get('busy') or current.get('connection')!='receiving':break
                time.sleep(.025)
            raise ValueError('灵巧手释放尚未确认，未标记解除配对 / Hand cleanup unconfirmed; unpair was not marked complete')
        if action in ('pause','resume'):
            if glove.get('busy'):
                if action=='resume':raise ValueError('Start hand following explicitly from teleoperation')
                return self.action(dict(name='glove_stop'))
            from task_sessions import hand_playback,hand_group_active
            controls=hand_playback(state)
            if hand_group_active(state):
                raise ValueError('Use the ensemble pause/resume control')
            if action=='pause' and controls['can_resume']:return dict(paused=True)
            if action=='resume' and controls['can_pause']:return dict(paused=False)
            if controls['can_'+action]:
                prefix={'program':'program','hardware':'hardware','preview':'demo'}[controls['mode']]
                command=dict(name=prefix+'_'+action)
                if prefix=='hardware':command['lease']=self.hardware_lease
                return self.action(command)
            # Old clients used pause as the emergency stop for pending/unknown
            # hardware. Preserve that escape, never reinterpret it as resume.
            if action=='resume' or not (self.hardware_lease or (state.get('hardware') or {}).get('active') is not False):
                raise ValueError('This playback control is unavailable; refresh the task')
            action='stop'
        if action=='stop':
            if glove.get('busy'):return self.action(dict(name='glove_stop'))
            result=self.action(dict(name='program_stop'))
            if self.hardware_lease or (state.get('hardware') or {}).get('active') is not False:
                return self.action(dict(name='hardware_stop'))
            if (state.get('playback') or {}).get('active'):return self.action(dict(name='demo_stop'))
            return result
        if action=='disconnect':
            if (state.get('calibrated_teleoperation') or {}).get('busy'):
                return self.action(dict(name='calibrated_teleoperation_cancel',
                    run_id=state['calibrated_teleoperation'].get('run_id'),
                    attempt_id=state['calibrated_teleoperation'].get('attempt_id')))
            from task_sessions import pending_teleoperation
            pending=pending_teleoperation(state)
            if pending:
                return self.action(dict(name='teleoperation_handover_cancel',
                    viewer_token=pending['viewer_token'],attempt_id=pending['attempt_id']))
            if glove.get('busy'):
                from device_termination import unconfirmed
                if (glove.get('hardware') or {}).get('active',False) is not False or self.glove.lease or unconfirmed(state):
                    raise ValueError('请先停止动作并确认停用，再断开设备 / Stop and confirm the exact hand disable before disconnecting')
                return self.action(dict(name='glove_disconnect'))
            if (state.get('official_viz') or {}).get('active'):return self.action(dict(name='official_viz_stop'))
            if (state.get('calibration') or {}).get('running'):return self.action(dict(name='calibration_cancel'))
            from device_termination import unconfirmed
            if self.hardware_lease or (state.get('hardware') or {}).get('active') is not False or unconfirmed(state):
                raise ValueError('请先停止动作并确认停用，再断开设备 / Stop and confirm the exact hand disable before disconnecting')
            return self.action(dict(name='disconnect'))
        raise ValueError('Unsupported typed task action')

    def log(self, text):
        self.state['log'].append(dict(time=datetime.now().strftime('%H:%M:%S'), text=text))
        self.state['log'] = self.state['log'][-60:]

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.state)
            result['session_generation']=getattr(self,'generation',0)
            try:result['retarget_binding']=self.retarget.selection(result['device_profile'])
            except (ValueError,OSError):result['retarget_binding']=None
            result['solver_runtime']=self.solver_runtime.snapshot()
            result['official_studio']=self.studio.snapshot()
            result['official_viz']=self.official_viz.snapshot()
            result['official_cli']=self.official_cli.snapshot()
            from cli_coordination import reservation
            marker=reservation()
            result['maintenance']=dict(busy=marker.get('kind') in {'firmware_check','firmware','sdk_check','sdk_update','unknown'},
                kind=marker.get('kind'),serial=marker.get('serial'))
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
            result['glove']['controller_config']=getattr(self,'sdk_session_configs',{}).get(result['glove'].get('session_id'))
            result['unconfirmed_devices']=self.termination.snapshot()
            result['program']=self.program.snapshot()
            # Public run identity without exposing the secret motion lease.
            result['hardware_control_id']=(__import__('hashlib').sha256(self.hardware_lease.encode()).hexdigest()
                if self.hardware_lease else None)
            result['group']=self.group.snapshot()
            with self.calibration.lock:
                result['calibration']={k:self.calibration.run.get(k) for k in ('running','status','side','user','id','serial','source','phase','cleanup_confirmed','interface')}
                result['calibration']['cleanup_required']=(self.calibration.run.get('source') in ('official_tty','official_jsonl_native')
                    and self.calibration.run.get('running') is False and self.calibration.run.get('id') is not None
                    and self.calibration.run.get('cleanup_confirmed') is not True)
            result['calibration_connection']=self.calibration_handover.snapshot()
            result['teleoperation_connection']=self.teleoperation_handover.snapshot()
            continuation=getattr(self,'calibrated_teleoperation',None)
            result['calibrated_teleoperation']=continuation.snapshot() if continuation is not None else dict(busy=False,can_continue=False)
            switch=getattr(self,'connection_switch',None)
            if switch is not None:result['connection_switch']=switch.snapshot(result)
            observer=getattr(self,'version_observer',None)
            if observer:observer([result])
            return result

    def parameter_snapshot(self):
        with self.lock:return self.parameters.snapshot()

    def device_inventory(self):
        """A passive shared directory. Reading it never discovers or connects."""
        from device_inventory import build_inventory,merge_inventories
        from cli_coordination import reservation
        state=self.snapshot();viz=state['official_viz']
        result=build_inventory(viz=viz,controller=state,glove=state['glove'])
        local_official={row['sn'] for row in result['devices'] if row['official_available']}
        owners={};workspace_errors=[];inventories=[result]
        for workspace in getattr(self,'inventory_observer',lambda:[])():
            if not workspace.get('state'):
                workspace_errors.append(dict(id=workspace.get('id'),error='Workspace unavailable'));continue
            observed=workspace['state']
            child=build_inventory(viz=observed.get('official_viz'),controller=observed,glove=observed.get('glove'))
            inventories.append(child)
            for row in child['devices']:
                if row['in_use']:owners.setdefault(row['sn'],[]).append(dict(id=workspace.get('id'),label=workspace.get('label')))
        if len(inventories)>1:result=merge_inventories(inventories)
        for row in result['devices']:
            # A sibling's advertisement is not this Viewer's scan authority.
            row['official_available']=row['sn'] in local_official
            if row['sn'] in owners:row['workspace_owners']=owners[row['sn']]
        reason=None
        if viz.get('scanning'):reason='正在扫描设备 / Device discovery is running'
        elif reservation(self.official_viz.folder):reason='官方 CLI 会话正在使用设备目录 / An official CLI session is active'
        else:
            try:self.official_viz.check_scan()
            except (ValueError,OSError,RuntimeError) as error:reason=str(error)[:700]
        # Explain the common local owner instead of an opaque global failure.
        if reason and state['glove'].get('busy'):
            reason='手套预览或遥操作正在使用设备；已有设备仍可查看，请先在原会话中断开再重新扫描 / Glove preview or teleoperation owns the device; disconnect in its session before rescanning'
        if workspace_errors and not reason:reason='无法确认其他工作区的状态 / Another workspace state is unavailable'
        result.update(scanning=bool(viz.get('scanning')),scanned_at=viz.get('scanned_at'),
                      error=viz.get('error'),scan_allowed=not reason and bool(viz.get('supported')),
                      blocked_reason=reason,backend=viz.get('backend'),workspace_errors=workspace_errors)
        return result

    def calibration_devices(self,fresh=False):
        from device_inventory import normalize_devices
        if fresh:
            rows=self.official_viz.discover_inline()
            return [row for row in normalize_devices(rows,source='official_cli',observed_at=time.time())['devices'] if row['selectable']]
        state=self.official_viz.snapshot()
        if (state.get('error') or state.get('scanning') or not state.get('scanned_at')
                or not 0<=time.time()-state['scanned_at']<=30):return []
        return [row for row in normalize_devices(state.get('devices',[]),source='official_cli',observed_at=state['scanned_at'])['devices'] if row['selectable']]

    def visualization_status(self,theme=None):
        result=self.official_viz.snapshot(theme)
        if result.get('can_start'):
            try:self.official_viz.idle_check()
            except (ValueError,OSError,RuntimeError) as error:
                result.update(can_start=False,blocked_reason=str(error)[:700])
        return result

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
        self.state['identified_device']=None
        self.state['device_version']=None
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
        self.termination.observe('hand',self.state.get('device_id'),self.state['hardware'],self.session_epochs.get(self.hardware_generation,0))

    def send(self, command):
        if self.stdin is None:
            raise ValueError('尚未建立反馈连接')
        self.stdin.write(json.dumps(command)+'\n')
        self.stdin.flush()

    def action(self, command):
        if isinstance(command,dict) and command.get('name') in {'connection_switch','connect','official_viz_start','glove_connect','glove_open','glove_scan','calibration_start','calibration_handover','teleoperation_handover','calibrated_teleoperation_continue'}:
            priority=getattr(self,'version_check_priority',None)
            if priority and not priority():raise ValueError('只读版本检查正在结束，请稍候重试 / Finishing the background version check; retry shortly')
        if isinstance(command,dict) and command.get('name')=='official_viz_start':
            preflight=getattr(self,'connection_version_check',None)
            if preflight:preflight(command.get('serial'))
        from firmware_host import enforce_maintenance
        enforce_maintenance(command)
        # A long-lived preview may outlast the official stable catalog. Do the
        # finite version lookup before controller/file locks; the bridge checks
        # this exact worker/session again at the actual follow dispatch.
        if isinstance(command,dict) and command.get('name')=='glove_follow':self.glove.verify_follow_sdk()
        from calibration_handover import workflow_action
        name=command.get('name') if isinstance(command,dict) else None
        switch=getattr(self,'connection_switch',None)
        if switch is not None:
            if name=='connection_switch':return switch.start(command)
            if name=='connection_switch_users':
                if set(command)!={'name'}:raise ValueError('Invalid wearer directory request')
                return switch.users()
            if (not switch.internal and switch.catalog.get('busy') and workflow_action(command)):
                raise ValueError('SDK wearer directory cleanup is still in progress')
            if name in {'disconnect','glove_disconnect','glove_stop','hardware_stop','official_viz_stop','session_close','calibration_cancel'}:
                switch.request_cancel()
            if not switch.internal and workflow_action(command):
                from studio_component import enforce_session_owner
                enforce_session_owner(command)
                from connection_switch import unsettled
                if unsettled(self.snapshot()):raise ValueError('设备切换或退出尚未确认 / Device switch or cleanup is unconfirmed')
        continuation=getattr(self,'calibrated_teleoperation',None)
        if name in {'task_owner','task_context_action','pair_prepare','unpair'}:
            raise ValueError('Task owner operations require the private controller service')
        if name=='calibrated_teleoperation_continue':
            if not isinstance(command,dict) or set(command)!={'name','run_id','expected_revision'}:raise ValueError('Invalid continuation request')
            if continuation.snapshot().get('busy'):raise ValueError('Continuation is still in progress')
            with self.lock:self._continuation_profile=self.state['device_profile']['id']
            return dict(calibrated_teleoperation=continuation.execute({k:command[k] for k in ('run_id','expected_revision')}))
        if name=='calibrated_teleoperation_cancel':
            if set(command)!={'name','run_id','attempt_id'}:raise ValueError('Invalid continuation cancellation')
            return dict(calibrated_teleoperation=continuation.cancel({k:command[k] for k in ('run_id','attempt_id')}))
        if continuation is not None:
            if name in {'glove_disconnect','session_close'} and continuation.snapshot().get('busy'):continuation.cancel()
            if not continuation.internal and continuation.snapshot().get('busy') and (workflow_action(command) or name in {
                    'retarget_binding_save','retarget_engine_select','retarget_tuning_save','retarget_save','retarget_apply'}):
                raise ValueError('标定资料正在接续，请先等待或取消 / Calibration continuation is in progress; wait or cancel first')
        handover=self.calibration_handover
        teleoperation=getattr(self,'teleoperation_handover',None)
        if name=='teleoperation_handover':return self.teleoperation_handover.start(command)
        if name=='teleoperation_handover_cancel':
            if set(command)!={'name','viewer_token','attempt_id'}:raise ValueError('Invalid teleoperation cancellation')
            return self.teleoperation_handover.cancel({k:command[k] for k in ('viewer_token','attempt_id')})
        if teleoperation is not None and name in {'glove_disconnect','official_viz_stop','session_close'}:teleoperation.request_cancel()
        if name=='calibration_handover':return handover.start(command)
        if name in {'calibration_cancel','official_viz_stop','session_close'}:handover.request_cancel()
        if not workflow_action(command):return self._workflow_action(command)
        if not handover.workflow_lock.acquire(blocking=False):
            raise ValueError('设备任务正在切换，请等待本次操作完成 / A device task transition is in progress')
        try:return self._workflow_action(command)
        finally:handover.workflow_lock.release()

    def _workflow_action(self, command):
        if isinstance(command,dict) and command.get('name') in {'connect','runtime_select','bridge_config_save','bridge_password_save','bridge_password_forget','parameters_sync','doctor_version','doctor_run','doctor_support','device_profile_select','glove_connect'}:
            from managed_runtime import status as runtime_status
            if runtime_status()['busy']:raise ValueError('内置控制环境正在安装，请等待完成 / Wait for controller setup to finish')
        if isinstance(command,dict) and command.get('name') in {'official_cli_check','official_cli_update','official_cli_rollback','official_cli_auto_update','official_viz_scan','device_inventory_scan','official_viz_start','official_viz_stop'}:
            if getattr(self,'desktop_closing',False) and command['name']!='official_viz_stop':raise ValueError('Session is closing')
            name=command['name']
            if name=='official_cli_auto_update':
                if set(command)!={'name','enabled'} or type(command.get('enabled')) is not bool:raise ValueError('Invalid CLI automatic update preference')
                return dict(official_cli=self.official_cli.set_auto_update(command['enabled']))
            if name=='official_cli_check':return dict(official_cli=self.official_cli.start(check_only=True))
            if name=='official_cli_update':return dict(official_cli=self.official_cli.start())
            if name=='official_cli_rollback':return dict(official_cli=self.official_cli.rollback())
            if name in {'official_viz_scan','device_inventory_scan'}:return dict(official_viz=self.official_viz.scan())
            if name=='official_viz_start':return dict(official_viz=self.official_viz.start(command.get('serial'),command.get('theme','light')))
            return dict(official_viz=self.official_viz.stop())
        from cli_coordination import gate,enforce
        # Stop/close must remain available while a component owns the gate.
        if isinstance(command,dict) and command.get('name') in {'session_close','disconnect','hardware_stop','glove_stop','glove_disconnect','calibration_cancel','calibration_terminal_input','program_stop','group_stop'}:
            return self._coordinated_action(command)
        with gate():
            from firmware_host import enforce_maintenance
            enforce_maintenance(command)
            enforce(command)
            return self._coordinated_action(command)

    def _coordinated_action(self, command):
        from studio_component import enforce_session_owner
        enforce_session_owner(command)
        if isinstance(command,dict) and command.get('name')=='control_tools_check':
            with self.lock:return self.official_tools.start_check()
        if isinstance(command,dict) and command.get('name')=='session_close':
            # The reader must acquire this lock to deliver a stop ACK. Reserve
            # the transition, then wait outside the lock while starts are gated.
            with self.lock:
                if getattr(self,'desktop_closing',False):return dict(ok=False,error='会话正在结束 / Session is already closing')
                self.desktop_closing=True
            try:
                from desktop_lifecycle import close_sessions
                self.program.stop()
                if self.group.active:self.group.stop('工作区正在关闭 / Workspace closing')
                self.calibration.cancel()
                return close_sessions(self)
            finally:
                with self.lock:self.desktop_closing=False
        if isinstance(command,dict) and command.get('name') in {'connect','runtime_select','bridge_config_save','bridge_password_save','bridge_password_forget','parameters_sync','doctor_version','doctor_run','doctor_support','device_profile_select','glove_connect'}:
            from managed_runtime import status as runtime_status
            if runtime_status()['busy']:
                raise ValueError('内置控制环境正在安装，请等待完成 / Wait for controller setup to finish')
        if isinstance(command,dict) and command.get('name') in {'runtime_install','runtime_select'}:
            from managed_runtime import start_install,select,status
            with self.lock:
                enforce_session_owner(command)
                if getattr(self,'desktop_closing',False):raise ValueError('会话正在结束 / Session is closing')
                if self.state['connection']!='disconnected' or self.state['hardware'].get('active') is not False or self.glove.busy or self.doctor.snapshot()['running'] or self.parameters.sync_status['busy']:
                    raise ValueError('Disconnect devices before setting up the control environment / 请先断开设备再配置控制环境')
                if command['name']=='runtime_select':select();return dict(runtime=status())
                checked=self.official_tools.snapshot()['check']
                if checked['busy']:raise ValueError('请等待官方组件检查完成 / Wait for the component check')
                repair=checked['status'] in {'ready','failed'} and not checked['compatible']
                return dict(runtime=start_install(repair=repair))
        # Serialize starts and configuration changes with diagnostics. RLock
        # also covers existing command branches that take the same lock.
        with self.lock:
            # A Studio reservation may have appeared while this request was
            # waiting for the controller lock. Recheck at the actual handover.
            enforce_session_owner(command)
            return self._action(command)

    def _action(self, command):
        if not isinstance(command,dict):raise ValueError('Invalid request')
        name=command.get('name','')
        if name in {'program_pause','program_resume','program_stop'} and 'expected_run' in command:
            # _coordinated_action holds the controller lock through validation
            # and dispatch. A late UI command must not affect a replacement run
            # (or stop a newly started group through the branch below).
            expected=command['expected_run']
            if (not isinstance(expected,str) or not re.fullmatch('[a-f0-9]{32}',expected)
                    or expected!=self.program.snapshot().get('run_id')):
                raise ValueError('节目单已切换，请核对当前播放后重试 / Playlist changed; check the current run and retry')
        if name in {'hardware_trial','demo_start'} and threading.current_thread() is self.program.thread:
            # This is the final start boundary under the controller lock. A
            # pause between playlist entries cannot leak one more start.
            self.program.check()
            if self.program.snapshot()['paused']:
                from program_runner import ProgramEntryPaused
                raise ProgramEntryPaused('Playlist is paused before the next entry')
        if getattr(self,'desktop_closing',False) and name not in {'disconnect','hardware_stop','glove_stop','glove_disconnect','glove_keepalive','hardware_keepalive','demo_stop','calibration_cancel','group_stop','program_stop'}:
            raise ValueError('会话正在结束，请稍候 / Session is closing; please wait')
        if name.startswith('group_'):
            return dict(group=self.group.handle(command))
        if self.group.active and not self.group.internal:
            if name in {'hardware_stop','demo_stop','disconnect','session_close','program_stop'}:
                self.group.stop('手动停止 / Manually stopped')
            elif name not in {'session_keepalive','hardware_keepalive','view_camera','display_selected'}:
                raise ValueError('请先停止编组 / Stop the group before changing this hand')
        if self.calibration.run['running'] and name in {'program_start','connect','hardware_start','hardware_trial','hardware_probe','demo_start','glove_scan','glove_hand_scan','glove_open','glove_prepare','glove_follow','doctor_run','doctor_version','doctor_support'}:
            raise ValueError('Finish hand-model calibration first / 请先结束手部模型标定')
        if name=='session_keepalive':
            if self.program.lease:self.program.keepalive(self.program.lease)
            if self.hardware_lease and self.state['hardware'].get('active'):
                self._action(dict(name='hardware_keepalive',lease=self.hardware_lease))
            if self.glove.lease:
                self._action(dict(name='glove_keepalive',lease=self.glove.lease))
            return dict(kept=True)
        if name=='program_start':return self.program.start(command)
        if name=='program_keepalive':return self.program.keepalive(command.get('lease'))
        if name=='program_stop':
            self.program.stop()
            if self.state['hardware'].get('active') is not False:self._action(dict(name='hardware_stop'))
            return dict(stopping=True)
        if name=='program_pause':return self.program.pause(True)
        if name=='program_resume':return self.program.pause(False)
        if self.program.snapshot()['active'] and threading.current_thread() is not self.program.thread:
            if name in {'hardware_start','hardware_trial','hardware_probe','demo_start','glove_scan','glove_hand_scan','glove_open','glove_prepare','glove_follow'}:
                raise ValueError('请先停止节目单 / Stop the active playlist first')
            if name in {'hardware_stop','disconnect','glove_stop','glove_disconnect'}:
                self.program.stop()
        if name=='calibration_cancel':
            return dict(calibration=self.calibration.cancel())
        if name=='calibration_terminal_input':
            if self.calibration.native is None:raise ValueError('No official terminal is active')
            return self.calibration.native.input(command)
        if name in {'calibration_profile_create','calibration_profile_switch','calibration_start'}:
            if self.program.snapshot()['active'] or self.glove.busy or self.state['connection']!='disconnected' or self.state['hardware'].get('active') is not False or self.doctor.snapshot()['running']:
                raise ValueError('End glove, hand and diagnostic sessions before calibration / 标定前请结束手套、灵巧手和诊断会话')
            if name=='calibration_profile_create':
                return dict(calibration=self.calibration.profile('create',command.get('profile')))
            if name=='calibration_profile_switch':
                return dict(calibration=self.calibration.profile('switch',command.get('profile')))
            return dict(calibration=self.calibration.start(command.get('side'),command.get('serial'),command.get('replace',False)))
        if name=='retarget_context':
            return dict(mapping=self.retarget.context(self.state['device_profile']))
        if name in {'solver_probe','solver_install'}:
            if self.glove.busy or self.calibration.run.get('running') or self.state['connection']!='disconnected':
                raise ValueError('Finish device sessions before preparing the solver environment')
            return dict(solver_runtime=self.solver_runtime.start(install=name=='solver_install'))
        if name=='retarget_engine_select':
            binding=self.retarget.context(self.state['device_profile'])['binding']
            if command.get('binding')!=binding:raise ValueError('Pairing changed; reload first')
            engine=command.get('engine')
            if engine not in ('sdk','official_open'):raise ValueError('Unknown solver engine')
            g=self.glove.snapshot()
            if (g.get('feedback') or {}).get('device_id') or self.glove.lease or self.state['connection']!='disconnected':
                raise ValueError('先断开灵巧手反馈再切换求解器 / Disconnect hand feedback before switching solver')
            context=self.tuning.context(binding)
            if command.get('revision')!=context['revision']:raise ValueError('Tuning changed; reload first')
            if self.glove.busy and g.get('connection') not in ('receiving','ready','error'):
                raise ValueError('Wait for glove connection to finish')
            if engine=='official_open':context=self.tuning.save(binding,command.get('values'),context['revision'])
            if g.get('connection')=='receiving':
                self.glove.action(dict(name='glove_solver',solver=dict(engine=engine,values=context['values'])))
            self.tuning.select_engine(binding,engine)
            return dict(tuning=self.tuning.context(binding),submitted=True,runtime_applied=False)
        if name in {'retarget_tuning_context','retarget_tuning_save','retarget_tuning_export'}:
            binding=self.retarget.context(self.state['device_profile'])['binding']
            if name=='retarget_tuning_context':return dict(tuning=self.tuning.context(binding))
            if command.get('binding')!=binding:raise ValueError('Device pairing changed; reload the tuning table / 配对已改变，请重新载入参数表')
            if name=='retarget_tuning_save':return dict(tuning=self.tuning.save(binding,command.get('values'),command.get('revision')))
            return dict(tuning_export=self.tuning.export(binding,command.get('values')))
        if name=='retarget_binding_save':
            glove=self.glove.snapshot()
            if glove['connection']=='receiving':
                from mapping_library import validate_binding
                binding=validate_binding(command.get('binding'))
                current=self.retarget.context(self.state['device_profile'])['binding']
                immutable=('generation','side','glove_serial','sdk_user')
                if (not self.glove.busy or not glove.get('stream',{}).get('fresh')
                        or (glove.get('feedback') or {}).get('device_id') or glove.get('hardware')
                        or self.glove.lease or any(binding[k]!=current[k] for k in immutable)
                        or current['glove_serial']!=glove.get('glove_serial')
                        or current['sdk_user']!=(glove.get('user') or {}).get('user_id')
                        or any(current[k]!=self.state['device_profile'][k] for k in ('generation','side'))):
                    raise ValueError('仅可在本次新鲜纯手套预览中选择目标灵巧手；其他配对字段须断开后修改 / Only target hand selection may change in this fresh glove-only preview')
                if binding['hand_serial']!=current['hand_serial']:
                    # Choosing a target must not silently load that target's
                    # other saved gain/solver settings into an existing stream.
                    from retarget_settings import validate as validate_output
                    target=self.retarget.library.snapshot(binding)
                    tuning=self.tuning.context(binding)
                    stream=glove.get('stream') or {};solver=stream.get('solver') or {}
                    try:equal=validate_output(stream.get('retarget'))==target['settings']
                    except ValueError:equal=False
                    equal=equal and solver.get('applied') is True and solver.get('engine')==tuning['engine']
                    if tuning['engine']=='official_open':equal=equal and solver.get('values_sha256')==tuning['values_sha256']
                    if not equal:
                        from retarget_workspace import MappingSwitchRequired
                        raise MappingSwitchRequired('此目标使用另一套映射，请使用连接灵巧手进行切换 / This target uses different mapping settings; use Connect hand to switch')
            elif glove['connection'] not in ('disconnected','ready','error'):
                raise ValueError('先断开手套预览再改变配对 / Disconnect preview before changing pairing')
            return dict(mapping=self.retarget.select(command.get('binding'),self.state['device_profile']))
        if name in {'retarget_save','retarget_preset_save','retarget_preset_delete','retarget_apply'}:
            from retarget_settings import validate
            context=self.retarget.context(self.state['device_profile'])
            if name=='retarget_preset_save':self.retarget.library.save_preset(command.get('preset'),validate(command.get('values')))
            elif name=='retarget_preset_delete':
                try:self.retarget.library.delete_preset(command.get('preset'))
                except KeyError:raise ValueError('Preset no longer exists / 预设已不存在，请刷新') from None
            elif name=='retarget_apply':
                g=self.glove.snapshot()
                if g.get('connection')!='receiving' or (g.get('feedback') or {}).get('device_id') or self.glove.lease:
                    raise ValueError('只在未连接灵巧手的手套预览中应用；已有反馈时请重新连接 / Apply in glove-only preview or reconnect')
                self.glove.action(dict(name='glove_retarget',retarget=context['settings']))
            else:
                self.retarget.library.save(context['binding'],validate(command.get('values')),command.get('revision'))
            return dict(mapping=self.retarget.context(self.state['device_profile']))
        if name=='glove_open':
            from glove_protocol import validate
            validate(command)
            context=self.retarget.open_glove(command,self.state['device_profile'],self.glove.snapshot().get('user') or {})
            tuning=self.tuning.context(context['binding'])
            if tuning['engine']!='sdk':self.tuning.select_engine(context['binding'],'sdk')
            command=dict(command,retarget=context['settings'],solver=dict(engine='sdk',values=tuning['values']))
        if name=='glove_prepare':
            serial=self.retarget.context(self.state['device_profile'])['binding']['hand_serial']
            if serial and command.get('serial') and command['serial']!=serial:raise ValueError('Hand differs from saved pairing / 灵巧手与保存配对不符')
            command=dict(command,serial=serial or command.get('serial',''))
        if name=='glove_follow':
            # Preparation verified a typed SDK device. Recheck the published
            # identity against the *current* binding before forwarding start;
            # dropdowns, old discovery and a fresh target alone are not proof.
            profile=self.state['device_profile'];binding=self.retarget.context(profile)['binding']
            glove=self.glove.snapshot();feedback=glove.get('feedback') or {}
            identity=feedback.get('identified_device') or {};user=glove.get('user') or {}
            hand=binding['hand_serial']
            if (not self.glove.busy or self.glove.stopping or glove.get('connection')!='receiving'
                    or not hand or feedback.get('device_id')!=hand or identity.get('serial')!=hand
                    or identity.get('source')!='sdk_identity' or identity.get('profile')!=profile['id']
                    or profile['id']!=profile['generation']+'_'+profile['side']
                    or glove.get('profile')!=profile['id']
                    or any(binding[k]!=profile[k] for k in ('generation','side'))
                    or not binding['glove_serial'] or glove.get('glove_serial')!=binding['glove_serial']
                    or not isinstance(user.get('user_id'),str) or user['user_id']!=binding['sdk_user']):
                raise ValueError('遥操作实际设备或档案与当前配对不符，请重新核对连接 / Actual teleoperation device or SDK profile differs from the current pairing; review the connection')
        if name=='hardware_stop' and self.glove.busy:
            return self.glove.action(dict(name='glove_stop'))
        if isinstance(name,str) and name.startswith('glove_'):
            if name not in {'glove_stop','glove_disconnect','glove_keepalive'} and (self.state['connection']!='disconnected' or self.state['hardware'].get('active') is not False or self.doctor.snapshot()['running'] or self.parameters.sync_status['busy']):
                raise ValueError('先结束其他设备会话或诊断 / Finish the other device session or diagnostics first')
            switch=getattr(self,'connection_switch',None)
            config=switch._config() if switch is not None and name in ('glove_scan','glove_connect') else None
            result=self.glove.action(command)
            if config is not None:self.sdk_session_configs[self.glove.session]=config
            if name in {'glove_scan','glove_open','glove_follow','glove_prepare'}:
                self.player.command(dict(name='demo_stop'));self.state['view_source']='glove'
            return result
        if self.glove.busy and name in {'connect','device_profile_select','bridge_config_save','parameters_sync','doctor_version','doctor_run','doctor_support','demo_start','hardware_start','hardware_probe','hardware_trial'}:
            raise ValueError('先断开手套遥操作会话 / Disconnect the glove session first')
        if command.get('name') in {'doctor_version','doctor_run','doctor_support'}:
            with self.lock:
                if self.state['connection']!='disconnected' or self.state['hardware'].get('active') is not False or self.parameters.sync_status['busy']:
                    raise ValueError('先断开设备连接，再运行独立诊断 / Disconnect the SDK session before diagnostics')
                if name=='doctor_support':return self.doctor.start_support(command.get('days',1),command.get('description',''))
                return self.doctor.start('version' if command['name']=='doctor_version' else 'diagnose',command.get('serial',''),command.get('address',''))
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
                        from bimanual_program import INTERNAL_IDS
                        grouped=self.group.active and self.group.internal and self.group.mode=='hardware'
                        if (command.get('action') not in ({x['id'] for x in CATALOG} | (INTERNAL_IDS if grouped else set())) or
                            type(command.get('amplitude')) not in {int,float} or command['amplitude'] not in {.25,.5,.75,1.} or
                            type(command.get('speed',1.)) not in {int,float} or command.get('speed',1.) not in PLAYBACK_SPEEDS or
                            type(command.get('cycles')) is not int or command['cycles'] not in {1,3} or
                            command.get('workspace_clear') is not True):raise ValueError('选择试运行幅度和1/3轮，并确认周围清空')
                        outgoing={k:command.get(k) for k in ('name','action','amplitude','cycles','workspace_clear')}
                        outgoing['speed']=command.get('speed',1.)
                        if grouped:outgoing['group_sync']=dict(token=self.group.token)
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
        if isinstance(command,dict) and command.get('name')=='teleoperation_view_mode':
            if set(command)-{'name','mode'} or command.get('mode') not in ('automatic','target','constrained','feedback'):
                raise ValueError('Unknown teleoperation display source')
            self.state['teleoperation_view_mode']=command['mode'];self.state['view_source']='glove'
            return dict(teleoperation_view_mode=command['mode'])
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
                self.log(f'保存{len(entries)}个显示关节对应；没有向灵巧手发送命令')
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
                self.clear_live()
                self.generation += 1
                generation = self.generation
                self.session_epochs[generation]=self.termination.new_session();self.hardware_generation=generation
                self.state['connection'] = 'connecting'
                self.state['message'] = '正在重新发现设备并核对型号、左右手'
                self.state['device_id'] = None
                self.state['devices'] = []
                self.state['hardware'] = dict(ready=False, active=False, actions=[], probe_ready=False, reason='正在识别新设备')
                self.hardware_lease = None
                self.log('用户请求连接所选设备，仅接收反馈')
                thread=threading.Thread(target=self.read_session,args=(command,generation),daemon=True)
                self.feedback_sessions[generation]=dict(serial=command.get('serial'),thread=thread,
                    worker_closed=False,transport_closed=False,reader_finished=False)
                thread.start()
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
            # Late close acknowledgements still belong to the old serial. They
            # may confirm that stop, but must never revive the new connection.
            if event.get('device_id'):self.session_devices[generation]=event['device_id']
            feedback_record=getattr(self,'feedback_sessions',{}).get(generation)
            if kind=='closed' and 'hardware' in event and feedback_record is not None:
                feedback_record['worker_closed']=True
                feedback_record['hardware']=copy.deepcopy(event['hardware'])
            if 'hardware' in event:
                self.termination.observe('hand',event.get('device_id') or self.session_devices.get(generation),event['hardware'],self.session_epochs.get(generation,0))
                if event['hardware'].get('active') is False and event['hardware'].get('stop_confirmed') is True:
                    self.glove.confirm_termination(event.get('device_id') or self.session_devices.get(generation),self.session_epochs.get(generation,0))
                if kind=='closed' and self.hardware_generation==generation and self.state['connection']=='disconnected' and self.state.get('device_id')==self.session_devices.get(generation):
                    self.state['hardware']=event['hardware']
                    self.state['motion_enabled']=event['hardware'].get('active')
            if generation != self.generation:
                return
            if kind == 'identity':
                from device_profiles import save_profile
                self.state['device_profile'] = save_profile(event['profile'])
                self.state['device_id'] = event['device_id']
                self.state['identified_device'] = dict(serial=event['device_id'],profile=event['profile'],source='sdk_identity')
                self.state['mapping'] = []
                self.state['mapping_device_id'] = None
                self.state['display_selected'] = None
                self.player.command(dict(name='demo_stop'))
                self.log('自动识别：'+self.state['device_profile']['zh']+' · '+event['device_id'])
            elif kind == 'selection':
                self.state['devices'] = event['devices']
                self.state['devices_at'] = time.time()
                self.state['connection'] = 'error'
                self.state['message'] = event['message']
                self.clear_live()
            elif kind == 'state':
                self.last_update = time.monotonic()
                self.state['device_version']=copy.deepcopy(event.get('device_version'))
                for key in ('connection', 'message', 'latest', 'metrics', 'recording', 'device_id', 'joint_rates'):
                    self.state[key] = event[key]
                if 'hardware' in event:
                    self.state['hardware']=event['hardware'];self.hardware_generation=generation
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
            elif kind == 'closed' and 'hardware' in event:
                self.state['hardware']=event['hardware']
                self.state['motion_enabled']=event['hardware'].get('active')

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
            closed=False
            try:
                if client and client.close() is False:raise ValueError('Feedback transport exit is unconfirmed')
                closed=True
            finally:
                record=getattr(self,'feedback_sessions',{}).get(generation)
                if record is not None:record.update(transport_closed=closed,reader_finished=True)
            with self.lock:
                if generation == self.generation:
                    self.client = self.stdin = None
                    self.state['recording']['active'] = False
                    self.clear_live()

    def stop_feedback_confirmed(self,generation,serial,timeout=8.):
        """Release exactly one inactive feedback reader, preserving uncertainty."""
        with self.lock:
            record=self.feedback_sessions.get(generation)
            if (generation!=self.generation or self.state.get('device_id')!=serial or record is None
                    or record.get('serial')!=serial or self.state['hardware'].get('active') is not False
                    or self.hardware_lease):
                raise ValueError('Hand feedback owner changed or stop is unconfirmed')
            thread=record['thread']
        self.action(dict(name='disconnect'))
        thread.join(timeout)
        with self.lock:
            if (thread.is_alive() or not record.get('reader_finished') or not record.get('transport_closed')
                    or not record.get('worker_closed') or (record.get('hardware') or {}).get('active') is not False
                    or (record.get('hardware') or {}).get('stop_confirmed') is not True):
                raise ValueError('Hand feedback release is unconfirmed; no new device was opened')
        return dict(serial=serial,session=generation,cleanup_confirmed=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, body, mime='application/json; charset=utf-8', filename=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
        style_nonce=None
        if mime.startswith('text/html') and b'<head>' in body:
            style_nonce=secrets.token_urlsafe(24)
            body=body.replace(b'<head>',b'<head><meta name="workbench-style-nonce" content="'+style_nonce.encode('ascii')+b'">',1)
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        parent=os.environ.get('WUJI_FLEET_PARENT','')
        ancestor=parent if re.fullmatch(r'http://127\.0\.0\.1:[0-9]{4,5}',parent) else "'none'"
        studio_view=urlsplit(self.path).path=='/official_studio_viewer.html'
        connect="'self' ws://127.0.0.1:*" if studio_view else "'self'"
        if studio_view:ancestor="'self'"
        task_query=parse_qs(urlsplit(self.path).query)
        if (urlsplit(self.path).path=='/' and task_query.get('task_view')==['1']
                and task_query.get('embedded')==['1'] and not parent
                and hasattr(self.server,'_tasks')
                and self.server._tasks.allows_embedded_main(task_query.get('task_id',[None])[0])):
            ancestor="'self'"
        styles="'self'"+(" 'nonce-"+style_nonce+"'" if style_nonce else '')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src "+styles+"; script-src 'self'; connect-src "+connect+"; frame-src http://127.0.0.1:*; frame-ancestors "+ancestor+"; base-uri 'none'; form-action 'self'")
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def allowed_host(self):
        return self.headers.get('Host') in {f'127.0.0.1:{PORT}', f'localhost:{PORT}'}

    def _task_view_options(self):
        # Read only an already-created catalog. A task-page check must not
        # create a controller, a workspace, or a device discovery process.
        workspaces=getattr(self.server,'_workspaces',None)
        tasks=getattr(self.server,'_tasks',None)
        if os.environ.get('WUJI_FLEET_PARENT') or workspaces is None or tasks is None:return {}
        return dict(read_groups=lambda:[row for row in tasks.snapshot()['tasks'] if row.get('kind')=='ensemble'],
                    group_action=workspaces.action,group_lock=workspaces.lock)

    def _task_view_headers(self):
        ident=self.headers.get('X-Task-Id')
        context=self.headers.get('X-Task-Context')
        query=parse_qs(urlsplit(self.headers.get('Referer','')).query)
        embedded=query.get('task_view')==['1']
        if not embedded and ident is None and context is None:return None
        if (not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{24}',ident)
                or not isinstance(context,str) or not re.fullmatch('[0-9a-f]{64}',context)
                or embedded and query.get('task_id')!=[ident]):
            raise ValueError('任务页面归属尚未核对，请重新打开此任务 / Task page owner is unverified; reopen this task')
        return ident,context

    def do_GET(self):
        if not self.allowed_host():
            return self.reply(403, dict(error='Local host required'))
        url = urlsplit(self.path)
        if url.path=='/api/connection-switch':
            try:
                query=parse_qs(url.query)
                if (set(query)!={'request_id'} or len(query['request_id'])!=1
                        or not re.fullmatch('[0-9a-f]{32}',query['request_id'][0])):raise ValueError('Invalid switch result query')
                switch=self.server.controller.connection_switch
                state=self.server.controller.snapshot();result=state['connection_switch']
                if result.get('request_id')!=query['request_id'][0]:raise ValueError('Switch request is not current')
                return self.reply(200,dict(connection_switch={key:result.get(key) for key in (
                    'busy','phase','request_id','attempt_id','target','source','receipt','error','cleanup_error','source_released','unchanged')}))
            except (ValueError,TypeError) as error:return self.reply(400,dict(error=str(error)))
            except Exception:return self.reply(503,dict(error='Switch result unavailable'))
        if url.path=='/api/task-view':
            try:
                from task_view import scope
                query=parse_qs(url.query)
                if set(query)!={'task_id'} or len(query['task_id'])!=1:raise ValueError('Invalid task view query')
                options=self._task_view_options()
                result=scope(self.server.controller,query['task_id'][0],read_groups=options.get('read_groups'))
                if not result['current'] and not os.environ.get('WUJI_FLEET_PARENT') and hasattr(self.server,'_fleet'):
                    # Resolve solely through the registered task/fleet records;
                    # a browser can never supply a controller URL or child ID.
                    tasks=self.server.tasks.snapshot().get('tasks',[])
                    matches=[r for r in tasks if r.get('task_id')==query['task_id'][0]
                             and r.get('endpoint_id')!='main' and r.get('status') not in ('disconnected','conflict')]
                    if len(matches)==1:
                        with self.server.fleet.lock:child=self.server.fleet.children.get(matches[0]['endpoint_id'])
                        if child is not None:
                            result=self.server.fleet.get(child,'/api/task-view?task_id='+query['task_id'][0])
                return self.reply(200,result)
            except (ValueError,TypeError) as error:return self.reply(400,dict(error=str(error)))
            except Exception:return self.reply(503,dict(error='Task owner state unavailable'))
        if url.path=='/api/task-sessions':
            if os.environ.get('WUJI_FLEET_PARENT'):return self.reply(403,dict(error='Use tasks in the main window'))
            return self.reply(200,self.server.tasks.snapshot())
        if url.path=='/api/official-studio':return self.reply(200,self.server.studio.snapshot())
        if url.path=='/api/control-tools':return self.reply(200,self.server.controller.official_tools.snapshot())
        if url.path=='/api/official-cli':return self.reply(200,self.server.controller.official_cli.snapshot())
        if url.path=='/api/device-maintenance':
            if os.environ.get('WUJI_FLEET_PARENT'):return self.reply(403,dict(error='Use device maintenance in the main window'))
            return self.reply(200,self.server.maintenance_snapshot())
        if url.path=='/api/sdk-maintenance':
            if os.environ.get('WUJI_FLEET_PARENT'):return self.reply(403,dict(error='Use component maintenance in the main window'))
            return self.reply(200,self.server.sdk_maintenance.snapshot())
        if url.path=='/api/update-notifications':
            if os.environ.get('WUJI_FLEET_PARENT'):return self.reply(403,dict(error='Use update notifications in the main window'))
            return self.reply(200,self.server.update_notifications.snapshot())
        if url.path=='/api/firmware-terminal':
            if os.environ.get('WUJI_FLEET_PARENT'):return self.reply(403,dict(error='Use device maintenance in the main window'))
            try:
                query=parse_qs(url.query)
                if set(query)!={'run','after'} or any(len(v)!=1 for v in query.values()):raise ValueError('Invalid firmware terminal query')
                return self.reply(200,self.server.firmware_transport.terminal_read(query['run'][0],int(query['after'][0])))
            except (ValueError,TypeError) as error:return self.reply(400,dict(error=str(error)))
        if url.path=='/api/official-viz':return self.reply(200,self.server.controller.visualization_status(parse_qs(url.query).get('theme',[None])[0]))
        if url.path=='/api/device-inventory':return self.reply(200,self.server.controller.device_inventory())
        if url.path=='/api/calibration-connection':return self.reply(200,self.server.controller.calibration_handover.snapshot())
        if url.path=='/api/calibration-guide':
            try:
                from official_calibration_guides import read_guide
                step=parse_qs(url.query).get('step',[])
                if len(step)!=1 or not re.fullmatch(r'[0-9]{1,2}',step[0]):raise ValueError('Invalid official calibration step')
                data,mime=read_guide(int(step[0]))
                return self.reply(200,data,mime)
            except (OSError,ValueError,RuntimeError,TimeoutError) as error:
                return self.reply(503,dict(error=str(error)[:500]))
        if url.path=='/api/official-viewer':
            from official_viewer import snapshot
            return self.reply(200,snapshot(parent_port=self.server.server_address[1]))
        if url.path.startswith('/studio-assets/'):
            try:path=self.server.studio.asset(url.path.removeprefix('/studio-assets/'))
            except (ValueError,OSError) as error:return self.reply(404,dict(error=str(error)))
            mime={'.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.txt':'text/plain; charset=utf-8','.svg':'image/svg+xml','.png':'image/png'}[path.suffix]
            return self.reply(200,path.read_bytes(),mime)
        if url.path=='/api/workspaces':
            if os.environ.get('WUJI_FLEET_PARENT'):return self.reply(403,dict(error='Use the main window'))
            return self.reply(200,self.server.workspaces.snapshot())
        if url.path=='/api/workspace-view':
            member=parse_qs(url.query).get('member',['main'])[0]
            if member=='main':
                jpeg,meta=self.server.pose.get()
                result=dict(meta=meta,image='data:image/jpeg;base64,'+base64.b64encode(jpeg).decode('ascii') if jpeg else None)
            else:
                with self.server.fleet.lock:r=self.server.fleet.children.get(member)
                if not r:return self.reply(404,dict(error='Unknown hand session'))
                try:result=self.server.fleet.get(r,'/api/view')
                except (OSError,ValueError):return self.reply(503,dict(error='Hand view unavailable'))
            return self.reply(200,result)
        if url.path == '/api/fleet':
            return self.reply(200,dict(devices=self.server.fleet.snapshot() if not os.environ.get('WUJI_FLEET_PARENT') else [],embedded=bool(os.environ.get('WUJI_FLEET_PARENT'))))
        if url.path in {'/api/retarget','/api/retarget/context'}:
            c=self.server.controller
            try:
                with c.lock:result=c.retarget.context(c.state['device_profile'])
            except (ValueError,OSError) as error:
                return self.reply(409,dict(error=str(error)))
            return self.reply(200,result if url.path.endswith('/context') else result['settings'])
        if url.path == '/api/calibration':
            return self.reply(200,self.server.controller.calibration.snapshot(refresh=parse_qs(url.query).get('refresh')==['1']))
        if url.path == '/api/workbench-update':
            return self.reply(200,self.server.updates.snapshot())
        if url.path == '/api/calibration-terminal':
            try:
                session=self.server.controller.calibration.native
                if session is None:raise ValueError('No official terminal is available')
                query=parse_qs(url.query);after=int(query.get('after',['0'])[0])
                result=session.output(query.get('run',[''])[0],after)
            except (ValueError,TypeError) as error:return self.reply(409,dict(error=str(error)))
            return self.reply(200,result)
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
        if url.path=='/api/support':
            from support_bundle import artifact
            try:
                query=parse_qs(url.query);path=artifact(query.get('id',[''])[0],query.get('kind',['all'])[0])
                stream=path.open('rb')
            except (ValueError,FileNotFoundError) as error:return self.reply(404,dict(error=str(error)))
            except OSError as error:return self.reply(503,dict(error=local_file_error(error),error_code='local_file_error'))
            with stream:
                self.send_response(200)
                self.send_header('Content-Type','application/zip' if path.suffix=='.zip' else 'text/plain; charset=utf-8')
                self.send_header('Content-Length',str(os.fstat(stream.fileno()).st_size))
                self.send_header('Content-Disposition','attachment; filename="'+path.name+'"')
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                self.end_headers()
                import shutil
                shutil.copyfileobj(stream,self.wfile,1024*1024)
            return
        if url.path in ('/api/doctor','/api/doctor/report'):
            state=self.server.controller.doctor.snapshot()
            if url.path.endswith('/report'):
                if not state.get('report'):return self.reply(404,dict(error='No completed diagnostic report'))
                fmt=parse_qs(url.query).get('format',['json'])[0]
                if fmt=='txt':
                    from doctor_export import report_text
                    return self.reply(200,report_text(state['report']).encode('utf-8-sig'),'text/plain; charset=utf-8',filename='wuji-doctor-report.txt')
                if fmt=='raw':
                    return self.reply(200,state['report']['stdout'].encode('utf-8'),'application/json; charset=utf-8',filename='wuji-doctor-official.json')
                if fmt!='json':return self.reply(400,dict(error='Unknown report format'))
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
        if url.path in {'/api/joints', '/api/joint-reference'}:
            from joint_reference import catalog as joint_catalog, image_path
            query = parse_qs(url.query)
            pid = query.get('profile', ['hand2_left'])[0]
            try:
                if url.path == '/api/joints':
                    return self.reply(200, joint_catalog(pid, query.get('language', ['zh'])[0]))
                path = image_path(pid, int(query.get('index', ['-1'])[0]))
            except (ValueError, TypeError):
                return self.reply(400, dict(error='Invalid joint reference'))
            if not path.is_file():
                return self.reply(404, dict(error='Joint reference unavailable'))
            return self.reply(200, path.read_bytes(), 'image/png')
        if url.path == '/api/report':
            ident = parse_qs(url.query).get('id', [''])[0]
            if not re.fullmatch('[a-f0-9]{32}', ident):
                return self.reply(400, dict(error='Invalid report'))
            path = self.server.controller.reports/(ident+'.json')
            try:
                return self.reply(200, path.read_bytes(), filename='left-hand-'+ident+'.json')
            except FileNotFoundError:return self.reply(404, dict(error='Report not found'))
            except OSError as error:return self.reply(503,dict(error=local_file_error(error),error_code='local_file_error'))
        window_query=parse_qs(url.query,keep_blank_values=True)
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
        for name in ('studio.js','locale.js','floating_panel.js','viewer.js','action_picker.js','brand.js','installation.js','profiles.js','doctor.js','glove.js','desktop_shell.js','device_network.js','connection_toolbar.js','workbench_upgrade.js','connection_hub.js','official_calibration.js','settings.js','group_panel.js','workspaces.js','group_view.js','workbench_sheet.js','workbench_clarity.js','numeric_grid.js','retarget_tuning.js','joint_inspector.js','connection_refinement.js','action_row.js','control_surfaces.js','navigation_motion.js','playlist_rows.js','playlist_editor.js'):
            assets['/'+name]=(name,'text/javascript; charset=utf-8')
        for name in ('studio.css','floating_panel.css','glass.css','glove.css','desktop_glass.css','desktop_refinement.css','glass_refresh.css','workbench_upgrade.css','connection_hub.css','official_calibration.css','group_panel.css','workspaces.css','group_view.css','workbench_sheet.css','workbench_clarity.css','numeric_grid.css','retarget_tuning.css','joint_inspector.css','connection_refinement.css','control_surfaces.css','spring_motion.css','navigation_motion.css','playlist_editor.css'):
            assets['/'+name]=(name,'text/css; charset=utf-8')
        assets['/viewer']=('viewer.html','text/html; charset=utf-8')
        for name in ('official_tools.js','official_studio.js','official_studio_viewer.js','official_viz.js'):assets['/'+name]=(name,'text/javascript; charset=utf-8')
        for name in ('official_tools.css','official_studio.css','official_studio_viewer.css','official_viz.css'):assets['/'+name]=(name,'text/css; charset=utf-8')
        assets['/device_maintenance.js']=('device_maintenance.js','text/javascript; charset=utf-8')
        assets['/device_maintenance.css']=('device_maintenance.css','text/css; charset=utf-8')
        assets['/sdk_maintenance.js']=('sdk_maintenance.js','text/javascript; charset=utf-8')
        assets['/sdk_maintenance.css']=('sdk_maintenance.css','text/css; charset=utf-8')
        assets['/update_notifications.js']=('update_notifications.js','text/javascript; charset=utf-8')
        assets['/update_notifications.css']=('update_notifications.css','text/css; charset=utf-8')
        for name in ('device_inventory.js','device_workflows.js','device_history.js','simple_mode.js','demonstration_navigation.js'):assets['/'+name]=(name,'text/javascript; charset=utf-8')
        assets['/device_inventory.css']=('device_inventory.css','text/css; charset=utf-8')
        for name in ('simple_mode.css','toolbar_refinement.css','demonstration_navigation.css'):assets['/'+name]=(name,'text/css; charset=utf-8')
        for name in ('official_terminal.js','workbench_update.js','vendor/terminal/xterm.js','vendor/terminal/addon-fit.js'):assets['/'+name]=(name,'text/javascript; charset=utf-8')
        for name in ('official_terminal.css','workbench_update.css','vendor/terminal/xterm.css'):assets['/'+name]=(name,'text/css; charset=utf-8')
        assets['/official_studio_viewer.html']=('official_studio_viewer.html','text/html; charset=utf-8')
        for name in ('task_workspace.js','calibration_continuation.js','viewport_resize.js'):assets['/'+name]=(name,'text/javascript; charset=utf-8')
        for name in ('task_frame_host.js','task_window.js'):assets['/'+name]=(name,'text/javascript; charset=utf-8')
        assets['/task_window.css']=('task_window.css','text/css; charset=utf-8')
        for name in ('task_workspace.css','calibration_continuation.css','viewport_resize.css'):assets['/'+name]=(name,'text/css; charset=utf-8')
        assets['/favicon.ico']=('favicon.ico','image/x-icon')
        assets['/app-icon.png']=('app-icon.png','image/png')
        assets['/wuji-logo.png']=('wuji-logo.png','image/png')
        if url.path=='/' and 'task_window' in window_query:
            ids=window_query['task_window']
            if len(ids)!=1 or not re.fullmatch(r'[0-9a-f]{24}',ids[0]) or any(key not in {'task_window','desktop'} for key in window_query) or ('desktop' in window_query and window_query['desktop']!=['1']):
                return self.reply(400,dict(error='Invalid task window'))
            if not any(task.get('task_id')==ids[0] and task.get('status')!='disconnected' for task in self.server.tasks.snapshot().get('tasks',[])):
                return self.reply(404,dict(error='Device task is not available'))
            assets['/']=('task_window.html','text/html; charset=utf-8')
        if url.path not in assets:
            return self.reply(404, dict(error='Not found'))
        name, mime = assets[url.path]
        return self.reply(200, (HERE/'web'/name).read_bytes(), mime)

    def do_POST(self):
        origin = self.headers.get('Origin')
        if self.path=='/api/connection-switch-owner-check':
            if not self.allowed_host() or os.environ.get('WUJI_FLEET_PARENT') or origin or not hasattr(self.server,'_fleet'):
                return self.reply(403,dict(ok=False,error='Registered child controller required'))
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=2048:raise ValueError('Invalid child switch check')
                payload=json.loads(self.rfile.read(size))
                if not isinstance(payload,dict) or set(payload)!={'workspace','request_id','attempt_id','expected_owner'}:
                    raise ValueError('Invalid child switch fields')
                if not re.fullmatch('[0-9a-f]{16}',str(payload['workspace'])):raise ValueError('Invalid child workspace')
                with self.server.fleet.lock:child=self.server.fleet.children.get(payload['workspace'])
                if child is None or not child.get('owner_token') or not secrets.compare_digest(self.headers.get('X-Workbench-Owner',''),child['owner_token']):
                    return self.reply(403,dict(ok=False,error='Registered child controller required'))
                state=self.server.fleet.get(child,'/api/state');transition=state.get('connection_switch') or {}
                from task_sessions import context_digest
                if (transition.get('busy') is not True or transition.get('request_id')!=payload['request_id']
                        or transition.get('attempt_id')!=payload['attempt_id'] or context_digest(state)!=payload['expected_owner']):
                    raise ValueError('Child switch owner changed')
                self.server.switch_siblings_idle(payload['workspace'])
                return self.reply(200,dict(ok=True))
            except (ValueError,TypeError,OSError) as error:return self.reply(400,dict(ok=False,error=str(error)[:700]))
            except Exception:return self.reply(503,dict(ok=False,error='Parent workspace ownership unavailable'))
        if self.path=='/api/task-owner':
            parent=os.environ.get('WUJI_FLEET_PARENT','')
            token=os.environ.get('WUJI_FLEET_OWNER_TOKEN','')
            if (not self.allowed_host() or not parent or origin!=parent or not token
                    or not secrets.compare_digest(self.headers.get('X-Workbench-Owner',''),token)):
                return self.reply(403,dict(ok=False,error='Parent task owner required'))
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=4096 or self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    raise ValueError('Invalid private task request')
                payload=json.loads(self.rfile.read(length))
                if not isinstance(payload,dict) or set(payload)!={'expected_context','action','payload'}:
                    raise ValueError('Invalid private task fields')
                result=self.server.controller.task_context_action(payload['expected_context'],payload['action'],payload['payload'])
                return self.reply(200,dict(ok=True,**(result or {})))
            except (ValueError,TypeError) as error:return self.reply(409,dict(ok=False,error=str(error)))
            except Exception:return self.reply(503,dict(ok=False,error='Task owner unavailable'))
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
            task_scope=self._task_view_headers()
            if str(payload.get('name','')).startswith(('firmware_','sdk_update','sdk_release_')):
                if self.path!='/api/action' or task_scope is not None or os.environ.get('WUJI_FLEET_PARENT'):
                    raise ValueError('设备维护请在主窗口操作 / Use device maintenance in the main window')
                result=self.server.maintenance_action(payload)
                return self.reply(200,dict(ok=True,**(result or {})))
            if task_scope is not None:
                from task_view import execute
                if self.path!='/api/action':raise ValueError('Task pages cannot manage the desktop')
                if (os.environ.get('WUJI_FLEET_PARENT') and payload.get('name') in {
                        'official_viz_start','calibration_start','calibration_handover',
                        'calibration_profile_create','calibration_profile_switch','official_cli_update','official_cli_rollback','official_cli_auto_update'}):
                    raise ValueError('请在主工作台执行官方标定任务 / Open official calibration in the main workbench')
                result=execute(self.server.controller,*task_scope,payload,**self._task_view_options())
            elif self.path == '/api/desktop':
                from desktop_tools import dispatch
                host=getattr(self.server,'desktop',None)
                if not host:return self.reply(409,dict(ok=False,error='Open the Windows desktop application to use this interface'))
                result=dispatch(host,payload)
            elif payload.get('name') in ('desktop_open','desktop_viewer') and getattr(self.server,'desktop',None):
                result=self.server.desktop.open_viewer() if payload['name']=='desktop_viewer' else dict(ok=True,native_opened=True)
            elif payload.get('name')=='task_session_action':
                if os.environ.get('WUJI_FLEET_PARENT'):raise ValueError('Use tasks in the main window')
                result=self.server.tasks.action(payload)
            elif (os.environ.get('WUJI_FLEET_PARENT') and payload.get('name') in {
                    'official_viz_start','calibration_start','calibration_handover',
                    'calibration_profile_create','calibration_profile_switch','official_cli_update','official_cli_rollback','official_cli_auto_update'}):
                raise ValueError('请在主工作台执行官方标定任务 / Open official calibration in the main workbench')
            elif str(payload.get('name','')).startswith('official_studio_'):
                if payload.get('name') not in {'official_studio_close','official_studio_probe'}:
                    raise ValueError('官方 Studio 已停用；请在设置中使用官方 CLI / SDK / Studio integration is retired; use official CLI / SDK in Settings')
                if os.environ.get('WUJI_FLEET_PARENT'):raise ValueError('请在主工作台打开官方 Studio / Open official Studio in the main workbench')
                result=self.server.studio.handle(payload)
            elif str(payload.get('name','')).startswith(('workspace_','ensemble_')):
                if os.environ.get('WUJI_FLEET_PARENT'):raise ValueError('Open workspaces in the main window')
                result=self.server.workspaces.action(payload)
            elif payload.get('name')=='fleet_create':
                if os.environ.get('WUJI_FLEET_PARENT'):raise ValueError('Open device manager in the main workspace')
                result=dict(device=self.server.fleet.create(payload.get('label'),payload.get('profile')))
            elif payload.get('name')=='fleet_remove':
                if os.environ.get('WUJI_FLEET_PARENT'):raise ValueError('Open device manager in the main workspace')
                result=self.server.fleet.remove(payload.get('id'))
            elif payload.get('name')=='fleet_rename':
                if os.environ.get('WUJI_FLEET_PARENT'):raise ValueError('Open device manager in the main workspace')
                result=dict(device=self.server.fleet.rename(payload.get('id'),payload.get('label')))
            elif payload.get('name')=='fleet_keepalive':
                self.server.controller.action(dict(name='session_keepalive'))
                result=dict(errors=self.server.fleet.heartbeat())
            elif payload.get('name')=='fleet_stop':
                if hasattr(self.server,'_workspaces'):
                    for coordinator in self.server._workspaces.coordinators.values():coordinator.stop()
                errors=[]
                for name in ('program_stop','glove_stop' if self.server.controller.glove.busy else 'hardware_stop'):
                    try:self.server.controller.action(dict(name=name))
                    except (ValueError, OSError) as error:errors.append(dict(id='main',action=name,error=str(error)))
                result=dict(errors=errors+self.server.fleet.stop_all())
            elif payload.get('name') in {'workbench_update_check','workbench_update_prepare','workbench_update_apply'}:
                name=payload['name']
                if name=='workbench_update_apply':result=dict(workbench_update=self.server.updates.apply())
                else:result=dict(workbench_update=self.server.updates.start('check' if name=='workbench_update_check' else 'prepare'))
            else:result = self.server.controller.action(payload)
            self.reply(200, {'ok':True, **(result or {})})
        except (ValueError, TypeError) as error:
            self.reply(400, dict(ok=False, error=str(error)))
        except Exception as error:
            # These operations only read/write application files. Preserve the
            # OS failure category; device/session exceptions retain their guard.
            file_action=(self.path=='/api/action' and isinstance(locals().get('payload'),dict)
                and payload.get('name') in {'workbench_update_check','workbench_update_prepare','workbench_update_apply',
                    'parameters_save','retarget_tuning_save','retarget_tuning_export'})
            file_desktop=(self.path=='/api/desktop' and isinstance(locals().get('payload'),dict)
                and payload.get('operation') in {'import_model','prepare_update'})
            if (file_action or file_desktop) and isinstance(error,OSError) and not isinstance(error,TimeoutError):
                return self.reply(503,dict(ok=False,error=local_file_error(error),error_code='local_file_error'))
            if self.path == '/api/desktop':
                import logging
                logging.exception('Desktop code interface failed')
                return self.reply(503,dict(ok=False,error='Desktop operation unavailable; check desktop.log'))
            self.reply(503, dict(ok=False, error='反馈通道暂不可用，请检查连接状态'))


class ConsoleHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False
    allow_reuse_port = False

    @property
    def firmware_transport(self):
        with self.controller.lock:
            if not hasattr(self,'_firmware_transport'):
                from firmware_host import FirmwareHost
                self._firmware_transport=FirmwareHost(self.controller)
            return self._firmware_transport

    @property
    def firmware(self):
        with self.controller.lock:
            if not hasattr(self,'_firmware'):
                from firmware_service import FirmwareService
                host=self.firmware_transport
                service=FirmwareService(host,host.cli_identity,self.controller.official_cli.idle_check,
                    host.acquire,host.release,host.device,recovery_check=host.recovery_check,cancel_query=host.cancel_background_check)
                from cli_coordination import reservation
                marker=reservation(host.folder)
                if marker.get('kind')=='firmware':service.restore_unknown(marker)
                self._firmware=service
            return self._firmware

    def maintenance_snapshot(self):
        from bridge_config import load_config
        from official_device_discovery import use_native
        result=self.firmware.snapshot()
        run=self.firmware_transport.run_snapshot()
        device=result.get('device') or {}
        # A subsequent read belongs to a new operation. Do not attach the old
        # completed PTY (or another device's PTY) to its progress and controls.
        result['run']=run if (result.get('operation')=='upgrade' and isinstance(run,dict)
            and run.get('serial')==(device.get('sn') or device.get('serial'))) else None
        result['supported']=use_native(load_config())
        viewer=self.controller.official_viz.snapshot()
        result['viewer']=dict(active=viewer.get('active') is True,phase=viewer.get('phase'),
            serial=(viewer.get('reported_device') or {}).get('sn'),session_id=viewer.get('session_id'))
        return result

    def firmware_check_from_viewer(self,serial,viewer_token):
        """One explicit action ends only its exact read-only Viewer, then checks."""
        viewer=self.controller.official_viz;workflow=self.controller.calibration_handover.workflow_lock
        if not workflow.acquire(blocking=False):raise ValueError('设备任务正在切换，请稍候 / A device task is switching')
        try:
            from cli_coordination import gate
            with gate(viewer.folder):
                if getattr(self.controller,'desktop_closing',False):raise ValueError('Workbench is closing')
                viewer.connection_record(serial,viewer_token,require_glove=False)
                self.firmware_preview_idle(viewer_token)
            viewer.stop_owned(serial,viewer_token,require_glove=False)
            self.controller.official_cli.idle_check()
            rows=self.controller.calibration_devices(fresh=True)
            if not any((row.get('serial') or row.get('sn'))==serial for row in rows):
                raise ValueError('所选设备暂不可见，未检查其它设备 / Selected device is unavailable; no other device was checked')
            return self.firmware.start_check(serial)
        finally:workflow.release()

    def start_firmware_check(self,serial):
        """An explicit version check refreshes expired discovery once, for this SN."""
        if not isinstance(serial,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',serial):
            raise ValueError('Select a discovered device')
        workflow=self.controller.calibration_handover.workflow_lock
        if not workflow.acquire(blocking=False):raise ValueError('设备任务正在切换，请稍候 / A device task is switching')
        try:
            if getattr(self.controller,'desktop_closing',False):raise ValueError('Workbench is closing')
            self.controller.official_cli.idle_check()
            rows=self.controller.calibration_devices()
            if not any((row.get('serial') or row.get('sn'))==serial for row in rows):
                rows=self.controller.calibration_devices(fresh=True)
            if not any((row.get('serial') or row.get('sn'))==serial for row in rows):
                raise ValueError('所选设备暂不可见，未检查其它设备 / Selected device is unavailable; no other device was checked')
            # The service repeats freshness and reserves the shared gate before
            # its exact-SN read. Discovery alone never authorizes an upgrade.
            return self.firmware.start_check(serial)
        finally:workflow.release()

    def check_connect_version(self,serial):
        """A bounded read in an explicit connection, never a mandatory update.

        It precedes the Viewer opening its SDK handle. Offline reads may be
        skipped only after their child has certainly ended and released its
        lease. An uncertain exit never permits a competing device connection.
        """
        if not isinstance(serial,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',serial):return
        from bridge_config import load_config
        from official_device_discovery import use_native
        if not use_native(load_config()):return
        from native_cli import resolve
        try:resolve()
        except (OSError,ValueError):return  # Existing connection prepares CLI.
        service=self.firmware;state=service.snapshot();report=state.get('notification') or {}
        if report.get('serial')==serial and type(state.get('checked_at')) in (int,float) and 0<=time.time()-state['checked_at']<24*3600:return
        from cli_coordination import reservation
        if reservation(self.firmware_transport.folder):return  # Original gate explains its current owner.
        started=time.monotonic()
        try:service.start_check(serial,notification_only=True)
        except Exception:
            if service.snapshot().get('locked'):raise
            return
        thread=service.thread
        if thread:thread.join(max(0.,3.-(time.monotonic()-started)))
        if service.snapshot().get('busy') and not service.cancel_notification_check(timeout=2):
            raise ValueError('版本检查退出尚未确认，未打开另一会话 / Version check exit is unconfirmed; no competing session was opened')
        if service.snapshot().get('locked'):raise ValueError('版本检查退出尚未确认 / Version check exit is unconfirmed')

    def maintenance_action(self,command):
        name=command.get('name')
        if name in {'firmware_check','firmware_upgrade','firmware_recovery_check',
                    'sdk_release_check','sdk_update_prepare','sdk_update_recover'}:
            # The operator's request yields our read-only discovery first.
            # No Viewer, calibration, teleoperation or firmware write is stopped.
            with self.controller.official_viz.discovery_priority():
                monitor=getattr(self,'_update_notifications',None)
                if monitor is not None and not monitor.give_user_priority():
                    raise ValueError('版本检查退出尚未确认 / Version-check process exit is unconfirmed')
                return self._maintenance_action(command)
        return self._maintenance_action(command)

    def _maintenance_action(self,command):
        name=command.get('name')
        if name in {'sdk_release_check','sdk_update_prepare'} and set(command)=={'name'}:
            return dict(sdk_maintenance=self.sdk_maintenance.start('check' if name=='sdk_release_check' else 'update'))
        if name=='sdk_update_recover' and set(command)=={'name'}:
            return dict(sdk_maintenance=self.sdk_maintenance.recover())
        if name=='firmware_check' and set(command)=={'name','serial','viewer_token'}:
            self.firmware_check_from_viewer(command['serial'],command['viewer_token'])
        elif name=='firmware_check' and set(command)=={'name','serial'}:
            self.start_firmware_check(command['serial'])
        elif name=='firmware_upgrade' and set(command)=={'name','preview_token','confirmed'}:
            self.firmware.start_upgrade(command['preview_token'],confirmed=command['confirmed'])
        elif name=='firmware_terminal_input':
            return self.firmware_transport.terminal_input(command)
        elif name=='firmware_recovery_check' and set(command)=={'name'}:
            self.firmware.verify_recovery()
        else:raise ValueError('Unsupported device maintenance request')
        return dict(device_maintenance=self.maintenance_snapshot())

    @property
    def sdk_maintenance(self):
        with self.controller.lock:
            if not hasattr(self,'_sdk_maintenance'):
                from sdk_maintenance import SDKMaintenance
                from bridge_config import load_config
                host=self.firmware_transport
                self._sdk_maintenance=SDKMaintenance(acquire=host.acquire,release=host.release,
                    idle=self.controller.official_cli.idle_check,config=load_config,folder=host.folder)
                from cli_coordination import reservation
                marker=reservation(host.folder)
                if marker.get('kind')=='sdk_update':self._sdk_maintenance.restore_unknown(marker)
            return self._sdk_maintenance

    @property
    def update_notifications(self):
        with self.controller.lock:
            if not hasattr(self,'_update_notifications'):
                from update_notifications import UpdateNotifications
                def states():
                    rows=[self.controller.snapshot()]
                    if hasattr(self,'_fleet'):
                        rows.extend(row['state'] for row in self._fleet.inventory_snapshots() if row.get('state'))
                    return rows
                self._update_notifications=UpdateNotifications(states=states,
                    app=lambda:self.updates,cli=lambda:self.controller.official_cli,
                    sdk=lambda:self.sdk_maintenance,firmware=lambda:self.firmware,
                    devices=self.controller.calibration_devices,idle=lambda:self.controller.official_cli.idle_check())
                self.controller.version_observer=self._update_notifications.observe
                self.controller.version_check_priority=self._update_notifications.give_user_priority
                self.controller.connection_version_check=self.check_connect_version
            return self._update_notifications

    def configure_official_cli(self,startup=True):
        def check_workspaces(check,allow_local_viewer=None,allow_local_device=None,exclude_workspace=None):
            from managed_runtime import status as runtime_status
            if runtime_status().get('busy'):raise ValueError('Wait for control environment installation')
            if getattr(self.controller,'desktop_closing',False):raise ValueError('Workbench is closing')
            def one(state,doctor,local=False):
                checked=state
                local_switch=getattr(self.controller,'connection_switch',None)
                if local and local_switch is not None and local_switch.internal:
                    checked=copy.deepcopy(state);checked['connection_switch']={}
                    if allow_local_device is not None:
                        from connection_switch import owner
                        if owner(state)!=allow_local_device:raise ValueError('Device owner changed before preflight')
                        checked.update(connection='disconnected',glove={'busy':False},hardware={'active':False},
                            recording={'active':False},unconfirmed_devices=[],program={'active':False})
                check(checked,doctor)
                transition=state.get('calibration_connection') or {}
                if transition.get('busy') and not (local and self.controller.calibration_handover.internal):
                    raise ValueError('另一个工作区正在切换设备任务 / Another workspace is switching device tasks')
                teleoperation=state.get('teleoperation_connection') or {}
                local_teleop=getattr(self.controller,'teleoperation_handover',None)
                if teleoperation.get('busy') and not (local and local_teleop is not None and local_teleop.internal):
                    raise ValueError('另一个工作区正在切换遥操作任务 / Another workspace is switching teleoperation tasks')
                switch=state.get('connection_switch') or {}
                local_switch=getattr(self.controller,'connection_switch',None)
                if switch.get('busy') and not (local and local_switch is not None and local_switch.internal):
                    raise ValueError('另一个工作区正在切换设备 / Another workspace is switching devices')
                continuation=state.get('calibrated_teleoperation') or {}
                local_continuation=getattr(self.controller,'calibrated_teleoperation',None)
                if continuation.get('busy') and not (local and local_continuation is not None and local_continuation.internal):
                    raise ValueError('另一个工作区正在接续标定任务 / Another workspace is continuing calibration')
                if (state.get('calibration') or {}).get('cleanup_required') is True:
                    raise ValueError('标定退出尚未确认，请先核对原会话 / Calibration cleanup is unconfirmed; review its owning session')
                viewer=state.get('official_viz') or {}
                owned=viewer.get('active') or viewer.get('busy') or viewer.get('scanning') or viewer.get('session_id') or viewer.get('phase') in ('starting','running','stopping')
                if owned and not (local and allow_local_viewer and viewer.get('session_id')==allow_local_viewer
                                  and viewer.get('active') is True and viewer.get('phase')=='running'):
                    raise ValueError('请先结束原版画面会话 / Finish the original Viewer session first')
            one(self.controller.snapshot(),self.controller.doctor.snapshot(),True)
            if self.controller.official_tools.snapshot()['check']['busy']:raise ValueError('Wait for official software checks')
            if hasattr(self,'_fleet'):
                with self.fleet.lock:records=list(self.fleet.children.values())
                for row in records:
                    if exclude_workspace is not None and row.get('id')==exclude_workspace:continue
                    try:one(self.fleet.get(row,'/api/state'),self.fleet.get(row,'/api/doctor'))
                    except OSError:raise ValueError('无法确认另一个工作区状态 / Another workspace state is unavailable') from None
        def all_idle():
            from studio_component import require_idle
            handover=self.controller.calibration_handover
            teleoperation=getattr(self.controller,'teleoperation_handover',None)
            switch=getattr(self.controller,'connection_switch',None)
            token=handover.viewer_token if handover.internal else teleoperation.viewer_token if teleoperation is not None and teleoperation.internal else switch.viewer_token if switch is not None and switch.internal else None
            check_workspaces(require_idle,token)
        def scan_idle():
            from bridge_config import load_config
            from official_device_discovery import use_native
            from discovery_guard import require_discovery_idle
            if use_native(load_config()):check_workspaces(require_discovery_idle)
            else:all_idle()
        self.controller.official_cli.idle_check=all_idle
        self.switch_siblings_idle=lambda ident:check_workspaces(__import__('studio_component').require_idle,exclude_workspace=ident)
        self.controller.official_viz.idle_check=all_idle
        self.controller.official_viz.scan_check=scan_idle
        self.controller.calibration.device_provider=self.controller.calibration_devices
        def calibration_idle():
            from cli_coordination import reservation
            if reservation(self.controller.official_viz.folder):
                raise ValueError('官方 CLI 会话正在运行；用户档案暂不刷新 / Official CLI session active; user profiles have not been refreshed')
            all_idle()
        self.controller.calibration.metadata_check=calibration_idle
        self.controller.calibration_handover.idle_check=all_idle
        def firmware_preview_idle(token):
            from studio_component import require_idle
            check_workspaces(require_idle,token)
        self.firmware_preview_idle=firmware_preview_idle
        if hasattr(self.controller,'teleoperation_handover'):self.controller.teleoperation_handover.idle_check=all_idle
        if hasattr(self.controller,'connection_switch'):self.controller.connection_switch.idle_check=all_idle
        if hasattr(self.controller,'connection_switch'):
            def switch_preflight(source):
                from studio_component import require_idle
                token=source['session'] if source and source['mode']=='official' else None
                check_workspaces(require_idle,token,source)
            self.controller.connection_switch.preflight_check=switch_preflight
        if hasattr(self.controller,'calibrated_teleoperation'):self.controller.calibrated_teleoperation.idle_check=all_idle
        def profile_read_idle():
            # Native user list/show reads local SDK files, not a device. A
            # discovery/calibration reservation must not require a connected
            # Viewer or turn a harmless metadata refresh into a device task.
            # Mutations/start/handover retain calibration_idle/all_idle above.
            from managed_runtime import status as runtime_status
            if runtime_status().get('busy'):raise ValueError('Wait for control environment installation')
            if getattr(self.controller,'desktop_closing',False):raise ValueError('Workbench is closing')
            if self.controller.official_tools.snapshot()['check']['busy']:raise ValueError('Wait for official software checks')
        self.controller.calibration.profile_read_check=profile_read_idle
        self.controller.inventory_observer=lambda:self.fleet.inventory_snapshots() if hasattr(self,'_fleet') else []
        if startup:
            self.controller.official_cli.startup()
            self.update_notifications.startup()

    @property
    def updates(self):
        with self.controller.lock:
            if not hasattr(self,'_workbench_updates'):
                from workbench_update import WorkbenchUpdate
                from runtime_paths import EDITION
                self._workbench_updates=WorkbenchUpdate(DATA,EDITION,
                    idle_check=lambda:self.controller.official_cli.idle_check(),desktop=lambda:getattr(self,'desktop',None))
            return self._workbench_updates

    @property
    def studio(self):
        def all_idle():
            from studio_component import require_idle
            require_idle(self.controller.snapshot(),self.controller.doctor.snapshot())
            if hasattr(self,'_fleet'):
                with self.fleet.lock:records=list(self.fleet.children.values())
                for row in records:
                    try:require_idle(self.fleet.get(row,'/api/state'),self.fleet.get(row,'/api/doctor'))
                    except OSError:raise ValueError('无法确认其他工作区状态，请先结束其会话 / Another workspace state is unavailable') from None
        self.controller.studio.idle_check=all_idle
        return self.controller.studio

    @property
    def workspaces(self):
        # Lazy construction can race first polling and first button request.
        with self.controller.lock:
            if not hasattr(self,'_workspaces'):
                from workspace_groups import WorkspaceGroups
                self._workspaces=WorkspaceGroups(self.fleet,self.controller,DATA)
            return self._workspaces

    @property
    def tasks(self):
        with self.controller.lock:
            if not hasattr(self,'_tasks'):
                from task_groups import TaskWorkspaces
                self._tasks=TaskWorkspaces(self)
            return self._tasks

    @property
    def fleet(self):
        with self.controller.lock:
            if not hasattr(self,'_fleet'):
                from device_fleet import DeviceFleet
                self._fleet=DeviceFleet(DATA,RESOURCE,f'http://127.0.0.1:{self.server_address[1]}')
            return self._fleet

    def server_bind(self):
        if sys.platform == 'win32':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def server_close(self):
        monitor=getattr(self,'_update_notifications',None)
        if monitor:monitor.close()
        super().server_close()


def main():
    server = ConsoleHTTPServer(('127.0.0.1', PORT), Handler)
    server.controller = Controller()
    server.pose = PoseView(server.controller)
    server.configure_official_cli()
    try:
        server.serve_forever()
    finally:
        try:
            if hasattr(server,'_fleet'):server._fleet.close()
        finally:
            server.controller.action(dict(name='disconnect'))
            server.server_close()


if __name__ == '__main__':
    main()
