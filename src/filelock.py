"""
filelock.py — One search at a time, across processes
=====================================================
The desktop app and terminal mode share one data folder. Two searches at once
would double the requests to every site, so both take an exclusive OS lock on
<data>/run.lock before searching. The lock is non-blocking: whoever comes
second is refused. The OS releases it automatically if the holder dies, so a
crash never leaves the user locked out.
"""

import sys
from pathlib import Path

from paths import data_dir


class RunLock:
    def __init__(self, path=None):
        self.path = Path(path) if path else data_dir() / "run.lock"
        self._handle = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        """Take the lock without waiting. False if someone else holds it."""
        if self._handle is not None:
            return True
        handle = open(self.path, "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
