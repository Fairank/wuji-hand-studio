"""Controller-local coordination for SDK profile changes and calibration.

Glove sessions share a profile-read lease. Calibration/profile mutation takes
an exclusive lease; per-device ownership is independent. No actuator calls.
"""
import hashlib
import os
from pathlib import Path
import tempfile


class ProfileLease:
    def __init__(self, exclusive=False, selection=False):
        import fcntl
        root = Path(tempfile.gettempdir()) / ('hand-workbench-locks-' + str(os.getuid()))
        root.mkdir(mode=0o700, exist_ok=True)
        name = 'sdk-user-selection' if selection else 'sdk-profile-data'
        self.file = (root / (hashlib.sha256(name.encode()).hexdigest()+'.lock')).open('a')
        mode = fcntl.LOCK_EX if exclusive or selection else fcntl.LOCK_SH
        try:
            fcntl.flock(self.file, mode | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise ValueError('另一个工作区正在使用标定用户，请结束相关会话后重试 / SDK profile is in use by another workspace') from None

    def close(self):
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
