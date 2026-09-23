"""Prevent two workbench workers from owning the same discovered device."""
import hashlib
import os
from pathlib import Path
import tempfile


class DeviceOwnership:
    def __init__(self, serial):
        import fcntl
        root = Path(tempfile.gettempdir()) / ('hand-workbench-locks-' + str(os.getuid()))
        root.mkdir(mode=0o700, exist_ok=True)
        self.file = (root / (hashlib.sha256(str(serial).encode()).hexdigest()+'.lock')).open('a')
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise ValueError('这只设备已在另一个工作区连接 / Device is already owned by another workspace') from None

    def close(self):
        self.file.close()
