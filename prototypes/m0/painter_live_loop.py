"""M0 live sync loop prototype for Painter 11.0 (route A, not product code).

TextureStateEvent -> Scheduler (idle debounce, serial) -> Painter export of the
changed Texture Sets / UV Tiles -> naming -> per-file atomic publication into the
monitored source-image directory. Every batch logs stage timings, the main-thread
stall and the wall-clock publication time so UE reimport records can be joined.

    import sys; sys.path.insert(0, "<repo>/prototypes/m0")
    import painter_live_loop as loop
    loop.start("<config.json>")      # see example_live_config.json
    loop.set_enabled(False); loop.request_full(); loop.immediate(); loop.stop()

Callbacks only record changes; exports start from a Qt timer on the main thread.
stop() disconnects every event and timer; start() stops a previous instance first.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import sys
import time
import types

from PySide6 import QtCore

import substance_painter as sp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stou_m0.naming import NamingError, TextureSetNaming, plan_names  # noqa: E402
from stou_m0.publish import publish_batch  # noqa: E402
from stou_m0.scheduler import WHOLE, Scheduler  # noqa: E402
from stou_m0.target_lock import LockBusy, TargetLock  # noqa: E402

_STATE = sys.modules.setdefault("_stou_m0_live_state", types.ModuleType("_stou_m0_live_state"))


class LiveLoop:
    def __init__(self, config: dict):
        self.config = config
        self.log_path = config["log"]
        self.scheduler = Scheduler(debounce=config.get("debounce_s", 1.0),
                                   enabled=config.get("enabled", True))
        self.namings = {}
        for name, spec in config["texture_sets"].items():
            spec = dict(spec)
            spec["tile_parts"] = {int(k): v for k, v in spec.get("tile_parts", {}).items()}
            self.namings[name] = TextureSetNaming(name, **spec)
        self.preset_url, self.templates = self._resolve_preset(config["preset"])
        self.batch_counter = 0
        self.first_change = None
        self.last_change = None
        self.paused_reason = None
        self.project_uuid = str(sp.project.get_uuid()) if sp.project.is_open() else None
        self.lock = TargetLock(config["target_dir"])
        self._previous_throttle = None
        if config.get("throttle_ms"):
            self._previous_throttle = sp.event.TextureStateEvent.cache_key_invalidation_throttling_period()
            sp.event.TextureStateEvent.set_cache_key_invalidation_throttling_period(
                datetime.timedelta(milliseconds=config["throttle_ms"]))
        self._handlers = []
        for event_type, handler in ((sp.event.TextureStateEvent, self._on_texture),
                                    (sp.event.ProjectAboutToClose, self._on_project_left),
                                    (sp.event.ProjectEditionLeft, self._on_project_left),
                                    (sp.event.ProjectOpened, self._on_project_left)):
            sp.event.DISPATCHER.connect(event_type, handler)
            self._handlers.append((event_type, handler))
        self._timer = QtCore.QTimer()
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._beat = QtCore.QTimer()
        self._beat.setInterval(10)
        self._beat.timeout.connect(self._heartbeat)
        self._last_beat = time.perf_counter()
        self.max_gap = 0.0
        self._immediate = False
        self._timer.start()
        self._beat.start()
        self.log("start", preset=config["preset"], templates=self.templates,
                 debounce_s=self.scheduler.debounce, throttle_ms=config.get("throttle_ms"),
                 project_uuid=self.project_uuid)

    # ---------------------------------------------------------------- helpers
    def log(self, kind, **data):
        record = {"wall": round(time.time(), 4), "kind": kind, **data}
        with open(self.log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    @staticmethod
    def _resolve_preset(name):
        for preset in sp.export.list_resource_export_presets():
            if preset.resource_id.name == name:
                templates = [m["fileName"] for m in preset.list_output_maps()]
                return preset.resource_id.url(), templates
        raise LookupError(f"export preset not found: {name}")

    def _heartbeat(self):
        now = time.perf_counter()
        self.max_gap = max(self.max_gap, now - self._last_beat)
        self._last_beat = now

    # ---------------------------------------------------------------- events
    def _on_texture(self, event):
        try:
            texture_set = sp.textureset.Stack(stack_id=event.stack_id).material()
            name = texture_set.name()
        except Exception:  # pylint: disable=broad-except
            return
        if name not in self.namings:
            return
        tile = tuple(event.tile_indices) if texture_set.has_uv_tiles() else None
        now = time.perf_counter()
        self.scheduler.record(name, tile, now)
        self.last_change = time.time()
        self.first_change = self.first_change or self.last_change

    def _on_project_left(self, _event):
        self.scheduler.invalidate()
        self.log("invalidated", reason="project closed, left edition or reopened")

    # ---------------------------------------------------------------- control
    def set_enabled(self, enabled):
        self.scheduler.set_enabled(enabled)
        self.log("enabled", value=enabled, generation=self.scheduler.generation)

    def request_full(self):
        self.scheduler.request_full()

    def immediate(self):
        self._immediate = True

    def stop(self):
        self._timer.stop()
        self._beat.stop()
        for event_type, handler in self._handlers:
            try:
                sp.event.DISPATCHER.disconnect(event_type, handler)
            except Exception:  # pylint: disable=broad-except
                pass
        self._handlers = []
        if self._previous_throttle is not None:
            sp.event.TextureStateEvent.set_cache_key_invalidation_throttling_period(self._previous_throttle)
        self.log("stopped")

    # ---------------------------------------------------------------- work
    def _tick(self):
        if self.paused_reason or not sp.project.is_open() or not sp.project.is_in_edition_state() \
                or sp.project.is_busy():
            return
        batch = self.scheduler.next_batch(time.perf_counter(), immediate=self._immediate)
        self._immediate = False
        if batch is None:
            return
        try:
            failed = self._run(batch)
        except Exception as exc:  # pylint: disable=broad-except
            self.log("batch_error", batch=batch.number, error=repr(exc))
            failed = True
        self.scheduler.finish(batch, failed=failed)

    def _export_config(self, batch, stage):
        scope = {name: WHOLE for name in self.namings} if batch.full else batch.scope
        export_list = []
        for name, tiles in sorted(scope.items()):
            entry = {"rootPath": name}
            if tiles is not WHOLE:
                entry["filter"] = {"uvTiles": [list(t) for t in sorted(tiles)]}
            export_list.append(entry)
        parameters = [{"parameters": {"paddingAlgorithm": "infinite",
                                      "sizeLog2": self.config.get("size_log2", 10)}}]
        for name, size in self.config.get("texture_set_size_log2", {}).items():
            parameters.append({"filter": {"dataPaths": [name]}, "parameters": {"sizeLog2": size}})
        return {"exportShaderParams": False, "exportPath": stage,
                "defaultExportPreset": self.preset_url, "exportList": export_list,
                "exportParameters": parameters}

    def _run(self, batch) -> bool:
        self.batch_counter += 1
        first, last = self.first_change, self.last_change
        self.first_change = None
        stage = os.path.join(self.config["stage_dir"], f"batch_{batch.number:05d}")
        shutil.rmtree(stage, ignore_errors=True)
        os.makedirs(stage)
        config = self._export_config(batch, stage)
        t0 = time.time()
        planned = sp.export.list_project_textures(config)
        self.max_gap = 0.0
        self._last_beat = time.perf_counter()
        t1 = time.time()
        result = sp.export.export_project_textures(config)
        t2 = time.time()
        record = {"batch": batch.number, "full": batch.full,
                  "scope": {k: (sorted(v) if v is not WHOLE else "whole") for k, v in batch.scope.items()},
                  "first_change_wall": first, "last_change_wall": last, "start_wall": round(t0, 4),
                  "wait_after_last_change_s": round(t0 - last, 3) if last else None,
                  "export_s": round(t2 - t1, 3), "status": str(result.status)}
        if result.status != sp.export.ExportStatus.Success:
            self.log("batch", **record, outcome="export_not_success", message=result.message)
            return True
        if not self.scheduler.is_current(batch) or str(sp.project.get_uuid()) != self.project_uuid:
            self.log("batch", **record, outcome="stale_before_publish")
            return True
        grouped = {}
        for (texture_set, _stack), paths in result.textures.items():
            grouped.setdefault(texture_set, []).extend(paths)
        planned_names = {os.path.basename(p) for paths in planned.values() for p in paths}
        actual_names = {os.path.basename(p) for paths in grouped.values() for p in paths}
        unexpected = sorted(actual_names - planned_names)
        record["not_written"] = sorted(planned_names - actual_names)
        if unexpected:
            self.log("batch", **record, outcome="unexpected_outputs", files=unexpected)
            return True
        try:
            names = plan_names(grouped, self.templates, self.namings)
        except NamingError as exc:
            self.log("batch", **record, outcome="naming_blocked", error=str(exc))
            self.paused_reason = "naming"
            return True
        try:
            self.lock.acquire(f"painter batch {batch.number}")
        except LockBusy as exc:
            self.log("batch", **record, outcome="target_locked", error=str(exc))
            return True
        try:
            t3 = time.time()
            published = publish_batch(batch.number, {final: path for path, final in names.items()},
                                      self.config["target_dir"], dedup=not batch.full,
                                      is_current=lambda: self.scheduler.is_current(batch))
            t4 = time.time()
        finally:
            self.lock.release()
        record.update(
            stall_ms=round(self.max_gap * 1000.0, 1), validate_s=round(t3 - t2, 3),
            publish_s=round(t4 - t3, 3), published_wall=round(t4, 4), state=published.state,
            files={f.name: f.status for f in published.files},
            sp_side_s=round(t4 - last, 3) if last else None)
        self.log("batch", **record, outcome=published.state)
        if published.state == "partial_publication":
            self.paused_reason = "partial publication"
        if not self.config.get("keep_stage", False) and published.state == "file_published":
            shutil.rmtree(stage, ignore_errors=True)
        return published.state != "file_published"


def start(config_path: str) -> LiveLoop:
    stop()
    with open(config_path, encoding="utf-8") as handle:
        config = json.load(handle)
    _STATE.loop = LiveLoop(config)
    return _STATE.loop


def stop():
    loop = getattr(_STATE, "loop", None)
    if loop is not None:
        loop.stop()
        _STATE.loop = None


def set_enabled(enabled: bool):
    _STATE.loop.set_enabled(enabled)


def request_full():
    _STATE.loop.request_full()


def immediate():
    _STATE.loop.immediate()
