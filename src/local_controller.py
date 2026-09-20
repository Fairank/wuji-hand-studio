"""Linux-only controller transport; launches the SDK process without SSH."""
import os,subprocess,sys
from pathlib import Path

class LocalFiles:
    def __init__(self,root):self.root=Path(root).resolve()
    def path(self,value):
        p=Path(value).resolve()
        if not p.is_relative_to(self.root):raise ValueError('Controller file escapes configured directory')
        return p
    def open(self,path,mode):return self.path(path).open(mode)
    def posix_rename(self,source,target):self.path(source).replace(self.path(target))
    def listdir(self,path):return [p.name for p in self.path(path).iterdir()]
    def remove(self,path):self.path(path).unlink()
    def __enter__(self):return self
    def __exit__(self,*_):pass

class Channel:
    def __init__(self,process):self.process=process
    @property
    def closed(self):return self.process.poll() is not None

class ReadStream:
    def __init__(self,stream,process):self.stream=stream;self.channel=Channel(process)
    def __iter__(self):return iter(self.stream)
    def read(self,*args):return self.stream.read(*args)

class LocalController:
    def __init__(self,config,profile_id):
        if not sys.platform.startswith('linux'):raise ValueError('Local SDK control requires Linux; select remote Linux on this platform')
        self.agent_directory=str(Path(config['agent_directory']).resolve());self.process=None
        agent=Path(self.agent_directory)/'console_agent.py'
        if not agent.is_file():raise ValueError('Controller scripts not found in configured directory')
        self.agent_command=[config['python'],'-u',str(agent)]
        self.environment={**os.environ,'WUJI_HAND_PROFILE':profile_id}
    def open_sftp(self):return LocalFiles(self.agent_directory)
    def exec_command(self,command,timeout=None):
        if command!=self.agent_command or self.process is not None:raise ValueError('Only the configured controller process may be started')
        self.process=subprocess.Popen(command,cwd=self.agent_directory,env=self.environment,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',bufsize=1)
        return self.process.stdin,ReadStream(self.process.stdout,self.process),ReadStream(self.process.stderr,self.process)
    def close(self):
        p=self.process
        if p is None:return
        try:p.stdin.close()
        except (OSError,ValueError):pass
        try:p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.terminate()
            try:p.wait(timeout=3)
            except subprocess.TimeoutExpired:p.kill();p.wait(timeout=3)
        for stream in (p.stdout,p.stderr):stream.close()
        self.process=None
