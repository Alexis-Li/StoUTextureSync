"""Cross-process write exclusion for one source-image directory."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

LOCK_NAME = ".stou_texture_sync.lock"


class LockBusy(RuntimeError):
    pass


def _lock_byte(handle, acquire: bool) -> None:
    handle.seek(0)
    if sys.platform == "win32":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK if acquire else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB if acquire else fcntl.LOCK_UN)


class TargetLock:
    def __init__(self, directory: str):
        self.path = os.path.join(directory, LOCK_NAME)
        self._handle = None

    def acquire(self, owner: str) -> None:
        if self._handle is not None:
            raise RuntimeError("target lock is already acquired")
        if Path(self.path).is_symlink():
            raise OSError("target lock file cannot be a symbolic link")
        handle = open(self.path, "a+b")
        try:
            try:
                _lock_byte(handle, acquire=True)
            except OSError as exc:
                raise LockBusy("另一个写入方持有目标目录锁") from exc
            # The OS lock owns byte zero. The JSON is diagnostic only.
            handle.seek(1)
            handle.truncate()
            handle.write(json.dumps({"owner": owner, "pid": os.getpid(),
                                     "since": time.time()}).encode())
            handle.flush()
        except BaseException:
            handle.close()
            raise
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            _lock_byte(self._handle, acquire=False)
        finally:
            try:
                self._handle.close()
            finally:
                self._handle = None

    def __enter__(self):
        self.acquire("context")
        return self

    def __exit__(self, *exc):
        self.release()
