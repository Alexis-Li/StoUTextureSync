"""Publish staged images with atomic replacement and target deduplication."""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .naming import validate_basename
from .target_lock import LOCK_NAME, LockBusy

TEMP_SUFFIX = ".stou-tmp"
STAGE_MARKER = ".stou_stage_dir"
STALE_ERROR = "batch no longer current"
CHUNK_SIZE = 1 << 20


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class FileResult:
    name: str
    status: str
    sha: str = ""
    error: str = ""
    attempts: int = 0
    published_at: float = 0.0
    size_bytes: int = 0


@dataclass
class BatchResult:
    batch: int
    files: list[FileResult] = field(default_factory=list)
    target_locked: bool = False
    error: str = ""

    @property
    def state(self) -> str:
        if self.target_locked:
            return "target_locked"
        if not self.error and all(file.status != "failed" for file in self.files):
            return "file_published"
        if any(file.status == "published" for file in self.files):
            return "partial_publication"
        return "failed"


def prepare_stage_dir(stage_root: str) -> str:
    """Reset the owned current staging directory, or refuse an unmarked one."""
    current = Path(stage_root).resolve() / "current"
    marker = current / STAGE_MARKER
    if current.exists() or current.is_symlink():
        if current.resolve() != current or marker.is_symlink() or not marker.is_file():
            raise RuntimeError("Refusing to clear an unowned staging directory")
        shutil.rmtree(current)
    current.mkdir(parents=True)
    marker.touch(exist_ok=False)
    return str(current)


def _target_path(target, target_dir) -> Path:
    target = Path(target)
    validate_basename(target.name)
    if target.name.casefold() == LOCK_NAME or target.name.casefold().endswith(TEMP_SUFFIX):
        raise ValueError("reserved target file name")
    resolved = target.resolve()
    if target.is_symlink() or (target_dir is not None
                              and resolved.parent != Path(target_dir).resolve()):
        raise ValueError("target must be a file directly inside target_dir")
    return resolved


def publish_file(staged, target, *, target_dir=None, retries=5, delay=0.2,
                 replace=os.replace, staged_sha=None, is_current=None, wait_fn=None) -> FileResult:
    """Write a complete sibling temp file, then replace the target while current."""
    result = FileResult(Path(target).name, "failed")
    current = is_current if is_current is not None else lambda: True
    wait = wait_fn if wait_fn is not None else time.sleep
    if not current():
        result.error = STALE_ERROR
        return result
    temp = None
    try:
        target = _target_path(target, target_dir)
        temp_path = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}{TEMP_SUFFIX}")
        digest = hashlib.sha256() if staged_sha is None else None
        result.sha = staged_sha or ""
        with open(staged, "rb") as src, temp_path.open("xb") as dst:
            temp = temp_path
            for chunk in iter(lambda: src.read(CHUNK_SIZE), b""):
                dst.write(chunk)
                result.size_bytes += len(chunk)
                if digest is not None:
                    digest.update(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        for attempt in range(1, retries + 1):
            if not current():
                result.error = STALE_ERROR
                break
            result.attempts = attempt
            try:
                replace(str(temp), str(target))
            except PermissionError as exc:
                result.error = f"replace: {exc}"
                if attempt < retries:
                    wait(delay)
            except OSError as exc:
                result.error = f"replace: {exc}"
                break
            else:
                temp = None
                result.status = "published"
                result.error = ""
                result.published_at = time.time()
                break
        if digest is not None:
            result.sha = digest.hexdigest()
    except (OSError, ValueError, RuntimeError) as exc:
        result.error = str(exc)
    finally:
        if temp is not None:
            try:
                temp.unlink(missing_ok=True)
            except OSError as exc:
                result.error = f"{result.error}; temp cleanup: {exc}"
    return result


def publish_batch(batch, staged, target_dir, *, dedup=True, is_current=lambda: True,
                  lock=None, lock_owner=None, **kwargs) -> BatchResult:
    """Publish final basenames under one writer lock."""
    result = BatchResult(batch)
    if lock is not None:
        try:
            lock.acquire(lock_owner or f"batch-{batch}")
        except LockBusy as exc:
            return BatchResult(batch, target_locked=True, error=str(exc))
    try:
        for name in sorted(staged):
            if not is_current():
                result.files.append(FileResult(name, "failed", error=STALE_ERROR))
                continue
            try:
                validate_basename(name)
                target = _target_path(Path(target_dir) / name, target_dir)
                source = staged[name]
                size = os.path.getsize(source)
                staged_sha = sha256(source)
                try:
                    same = dedup and target.is_file() and sha256(str(target)) == staged_sha
                except OSError:
                    same = False
                if same and is_current():
                    file = FileResult(name, "skipped_same", sha=staged_sha, size_bytes=size)
                else:
                    file = publish_file(source, target, target_dir=target_dir, staged_sha=staged_sha,
                                        is_current=is_current, **kwargs)
            except (OSError, ValueError, RuntimeError) as exc:
                file = FileResult(name, "failed", error=str(exc))
            result.files.append(file)
    finally:
        if lock is not None:
            try:
                lock.release()
            except OSError as exc:
                result.error = f"target lock release: {exc}"
    return result
