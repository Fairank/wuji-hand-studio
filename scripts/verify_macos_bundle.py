"""Verify a real Mac artifact without claiming VM, device or visual acceptance."""
import hashlib
import json
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'dist/HandWorkbench.app'


def main():
    if platform.system() != 'Darwin':
        raise RuntimeError('Validate the bundle on macOS')
    binary = APP / 'Contents/MacOS/HandWorkbench'
    arch = subprocess.check_output(['lipo', '-archs', str(binary)], text=True).strip()
    if 'arm64' not in arch.split():
        raise ValueError('Missing native Apple Silicon executable')
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(APP)], check=True)
    root = APP / 'Contents/Resources/mac-runtime'
    m = json.loads((root / 'manifest.json').read_text())
    for name, expected in m['files'].items():
        p = (root / name).resolve()
        if not p.is_relative_to(root.resolve()):
            raise ValueError('Payload escapes app resources')
        with p.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                raise ValueError('Payload checksum mismatch: ' + name)
    result = dict(architecture=arch, bundle_verified=True, bundled_linux=True,
                  ad_hoc_signed=True, notarized=False, native_ui_opened=False,
                  vm_boot_verified=False, real_hand_verified=False, liquid_glass_visually_verified=False)
    (ROOT / 'dist/macos-validation.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
