"""Build the Windows per-user installer after build.py; never starts hardware."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil

ROOT=Path(__file__).resolve().parent


def main():
    if sys.platform!='win32':raise RuntimeError('Build the installer on Windows')
    parser=argparse.ArgumentParser()
    parser.add_argument('--iscc',type=Path,required=True)
    parser.add_argument('--webview-bootstrap',type=Path,required=True)
    parser.add_argument('--zh-language',type=Path,required=True)
    args=parser.parse_args()
    config=json.loads((ROOT/'src/edition.json').read_text(encoding='utf-8'))
    runtime=json.loads((ROOT/'src/runtime_manifest.json').read_text(encoding='utf-8'))
    if hashlib.sha256((ROOT/'runtime_payload'/runtime['file']).read_bytes()).hexdigest()!=runtime['sha256']:raise ValueError('Runtime image hash mismatch')
    name='HandWorkbench'
    guid='44DF38B2-39A0-491F-B154-E587231C83CE'
    env=dict(os.environ,WUJI_BUILD_BOOTSTRAP=str(args.webview_bootstrap.resolve()))
    # Do not pass a PowerShell 7 module path into Windows PowerShell 5.1.
    for key in list(env):
        if key.upper()=='PSMODULEPATH':env.pop(key)
    check="$s=Get-AuthenticodeSignature -LiteralPath $env:WUJI_BUILD_BOOTSTRAP; if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw 'Microsoft signature validation failed' }"
    subprocess.run([shutil.which('pwsh') or 'powershell.exe','-NoProfile','-NonInteractive','-Command',check],env=env,check=True)
    output=f"{name}-{config['version']}-windows-x64-setup"
    defines=dict(ProjectRoot=ROOT,PayloadDir=ROOT/'dist'/name,AppExe=name+'.exe',AppName='Hand Workbench',
                 AppGuid=guid,AppVersion=config['version'],OutputDir=ROOT/'dist',OutputName=output,
                 IconFile=ROOT/'src/WujiStudio.ico',LicenseFile=ROOT/'LICENSE',RuntimeFile=runtime['file'],
                 WebViewBootstrap=args.webview_bootstrap.resolve(),ZhIsl=args.zh_language.resolve(),MutexName='Local\\'+name)
    subprocess.run([str(args.iscc.resolve()),*[f'/D{k}={v}' for k,v in defines.items()],str(ROOT/'installer/studio.iss')],check=True)
    path=ROOT/'dist'/(output+'.exe')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name+'.sha256').write_text(digest+'  '+path.name+'\n',encoding='ascii')
    print(json.dumps(dict(installer=str(path),sha256=digest,edition=config['name'])))


if __name__=='__main__':main()
