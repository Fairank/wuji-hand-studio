"""Build on the target OS. This does not cross-compile or start hardware."""
import hashlib,json,os,platform,shutil,subprocess,sys,tempfile,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent
os.chdir(ROOT)
config=json.loads((ROOT/'src/edition.json').read_text(encoding='utf-8'))
edition=config['name'];version=config['version']
name='HandWorkbench'
args=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--name',name,'--paths','src',
    '--collect-all','mujoco','--collect-all','glfw','--collect-all','paramiko','--collect-all','scipy','--hidden-import','console_server','--hidden-import','PIL.Image',
    '--hidden-import','PIL.JpegImagePlugin','--hidden-import','bridge_config',
    '--add-data','src/web'+os.pathsep+'web','--add-data','src/assets'+os.pathsep+'assets',
    '--add-data','src/official_data'+os.pathsep+'official_data',
    '--add-data','src/trial_sdk_poses.json'+os.pathsep+'.','--add-data','src/motion_parameters.py'+os.pathsep+'.',
    '--add-data','src/edition.json'+os.pathsep+'.','--add-data','LICENSE'+os.pathsep+'.',
    '--add-data','src/runtime_manifest.json'+os.pathsep+'.',
    '--add-data','src/runtime_update.py'+os.pathsep+'.',
    '--add-data','src/network_setup_windows.ps1'+os.pathsep+'.',
    '--add-data','THIRD_PARTY_NOTICES.md'+os.pathsep+'.']
if (ROOT/'src/private_models').exists():raise RuntimeError('Private models are optional local data and must not be bundled')
if sys.platform=='win32':args+=['--windowed','--icon','src/WujiStudio.ico','--collect-all','webview','--collect-all','pythonnet','--collect-all','clr_loader','--hidden-import','webview.platforms.winforms','--hidden-import','webview.platforms.edgechromium']
elif sys.platform=='darwin':
    from PIL import Image
    icon=ROOT/'build-icons/app.icns';icon.parent.mkdir(exist_ok=True)
    im=Image.open('src/web/app-icon.png').convert('RGBA').resize((1024,1024))
    im.save(icon,format='ICNS');args+=['--windowed','--icon',str(icon),'--osx-bundle-identifier','io.github.fairank.handworkbench',
        '--collect-all','webview','--hidden-import','webview.platforms.cocoa','--hidden-import','macos_desktop',
        '--hidden-import','AppKit','--hidden-import','WebKit','--hidden-import','Foundation',
        '--add-data','src/macos_provision.sh'+os.pathsep+'.']
    controller_source=ROOT/'build/controller-source';controller_source.mkdir(parents=True,exist_ok=True)
    for source in (ROOT/'src').iterdir():
        if source.is_file() and source.suffix in ('.py','.json') and not source.name.startswith('test_'):
            shutil.copy2(source,controller_source/source.name)
    args+=['--add-data',str(controller_source)+os.pathsep+'controller-source']
else:
    # PyOpenGL loads its backend by plugin name; static analysis cannot see it.
    args+=['--hidden-import','OpenGL.platform.egl','--hidden-import','OpenGL.platform.glx',
           '--hidden-import','OpenGL.platform.osmesa','--hidden-import','OpenGL.EGL',
           '--hidden-import','OpenGL.GL','--hidden-import','OpenGL.osmesa']
import importlib.metadata as metadata
licenses=ROOT/'build-notices';licenses.mkdir(exist_ok=True)
for distribution in metadata.distributions():
    for item in distribution.files or []:
        if any(word in str(item).lower() for word in ('license','copying','notice')):
            f=Path(distribution.locate_file(item))
            if f.is_file() and f.stat().st_size<2000000:
                dest=licenses/distribution.metadata['Name']/str(item).replace('../','').replace('..\\','')
                dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,dest)
args+=['--add-data',str(licenses)+os.pathsep+'third-party-licenses','src/desktop.py']
if '--package-only' not in sys.argv:subprocess.run(args,check=True)
binary=ROOT/'dist'/name/(name+('.exe' if sys.platform=='win32' else ''))
if sys.platform=='darwin':binary=ROOT/'dist'/(name+'.app')/'Contents/MacOS'/name
if sys.platform=='darwin':
    app=ROOT/'dist'/(name+'.app')
    mac_payload=ROOT/'runtime_payload/macos'
    if not (mac_payload/'manifest.json').is_file():raise RuntimeError('Mac distribution must include Linux; run scripts/prepare_macos_payload.py first')
    shutil.copytree(mac_payload,app/'Contents/Resources/mac-runtime',dirs_exist_ok=True,symlinks=True)
    shutil.copytree(ROOT/'docs',app/'Contents/Resources/docs',dirs_exist_ok=True)
    for document in ('README.md','LICENSE','THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT/document,app/'Contents/Resources'/document)
    import plistlib
    plist=app/'Contents/Info.plist'
    info=plistlib.loads(plist.read_bytes());info.update(CFBundleDisplayName='Hand Workbench',CFBundleShortVersionString=version,
        NSLocalNetworkUsageDescription='Discover and communicate with your Wuji hand on the local network.',
        LSMinimumSystemVersion='13.5')
    plist.write_bytes(plistlib.dumps(info))
    subprocess.run(['codesign','--force','--sign','-',str(app)],check=True)
subprocess.run([str(binary),'--self-check'],check=True)
if '--binary-only' in sys.argv:raise SystemExit(0)
if sys.platform=='darwin':
    archive=ROOT/'dist'/f'{name}-{version}-macos-arm64.zip'
    subprocess.run(['ditto','-c','-k','--sequesterRsrc','--keepParent',str(app),str(archive)],check=True)
    with archive.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    archive.with_name(archive.name+'.sha256').write_text(digest+'  '+archive.name+'\n')
    print(archive)
    raise SystemExit(0)
# Include documentation/controller sources alongside each executable.
platform_name={'win32':'windows','darwin':'macos'}.get(sys.platform,'ubuntu')
arch='arm64' if platform.machine().lower() in ('arm64','aarch64') else 'x64'
staging=Path(tempfile.mkdtemp(prefix='package-',dir=ROOT/'build'))
release=staging/f'{name}-{version}-{platform_name}-{arch}'
release.mkdir(exist_ok=True)
item=ROOT/'dist'/(name+'.app' if sys.platform=='darwin' else name)
shutil.copytree(item,release/item.name,dirs_exist_ok=True,symlinks=True)
for f in ('README.md','LICENSE','THIRD_PARTY_NOTICES.md'):shutil.copy2(ROOT/f,release/f)
if (ROOT/'docs').is_dir():shutil.copytree(ROOT/'docs',release/'docs')
shutil.copytree(ROOT/'scripts',release/'scripts',ignore=shutil.ignore_patterns('__pycache__'))
shutil.copytree(ROOT/'controller',release/'controller',dirs_exist_ok=True)
shutil.copytree(ROOT/'src',release/'controller/source',ignore=shutil.ignore_patterns('__pycache__','private_models','test_*','desktop.py','web'),dirs_exist_ok=True)
if sys.platform=='win32' and (ROOT/'runtime_payload').is_dir():
    manifest=json.loads((ROOT/'src/runtime_manifest.json').read_text(encoding='utf-8'));folder=release/item.name/'runtime';folder.mkdir(exist_ok=True)
    shutil.copy2(ROOT/'runtime_payload'/manifest['file'],folder/manifest['file'])
if sys.platform=='darwin':
    archive=ROOT/'dist'/(release.name+'.zip')
    subprocess.run(['ditto','-c','-k','--sequesterRsrc','--keepParent',str(release),str(archive)],check=True)
elif sys.platform=='win32':archive=Path(shutil.make_archive(str(ROOT/'dist'/release.name),'zip',release.parent,release.name))
else:archive=Path(shutil.make_archive(str(ROOT/'dist'/release.name),'gztar',release.parent,release.name))
archive.with_name(archive.name+'.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
if staging.resolve().parent!=(ROOT/'build').resolve() or not staging.name.startswith('package-'):raise RuntimeError('Unexpected staging directory')
shutil.rmtree(staging)
print(archive)
