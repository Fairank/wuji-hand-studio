"""Separate immutable application resources from per-user configuration."""
import json, os, shutil
from pathlib import Path
from user_dirs import user_data_dir

RESOURCE=Path(__file__).resolve().parent
EDITION=json.loads((RESOURCE/'edition.json').read_text(encoding='utf-8'))
PORT=int(os.environ.get('WUJI_STUDIO_PORT','8781'))
if not 1024 <= PORT <= 65535:raise ValueError('Invalid local port')
DATA=Path(os.environ['WUJI_STUDIO_DATA']).expanduser().resolve() if os.environ.get('WUJI_STUDIO_DATA') else user_data_dir('WujiStudio')

def initialize():
    DATA.mkdir(parents=True,exist_ok=True)
    target=DATA/'motion_parameters.py'
    if not target.exists():
        # Exclusive create: never overwrite a user's tuning during upgrades.
        try:
            with target.open('x',encoding='utf-8') as f:f.write((RESOURCE/'motion_parameters.py').read_text(encoding='utf-8'))
        except FileExistsError:pass
