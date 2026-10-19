"""M0 Painter probe: scripted host scenarios that record JSON lines.

Load inside Painter 11.0 (remote scripting or the Python console):

    import sys; sys.path.insert(0, "<repo>/prototypes/m0")
    import painter_probe; painter_probe.start("identity", "<scratch>", "<log.jsonl>")

Scenarios run as generators driven by a Qt timer so Painter's event loop keeps
running between steps; nothing blocks the main thread except the host calls
under test. ``stop()`` disconnects every handler and timer, and starting again
stops the previous run first, so re-running is safe.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import struct
import sys
import time
import types
import uuid as uuidlib

from PySide6 import QtCore

import substance_painter as sp

_STATE = sys.modules.setdefault("_stou_m0_probe_state", types.ModuleType("_stou_m0_probe_state"))
METADATA_CONTEXT = "stou_texture_sync_m0"

# Neutral sample identities for the meshes shipped with Painter's own test resources.
SAMPLES = {
    "body_udim": "4udims.obj",          # one Texture Set, four UV Tiles
    "clothing_2ts": "cubes_2_ts.fbx",   # two Texture Sets, no UV Tiles
}


def sample_path(name: str) -> str:
    base = os.path.join(os.path.dirname(sp.__file__), "..", "automated_tests", "resources")
    return os.path.normpath(os.path.join(base, SAMPLES[name]))


def png_size(path: str) -> tuple[int, int]:
    with open(path, "rb") as handle:
        head = handle.read(24)
    return struct.unpack(">II", head[16:24])


def file_sha(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pixel_sha(path: str) -> str:
    """Hash decoded pixels so metadata-only differences can be told apart."""
    from PySide6 import QtGui
    image = QtGui.QImage(path)
    data = image.constBits().tobytes() if image.constBits() is not None else b""
    return hashlib.sha256(data).hexdigest()


class Runner:
    def __init__(self, scratch: str, log_path: str):
        self.scratch = scratch
        self.log_path = log_path
        self.t0 = time.perf_counter()
        self._log = open(log_path, "a", encoding="utf-8")
        self._gen = None
        self._wait = None
        self._timer = QtCore.QTimer()
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._step)
        self._beat = QtCore.QTimer()
        self._beat.setInterval(10)
        self._beat.timeout.connect(self._heartbeat)
        self._last_beat = time.perf_counter()
        self.max_gap = 0.0
        self.gaps: list[tuple[float, float]] = []
        self.events: list[dict] = []
        self._handlers = []
        for name in ("TextureStateEvent", "BusyStatusChanged", "ExportTexturesAboutToStart",
                     "ExportTexturesEnded", "ProjectEditionEntered", "ProjectEditionLeft",
                     "ProjectOpened", "ProjectCreated", "ProjectClosed", "ProjectSaved",
                     "EngineComputationsStatusChanged", "LayerStacksModelDataChanged"):
            event_type = getattr(sp.event, name)
            handler = (lambda e, n=name: self._on_event(n, e))
            sp.event.DISPATCHER.connect(event_type, handler)
            self._handlers.append((event_type, handler))

    def now(self) -> float:
        return round((time.perf_counter() - self.t0) * 1000.0, 1)

    def log(self, kind: str, **data):
        record = {"t_ms": self.now(), "kind": kind, **data}
        self._log.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._log.flush()

    def _on_event(self, name, event):
        data = {"event": name}
        for attr in ("action", "stack_id", "tile_indices", "channel_type", "cache_key",
                     "busy", "status", "message", "engine_computations_status"):
            if hasattr(event, attr):
                value = getattr(event, attr)
                data[attr] = str(value) if not isinstance(value, (int, float, list, tuple)) else value
        if name == "ExportTexturesEnded":
            data["textures"] = {"/".join(k): len(v) for k, v in getattr(event, "textures", {}).items()}
        data["t_ms"] = self.now()
        self.events.append(data)
        if name not in ("LayerStacksModelDataChanged",):
            self.log("event", **data)

    def _heartbeat(self):
        now = time.perf_counter()
        gap = now - self._last_beat
        self._last_beat = now
        if gap > 0.1:
            self.gaps.append((self.now(), round(gap * 1000.0, 1)))
        self.max_gap = max(self.max_gap, gap)

    def reset_gaps(self):
        self.max_gap = 0.0
        self.gaps = []
        self._last_beat = time.perf_counter()

    def start(self, generator):
        self._gen = generator
        self._timer.start()
        self._beat.start()

    def _step(self):
        if self._wait is not None:
            predicate, deadline, label = self._wait
            ok = False
            try:
                ok = bool(predicate())
            except Exception:  # pylint: disable=broad-except
                ok = False
            if not ok and time.perf_counter() < deadline:
                return
            if not ok and label != "sleep":
                self.log("timeout", label=label)
            self._wait = None
        try:
            request = next(self._gen)
        except StopIteration:
            self.log("done")
            self.stop()
            return
        except Exception as exc:  # pylint: disable=broad-except
            import traceback
            self.log("error", error=repr(exc), trace=traceback.format_exc())
            self.stop()
            return
        if request is None:
            return
        if isinstance(request, (int, float)):
            deadline = time.perf_counter() + float(request)
            self._wait = (lambda: False, deadline, "sleep")
        else:
            predicate, timeout, label = request
            self._wait = (predicate, time.perf_counter() + timeout, label)

    def stop(self):
        self._timer.stop()
        self._beat.stop()
        for event_type, handler in self._handlers:
            try:
                sp.event.DISPATCHER.disconnect(event_type, handler)
            except Exception:  # pylint: disable=broad-except
                pass
        self._handlers = []
        if not self._log.closed:
            self._log.close()


def ready():
    return (sp.project.is_open() and sp.project.is_in_edition_state()
            and not sp.project.is_busy())


def wait_ready(label="ready", timeout=120.0):
    return (ready, timeout, label)


def wait_idle(runner, quiet=2.0, timeout=60.0):
    """Wait until no texture event has arrived for `quiet` seconds and the host is idle."""
    since = runner.now()

    def predicate():
        last = max((e["t_ms"] for e in runner.events if e["event"] == "TextureStateEvent"),
                   default=0.0)
        last = max(last, since)
        return ready() and runner.now() - last > quiet * 1000.0
    return (predicate, timeout, "idle")


def create_project(name, resolution=1024, uv_tiles=False):
    settings = sp.project.Settings(
        default_texture_resolution=resolution,
        normal_map_format=sp.project.NormalMapFormat.DirectX,
        project_workflow=(sp.project.ProjectWorkflow.UVTile if uv_tiles
                          else sp.project.ProjectWorkflow.Default),
        import_cameras=False)
    sp.project.create(mesh_file_path=sample_path(name), settings=settings)


def close_project():
    if sp.project.is_open():
        sp.project.close()


def describe_project(runner):
    sets = []
    for texture_set in sp.textureset.all_texture_sets():
        res = texture_set.get_resolution()
        tiles = [(t.u, t.v) for t in texture_set.all_uv_tiles()] if texture_set.has_uv_tiles() else []
        stacks = [s.name() for s in texture_set.all_stacks()]
        sets.append({"name": texture_set.name(), "resolution": [res.width, res.height],
                     "uv_tiles": tiles, "stacks": stacks,
                     "stack_ids": [int(s.uid()) if hasattr(s, "uid") else None
                                   for s in texture_set.all_stacks()]})
    runner.log("project", name=sp.project.name(), texture_sets=sets)
    return sets


def preset_url(name):
    for preset in sp.export.list_resource_export_presets():
        if preset.resource_id.name == name:
            return preset.resource_id.url()
    raise LookupError(f"export preset not found: {name}")


def export_config(stage, preset, roots, size_log2=10, uv_tiles=None, extra_filter=None):
    export_list = []
    for root in roots:
        entry = {"rootPath": root}
        if uv_tiles is not None:
            entry["filter"] = {"uvTiles": uv_tiles}
        if extra_filter:
            entry.setdefault("filter", {}).update(extra_filter)
        export_list.append(entry)
    return {
        "exportShaderParams": False,
        "exportPath": stage,
        "defaultExportPreset": preset,
        "exportList": export_list,
        "exportParameters": [{"parameters": {"sizeLog2": size_log2, "paddingAlgorithm": "infinite"}}],
    }


def timed_export(runner, label, config, hash_pixels=False):
    """Run one export, returning a summary; logs planned list, result, timing and stall."""
    os.makedirs(config["exportPath"], exist_ok=True)
    started = time.perf_counter()
    planned = sp.export.list_project_textures(config)
    list_ms = round((time.perf_counter() - started) * 1000.0, 1)
    planned_files = sorted(os.path.basename(p) for files in planned.values() for p in files)
    runner.reset_gaps()
    begin = time.perf_counter()
    try:
        result = sp.export.export_project_textures(config)
    except Exception as exc:  # pylint: disable=broad-except
        runner.log("export_error", label=label, error=repr(exc))
        return None
    export_ms = round((time.perf_counter() - begin) * 1000.0, 1)
    files = sorted(p for paths in result.textures.values() for p in paths)
    details = []
    for path in files:
        entry = {"file": os.path.basename(path), "bytes": os.path.getsize(path),
                 "size": png_size(path) if path.lower().endswith(".png") else None,
                 "sha": file_sha(path)[:16]}
        if hash_pixels:
            entry["pixel_sha"] = pixel_sha(path)[:16]
        details.append(entry)
    summary = {"label": label, "status": str(result.status), "message": result.message,
               "list_ms": list_ms, "export_ms": export_ms, "planned": planned_files,
               "actual": [d["file"] for d in details],
               "planned_matches_actual": planned_files == [d["file"] for d in details],
               "files": details}
    runner.log("export", **summary)
    return summary


def measured_export(runner, label, config, **kwargs):
    """Export, then let the event loop run once to record the main-thread stall."""
    summary = timed_export(runner, label, config, **kwargs)
    yield 0.05
    runner.log("stall", label=label, max_gap_ms=round(runner.max_gap * 1000.0, 1),
               export_ms=summary["export_ms"] if summary else None)
    return summary


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def _set_fill_color(fill, rgb, channel=None):
    color = sp.colormanagement.Color(*rgb)
    fill.set_source(channel or sp.textureset.ChannelType.BaseColor, color)


# --------------------------------------------------------------------------- scenarios

def scenario_identity(runner):
    """POC-05 / FR-06: UUID and Metadata semantics across save, reopen, save-as, copy, move."""
    base = os.path.join(runner.scratch, "identity")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base)
    close_project()
    yield (lambda: not sp.project.is_open(), 30, "closed")

    create_project("clothing_2ts")
    yield wait_ready()
    runner.log("identity", step="created_unsaved", uuid=str(sp.project.get_uuid()),
               file_path=sp.project.file_path())
    meta = sp.project.Metadata(METADATA_CONTEXT)
    plugin_id = str(uuidlib.uuid4())
    meta.set("project_id", plugin_id)
    runner.log("identity", step="metadata_set", plugin_id=plugin_id,
               needs_saving=sp.project.needs_saving())

    path_a = os.path.join(base, "sample_A.spp")
    sp.project.save_as(path_a, sp.project.ProjectSaveMode.Full)
    yield (lambda: not sp.project.is_busy() and os.path.exists(path_a), 60, "save_a")
    runner.log("identity", step="saved_A", uuid=str(sp.project.get_uuid()))
    uuid_a = str(sp.project.get_uuid())

    path_copy = os.path.join(base, "sample_A_copy.spp")
    sp.project.save_as_copy(path_copy, sp.project.ProjectSaveMode.Full)
    yield (lambda: not sp.project.is_busy() and os.path.exists(path_copy), 60, "save_as_copy")
    runner.log("identity", step="save_as_copy_done", current_path=sp.project.file_path(),
               uuid=str(sp.project.get_uuid()))

    close_project()
    yield (lambda: not sp.project.is_open(), 30, "closed")
    shutil.copy2(path_a, os.path.join(base, "sample_A_oscopy.spp"))
    moved_dir = os.path.join(base, "移动后目录")
    os.makedirs(moved_dir)
    shutil.copy2(path_a, os.path.join(moved_dir, "样本A.spp"))

    for label, path in (("reopen_A", path_a),
                        ("open_save_as_copy", path_copy),
                        ("open_os_copy", os.path.join(base, "sample_A_oscopy.spp")),
                        ("open_moved_chinese_path", os.path.join(moved_dir, "样本A.spp"))):
        sp.project.open(path)
        yield wait_ready(label)
        meta = sp.project.Metadata(METADATA_CONTEXT)
        runner.log("identity", step=label, uuid=str(sp.project.get_uuid()),
                   same_uuid_as_A=str(sp.project.get_uuid()) == uuid_a,
                   plugin_id=meta.get("project_id"), metadata_keys=meta.list(),
                   file_path=sp.project.file_path())
        if label == "reopen_A":
            path_b = os.path.join(base, "sample_B_save_as.spp")
            sp.project.save_as(path_b, sp.project.ProjectSaveMode.Full)
            yield (lambda: not sp.project.is_busy() and os.path.exists(path_b), 60, "save_b")
            runner.log("identity", step="save_as_B", uuid=str(sp.project.get_uuid()),
                       same_uuid_as_A=str(sp.project.get_uuid()) == uuid_a,
                       plugin_id=sp.project.Metadata(METADATA_CONTEXT).get("project_id"),
                       file_path=sp.project.file_path())
        close_project()
        yield (lambda: not sp.project.is_open(), 30, "closed")

    create_project("clothing_2ts")
    yield wait_ready()
    runner.log("identity", step="new_project_unsaved", uuid=str(sp.project.get_uuid()),
               same_uuid_as_A=str(sp.project.get_uuid()) == uuid_a)
    close_project()
    yield (lambda: not sp.project.is_open(), 30, "closed")


def _edit(runner, label, func):
    runner.log("edit", label=label)
    start = len(runner.events)
    with sp.layerstack.ScopedModification(label):
        func()
    return start


def _events_since(runner, start):
    out = []
    for e in runner.events[start:]:
        if e["event"] == "TextureStateEvent":
            out.append((e.get("stack_id"), tuple(e.get("tile_indices") or ()),
                        e.get("channel_type"), e.get("action")))
    return sorted(set(out), key=str)


def scenario_events(runner, throttle_ms=None):
    """POC-02: event identity and tile coverage for scripted layer-stack edits."""
    close_project()
    yield (lambda: not sp.project.is_open(), 30, "closed")
    create_project("body_udim", uv_tiles=True)
    yield wait_ready()
    yield wait_idle(runner, quiet=3.0)
    sets = describe_project(runner)
    runner.log("throttle", period_ms=sp.event.TextureStateEvent
               .cache_key_invalidation_throttling_period().total_seconds() * 1000)
    texture_set = sp.textureset.all_texture_sets()[0]
    stack = texture_set.get_stack()
    pos = sp.layerstack.InsertPosition.from_textureset_stack(stack)
    holder = {}

    def add_fill():
        holder["fill"] = sp.layerstack.insert_fill(pos)
        holder["fill"].active_channels = {sp.textureset.ChannelType.BaseColor,
                                          sp.textureset.ChannelType.Roughness}
        _set_fill_color(holder["fill"], (0.8, 0.1, 0.1))

    cases = [
        ("insert_fill_all_tiles", add_fill),
        ("fill_color_change", lambda: _set_fill_color(holder["fill"], (0.1, 0.8, 0.1))),
        ("restrict_fill_to_tile_1_0", lambda: holder["fill"].set_geometry_mask_enabled_uv_tiles(
            [texture_set.uv_tile(1, 0)])),
        ("fill_color_change_tile_1_0", lambda: _set_fill_color(holder["fill"], (0.1, 0.1, 0.8))),
        ("opacity_change", lambda: holder["fill"].set_opacity(0.5, sp.textureset.ChannelType.BaseColor)),
        ("add_black_mask", lambda: holder["fill"].add_mask(sp.layerstack.MaskBackground.Black)),
        ("hide_layer", lambda: holder["fill"].set_visible(False)),
        ("insert_paint_layer_empty", lambda: sp.layerstack.insert_paint(pos)),
        ("show_layer_roughness_only", lambda: (holder["fill"].set_visible(True),
                                               holder["fill"].set_opacity(
                                                   0.3, sp.textureset.ChannelType.Roughness))),
    ]
    import datetime
    previous = sp.event.TextureStateEvent.cache_key_invalidation_throttling_period()
    if throttle_ms:
        sp.event.TextureStateEvent.set_cache_key_invalidation_throttling_period(
            datetime.timedelta(milliseconds=throttle_ms))
        runner.log("throttle_set", period_ms=throttle_ms)
    for label, func in cases:
        start = _edit(runner, label, func)
        edit_ms = runner.now()
        yield wait_idle(runner, quiet=3.0)
        delays = [round(e["t_ms"] - edit_ms, 1) for e in runner.events[start:]
                  if e["event"] == "TextureStateEvent"]
        runner.log("edit_events", label=label, events=_events_since(runner, start),
                   first_ms=min(delays, default=None), last_ms=max(delays, default=None))
    sp.event.TextureStateEvent.set_cache_key_invalidation_throttling_period(previous)
    runner.log("summary_sets", sets=sets, throttle_restored_ms=previous.total_seconds() * 1000)


def scenario_export(runner, preset_name="兔皮鸭套装输出"):
    """POC-02/03/04: preset outputs, uvTiles filter, size, timing, stall and hash stability."""
    stage_root = os.path.join(runner.scratch, "export")
    shutil.rmtree(stage_root, ignore_errors=True)
    preset = preset_url(preset_name)
    runner.log("preset", name=preset_name, url_has_version="?version=" in preset)

    # Body UDIM sample
    close_project()
    yield (lambda: not sp.project.is_open(), 30, "closed")
    create_project("body_udim", uv_tiles=True)
    yield wait_ready()
    yield wait_idle(runner, quiet=3.0)
    sets = describe_project(runner)
    root = sets[0]["name"]
    texture_set = sp.textureset.all_texture_sets()[0]
    fill = sp.layerstack.insert_fill(
        sp.layerstack.InsertPosition.from_textureset_stack(texture_set.get_stack()))
    _set_fill_color(fill, (0.6, 0.3, 0.2))
    yield wait_idle(runner, quiet=2.0)

    def stage(name):
        return os.path.join(stage_root, name)

    yield 0.5
    timed_export(runner, "body_full_1k_a", export_config(stage("body_full_1k_a"), preset, [root]),
                 hash_pixels=True)
    yield 1.0
    timed_export(runner, "body_full_1k_b", export_config(stage("body_full_1k_b"), preset, [root]),
                 hash_pixels=True)
    yield 1.0
    timed_export(runner, "body_full_2k", export_config(stage("body_full_2k"), preset, [root], 11))
    yield 1.0
    for variant in ([[1, 0]], [[0, 0]], [[1, 1]], [[2, 1]], [[1, 0], [2, 0]], ["1002"], [1002]):
        label = "body_tiles_" + json.dumps(variant).replace(" ", "")
        cfg = export_config(stage("tiles_" + str(abs(hash(label)) % 10**6)), preset, [root],
                            uv_tiles=variant)
        try:
            planned = sp.export.list_project_textures(cfg)
            runner.log("uvtiles_list", variant=variant,
                       planned=sorted(os.path.basename(p) for v in planned.values() for p in v))
        except Exception as exc:  # pylint: disable=broad-except
            runner.log("uvtiles_list", variant=variant, error=repr(exc))
        yield 0.2
    timed_export(runner, "body_one_tile_1k",
                 export_config(stage("body_one_tile"), preset, [root], uv_tiles=[[1, 1]]))
    yield 1.0
    timed_export(runner, "body_two_tiles_1k",
                 export_config(stage("body_two_tiles"), preset, [root], uv_tiles=[[1, 1], [2, 1]]))
    yield 1.0
    timed_export(runner, "body_chinese_stage",
                 export_config(stage("中文暂存"), preset, [root]))
    yield 1.0
    # Export repeatability across 5 runs for timing statistics
    for i in range(5):
        timed_export(runner, f"body_full_1k_rep{i}", export_config(stage(f"rep{i}"), preset, [root]))
        yield 0.5

    # Clothing sample with two Texture Sets
    close_project()
    yield (lambda: not sp.project.is_open(), 30, "closed")
    create_project("clothing_2ts")
    yield wait_ready()
    yield wait_idle(runner, quiet=3.0)
    sets = describe_project(runner)
    roots = [s["name"] for s in sets]
    timed_export(runner, "clothing_all_1k", export_config(stage("clothing_all"), preset, roots))
    yield 1.0
    timed_export(runner, "clothing_one_set_1k", export_config(stage("clothing_one"), preset, roots[:1]))
    yield 1.0
    timed_export(runner, "clothing_all_2k", export_config(stage("clothing_all_2k"), preset, roots, 11))
    yield 1.0
    # Wrong configuration must raise before writing
    try:
        sp.export.list_project_textures(export_config(stage("invalid"), preset, ["NoSuchSet"]))
    except ValueError as exc:
        runner.log("invalid_root_rejected", error=str(exc),
                   stage_written=os.path.exists(stage("invalid")))
    yield from measured_export(runner, "clothing_all_1k_stall",
                               export_config(stage("clothing_stall"), preset, roots))


def scenario_heavy(runner, preset_name="兔皮鸭套装输出", smart_materials=8, reps=7):
    """POC-04: export timing and stall for a heavier procedural stack on the UDIM sample."""
    stage_root = os.path.join(runner.scratch, "heavy")
    shutil.rmtree(stage_root, ignore_errors=True)
    preset = preset_url(preset_name)
    close_project()
    yield (lambda: not sp.project.is_open(), 30, "closed")
    create_project("body_udim", uv_tiles=True)
    yield wait_ready()
    yield wait_idle(runner, quiet=3.0)
    texture_set = sp.textureset.all_texture_sets()[0]
    root = texture_set.name()
    base = sp.layerstack.insert_fill(
        sp.layerstack.InsertPosition.from_textureset_stack(texture_set.get_stack()))
    _set_fill_color(base, (0.5, 0.5, 0.5))
    found = [r.identifier() for r in sp.resource.search("u:smartmaterial")
             if r.identifier().context == "starter_assets"][:smart_materials]
    for resource_id in found:
        sp.layerstack.insert_smart_material(sp.layerstack.InsertPosition.above_node(base),
                                            resource_id)
    layer_count = 0
    def count(nodes):
        total = 0
        for node in nodes:
            total += 1
            if hasattr(node, "sub_layers"):
                total += count(node.sub_layers())
        return total
    layer_count = count(sp.layerstack.get_root_layer_nodes(texture_set.get_stack()))
    runner.log("heavy_stack", smart_materials=[r.name for r in found], layer_nodes=layer_count)
    yield wait_idle(runner, quiet=4.0, timeout=300)
    cases = [("heavy_full_1k", None, 10), ("heavy_one_tile_1k", [[1, 0]], 10),
             ("heavy_full_2k", None, 11), ("heavy_one_tile_2k", [[1, 0]], 11)]
    for label, tiles, size in cases:
        times, stalls = [], []
        for i in range(reps):
            summary = yield from measured_export(
                runner, f"{label}_{i}",
                export_config(os.path.join(stage_root, f"{label}_{i}"), preset, [root],
                              size, uv_tiles=tiles))
            if summary:
                times.append(summary["export_ms"])
                stalls.append(round(runner.max_gap * 1000.0, 1))
            yield 0.3
        runner.log("timing", label=label, n=len(times), median_ms=percentile(times, 0.5),
                   p95_ms=percentile(times, 0.95), max_ms=max(times, default=None),
                   stall_median_ms=percentile(stalls, 0.5), stall_max_ms=max(stalls, default=None))


def scenario_live(runner, config, edits=10, spacing_s=8.0, toggle_test=True):
    """POC-01/02/04: scripted edits through the live loop prototype into a monitored directory."""
    import painter_live_loop as loop
    importlib.reload(loop)
    close_project()
    yield (lambda: not sp.project.is_open(), 30, "closed")
    create_project("body_udim", uv_tiles=True)
    yield wait_ready()
    texture_set = sp.textureset.all_texture_sets()[0]
    fill = sp.layerstack.insert_fill(
        sp.layerstack.InsertPosition.from_textureset_stack(texture_set.get_stack()))
    fill.active_channels = {sp.textureset.ChannelType.BaseColor, sp.textureset.ChannelType.Roughness}
    _set_fill_color(fill, (0.5, 0.4, 0.3))
    tile_fill = sp.layerstack.insert_fill(
        sp.layerstack.InsertPosition.from_textureset_stack(texture_set.get_stack()))
    tile_fill.active_channels = {sp.textureset.ChannelType.BaseColor}
    tile_fill.set_geometry_mask_enabled_uv_tiles([texture_set.uv_tile(0, 0)])  # Face (1001)
    _set_fill_color(tile_fill, (0.9, 0.7, 0.6))
    yield wait_idle(runner, quiet=4.0)
    loop.start(config)
    loop.immediate()
    loop.request_full()        # bring targets to the current state first
    yield 10.0
    for i in range(edits):
        whole = i % 2 == 1
        shade = 0.2 + 0.07 * i
        with sp.layerstack.ScopedModification(f"live edit {i}"):
            if whole:
                _set_fill_color(fill, (shade, 0.5, 1.0 - shade))
            else:
                _set_fill_color(tile_fill, (1.0 - shade, shade, 0.5))
        runner.log("live_edit", i=i, scope="whole_set" if whole else "tile_1001", wall=time.time())
        yield spacing_s
    if toggle_test:
        # Switch real-time off right after an edit: no new automatic publication may follow.
        with sp.layerstack.ScopedModification("edit then disable"):
            _set_fill_color(tile_fill, (0.1, 0.9, 0.1))
        runner.log("live_edit", i=edits, scope="tile_1001_then_disabled", wall=time.time())
        yield 0.2
        loop.set_enabled(False)
        yield spacing_s
        loop.immediate()       # manual sync still works while real-time is off
        runner.log("manual_immediate", wall=time.time())
        yield spacing_s
    loop.stop()


SCENARIOS = {"identity": scenario_identity, "events": scenario_events, "export": scenario_export,
             "heavy": scenario_heavy, "live": scenario_live}


def start(name: str, scratch: str, log_path: str, **kwargs):
    stop()
    os.makedirs(scratch, exist_ok=True)
    runner = Runner(scratch, log_path)
    runner.log("start", scenario=name, painter=sp.application.version(),
               python=sys.version.split()[0])
    _STATE.runner = runner
    runner.start(SCENARIOS[name](runner, **kwargs))
    return runner


def stop():
    runner = getattr(_STATE, "runner", None)
    if runner is not None:
        runner.stop()
        _STATE.runner = None
