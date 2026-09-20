"""Optional, data-only simulation model pack. Never connects to motors."""
import hashlib
import json
from pathlib import Path
import tempfile
import shutil
import zipfile
import numpy as np
from runtime_paths import DATA

CONTRACT='wuji-hand2-left-sim-feedback-86-v1'
FILES={'manifest.json','recognizer.npz','parity.npz'}
SHAPES={'mean':(86,), 'std':(86,), 'rnn.weight_ih_l0':(105,86),
        'rnn.weight_hh_l0':(105,35),'rnn.bias_ih_l0':(105,), 'rnn.bias_hh_l0':(105,),
        **{n+'.weight':(5,35) for n in ('decoder','recent','force')},
        **{n+'.bias':(5,) for n in ('decoder','recent','force')}}


def load_weights(folder):
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format')!='hand-workbench-model-v1' or manifest.get('contract')!=CONTRACT:
        raise ValueError('Unsupported model format or feedback contract')
    for name in ('recognizer.npz','parity.npz'):
        path=folder/name
        if not path.is_file() or path.stat().st_size>2_000_000:
            raise ValueError('Invalid model file size')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=manifest.get('sha256',{}).get(name):
            raise ValueError('Model checksum mismatch')
        with zipfile.ZipFile(path) as pack:
            if sum(x.file_size for x in pack.infolist())>4_000_000:raise ValueError('Model array size exceeded')
    with np.load(folder/'recognizer.npz',allow_pickle=False) as pack:
        if set(pack.files)!=set(SHAPES):raise ValueError('Unexpected model arrays')
        weights={name:pack[name].copy() for name in SHAPES}
    for name,shape in SHAPES.items():
        v=weights[name]
        if v.shape!=shape or v.dtype.kind!='f' or not np.isfinite(v).all():
            raise ValueError('Invalid model shape or values')
    if np.any(weights['std']<=0):raise ValueError('Invalid normalization')
    return weights,manifest


def sigmoid(x):return np.exp(-np.logaddexp(0.,-x))


class Recognizer:
    def __init__(self,folder=None):
        self.folder=Path(folder) if folder else DATA/'models/left-touch-v1'
        self.w,self.manifest=load_weights(self.folder);self.h=np.zeros(35,dtype=np.float32)

    def consume(self,frames,*,contract):
        if contract!=CONTRACT:raise ValueError('Simulation feedback contract required; device current A is not simulated torque')
        x=np.asarray(frames,dtype=np.float32)
        if x.ndim!=2 or x.shape[1]!=86 or not 1<=len(x)<=100000 or not np.isfinite(x).all():
            raise ValueError('Expected chronological finite [frames,86] feedback')
        w=self.w;x=(x-w['mean'])/w['std'];rows=[]
        for frame in x:
            ir,iz,inn=np.split(frame@w['rnn.weight_ih_l0'].T+w['rnn.bias_ih_l0'],3)
            hr,hz,hn=np.split(self.h@w['rnn.weight_hh_l0'].T+w['rnn.bias_hh_l0'],3)
            reset=sigmoid(ir+hr);update=sigmoid(iz+hz)
            self.h=(1-update)*np.tanh(inn+reset*hn)+update*self.h;rows.append(self.h.copy())
        hidden=np.stack(rows);out={}
        for name in ('decoder','recent','force'):
            logits=hidden@w[name+'.weight'].T+w[name+'.bias']
            out[name]=np.logaddexp(0.,logits) if name=='force' else sigmoid(logits)
        return out


def validate_parity(folder):
    model=Recognizer(folder)
    with np.load(folder/'parity.npz',allow_pickle=False) as pack:
        result=model.consume(pack['input'][0],contract=CONTRACT)
        error=max(float(np.max(np.abs(result[k]-pack[k][0]))) for k in result)
        error=max(error,float(np.max(np.abs(model.h-pack['hidden'][0,0]))))
    if not np.isfinite(error) or error>1e-4:raise ValueError('Model parity check failed')
    return error


def status(data_dir=DATA):
    folder=Path(data_dir)/'models/left-touch-v1'
    if not folder.exists():return dict(installed=False,hardware_enabled=False)
    try:
        _,manifest=load_weights(folder)
        return dict(installed=True,profile='hand2_left',contract=CONTRACT,mode='offline_simulation_only',
                    hardware_enabled=False,sha256=manifest['sha256']['recognizer.npz'])
    except (OSError,ValueError,KeyError,zipfile.BadZipFile):return dict(installed=False,invalid=True,hardware_enabled=False)


def install(source,data_dir=DATA):
    root=Path(data_dir).resolve()/'models'
    if root.resolve()!=root:raise ValueError('Model data must remain inside application data')
    root.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='import-',dir=root));target=root/'left-touch-v1'
    try:
        if target.resolve()!=target:raise ValueError('Invalid installed model directory')
        with zipfile.ZipFile(source) as archive:
            entries=archive.infolist()
            if len(entries)!=3 or {x.filename for x in entries}!=FILES:
                raise ValueError('Model packs contain only manifest.json and two NPZ files')
            if any(x.file_size>2_000_000 or x.flag_bits&1 for x in entries):raise ValueError('Invalid model pack size or encryption')
            for name in FILES:(staging/name).write_bytes(archive.read(name))
        error=validate_parity(staging)
        if target.exists():
            if any((target/n).read_bytes()!=(staging/n).read_bytes() for n in FILES):
                raise ValueError('A different model is already installed; preserve it before replacing')
        else:staging.rename(target)
        return dict(ok=True,model=status(data_dir),parity_max_abs_error=error,motor_commands_sent=False)
    finally:
        if staging.exists():
            if staging.resolve().parent!=root.resolve() or not staging.name.startswith('import-'):raise RuntimeError('Unexpected staging directory')
            shutil.rmtree(staging)


def run_file(source,destination):
    model=Recognizer()
    with np.load(source,allow_pickle=False) as pack:
        out=model.consume(pack['frames'],contract=str(pack['contract'].item()))
    with Path(destination).open('xb') as stream:
        np.savez_compressed(stream,current_contact=out['decoder'],recent_touch=out['recent'],simulated_force_prediction=out['force'])
    return dict(ok=True,motor_commands_sent=False)
