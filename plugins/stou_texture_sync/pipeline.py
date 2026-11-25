"""Host-independent export validation, publication, scheduling and diagnostics."""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import SyncConfig
from .naming import NamingError, plan_names, udim_to_uv
from .publish import STAGE_MARKER, BatchResult, publish_batch
from .scheduler import WHOLE, Batch, Scheduler
from .target_lock import TargetLock

STATUS_TEXT = {
    "idle": "空闲", "unconfigured": "未配置", "waiting_idle": "等待空闲",
    "pending": "待同步", "exporting": "导出中", "publishing": "发布中",
    "file_published": "文件已发布", "partial_publication": "部分发布",
    "failed": "失败", "paused": "已暂停", "target_locked": "目标目录被占用",
    "stale_before_publish": "批次已取消", "stale_during_publish": "批次已取消",
    "stopped": "已停止",
}

# UE package names such as /Game/Textures/T_Body are virtual paths.
LOCAL_PATH_PATTERN = re.compile(
    r"(?<![\w])(?:[A-Z]:[\\/]|\\\\|(?<![:/])/(?:Users|home|private|Volumes|tmp|var|mnt|"
    r"media|opt|root|etc|srv|run|dev|proc|sys|bin|lib|lib64|usr|workspace|workspaces)(?:/|(?=$)))"
    r"[^\r\n]*",
    re.IGNORECASE,
)


def format_status(state: str, reason: str | None = None) -> str:
    text = STATUS_TEXT.get(state, state)
    return f"{text}：{reason}" if reason else text


def _basename(path) -> str:
    return re.split(r"[/\\]", os.fspath(path))[-1]


def _sanitize(value):
    if isinstance(value, str):
        return LOCAL_PATH_PATTERN.sub("<local path>", value)
    if isinstance(value, dict):
        return {_sanitize(key): _sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return value


def build_export_configs(config: SyncConfig, batch: Batch, preset_url: str, stage_dir: str,
                         available_tiles: dict[str, list[tuple[int, int]]] | None = None) -> list[dict]:
    scope = dict.fromkeys(config.texture_sets, WHOLE) if batch.full else batch.scope
    entries = []
    parameters = [{"parameters": {"paddingAlgorithm": "infinite", "sizeLog2": config.size_log2}}]
    for name, tiles in sorted(scope.items()):
        binding = config.texture_sets.get(name)
        if binding is None or not binding.enabled or tiles == set():
            continue
        entry = {"rootPath": name}
        if tiles is not WHOLE:
            entry["filter"] = {"uvTiles": [list(tile) for tile in sorted(tiles)]}
        entries.append(entry)
        if binding.size_log2 is not None:
            parameters.append({"filter": {"dataPaths": [name]},
                               "parameters": {"sizeLog2": binding.size_log2}})
        selected = tiles
        if available_tiles is not None and name in available_tiles:
            available = set(available_tiles[name])
            selected = available if tiles is WHOLE else tiles & available
        for udim, size in sorted(binding.tile_size_log2.items()):
            tile = udim_to_uv(udim)
            if selected is None or tile in selected:
                parameters.append({"filter": {"dataPaths": [name], "uvTiles": [list(tile)]},
                                   "parameters": {"sizeLog2": size}})
    if not entries:
        return []
    return [{"exportShaderParams": False, "exportPath": stage_dir,
             "defaultExportPreset": preset_url, "exportList": entries,
             "exportParameters": parameters}]


@dataclass
class ValidationOutcome:
    outcome: str
    staged: dict[str, str] = field(default_factory=dict)
    not_written: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    error: str = ""


def _stage_path(stage: Path, path) -> Path:
    candidate = stage / path
    resolved = candidate.resolve()
    if (resolved == stage or not resolved.is_relative_to(stage)
            or resolved.name == STAGE_MARKER or candidate.is_symlink()):
        raise ValueError(f"output outside the staging area: {_basename(path)}")
    return resolved


def validate_stage_paths(stage_dir: str, textures: dict) -> None:
    """Check Painter's planned paths before allowing it to write."""
    stage = Path(stage_dir).resolve()
    paths = set()
    for outputs in textures.values():
        for path in outputs:
            resolved = _stage_path(stage, path)
            key = str(resolved).casefold()
            if key in paths:
                raise ValueError(f"duplicate planned output: {_basename(path)}")
            paths.add(key)


def validate_export_and_plan(config, templates, stage_dir, export_status, export_message,
                             planned_textures, actual_textures) -> ValidationOutcome:
    if export_status != "Success":
        return ValidationOutcome("export_not_success", error=export_message or str(export_status))
    stage = Path(stage_dir).resolve()
    planned_names = {_basename(path) for paths in planned_textures.values() for path in paths}
    actual_names = {_basename(path) for paths in actual_textures.values() for path in paths}
    not_written = sorted(planned_names - actual_names)
    unexpected = actual_names - planned_names
    try:
        validate_stage_paths(stage_dir, planned_textures)
        grouped = {}
        for key, paths in actual_textures.items():
            name = key[0] if isinstance(key, tuple) else key
            expected = {_stage_path(stage, path) for path in planned_textures.get(key, ())}
            for path in paths:
                resolved = _stage_path(stage, path)
                if resolved not in expected or not resolved.is_file():
                    unexpected.add(_basename(path))
                grouped.setdefault(name, []).append(str(resolved))
    except (OSError, ValueError, RuntimeError) as exc:
        return ValidationOutcome("unexpected_outputs", not_written=not_written,
                                 unexpected=sorted(unexpected), error=str(exc))
    if unexpected:
        names = sorted(unexpected)
        return ValidationOutcome("unexpected_outputs", not_written=not_written,
                                 unexpected=names, error=f"未确认的导出文件：{', '.join(names)}")
    try:
        namings = {name: spec.to_naming() for name, spec in config.texture_sets.items() if spec.enabled}
        names = plan_names(grouped, templates, namings)
    except NamingError as exc:
        return ValidationOutcome("naming_error", not_written=not_written, error=str(exc))
    return ValidationOutcome("ready", {name: path for path, name in names.items()}, not_written)


@dataclass
class _PublishCompletion:
    batch: Batch
    result: BatchResult
    elapsed_s: float
    cancelled: bool


class PublishWorker:
    """Run at most one file publication on a background thread."""

    def __init__(self, config: SyncConfig, *, replace=None):
        self.config = config
        self._replace = replace
        self._results: queue.Queue[_PublishCompletion] = queue.Queue()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False

    def submit(self, batch: Batch, staged: dict[str, str]) -> None:
        if self._closed or self._thread is not None:
            raise RuntimeError("publication worker is busy or closed")
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(batch, staged, self._cancel),
                                        name="StoUTextureSync publish", daemon=True)
        try:
            self._thread.start()
        except BaseException:
            self._thread = None
            raise

    def _run(self, batch: Batch, staged: dict[str, str], cancel: threading.Event) -> None:
        start = time.perf_counter()
        try:
            result = publish_batch(
                batch.number, staged, self.config.target_dir, dedup=True,
                is_current=lambda: not cancel.is_set(), wait_fn=cancel.wait,
                lock=TargetLock(self.config.target_dir), retries=self.config.publish_retries,
                delay=self.config.publish_retry_delay_s,
                **({"replace": self._replace} if self._replace is not None else {}),
            )
        except Exception as exc:
            result = BatchResult(batch.number, error=str(exc))
        self._results.put(_PublishCompletion(batch, result, time.perf_counter() - start,
                                             cancel.is_set()))

    def cancel(self) -> None:
        self._cancel.set()

    def poll(self) -> _PublishCompletion | None:
        try:
            result = self._results.get_nowait()
        except queue.Empty:
            return None
        # The result is queued after all file operations and lock cleanup.
        self._thread = None
        return result

    def shutdown(self, timeout=5.0) -> bool:
        self._closed = True
        self.cancel()
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()


class SyncLogWriter:
    def __init__(self, path: str, sample_name: str = ""):
        self.path = Path(path)
        self.sample_name = sample_name

    def write(self, kind: str, **fields) -> None:
        record = _sanitize({"schema": "stou.sync.v1", "wall": time.time(),
                            "kind": kind, "sample": self.sample_name, **fields})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")


@dataclass
class _BatchMetrics:
    first_change_wall: float | None
    last_change_wall: float | None
    start_wall: float
    list_s: float | None = None
    export_s: float | None = None
    validate_s: float | None = None
    stall_ms: float | None = None
    not_written: list[str] = field(default_factory=list)
    publishing: bool = False


class SyncEngine:
    """Coordinate one project and one batch from the host's main thread."""

    def __init__(self, config: SyncConfig, scheduler: Scheduler | None = None,
                 worker: PublishWorker | None = None, log_writer: SyncLogWriter | None = None,
                 on_status_changed=None):
        self.config = config
        self.scheduler = scheduler if scheduler is not None else Scheduler(config.debounce_s, config.enabled)
        self.worker = worker if worker is not None else PublishWorker(config)
        self.log_writer = log_writer if log_writer is not None else SyncLogWriter(config.log_path, config.sample_name)
        self.on_status_changed = on_status_changed
        self.state = "idle"
        self.status_text = format_status(self.state)
        self._closed = False
        self._pending_first: float | None = None
        self._pending_last: float | None = None
        self._metrics: _BatchMetrics | None = None

    def _set_status(self, state: str, reason: str | None = None) -> None:
        status = format_status(state, _sanitize(reason))
        if (state, status) != (self.state, self.status_text):
            self.state, self.status_text = state, status
            if self.on_status_changed is not None:
                self.on_status_changed(state, status)

    def _show_pending(self) -> None:
        if self.scheduler.paused_reason is not None:
            self._set_status("paused", self.scheduler.paused_reason)
        elif self.scheduler.has_pending():
            self._set_status("waiting_idle" if self.scheduler.enabled else "pending")
        else:
            self._set_status("idle")

    def record_texture_change(self, texture_set, tile, now_mono, now_wall) -> bool:
        binding = self.config.texture_sets.get(texture_set)
        if self._closed or binding is None or not binding.enabled:
            return False
        accepted = self.scheduler.record(texture_set, tile, now_mono)
        if accepted:
            if self._pending_first is None or now_wall < self._pending_first:
                self._pending_first = now_wall
            if self._pending_last is None or now_wall > self._pending_last:
                self._pending_last = now_wall
            if self.scheduler.running_batch() is None and self.scheduler.paused_reason is None:
                self._show_pending()
        return accepted

    def on_project_changed(self, project_id: str | None) -> None:
        if not self._closed and self.scheduler.set_project(project_id):
            self.worker.cancel()
            self._pending_first = self._pending_last = None
            self._show_pending()

    def on_project_closed(self) -> None:
        self.on_project_changed(None)

    def set_realtime_enabled(self, enabled: bool) -> bool:
        batch = self.scheduler.running_batch()
        current = batch is not None and self.scheduler.is_current(batch)
        if self._closed or not self.scheduler.set_enabled(enabled):
            return False
        if not enabled:
            self.worker.cancel()
            if current and batch.scope:
                self._restore_change_times(self._metrics)
            self._show_pending()
        elif batch is None or not current:
            self._show_pending()
        return True

    def _request(self, request) -> tuple[bool, str | None]:
        if self._closed:
            return False, "同步已停止"
        accepted, reason = request()
        if self.scheduler.running_batch() is None:
            if accepted:
                self._set_status("pending")
            elif self.scheduler.paused_reason is not None:
                self._set_status("paused", reason)
            else:
                self._set_status("idle", reason)
        return accepted, reason

    def request_immediate_sync(self):
        return self._request(self.scheduler.request_immediate)

    def request_full_sync(self):
        return self._request(self.scheduler.request_full)

    def begin_next_batch(self, now_mono) -> Batch | None:
        if self._closed:
            return None
        batch = self.scheduler.next_batch(now_mono)
        if batch is not None:
            self._metrics = _BatchMetrics(self._pending_first, self._pending_last, time.time())
            self._pending_first = self._pending_last = None
            self._set_status("exporting")
        return batch

    def submit_export_result(self, batch, *, preset_templates, stage_dir, export_status,
                             export_message, planned_textures, actual_textures, list_s,
                             export_s, stall_ms, start_wall) -> ValidationOutcome | None:
        metrics = self._metrics
        if (self._closed or self.scheduler.running_batch() is not batch
                or metrics is None or metrics.publishing):
            return None
        metrics.start_wall = start_wall
        metrics.list_s, metrics.export_s, metrics.stall_ms = list_s, export_s, stall_ms
        if not self.scheduler.is_current(batch):
            self._complete(batch, "stale_before_publish")
            return None
        start = time.perf_counter()
        outcome = validate_export_and_plan(
            self.config, preset_templates, stage_dir, export_status, export_message,
            planned_textures, actual_textures)
        metrics.validate_s = time.perf_counter() - start
        metrics.not_written = outcome.not_written
        if outcome.outcome != "ready":
            self._complete(batch, outcome.outcome, error=outcome.error)
        elif outcome.staged:
            try:
                self.worker.submit(batch, outcome.staged)
            except (OSError, RuntimeError) as exc:
                self._complete(batch, "failed", error=str(exc))
            else:
                metrics.publishing = True
                self._set_status("publishing")
        else:
            self._complete(batch, "file_published")
        return outcome

    def poll_publish_result(self) -> BatchResult | None:
        completion = self.worker.poll()
        if completion is None:
            return None
        batch, result = completion.batch, completion.result
        state = result.state
        if completion.cancelled or not self.scheduler.is_current(batch):
            state = "stale_during_publish"
        self._complete(batch, state, result=result, publish_s=completion.elapsed_s, error=result.error)
        return result

    def _restore_change_times(self, metrics: _BatchMetrics | None) -> None:
        if metrics is not None:
            if metrics.first_change_wall is not None:
                self._pending_first = metrics.first_change_wall
            if self._pending_last is None:
                self._pending_last = metrics.last_change_wall

    def _complete(self, batch, outcome, *, result=None, publish_s=None, error="") -> None:
        current = self.scheduler.is_current(batch)
        metrics, self._metrics = self._metrics, None
        files = result.files if result is not None else []
        published_wall = max((file.published_at for file in files if file.status == "published"),
                             default=None)
        stale = outcome in ("stale_before_publish", "stale_during_publish")
        failed = outcome != "file_published" and not stale
        reason = {
            "partial_publication": "请执行完整同步以恢复",
            "target_locked": "目标目录有其他写入方",
            "export_not_success": "Painter 导出未成功",
            "unexpected_outputs": "导出文件与计划不符",
            "naming_error": "文件命名或 Tile 映射无效",
        }.get(outcome, "文件发布失败")
        require_full = outcome == "partial_publication"
        if failed and current and not batch.full and not require_full and metrics is not None:
            self._restore_change_times(metrics)
        self.scheduler.finish(batch, failed=failed, pause_reason=reason,
                              require_full_recovery=require_full)
        if current and not stale:
            if failed:
                state = outcome if outcome in STATUS_TEXT else "failed"
                self._set_status(state, reason if state != "target_locked" else None)
            else:
                note = None if published_wall else "没有需要发布的文件"
                self._set_status("file_published" if published_wall else "idle", note)
                if self.scheduler.has_pending():
                    self._show_pending()
        if metrics is not None:
            self._write_batch(batch, metrics, outcome, publish_s, files, published_wall, error,
                              self.scheduler.paused_reason if current else None)

    def _write_batch(self, batch, metrics, outcome, publish_s, files, published_wall, error, pause_reason):
        scope = (dict.fromkeys(name for name, spec in self.config.texture_sets.items() if spec.enabled)
                 if batch.full else batch.scope)
        last = metrics.last_change_wall
        try:
            self.log_writer.write(
                "batch", batch=batch.number, generation=batch.generation, trigger=batch.trigger,
                full=batch.full, scope={k: "whole" if v is WHOLE else sorted(v) for k, v in scope.items()},
                outcome=outcome, first_change_wall=metrics.first_change_wall, last_change_wall=last,
                start_wall=metrics.start_wall, published_wall=published_wall,
                wait_after_last_change_s=metrics.start_wall - last if last is not None else None,
                list_s=metrics.list_s, export_s=metrics.export_s, validate_s=metrics.validate_s,
                publish_s=publish_s, sp_side_s=(published_wall or time.time()) - last if last is not None else None,
                stall_ms=metrics.stall_ms, not_written=[_basename(name) for name in metrics.not_written],
                files=[{"name": _basename(file.name), "status": file.status, "sha": file.sha,
                        "attempts": file.attempts, "size_bytes": file.size_bytes, "error": file.error}
                       for file in files],
                pause_reason=pause_reason, error=error,
            )
        except (OSError, ValueError) as exc:
            self._set_status(self.state, f"无法写入诊断日志 ({type(exc).__name__})")

    def close(self) -> bool:
        if not self._closed:
            self._closed = True
            self.scheduler.invalidate_project()
            self._pending_first = self._pending_last = None
        stopped = self.worker.shutdown()
        if stopped:
            completion = self.poll_publish_result()
            if completion is None and self.scheduler.running_batch() is not None:
                self._complete(self.scheduler.running_batch(), "stale_before_publish")
        self._set_status("stopped", None if stopped else "后台发布线程仍在退出")
        return stopped
