"""Local-only official log export and evidence-labelled fault brief.

Only CLI logs export is executed. No recipes, clear, enable, firmware or motion.
"""
import hashlib
import json
from pathlib import Path
import shutil
import time
import uuid
import zipfile

from runtime_paths import DATA
from doctor_export import report_text


def brief(report,description,export):
    lines=['WUJI 故障简报 / Fault brief','',
           '【结论 / Conclusion】',
           '本简报汇总用户现象、官方原始诊断与导出状态，不推断尚未采集到的硬件故障。',
           'Evidence summary only; hardware faults are not inferred from missing data.',
           '官方支持包：'+('已生成 / Created' if export.get('official_archive_available') else '未生成 / Not generated'),
           '导出状态：'+('命令正常结束 / Command completed' if export['exit_code']==0 else '存在采集失败，见官方原文 / Collection failures; see official output'),'',
           '【用户描述 / Operator observation】',description or '未填写 / Not supplied','',
           '【证据：已实际执行 / Actual evidence】',
           '仅运行只读官方诊断及日志导出；没有执行清错、重新使能或运动复测。',
           'Official diagnostic and log export only; no fault clearing, enabling or motion retest.',
           '官方支持包退出码 / Official support export exit: '+str(export['exit_code']),
           '支持包 SHA-256 / Support ZIP SHA-256: '+export.get('sha256','未生成 / Not generated'),'',
           '【结果边界 / Evidence limits】',
           '用户描述未经本程序独立验证。未发现设备时，不能确认关节状态、错误码、温度或具体故障部件。',
           'No discovered device means joint state, fault code, temperature and component failure are unconfirmed.',
           '退出码 1 可能表示部分采集失败；支持包仍保留，具体失败以官方原文为准。',
           '', '【建议 / Suggested next step】',
           '以附录中官方 Doctor 的修复建议为准。向支持人员提供此简报、官方 ZIP 和现象发生时间。',
           'Follow the official Doctor recommendations below; provide this brief, official ZIP and incident time to support.',
           '', '【官方日志导出输出 / Official log export output】',export['stdout'],export['stderr']]
    if export.get('collection_error'):
        lines+=['','【本机收取问题 / Local collection issue】',export['collection_error']]
    if report:lines+=['','【附：最近诊断，时间以报告为准 / Latest saved diagnostic】',report_text(report)]
    else:lines+=['','尚无已保存的 Doctor 报告 / No saved Doctor report.']
    return '\n'.join(lines)+'\n'


def export_support(config,days,description,report,run_local,run_remote):
    if type(days) is not int or not 1<=days<=30:raise ValueError('Days must be 1–30')
    if not isinstance(description,str) or len(description)>2000:raise ValueError('Description must be at most 2000 characters')
    from doctor import sdk_command
    mode=config['mode'];token=uuid.uuid4().hex;collection_error=None
    folder=DATA/'support'/token;folder.mkdir(parents=True)
    output=folder/'official-wuji-support.zip'
    remote='/tmp/hand-workbench-support-'+token+'.zip'
    def collect(transfer):
        nonlocal collection_error
        incoming=folder/'official-wuji-support.zip.partial'
        try:
            transfer(incoming)
            if not incoming.is_file() or incoming.stat().st_size>1024**3 or not zipfile.is_zipfile(incoming):
                raise RuntimeError('Controller copy did not produce a valid ZIP support bundle within 1 GiB')
            with zipfile.ZipFile(incoming) as package:
                damaged=package.testzip()
                if damaged is not None:
                    raise RuntimeError('Official support ZIP entry failed CRC verification: '+damaged[:300])
            incoming.replace(output)
        except Exception as error:
            collection_error=str(error)[:900] or 'Official support ZIP could not be collected from the controller'
        finally:
            # A failed/partial transfer never becomes the official attachment.
            try:incoming.unlink(missing_ok=True)
            except OSError:pass
    destination=str(folder/'official-wuji-support.pending.zip') if mode=='local' else remote
    from official_cli import cli_path
    command=sdk_command(config,[cli_path(config),'--json','logs','export','--days',str(days),
                                '--source','sdk,studio,stderr,cli','--output',destination])
    if mode=='wsl':
        from managed_runtime import args,check_ready,NAME
        check_ready();code,stdout,stderr=run_local(args(command),timeout=180)
        source=Path('\\\\wsl.localhost')/NAME/'tmp'/Path(remote).name
        def transfer(incoming):
            if not source.is_file():raise FileNotFoundError('Official support ZIP is missing from the controller')
            if source.stat().st_size>1024**3:raise ValueError('Official support bundle exceeds 1 GiB; retained in controller')
            shutil.copyfile(source,incoming)
        collect(transfer)
    elif mode=='ssh':
        from bridge_config import ssh_client
        code,stdout,stderr=run_remote(config,command,timeout=180)
        def transfer(incoming):
            client=ssh_client(config)
            try:
                files=client.open_sftp()
                try:
                    if files.stat(remote).st_size>1024**3:raise ValueError('Official support bundle exceeds 1 GiB; retained in controller')
                    files.get(remote,str(incoming))
                finally:files.close()
            finally:client.close()
        collect(transfer)
    elif mode=='macvm':
        from macos_runtime import ensure_running,run,NAME
        from calibration_cli import controller_args
        ensure_running()
        code,stdout,stderr=run_local(controller_args(config,command),timeout=180)
        # Lima copies only the generated, fixed-name artifact from its own VM.
        # A missing official ZIP must not discard the already collected CLI
        # output and the local fault brief, including partial official failures.
        collect(lambda incoming:run('copy',NAME+':'+remote,str(incoming),timeout=90))
    elif mode=='local':
        code,stdout,stderr=run_local(command,timeout=180)
        collect(lambda incoming:Path(destination).replace(incoming))
    else:raise ValueError('Unsupported controller')
    result=dict(id=token,controller=mode,days=days,finished=time.time(),exit_code=code,
                stdout=stdout,stderr=stderr,motor_commands_sent=False,
                automatic_upload=False,official_archive_available=output.is_file())
    if collection_error:result['collection_error']=collection_error
    result['collection_complete']=code==0 and result['official_archive_available'] and not collection_error
    if output.is_file():
        if not zipfile.is_zipfile(output):raise ValueError('CLI did not produce a valid ZIP support bundle')
        with output.open('rb') as stream:result['sha256']=hashlib.file_digest(stream,'sha256').hexdigest()
        result['bytes']=output.stat().st_size
    (folder/'fault-brief.txt').write_text(brief(report,description,result),encoding='utf-8-sig')
    (folder/'export-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    if report:(folder/'doctor-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with zipfile.ZipFile(folder/'workbench-support.zip','w',compression=zipfile.ZIP_DEFLATED) as package:
        for name in ('official-wuji-support.zip','fault-brief.txt','export-result.json','doctor-report.json'):
            path=folder/name
            if path.is_file():package.write(path,name)
    return result


def artifact(identifier,kind):
    import re
    if not isinstance(identifier,str) or not re.fullmatch('[a-f0-9]{32}',identifier):raise ValueError('Invalid support identifier')
    names={'official':'official-wuji-support.zip','brief':'fault-brief.txt','all':'workbench-support.zip'}
    if kind not in names:raise ValueError('Unknown support artifact')
    root=(DATA/'support').resolve();path=root/identifier/names[kind]
    if path.is_symlink() or not path.resolve().is_relative_to(root):raise ValueError('Support path is redirected')
    if not path.is_file():raise FileNotFoundError('Official export did not produce this artifact')
    return path
