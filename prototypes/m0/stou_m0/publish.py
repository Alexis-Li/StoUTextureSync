"""Publish validated staged images to fixed source-image paths, one complete file at a time."""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field

TEMP_SUFFIX = ".stou-tmp"  # never matches the monitored *.png wildcard


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class FileResult:
    name: str
    status: str            # published | skipped_same | failed
    sha: str = ""
    error: str = ""
    attempts: int = 0
    published_at: float = 0.0


@dataclass
class BatchResult:
    batch: int
    files: list[FileResult] = field(default_factory=list)

    @property
    def state(self) -> str:
        failed = [f for f in self.files if f.status == "failed"]
        done = [f for f in self.files if f.status != "failed"]
        if not failed:
            return "file_published"
        return "partial_publication" if done and any(f.status == "published" for f in done) \
            else "failed"


def publish_file(staged: str, target: str, retries: int = 5, delay: float = 0.2,
                 replace=os.replace) -> FileResult:
    """Copy into a temporary file beside the target, flush it, then atomically replace."""
    name = os.path.basename(target)
    digest = sha256(staged)
    temp = os.path.join(os.path.dirname(target), f".{name}.{os.getpid()}{TEMP_SUFFIX}")
    result = FileResult(name, "failed", digest)
    try:
        with open(staged, "rb") as src, open(temp, "wb") as dst:
            for chunk in iter(lambda: src.read(1 << 20), b""):
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
    except OSError as exc:
        result.error = f"write temp: {exc}"
        _remove_quietly(temp)
        return result
    for attempt in range(1, retries + 1):
        result.attempts = attempt
        try:
            replace(temp, target)
            result.status = "published"
            result.published_at = time.time()
            return result
        except PermissionError as exc:  # target held open by a reader on Windows
            result.error = f"replace: {exc}"
            time.sleep(delay)
        except OSError as exc:
            result.error = f"replace: {exc}"
            break
    _remove_quietly(temp)
    return result


def publish_batch(batch: int, staged: dict[str, str], target_dir: str, dedup: bool = True,
                  is_current=lambda: True, **kwargs) -> BatchResult:
    """Publish staged files ({final name: staged path}) into target_dir.

    Skips a file only when the target exists with identical content (dedup by
    the target's actual bytes). Stops before each file if the batch became stale.
    """
    result = BatchResult(batch)
    for name in sorted(staged):
        target = os.path.join(target_dir, name)
        if not is_current():
            result.files.append(FileResult(name, "failed", error="batch no longer current"))
            continue
        if dedup and os.path.exists(target):
            try:
                if sha256(target) == sha256(staged[name]):
                    result.files.append(FileResult(name, "skipped_same", sha256(target)))
                    continue
            except OSError:
                pass
        result.files.append(publish_file(staged[name], target, **kwargs))
    return result


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
