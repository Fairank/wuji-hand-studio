"""Cross-platform local desktop/browser entry. Never auto-connects hardware."""
import argparse,json,os,shutil,socket,subprocess,sys,time,webbrowser
from pathlib import Path
from urllib.request import urlopen
from runtime_paths import DATA,RESOURCE,PORT,EDITION,initialize

URL=f'http://127.0.0.1:{PORT}/'

def ready(port=None):
    try:
        with urlopen(f'http://127.0.0.1:{port or PORT}/api/installation',timeout=1) as r:s=json.load(r)
        return s.get('edition',{}).get('name')==EDITION['name'] and s.get('version')==EDITION['version']
    except Exception:return False


def choose_port():
    """Keep explicit ports; a double-click coexists with an older workbench."""
    global PORT,URL
    if os.environ.get('WUJI_STUDIO_PORT'):return PORT
    saved=DATA/'desktop-port.json';candidates=list(range(8781,8801))
    try:
        last=json.loads(saved.read_text())['port']
        if type(last) is int and 8781<=last<=8800:candidates.remove(last);candidates.insert(0,last)
    except (OSError,ValueError,KeyError,TypeError):pass
    for candidate in candidates:
        if ready(candidate):break
        try:
            with socket.socket() as probe:
                if sys.platform=='win32':probe.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
                probe.bind(('127.0.0.1',candidate))
            break
        except OSError:continue
    else:raise RuntimeError('No free local port; set WUJI_STUDIO_PORT explicitly')
    PORT=candidate;URL=f'http://127.0.0.1:{PORT}/'
    import runtime_paths
    runtime_paths.PORT=PORT;os.environ['WUJI_STUDIO_PORT']=str(PORT)
    saved.write_text(json.dumps(dict(port=PORT)),encoding='utf-8')
    return PORT

def open_app(viewer=False):
    url=URL+('viewer' if viewer else '#library')
    candidates=[]
    if sys.platform=='win32':
        candidates=[Path(os.environ.get('PROGRAMFILES(X86)','C:/Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe',Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'Google/Chrome/Application/chrome.exe']
    elif sys.platform=='darwin':
        candidates=[Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),Path('/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge')]
    else:
        candidates=[Path(p) for n in ('chromium','chromium-browser','google-chrome','microsoft-edge') if (p:=shutil.which(n))]
    for browser in candidates:
        if browser.is_file():
            subprocess.Popen([str(browser),'--app='+url,'--new-window','--user-data-dir='+str(DATA/'browser'),'--window-size='+('760,640' if viewer else '1280,820'),'--no-first-run'],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            return True
    return bool(webbrowser.open(url))

def self_check():
    from pose_view import load_left_model
    from gesture_library import catalog
    from bridge_config import validate,DEFAULT
    import numpy as np
    from device_profiles import PROFILES,load_native_model
    for profile_id in PROFILES:
        m=load_native_model(profile_id);assert m.nu==20
    validate(DEFAULT)
    assert len(catalog()['actions'])>=40
    data=dict(ok=True,edition=EDITION['name'],joints=m.nu,profiles=list(PROFILES),actions=len(catalog()['actions']),hardware_connected=False)
    if '--render-check' in sys.argv:
        import mujoco as mj
        d=mj.MjData(m);mj.mj_forward(m,d)
        with mj.Renderer(m,height=160,width=160) as r:
            r.update_scene(d);pic=r.render();assert pic.shape==(160,160,3) and np.isfinite(pic).all()
        data['render']=True
    print(json.dumps(data));return 0

def main():
    if '--self-check' in sys.argv:return self_check()
    initialize()
    if '--serve' in sys.argv:
        from console_server import main as serve
        serve();return
    choose_port()
    if not ready():
        cmd=[sys.executable,'--serve'] if getattr(sys,'frozen',False) else [sys.executable,'-u',str(RESOURCE/'desktop.py'),'--serve']
        with (DATA/'console.log').open('ab') as log:
            child=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=log,stderr=log,cwd=DATA,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),start_new_session=sys.platform!='win32')
        for _ in range(150):
            if ready():break
            if child.poll() is not None:raise RuntimeError('Startup failed or port occupied; see '+str(DATA/'console.log'))
            time.sleep(.1)
        else:raise RuntimeError('Startup timeout; see '+str(DATA/'console.log'))
    open_app(viewer='--viewer' in sys.argv)

if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()
