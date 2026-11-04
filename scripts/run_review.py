"""Reproducible source checks, isolated software tests, and retained evidence.

No application, device, installer, SDK session, or release is started by this
runner. Existing tests use their own synthetic boundaries. This is a regression
runner, not a sandbox for executing untrusted code on a personal computer.
"""
import argparse
import ast
from datetime import datetime, timezone
import faulthandler
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import unittest

ROOT=Path(__file__).resolve().parents[1]


def isolated_environment(base,original=None):
    env=dict(os.environ if original is None else original)
    roots={key:base/name for key,name in {
        'WUJI_STUDIO_DATA':'app','WUJI_OFFICIAL_CLI_DATA':'official-cli',
        'WUJI_STUDIO_COMPONENT_DATA':'official-studio','LOCALAPPDATA':'localappdata',
        'APPDATA':'appdata','TEMP':'temp','TMP':'temp',
        'WUJI_CONTROL_QA_ROOT':'control-qa','WUJI_RUNTIME_QA_ROOT':'runtime-qa',
        'WUJI_RELEASE_QA_ROOT':'release-qa',
    }.items()}
    for key,value in roots.items():
        value.mkdir(parents=True,exist_ok=True);env[key]=str(value)
    env.update(WUJI_CLI_AUTO_CHECK='0',WUJI_APP_AUTO_CHECK='0',
               WUJI_NO_UPDATE_CHECK='1',WUJI_TELEMETRY_DISABLE='1',
               PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1',
               PYTHONIOENCODING='utf-8',PYTHONUTF8='1')
    for key in ('WUJI_FLEET_PARENT','WUJI_FLEET_ID','WUJI_STUDIO_PORT'):
        env.pop(key,None)
    return env,roots


def fresh_record_directory(value=None):
    if value is None:return Path(tempfile.mkdtemp(prefix='workbench-review-')).resolve()
    folder=Path(value).resolve();folder.mkdir(parents=True,exist_ok=False);return folder


def source_hashes(root):
    files={p for folder in ('src','scripts','.github') for p in (root/folder).rglob('*')
           if p.is_file() and p.suffix in ('.py','.js','.mjs','.cjs','.yml','.yaml','.md','.html','.css','.svg','.json')}
    files.update(p for p in (root/'build.py',root/'requirements.txt',root/'requirements-build.txt',
        root/'AGENTS.md',root/'docs/CODE_REVIEW.md',root/'docs/QA_WORKFLOW.md',root/'docs/ci/code-review.yml') if p.is_file())
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}


def check_python_syntax(root):
    files=sorted({* (root/'src').rglob('*.py'),* (root/'scripts').rglob('*.py')})
    if (root/'build.py').is_file():files.append(root/'build.py')
    for path in files:ast.parse(path.read_text(encoding='utf-8-sig'),filename=str(path))
    return len(files)


def audit_guard(events,sdk_roots=()):
    protected=tuple(Path(p).absolute() for p in sdk_roots)
    def guard(event,args):
        if event=='open' and args and isinstance(args[0],(str,bytes,os.PathLike)):
            path=Path(os.fsdecode(args[0])).absolute()
            if any(path.is_relative_to(root) for root in protected):
                events.append({'event':event,'reason':'default SDK user data'})
                raise RuntimeError('Default SDK user data is unavailable in software review; use an isolated fixture')
            return
        if event=='socket.getaddrinfo':host=args[0]
        elif event in ('socket.connect','socket.sendto'):
            address=args[-1]
            if not isinstance(address,tuple):return
            host=address[0]
        else:return
        if host in (None,'','localhost','0.0.0.0','::'):return
        try:
            if ipaddress.ip_address(host).is_loopback:return
        except ValueError:pass
        events.append({'event':event,'host':str(host)[:150]})
        raise RuntimeError('Unexpected non-loopback network access blocked by software review')
    return guard


def console_text(value):
    # A redirected Windows console can still use GBK/ASCII. Keep the original
    # UTF-8 log intact while escaping characters this display cannot encode.
    encoding=getattr(sys.stdout,'encoding',None) or 'utf-8'
    print(str(value).encode(encoding,errors='backslashreplace').decode(encoding),flush=True)


def verify_passes(first,second):
    """Require two complete successful reports for the same frozen source."""
    folders=[Path(first).resolve(),Path(second).resolve()]
    if folders[0]==folders[1]:raise ValueError('Acceptance passes require different evidence directories')
    reports=[json.loads((folder/'summary.json').read_text(encoding='utf8')) for folder in folders]
    for report in reports:
        if (report.get('successful') is not True or report.get('mode')!='all'
                or report.get('changed_sources')!=[] or not isinstance(report.get('sources_before'),dict)
                or not report.get('sources_before')
                or report.get('sources_before')!=report.get('sources_after')):
            raise ValueError('Both complete acceptance passes must succeed without source changes')
    sources=reports[0]['sources_after']
    if sources!=reports[1]['sources_before']:
        raise ValueError('Acceptance source changed between passes; restart both passes')
    result=dict(successful=True,passes=[str(folder) for folder in folders],source_files=len(sources),
        source_fingerprint=hashlib.sha256(json.dumps(sources,sort_keys=True).encode('utf8')).hexdigest())
    console_text(json.dumps(result,ensure_ascii=False))
    return result


def run_command(command,log,root,env,timeout=900):
    started=time.monotonic()
    with log.open('x',encoding='utf8') as stream:
        try:
            result=subprocess.run(command,cwd=root,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout,check=False)
            code=result.returncode
        except subprocess.TimeoutExpired:
            stream.write('\nReview command timed out.\n');code=124
        except OSError as error:
            stream.write(str(error)+'\n');code=127
    if code:console_text(log.read_text(encoding='utf8',errors='replace')[-7000:])
    return {'returncode':code,'elapsed_seconds':round(time.monotonic()-started,3),'log':log.name}


def python_worker(out):
    env,roots=isolated_environment(out/'isolation')
    os.environ.update(env)
    for key in ('WUJI_FLEET_PARENT','WUJI_FLEET_ID','WUJI_STUDIO_PORT'):os.environ.pop(key,None)
    tempfile.tempdir=str(roots['TEMP']);sys.dont_write_bytecode=True
    sys.path[:0]=[str(ROOT/'src'),str(ROOT)];os.chdir(ROOT)
    # Keep HOME/USERPROFILE/CODEX_HOME unchanged. Accidental default SDK-file
    # reads fail rather than touching a real registry or redirecting its meaning.
    homes={Path.home()}
    for key in ('HOME','USERPROFILE'):
        if os.environ.get(key) and Path(os.environ[key]).is_absolute():homes.add(Path(os.environ[key]))
    blocked=[];sys.addaudithook(audit_guard(blocked,[home/'.wuji' for home in homes]))
    class NoPhysicalSDK:
        def find_spec(self,fullname,path=None,target=None):
            if fullname.split('.')[0]=='wuji_sdk':
                blocked.append({'event':'import','module':fullname})
                raise ModuleNotFoundError('Real wuji_sdk imports are disabled in source review; supply a test double')
    sys.meta_path.insert(0,NoPhysicalSDK())
    import cli_coordination
    import runtime_paths
    import studio_component
    import managed_runtime
    resolved={'app':runtime_paths.DATA,'cli':cli_coordination.root_folder(),
              'component':studio_component.root_folder(),'runtime':managed_runtime.ROOT}
    for path in resolved.values():
        if not Path(path).resolve().is_relative_to((out/'isolation').resolve()):
            raise RuntimeError('App state escaped review isolation: '+str(path))
    suite=unittest.defaultTestLoader.discover(str(ROOT/'src'),pattern='test_*.py')
    faulthandler.dump_traceback_later(180,repeat=True)
    try:result=unittest.TextTestRunner(verbosity=2).run(suite)
    finally:faulthandler.cancel_dump_traceback_later()
    report={'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
            'skipped':len(result.skipped),'successful':result.wasSuccessful() and not blocked,
            'blocked_operations':blocked,'isolated_environment':{k:str(v) for k,v in roots.items()},
            'resolved_roots':{k:str(v) for k,v in resolved.items()},
            'failed_tests':[{'test':str(t),'traceback':trace} for t,trace in result.failures],
            'errored_tests':[{'test':str(t),'traceback':trace} for t,trace in result.errors]}
    (out/'python.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return 0 if report['successful'] else 1


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record-dir',type=Path,help='New evidence directory; existing directories are refused')
    parser.add_argument('--only',choices=('all','python','node','syntax'),default='all')
    parser.add_argument('--node',default='node',help='Installed Node executable; never downloads dependencies')
    parser.add_argument('--python-worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if args.python_worker:
        if args.record_dir is None or not args.record_dir.is_dir():parser.error('internal worker requires an existing evidence directory')
        return python_worker(args.record_dir.resolve())
    out=fresh_record_directory(args.record_dir);env,roots=isolated_environment(out/'isolation')
    before=source_hashes(ROOT);started=datetime.now(timezone.utc).isoformat();checks={}
    try:
        if args.only in ('all','syntax'):
            checks['python_ast']={'files':check_python_syntax(ROOT),'returncode':0}
            checks['runner_tests']=run_command([sys.executable,'-m','unittest','discover','-s','scripts','-p','run_review_tests.py','-v'],out/'runner-tests.log',ROOT,env)
        if args.only in ('all','node','syntax'):
            node=shutil.which(args.node)
            if not node:raise RuntimeError('Node is not installed; install Node 24 before running review checks')
            scripts=sorted([p for p in (ROOT/'src/web').rglob('*') if p.suffix in ('.js','.mjs','.cjs') and 'vendor' not in p.relative_to(ROOT/'src/web').parts]+list((ROOT/'src').glob('test_*.cjs'))+list((ROOT/'scripts').glob('*.cjs')))
            failed=[]
            with (out/'javascript-syntax.log').open('x',encoding='utf8') as log:
                for path in scripts:
                    status=subprocess.run([node,'--check',str(path)],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=30,check=False)
                    if status.returncode:failed.append(path.relative_to(ROOT).as_posix())
            checks['javascript_syntax']={'files':len(scripts),'failed_files':failed,'returncode':int(bool(failed))}
            if args.only in ('all','node'):
                files=sorted((ROOT/'src').glob('test_*.cjs'))
                if not files:raise RuntimeError('No Node regression files were discovered')
                checks['node_tests']=run_command([node,'--test',*[str(p) for p in files]],out/'node-tests.log',ROOT,env)
                checks['node_tests']['files']=len(files)
                checks['connection_flow']=run_command([node,str(ROOT/'scripts/test_connection_flow.cjs')],out/'connection-flow.log',ROOT,env)
        if args.only in ('all','python'):
            checks['python_tests']=run_command([sys.executable,str(Path(__file__).resolve()),'--python-worker','--record-dir',str(out)],out/'python-tests.log',ROOT,env)
    except Exception as error:
        checks['runner_error']={'returncode':1,'error':str(error),'traceback':traceback.format_exc()}
        console_text(traceback.format_exc())
    after=source_hashes(ROOT);changed=sorted(k for k in set(before)|set(after) if before.get(k)!=after.get(k))
    report={'successful':bool(checks) and all(v['returncode']==0 for v in checks.values()) and not changed,
            'scope':'Source regression checks; not native UI, installer, device or hardware acceptance',
            'mode':args.only,'started_utc':started,'completed_utc':datetime.now(timezone.utc).isoformat(),
            'checks':checks,'changed_sources':changed,'sources_before':before,'sources_after':after,
            'isolated_environment':{k:str(v) for k,v in roots.items()},'evidence_directory':str(out)}
    (out/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    console_text(json.dumps({k:report[k] for k in ('successful','mode','checks','changed_sources','evidence_directory')},ensure_ascii=False,indent=2))
    return 0 if report['successful'] else 1


if __name__=='__main__':raise SystemExit(main())
