"""Bounded local IPC to an isolated optimizer environment on the controller."""
import json,os,queue,subprocess,threading
from pathlib import Path

def environment():
    return Path.home()/'.local/share/hand-workbench/official-solver-0212'

class SolverSession:
    def __init__(self,profile,values):
        python=environment()/'bin/python'
        if not python.is_file() or not (environment()/'ready.json').is_file():
            raise ValueError('请先在官方参数表准备求解环境 / Prepare the official solver environment first')
        self.responses=queue.Queue(maxsize=2);self.ident=0;self.process=None;self.info={}
        self.process=subprocess.Popen([str(python),'-u',str(Path(__file__).with_name('solver_worker.py'))],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',bufsize=1,
            env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1'))
        def read():
            try:
                for line in self.process.stdout:
                    self.responses.put(json.loads(line))
            except Exception:pass
            finally:
                try:self.responses.put_nowait(None)
                except queue.Full:pass
        threading.Thread(target=read,daemon=True).start()
        try:self.info=self.request(dict(op='init',profile=profile,values=values),30)['info']
        except Exception:self.close();raise

    def request(self,payload,timeout):
        self.ident+=1;self.process.stdin.write(json.dumps(dict(payload,id=self.ident),allow_nan=False)+'\n');self.process.stdin.flush()
        try:response=self.responses.get(timeout=timeout)
        except queue.Empty:self.close();raise ValueError('Official solver timeout; reconnect preview') from None
        if not response or response.get('id')!=self.ident:
            self.close();raise ValueError('Official solver session ended or reply order changed')
        if not response.get('ok'):raise ValueError(response.get('error','Official solver failed'))
        return response

    def step(self,points):
        response=self.request(dict(op='step',points=points.tolist() if hasattr(points,'tolist') else points),.2)
        self.info=response['info'];return response['q']

    def close(self):
        if self.process:
            try:self.process.stdin.close()
            except (OSError,ValueError):pass
            try:self.process.wait(timeout=.5)
            except subprocess.TimeoutExpired:self.process.terminate();self.process.wait(timeout=2)
