"""Root-owned software maintenance; never starts a device or a teleop worker."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import threading


def _owner_exited(marker):
    pid=marker.get('pid')
    if type(pid) is not int or pid<=0:return False
    if os.name=='nt':
        from firmware_host import process_state
        return process_state(pid,marker.get('start'))=='dead'
    try:os.kill(pid,0);return False
    except ProcessLookupError:return True
    except PermissionError:return False


class SDKMaintenance:
    def __init__(self, *, acquire, release, idle, config, resolver=None, probe=None, updater=None, recoverer=None, folder=None):
        from sdk_release import resolve_release
        from sdk_environment_update import probe_selected, update_owned
        self.acquire,self.release,self.idle,self.config=acquire,release,idle,config
        self.resolver,self.probe,self.updater=resolver or resolve_release,probe or probe_selected,updater or update_owned
        self.background_probe=probe or (lambda config:probe_selected(config,background=True))
        from cli_coordination import root_folder
        self.folder=Path(folder or root_folder());self.recoverer=recoverer;self.expected=None;self.config_digest=None
        self.checked_config_digest=None
        self.check_generation=0
        self.lock=threading.RLock();self.thread=None;self.token=None
        self.state=dict(busy=False,phase='unchecked',release=None,proof=None,error=None,
                        compatible=False,can_update=False,recovery_required=False,last_operation=None)

    def check_background(self,*,cancelled=lambda:False):
        """Read software metadata/imports only; never reserve/open a device.

        A manual operation or controller change supersedes this result. Active
        workers keep their imported SDK; a notice never switches their runtime.
        """
        from sdk_release import requirement
        from sdk_runtime import verify_proof
        with self.lock:
            if self.state['busy'] or self.state['recovery_required']:return False
            generation=self.check_generation
        if cancelled():return False
        config=self.config();digest=self._config_digest(config)
        release=self.resolver(force=True)
        if cancelled():return False
        proof=self.background_probe(config)
        expected=requirement(release)
        compatible=False
        try:verify_proof(proof,expected);compatible=True
        except ValueError:pass
        owned=(config.get('mode') in ('wsl','macvm')
            and config.get('agent_directory')=='/opt/hand-workbench'
            and config.get('python')=='/opt/hand-workbench-venv/bin/python')
        with self.lock:
            if (cancelled() or generation!=self.check_generation or self.state['busy'] or self.state['recovery_required']
                    or digest!=self._config_digest(self.config())):return False
            self.checked_config_digest=digest
            self.state.update(release=release,proof=proof,compatible=compatible,
                can_update=owned and not compatible,error=None,last_operation='check',
                phase='up_to_date' if compatible else 'available' if owned else 'external')
        return True

    def snapshot(self):
        with self.lock:state=copy.deepcopy(self.state);checked=self.checked_config_digest
        if checked is not None:
            try:current=self._config_digest(self.config())==checked
            except (ValueError,OSError):current=False
            if not current:
                state.update(compatible=False,can_update=False,proof=None,context_current=False)
                if not state['busy'] and not state['recovery_required']:state['phase']='changed'
        if state.get('release'):
            from sdk_release import validate_release
            try:validate_release(state['release'])
            except (ValueError,TypeError,KeyError):
                state.update(compatible=False,can_update=False,catalog_current=False)
                if not state['busy'] and not state['recovery_required'] and state['phase'] in ('up_to_date','available','external'):
                    state['phase']='expired'
        return state

    @staticmethod
    def _config_digest(config):
        return hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest()

    def restore_unknown(self, marker):
        if marker.get('kind')!='sdk_update' or not isinstance(marker.get('token'),str) or not re.fullmatch('[0-9a-f]{32}',marker['token']):
            raise ValueError('Invalid SDK maintenance owner')
        with self.lock:
            self.check_generation+=1
            self.token=marker['token'];self.state.update(busy=False,phase='unknown',recovery_required=True,
                compatible=False,can_update=False,last_operation='update',
                error='组件更新退出状态未确认，请核对组件 / Component update exit is unconfirmed; verify components')
        path=self.folder/'sdk-maintenance.json'
        try:
            if path.is_symlink() or path.stat().st_size>2*1024*1024:raise ValueError('Invalid SDK recovery record')
            saved=json.loads(path.read_text(encoding='utf-8'))
            if saved.get('token')!=self.token:raise ValueError('SDK recovery owner changed')
            from sdk_release import validate_release
            self.expected=validate_release(saved['release'],require_fresh=False)
            self.config_digest=saved['config_digest']
            with self.lock:self.state['release']=copy.deepcopy(self.expected)
        except (OSError,ValueError,KeyError,TypeError):pass

    def recover(self):
        with self.lock:
            if self.state['busy'] or not self.state['recovery_required'] or not self.token:
                raise ValueError('没有可核验的组件更新记录 / No verifiable component update record')
            self.check_generation+=1
            self.state.update(busy=True,phase='verifying',error=None)
        try:
            from cli_coordination import gate,reservation
            from sdk_environment_update import recover_owned
            with gate(self.folder):
                marker=reservation(self.folder)
                if marker.get('token')!=self.token or marker.get('kind')!='sdk_update':raise ValueError('SDK maintenance ownership changed')
                self.idle();config=self.config()
                pending=marker.get('stage')=='pending'
                if pending:
                    if not _owner_exited(marker):raise ValueError('Original maintenance process exit is unconfirmed')
                elif not self.expected or self._config_digest(config)!=self.config_digest:
                    raise ValueError('Controller configuration changed; restore its original configuration before verification')
            result=dict(ok=True) if pending else (self.recoverer or recover_owned)(config,self.expected,operation_token=self.token)
            if not isinstance(result,dict) or result.get('ok') is not True:raise ValueError('SDK maintenance exit remains unconfirmed')
            self.release(self.token);self.token=None
            proof=result.get('sdk_proof')
            with self.lock:self.state.update(busy=False,phase='unchecked',recovery_required=False,error=None,proof=proof,compatible=False,can_update=False)
        except Exception as error:
            with self.lock:self.state.update(busy=False,phase='unknown',recovery_required=True,error=str(error)[:900])
            raise
        return self.snapshot()

    def start(self, operation):
        if operation not in ('check','update'):raise ValueError('Unknown SDK maintenance action')
        with self.lock:
            if self.state['busy'] or self.state['recovery_required']:raise ValueError('SDK maintenance has not finished')
            if operation=='update' and not self.state['can_update']:raise ValueError('先检查控制组件 / Check controller components first')
            if operation=='update' and self.checked_config_digest!=self._config_digest(self.config()):
                raise ValueError('控制端已改变，请重新检查 / Controller changed; check again')
            self.check_generation+=1
            generation=self.check_generation
            self.state.update(busy=True,phase='checking' if operation=='check' else 'updating',error=None,last_operation=operation)
        try:
            # A check imports the selected interpreter and reads release metadata.
            # It neither owns nor opens a device, and must not ask users to close
            # a Viewer, calibration or teleoperation just to read SDK versions.
            if operation=='update':self.token=self.acquire('sdk_update')
            target=self._check_work if operation=='check' else self._work
            arguments=(generation,) if operation=='check' else (operation,)
            self.thread=threading.Thread(target=target,args=arguments,name='Official SDK maintenance',daemon=True)
            self.thread.start()
        except BaseException as error:
            if operation=='update' and self.token:self.release(self.token);self.token=None
            with self.lock:self.state.update(busy=False,phase='error',compatible=False,can_update=False,error=str(error)[:900])
            raise
        return self.snapshot()

    def _check_work(self, generation):
        try:
            from sdk_release import requirement
            from sdk_runtime import verify_proof
            config=self.config();digest=self._config_digest(config)
            release=self.resolver(force=True)
            proof=self.probe(config)
            expected=requirement(release)
            compatible=False
            try:verify_proof(proof,expected);compatible=True
            except ValueError:pass
            owned=(config.get('mode') in ('wsl','macvm')
                and config.get('agent_directory')=='/opt/hand-workbench'
                and config.get('python')=='/opt/hand-workbench-venv/bin/python')
            with self.lock:
                if generation!=self.check_generation or self.state['recovery_required']:return
                if digest!=self._config_digest(self.config()):
                    self.state.update(phase='changed',compatible=False,can_update=False,error=None);return
                self.checked_config_digest=digest
                self.state.update(release=release,proof=proof,compatible=compatible,
                    can_update=owned and not compatible,error=None,
                    phase='up_to_date' if compatible else 'available' if owned else 'external')
        except Exception as error:
            with self.lock:
                if generation==self.check_generation and not self.state['recovery_required']:
                    # Keep last-known facts for diagnosis without treating a
                    # failed refresh as current proof or update permission.
                    self.state.update(phase='error',error=str(error)[:900],compatible=False,can_update=False)
        finally:
            with self.lock:
                if generation==self.check_generation and not self.state['recovery_required']:self.state['busy']=False

    def _work(self, operation):
        uncertain=False;updater_started=False
        try:
            from sdk_release import requirement
            from sdk_runtime import verify_proof
            config=self.config();self.idle()
            if operation=='update' and self.checked_config_digest!=self._config_digest(config):raise ValueError('Controller changed; check again')
            release=self.resolver(force=True)
            proof=self.probe(config)
            owned=(config.get('mode') in ('wsl','macvm')
                and config.get('agent_directory')=='/opt/hand-workbench'
                and config.get('python')=='/opt/hand-workbench-venv/bin/python')
            compatible=False
            try:verify_proof(proof,requirement(release));compatible=True
            except ValueError:pass
            with self.lock:
                self.checked_config_digest=self._config_digest(config)
                self.state.update(release=release,proof=proof,compatible=compatible,can_update=owned and not compatible)
            if operation=='update' and not compatible:
                self.idle()
                from cli_coordination import atomic
                self.expected=copy.deepcopy(release);self.config_digest=self._config_digest(config)
                atomic(self.folder/'sdk-maintenance.json',dict(token=self.token,release=release,config_digest=self.config_digest))
                # The updater owns staging, same-interpreter verification and
                # rollback. Unknown rollback keeps the global maintenance lease.
                updater_started=True
                def on_dispatch():
                    from cli_coordination import gate,reservation
                    with gate(self.folder):
                        marker=reservation(self.folder)
                        if marker.get('kind')!='sdk_update' or marker.get('token')!=self.token:raise ValueError('SDK maintenance ownership changed before apply')
                        marker['stage']='apply';atomic(self.folder/'session.json',marker)
                result=self.updater(config,release,idle=self.idle,operation_token=self.token,on_dispatch=on_dispatch)
                if not isinstance(result,dict) or result.get('ok') is not True:
                    uncertain=True;raise ValueError('SDK update completion is unconfirmed')
                proof=self.probe(config);verify_proof(proof,requirement(release))
                with self.lock:self.state.update(proof=proof,compatible=True,can_update=False)
                compatible=True
            with self.lock:self.state['phase']='up_to_date' if compatible else 'available' if owned else 'external'
        except Exception as error:
            uncertain=uncertain or getattr(error,'rollback_confirmed',not updater_started) is not True
            with self.lock:self.state.update(phase='unknown' if uncertain else 'error',error=str(error)[:900],recovery_required=uncertain)
        finally:
            if not uncertain and self.token:
                try:self.release(self.token);self.token=None
                except Exception:
                    with self.lock:self.state.update(phase='unknown',recovery_required=True,error='SDK maintenance ownership is unconfirmed')
            with self.lock:self.state['busy']=False
