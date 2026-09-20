"""Build on the target OS. This does not cross-compile or start hardware."""
import hashlib,json,os,platform,shutil,subprocess,sys,tempfile,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent
os.chdir(ROOT)
edition=json.loads((ROOT/'src/edition.json').read_text())['name']
name='WujiStudioResearch' if edition=='research' else 'WujiStudio'
args=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--name',name,'--paths','src',
    '--collect-all','mujoco','--collect-all','glfw','--collect-all','paramiko','--collect-all','scipy','--hidden-import','console_server','--hidden-import','PIL.Image',
    '--hidden-import','PIL.JpegImagePlugin','--hidden-import','bridge_config',
    '--add-data','src/web'+os.pathsep+'web','--add-data','src/assets'+os.pathsep+'assets',
    '--add-data','src/official_data'+os.pathsep+'official_data',
    '--add-data','src/trial_sdk_poses.json'+os.pathsep+'.','--add-data','src/motion_parameters.py'+os.pathsep+'.',
    '--add-data','src/edition.json'+os.pathsep+'.','--add-data','LICENSE'+os.pathsep+'.',
    '--add-data','THIRD_PARTY_NOTICES.md'+os.pathsep+'.']
if (ROOT/'src/private_models').is_dir():
    if edition!='research':raise RuntimeError('Private weights in basic build')
    args+=['--add-data','src/private_models'+os.pathsep+'private_models']
if sys.platform=='win32':args+=['--icon','src/WujiStudio.ico']
elif sys.platform=='darwin':
    from PIL import Image
    icon=ROOT/'build-icons/app.icns';icon.parent.mkdir(exist_ok=True)
    im=Image.open('src/web/app-icon.png').convert('RGBA').resize((1024,1024))
    im.save(icon,format='ICNS');args+=['--windowed','--icon',str(icon),'--osx-bundle-identifier','io.github.fairank.wujistudio']
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
subprocess.run([str(binary),'--self-check'],check=True)
# Include documentation/controller sources alongside each executable.
platform_name={'win32':'windows','darwin':'macos'}.get(sys.platform,'ubuntu')
arch='arm64' if platform.machine().lower() in ('arm64','aarch64') else 'x64'
staging=Path(tempfile.mkdtemp(prefix='package-',dir=ROOT/'build'))
release=staging/f'{name}-0.1.0-{platform_name}-{arch}'
release.mkdir(exist_ok=True)
item=ROOT/'dist'/(name+'.app' if sys.platform=='darwin' else name)
shutil.copytree(item,release/item.name,dirs_exist_ok=True,symlinks=True)
for f in ('README.md','LICENSE','THIRD_PARTY_NOTICES.md'):shutil.copy2(ROOT/f,release/f)
shutil.copytree(ROOT/'controller',release/'controller',dirs_exist_ok=True)
shutil.copytree(ROOT/'src',release/'controller/source',ignore=shutil.ignore_patterns('__pycache__','private_models','test_*','desktop.py','web','assets'),dirs_exist_ok=True)
if edition=='research' and (ROOT/'research').exists():shutil.copytree(ROOT/'research',release/'research',dirs_exist_ok=True)
if sys.platform=='darwin':
    archive=ROOT/'dist'/(release.name+'.zip')
    subprocess.run(['ditto','-c','-k','--sequesterRsrc','--keepParent',str(release),str(archive)],check=True)
elif sys.platform=='win32':archive=Path(shutil.make_archive(str(ROOT/'dist'/release.name),'zip',release.parent,release.name))
else:archive=Path(shutil.make_archive(str(ROOT/'dist'/release.name),'gztar',release.parent,release.name))
archive.with_name(archive.name+'.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
if staging.resolve().parent!=(ROOT/'build').resolve() or not staging.name.startswith('package-'):raise RuntimeError('Unexpected staging directory')
shutil.rmtree(staging)
print(archive)
