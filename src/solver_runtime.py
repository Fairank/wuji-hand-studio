"""Operator-visible setup for the isolated official solver, without device sessions."""
import copy,json,subprocess,threading,sys
from pathlib import Path

class SolverRuntime:
    def __init__(self):
        self.lock=threading.Lock();self.state=dict(busy=False,ready=False,stage='unchecked',error='')

    def snapshot(self):
        with self.lock:return copy.deepcopy(self.state)

    def start(self,install=False):
        with self.lock:
            if self.state['busy']:raise ValueError('Solver environment check/setup is already running')
            self.state.update(busy=True,stage='installing' if install else 'checking',error='')
        threading.Thread(target=self.run,args=(install,),daemon=True).start()
        return self.snapshot()

    def run(self,install):
        try:
            from bridge_config import load_config
            from calibration_cli import controller_args
            config=load_config()
            if config['mode']=='wsl':
                from managed_runtime import WslController,PYTHON
                controller=WslController(config,'hand2_left');directory=controller.agent_directory;python=PYTHON
            elif config['mode']=='local':
                directory=config.get('agent_directory') or str(Path(__file__).parent);python=config.get('python') or 'python3'
            else:raise ValueError('This verified embedded solver currently supports Windows built-in Linux and local Linux x86_64 / 当前支持 Windows 内置 Linux 和本机 Linux x86_64')
            command=controller_args(config,[python,'-u',directory+'/solver_setup.py','install' if install else 'status'])
            completed=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=1100 if install else 40,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
            lines=completed.stdout.strip().splitlines()
            if not lines:raise ValueError((completed.stderr.strip() or 'Solver returned no status output')[-500:])
            try:result=json.loads(lines[-1])
            except (ValueError,TypeError):raise ValueError('Solver returned invalid status output') from None
            if completed.returncode or not result.get('ok'):raise ValueError(result.get('error') or 'Official solver environment failed')
            with self.lock:self.state.update(result,stage='ready' if result.get('ready') else 'not_installed',error='')
        except Exception as error:
            with self.lock:self.state.update(ready=False,stage='error',error=str(error)[:500])
        finally:
            with self.lock:self.state['busy']=False
