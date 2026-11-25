"""Painter 11.0 adapter. Painter and Qt calls stay on the main thread."""

from __future__ import annotations

import datetime
import logging
import math
import shutil
import tempfile
import time
from pathlib import Path

import substance_painter as sp
import substance_painter.event
import substance_painter.export
import substance_painter.project
import substance_painter.textureset
import substance_painter.ui
from PySide6 import QtCore

from .config import SyncConfig
from .panel import SyncPanel
from .pipeline import SyncEngine, build_export_configs, format_status, validate_stage_paths
from .publish import prepare_stage_dir

_LOG = logging.getLogger(__name__)


class PainterSyncController(QtCore.QObject):
    def __init__(self, config: SyncConfig | None, *, config_error: str | None = None):
        super().__init__()
        self.config = config
        self.panel = SyncPanel()
        self.engine = None
        self.dock = None
        self._closed = False
        self._handlers = []
        self._qt_connections = []
        self._previous_throttle = None
        self._stage_root = None
        self._in_export_call = False
        self._pending_submission = None
        self._export_gap = 0.0
        self._last_beat = time.perf_counter()
        self._idle_until = None
        self._idle_samples = None
        # Per stack and tile: first and last event clocks until the next tick.
        self._changes = {}
        self._project_check = True
        self._busy = True
        self._status = format_status("unconfigured", config_error)
        if config is not None:
            self.engine = SyncEngine(config, on_status_changed=self._on_status)
            self._status = self.engine.status_text
        self._tick_timer = QtCore.QTimer(self)
        self._tick_timer.setInterval(50)
        self._heartbeat_timer = QtCore.QTimer(self)
        self._heartbeat_timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self._heartbeat_timer.setInterval(10)
        try:
            self.panel.set_configured(config is not None)
            self.panel.set_realtime_checked(config is not None and config.enabled)
            self.panel.set_status(self._status)
            self.dock = sp.ui.add_dock_widget(self.panel)
            if config is not None:
                self._connect()
                self._apply_throttle(config.enabled)
                self._tick_timer.start()
                self._heartbeat_timer.start()
        except BaseException:
            self.close()
            raise

    def _connect(self) -> None:
        event = sp.event
        for event_type, handler in (
            (event.TextureStateEvent, self._on_texture),
            (event.ProjectAboutToClose, self._on_project_left),
            (event.ProjectClosed, self._on_project_left),
            (event.ProjectEditionLeft, self._on_project_left),
            (event.ProjectOpened, self._on_project_ready),
            (event.ProjectCreated, self._on_project_ready),
            (event.ProjectEditionEntered, self._on_project_ready),
            (event.BusyStatusChanged, self._on_busy),
        ):
            event.DISPATCHER.connect(event_type, handler)
            self._handlers.append((event_type, handler))
        for signal, slot in (
            (self.panel.realtime_toggled, self.set_realtime_enabled),
            (self.panel.immediate_requested, self.request_immediate_sync),
            (self.panel.full_requested, self.request_full_sync),
            (self._tick_timer.timeout, self._tick),
            (self._heartbeat_timer.timeout, self._heartbeat),
        ):
            signal.connect(slot)
            self._qt_connections.append((signal, slot))

    def _apply_throttle(self, enabled: bool) -> None:
        event = sp.event.TextureStateEvent
        if enabled and self.config.throttle_ms is not None and self._previous_throttle is None:
            previous = event.cache_key_invalidation_throttling_period()
            event.set_cache_key_invalidation_throttling_period(
                datetime.timedelta(milliseconds=self.config.throttle_ms))
            self._previous_throttle = previous
        elif not enabled and self._previous_throttle is not None:
            event.set_cache_key_invalidation_throttling_period(self._previous_throttle)
            self._previous_throttle = None

    def _on_texture(self, event) -> None:
        if self._closed or (not self._project_check and self.engine.scheduler.project_id is None):
            return
        indices = tuple(event.tile_indices or ())
        tile = indices if len(indices) == 2 and all(type(i) is int and i >= 0 for i in indices) else None
        mono, wall = time.perf_counter(), time.time()
        key = (event.stack_id, tile)
        first = self._changes.get(key, (wall, mono, wall))[0]
        self._changes[key] = first, mono, wall

    def _on_project_left(self, _event) -> None:
        self._changes.clear()
        self._project_check = False
        self.engine.on_project_closed()

    def _on_project_ready(self, _event) -> None:
        self._project_check = True

    def _on_busy(self, event) -> None:
        self._busy = event.busy

    def _refresh_project(self) -> None:
        if self._project_check and sp.project.is_open() and sp.project.is_in_edition_state():
            self._project_check = False
            self.engine.on_project_changed(str(sp.project.get_uuid()))
            self._busy = sp.project.is_busy()

    def _drain_changes(self) -> None:
        if self.engine.scheduler.project_id is None or not self._changes:
            return
        changes, self._changes = self._changes, {}
        stacks = {}
        # Preserve the latest debounce timestamp even when the same key recurs.
        for (stack_id, tile), (first, mono, last) in sorted(changes.items(), key=lambda item: item[1][1]):
            if stack_id not in stacks:
                try:
                    texture_set = sp.textureset.Stack(stack_id=stack_id).material()
                    stacks[stack_id] = texture_set.name(), texture_set.has_uv_tiles()
                except (ValueError, RuntimeError):
                    # A removed stack can no longer identify its Texture Set.
                    stacks[stack_id] = None
            binding = stacks[stack_id]
            if binding is None:
                scope = [(name, None) for name in self.config.texture_sets]
            else:
                name, has_tiles = binding
                scope = [(name, tile if has_tiles else None)]
            for name, changed_tile in scope:
                self.engine.record_texture_change(name, changed_tile, mono, first)
                if first != last:
                    self.engine.record_texture_change(name, changed_tile, mono, last)

    def _on_status(self, _state, text) -> None:
        self._status = text

    def set_realtime_enabled(self, enabled: bool) -> None:
        if self._closed or self.engine is None:
            return
        if not enabled and self.engine.set_realtime_enabled(False):
            self._changes.clear()
        try:
            self._apply_throttle(enabled)
        except (ValueError, RuntimeError, OverflowError):
            self._status = format_status("failed", "无法设置 Painter 事件节流")
        else:
            if enabled:
                self.engine.set_realtime_enabled(True)
        self.panel.set_realtime_checked(self.engine.scheduler.enabled)
        self.panel.set_status(self._status)

    def _manual_request(self, full: bool):
        if self._closed or self.engine is None:
            return False, "未配置同步"
        self._refresh_project()
        self._drain_changes()
        request = self.engine.request_full_sync if full else self.engine.request_immediate_sync
        result = request()
        self.panel.set_status(self._status)
        return result

    def request_immediate_sync(self):
        return self._manual_request(full=False)

    def request_full_sync(self):
        return self._manual_request(full=True)

    def _tick(self) -> None:
        if self._closed or self._in_export_call or self._pending_submission is not None:
            return
        self._refresh_project()
        self.engine.poll_publish_result()
        if self.engine.scheduler.project_id is None:
            self.panel.set_status(self._status)
            return
        self._drain_changes()
        self.panel.set_status(self._status)
        if self._busy or not sp.project.is_in_edition_state() or sp.project.is_busy():
            if self.engine.scheduler.has_pending() and not self.engine.scheduler.paused_reason:
                self.panel.set_status(format_status("pending", format_status("host_busy")))
            return
        if self._idle_until is not None:
            return
        batch = self.engine.begin_next_batch(time.perf_counter())
        if batch is not None:
            self.panel.set_status(self._status)
            self._run_export(batch)

    def _heartbeat(self) -> None:
        now = time.perf_counter()
        gap = (now - self._last_beat) * 1000
        self._last_beat = now
        export_active = self._in_export_call or self._pending_submission is not None
        if export_active:
            self._export_gap = max(self._export_gap, gap)
        if self._idle_until is not None and self._idle_samples is not None:
            if (not self._busy and not export_active and not self._changes
                    and self.engine.scheduler.running_batch() is None):
                self._idle_samples.append(gap)
            if now >= self._idle_until:
                self._idle_until = None
                try:
                    self.engine.log_writer.write("idle", stall_ms=self._idle_samples)
                except OSError:
                    self.panel.set_status(format_status("failed", "无法写入空闲基线日志"))

    def measure_idle_baseline(self, duration_s: float) -> list[float]:
        """Collect 10 ms heartbeat gaps and log them asynchronously."""
        if self._closed or self.engine is None:
            raise RuntimeError("sync is not configured")
        if not math.isfinite(duration_s) or duration_s <= 0:
            raise ValueError("duration_s must be finite and positive")
        if self._idle_until is not None or self.engine.scheduler.running_batch() is not None:
            raise RuntimeError("another measurement or sync is running")
        self._idle_samples = []
        self._last_beat = time.perf_counter()
        self._idle_until = self._last_beat + duration_s
        return self._idle_samples

    def _resolve_preset(self) -> tuple[str, list[str]]:
        config = self.config
        presets = sp.export.list_resource_export_presets()
        if config.preset_url is not None:
            matches = [preset for preset in presets if preset.resource_id.url() == config.preset_url]
        else:
            matches = [preset for preset in presets if preset.resource_id.name == config.preset]
        if len(matches) != 1:
            raise LookupError("找不到唯一的导出预设，请检查 preset 或 preset_url")
        preset = matches[0]
        return preset.resource_id.url(), [output["fileName"] for output in preset.list_output_maps()]

    def _run_export(self, batch) -> None:
        self._in_export_call = True
        self._export_gap = 0.0
        start_wall = time.time()
        self._last_beat = time.perf_counter()
        payload = dict(preset_templates=[], stage_dir="", export_status="Success", export_message="",
                       planned_textures={}, actual_textures={}, list_s=0.0, export_s=0.0,
                       start_wall=start_wall, available_tiles=None)
        try:
            if self._stage_root is None:
                root = Path(self.config.stage_dir)
                root.mkdir(parents=True, exist_ok=True)
                self._stage_root = tempfile.mkdtemp(prefix="painter-", dir=root)
            stage = payload["stage_dir"] = prepare_stage_dir(self._stage_root)
            url, payload["preset_templates"] = self._resolve_preset()
            available_tiles = payload["available_tiles"] = {
                ts.name(): [(tile.u, tile.v) for tile in ts.all_uv_tiles()] if ts.has_uv_tiles() else []
                for ts in sp.textureset.all_texture_sets()
            }
            for export_config in build_export_configs(self.config, batch, url, stage, available_tiles):
                if not self.engine.scheduler.is_current(batch):
                    break
                started = time.perf_counter()
                try:
                    planned = sp.export.list_project_textures(export_config)
                    validate_stage_paths(stage, planned)
                finally:
                    payload["list_s"] += time.perf_counter() - started
                payload["planned_textures"].update(planned)
                if not self.engine.scheduler.is_current(batch):
                    break
                started = time.perf_counter()
                try:
                    result = sp.export.export_project_textures(export_config)
                finally:
                    payload["export_s"] += time.perf_counter() - started
                payload["export_status"] = result.status.name
                payload["export_message"] = result.message
                payload["actual_textures"].update(result.textures)
                if result.status != sp.export.ExportStatus.Success:
                    break
        except Exception as exc:
            payload["export_status"] = "Error"
            payload["export_message"] = str(exc)
        finally:
            self._in_export_call = False
            if self._closed:
                self.close()
        if self._closed:
            return
        self._pending_submission = batch, payload
        # Deliver queued project and switch events before testing batch validity.
        self._drain_ui_events()
        submission, self._pending_submission = self._pending_submission, None
        if submission is not None:
            self._submit_export(*submission)

    def _drain_ui_events(self) -> None:
        QtCore.QCoreApplication.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents)

    def _submit_export(self, batch, payload) -> None:
        gap = (time.perf_counter() - self._last_beat) * 1000
        self._refresh_project()
        self.engine.submit_export_result(batch, **payload, stall_ms=round(max(gap, self._export_gap), 3))
        if not self._closed:
            self.panel.set_status(self._status)

    def _clear_staging(self) -> None:
        if self._stage_root is not None:
            shutil.rmtree(self._stage_root)
            self._stage_root = None

    def close(self) -> bool:
        if not self._closed:
            self._closed = True
            self._tick_timer.stop()
            self._heartbeat_timer.stop()
            for signal, slot in reversed(self._qt_connections):
                signal.disconnect(slot)
            self._qt_connections.clear()
            for event_type, handler in reversed(self._handlers):
                try:
                    sp.event.DISPATCHER.disconnect(event_type, handler)
                except (RuntimeError, ValueError):
                    _LOG.exception("Could not disconnect Painter event %s", event_type.__name__)
            self._handlers.clear()
            self._changes.clear()
            self._pending_submission = None
            self._idle_until = None
        stopped = self.engine is None or self.engine.close()
        try:
            self._apply_throttle(False)
        finally:
            if self.dock is not None:
                sp.ui.delete_ui_element(self.dock)
                self.dock = None
        if stopped and not self._in_export_call:
            self._clear_staging()
        return stopped and not self._in_export_call
