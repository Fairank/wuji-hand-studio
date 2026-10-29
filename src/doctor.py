"""Explicit official Wuji CLI diagnostics. No update, calibration or motor API."""
import copy,ipaddress,json,os,queue,re,shlex,shutil,subprocess,tempfile,threading,time
from pathlib import Path,PurePosixPath
from runtime_paths import DATA
from bridge_config import load_config,ssh_client

MAX_OUTPUT=2*1024*1024

def sdk_command(config, command):
    """The CLI must inspect the Python environment used by this controller."""
    from bridge_config import controller_executable
    python=controller_executable(config,config.get('python',''))
    directory=str(PurePosixPath(python).parent) if python.startswith('/') else ''
    if directory and directory!='/' and config.get('mode') in ('wsl','macvm','ssh','local'):
        return ['/usr/bin/env','PATH='+directory+':/usr/local/bin:/usr/bin:/bin',*command]
    return list(command)
def doctor_args(config,operation,serial='',address=''):
    if operation not in ('version','diagnose'):raise ValueError('Unsupported diagnostic operation')
    if not isinstance(serial,str) or (serial and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',serial)):raise ValueError('Invalid device serial number')
    if not isinstance(address,str) or len(address)>80:raise ValueError('Invalid device address')
    from official_cli import cli_path
    args=[cli_path(config)]
    if operation=='version':return args+['--version']
    args+=['doctor','--json']
    if address:
        host,sep,port=address.partition(':')
        try:ip=ipaddress.IPv4Address(host)
        except ValueError:raise ValueError('Expected device IPv4:port') from None
        if (ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_link_local
                or not sep or not port.isascii() or not port.isdecimal() or not 1<=int(port)<=65535):
            raise ValueError('Expected device IPv4:port')
        if serial:raise ValueError('Choose either serial number or address')
        args+=['--address',address]
    if serial:args+=['--sn',serial]
    return args

def local_run(args,timeout=45):
    if args[0]=='wuji':args[0]=shutil.which('wuji') or str(Path.home()/'.local/bin/wuji')
    p=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        env={**os.environ,'WUJI_NO_UPDATE_CHECK':'1','WUJI_TELEMETRY_DISABLE':'1'},creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    chunks=queue.Queue();output=[bytearray(),bytearray()];closed=0;deadline=time.monotonic()+timeout
    def read(stream,index):
        try:
            while chunk:=stream.read1(8192):chunks.put((index,chunk))
        finally:stream.close();chunks.put((index,None))
    readers=[];started=[]
    try:
        for i,stream in enumerate((p.stdout,p.stderr)):
            reader=threading.Thread(target=read,args=(stream,i),daemon=True)
            readers.append(reader);reader.start();started.append(reader)
        while closed<2:
            if time.monotonic()>deadline:raise TimeoutError('Official CLI timed out after '+str(timeout)+' seconds')
            try:i,data=chunks.get(timeout=.1)
            except queue.Empty:continue
            if data is None:closed+=1
            else:output[i].extend(data)
            if sum(map(len,output))>MAX_OUTPUT:raise ValueError('Diagnostic output exceeds 2 MiB; result incomplete')
        code=p.wait(timeout=2)
        return code,*[b.decode('utf-8',errors='replace') for b in output]
    finally:
        try:
            if p.poll() is None:p.kill();p.wait(timeout=3)
        finally:
            for reader in readers:
                if reader in started or reader.ident is not None:reader.join(timeout=3)
            for index,stream in enumerate((p.stdout,p.stderr)):
                if index>=len(readers) or readers[index] not in started and readers[index].ident is None:
                    stream.close()

def remote_run(config,args,timeout=45):
    c=ssh_client(config)
    try:
        i,o,e=c.exec_command('env WUJI_NO_UPDATE_CHECK=1 WUJI_TELEMETRY_DISABLE=1 '+shlex.join(args),timeout=timeout);i.channel.shutdown_write()
        channel=o.channel;buffers=[bytearray(),bytearray()];deadline=time.monotonic()+timeout
        while True:
            if time.monotonic()>deadline:channel.close();raise TimeoutError('Official CLI timed out after '+str(timeout)+' seconds')
            if channel.recv_ready():buffers[0].extend(channel.recv(8192))
            if channel.recv_stderr_ready():buffers[1].extend(channel.recv_stderr(8192))
            if sum(map(len,buffers))>MAX_OUTPUT:channel.close();raise ValueError('Diagnostic output exceeds 2 MiB; result incomplete')
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():break
            time.sleep(.01)
        return channel.recv_exit_status(),*[b.decode('utf-8',errors='replace') for b in buffers]
    finally:c.close()

class Doctor:
    def __init__(self):
        self.lock=threading.RLock();self.state=dict(running=False,status='idle',report=None,error=None)
        path=DATA/'doctor-latest.json'
        try:
            if path.is_file() and path.stat().st_size<=MAX_OUTPUT*3:
                report=json.loads(path.read_text(encoding='utf-8'))
                if (isinstance(report,dict) and type(report.get('exit_code')) is int
                        and isinstance(report.get('stdout'),str) and isinstance(report.get('stderr'),str)):
                    self.state.update(status='completed',report=report,restored=True)
        except (OSError,ValueError,TypeError):pass
    def snapshot(self):
        with self.lock:return copy.deepcopy(self.state)
    def _launch(self,target,args,previous):
        try:threading.Thread(target=target,args=args,daemon=True).start()
        except Exception as error:
            with self.lock:
                self.state=previous
                self.state.update(running=False,status='error',error='诊断任务未启动，请重试 / Diagnostic task did not start; retry: '+str(error)[:300])
            raise
    def start(self,operation,serial='',address=''):
        config=load_config();args=doctor_args(config,operation,serial,address)
        with self.lock:
            if self.state['running']:raise ValueError('Diagnostic already running')
            previous=copy.deepcopy(self.state)
            self.state=dict(running=True,status='running',report=None,error=None,operation=operation,started=time.time(),target=config['mode'])
        self._launch(self.work,(config,args,operation),previous)
        return self.snapshot()
    def start_support(self,days=1,description=''):
        if type(days) is not int or not 1<=days<=30:raise ValueError('Days must be 1–30')
        if not isinstance(description,str) or len(description)>2000:raise ValueError('Description too long')
        config=load_config()
        with self.lock:
            if self.state['running']:raise ValueError('Official CLI already running')
            previous_state=copy.deepcopy(self.state)
            previous=copy.deepcopy(self.state.get('report'))
            self.state.update(running=True,status='support_export',operation='support',error=None,support=None)
        def work():
            try:
                from support_bundle import export_support
                result=export_support(config,days,description,previous,local_run,remote_run)
                with self.lock:self.state.update(running=False,status='completed',support=result)
            except Exception as error:
                with self.lock:self.state.update(running=False,status='error',error=str(error)[:500])
        self._launch(work,(),previous_state)
        return self.snapshot()
    def work(self,config,args,operation):
        try:
            actual=sdk_command(config,args)
            if config['mode'] in ('macvm','wsl','local'):
                from calibration_cli import controller_args
                # Resolve a custom local CLI before sdk_command wraps it in env.
                command=controller_args(config,args) if config['mode']=='local' else args
                actual=sdk_command(config,command)
                code,out,err=local_run(controller_args(config,actual))
            else:code,out,err=remote_run(config,actual)
            report=dict(operation=operation,command=args,execution_command=actual,controller=config['mode'],sdk_python=config.get('python',''),exit_code=code,stdout=out,stderr=err,finished=time.time(),motor_commands_sent=False,cli_result_unmodified=True)
            if operation=='diagnose':
                from doctor_report import flatten_report
                try:
                    document=json.loads(out);report['rows']=flatten_report(document);report['official']=document
                    from doctor_export import counts
                    report['counts']=counts(report['rows'])
                except (ValueError,TypeError) as error:report['parse_error']=str(error)
            save_error=None
            pending=None
            try:
                DATA.mkdir(parents=True,exist_ok=True)
                path=DATA/'doctor-latest.json'
                with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=DATA,prefix=path.name+'.',suffix='.pending',delete=False) as stream:
                    pending=Path(stream.name);stream.write(json.dumps(report,ensure_ascii=False,indent=2));stream.flush();os.fsync(stream.fileno())
                os.replace(pending,path)
            except OSError:
                save_error='诊断已完成，但报告无法保存；本次原始输出仍可查看 / Diagnostic completed; report could not be saved, but its original output is still available'
            finally:
                if pending is not None:
                    try:pending.unlink(missing_ok=True)
                    except OSError:pass
            with self.lock:self.state.update(running=False,status='completed',report=report,error=save_error)
        except Exception as error:
            with self.lock:self.state.update(running=False,status='error',error=str(error)[:500])
