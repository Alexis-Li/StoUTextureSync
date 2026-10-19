"""Cross-process write exclusion for one source-image directory.

Uses an OS byte-range lock on a lock file. Windows releases the lock when the
holding process exits, so a stale lock file left by a crash is reusable while
a live holder still blocks others; no PID or timeout guessing is involved.
"""

from __future__ import annotations

import json
import os
import sys
import time

LOCK_NAME = ".stou_texture_sync.lock"


class LockBusy(RuntimeError):
    pass


class TargetLock:
    def __init__(self, directory: str):
        self.path = os.path.join(directory, LOCK_NAME)
        self._handle = None

    def acquire(self, owner: str) -> None:
        handle = open(self.path, "a+b")
        try:
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise LockBusy(f"another writer holds {self.path}") from exc
        # Record the owner after the lock byte for diagnostics only.
        handle.seek(1)
        handle.truncate()
        handle.write(json.dumps({"owner": owner, "pid": os.getpid(),
                                 "since": time.time()}).encode())
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self):
        self.acquire("context")
        return self

    def __exit__(self, *exc):
        self.release()
