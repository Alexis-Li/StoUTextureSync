"""M1a scheduling, source-image publication and diagnostic contracts."""

import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from plugins.stou_texture_sync import config, naming, publish
from plugins.stou_texture_sync.pipeline import (
    PublishWorker, SyncEngine, build_export_configs, format_status, validate_export_and_plan,
)
from plugins.stou_texture_sync.scheduler import Batch, Scheduler
from plugins.stou_texture_sync.target_lock import LOCK_NAME, LockBusy, TargetLock

TEMPLATE = "T_$textureSet_Albedo(_$colorSpace)(_$udim)"


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stage = self.root / "stage"
        self.target = self.root / "target"
        self.target.mkdir()
        self.stage.mkdir()

    def settings(self, **changes):
        return {"preset": "Demo", "target_dir": str(self.target), "stage_dir": str(self.stage),
                "texture_sets": {"Clothes": {}}, **changes}


class ConfigurationTests(WorkspaceTest):
    def test_import_does_not_load_host(self):
        source = """
import sys
from plugins import stou_texture_sync as plugin
from plugins.stou_texture_sync import config, naming, scheduler, target_lock, publish, pipeline
assert plugin.__version__ == "0.0.0"
assert not any(name.split(".")[0] in {"substance_painter", "PySide6"} for name in sys.modules)
"""
        result = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_defaults_and_legacy_aliases(self):
        parsed = config.parse_config({"preset": "Demo", "target_dir": str(self.target)})
        self.assertEqual(parsed.stage_dir, config.DEFAULT_STAGE_DIR)
        self.assertEqual(Path(parsed.log_path), Path(parsed.stage_dir) / "sync_log.jsonl")
        self.assertFalse(parsed.enabled)
        self.assertEqual((parsed.debounce_s, parsed.throttle_ms, parsed.size_log2), (1.0, 500, 10))
        self.assertEqual((parsed.publish_retries, parsed.publish_retry_delay_s), (5, 0.2))

        old_log = self.root / "old.jsonl"
        new_log = self.root / "new.jsonl"
        settings = self.settings(log=str(old_log), texture_set_size_log2={"Clothes": 11})
        parsed = config.parse_config(settings)
        self.assertEqual(Path(parsed.log_path), old_log)
        self.assertEqual(parsed.texture_sets["Clothes"].size_log2, 11)
        settings.update(log_path=str(new_log), throttle_ms=None,
                        texture_sets={"Clothes": {"size_log2": 10, "tile_size_log2": {"1008": 11}}})
        parsed = config.parse_config(settings)
        self.assertEqual(Path(parsed.log_path), new_log)
        self.assertIsNone(parsed.throttle_ms)
        self.assertEqual(parsed.texture_sets["Clothes"].size_log2, 10)
        self.assertEqual(parsed.texture_sets["Clothes"].tile_size_log2, {1008: 11})

    def test_invalid_config_values_and_path_overlap(self):
        invalid = [
            {"size_log2": 12}, {"size_log2": True}, {"throttle_ms": 499}, {"throttle_ms": True},
            {"debounce_s": float("nan")}, {"publish_retry_delay_s": float("inf")}, {"publish_retries": 0},
            {"enabled": "true"}, {"enabled": None}, {"target_dir": ""}, {"preset": ""},
            {"stage_dir": str(self.target / "stage")}, {"stage_dir": str(self.target)},
            {"stage_dir": str(self.root)}, {"log_path": str(self.target / "unsafe.png")},
            {"texture_sets": {"Clothes": {"size_log2": False}}},
            {"texture_sets": {"Clothes": {"tile_size_log2": {"1001": 12}}}},
            {"texture_sets": {"Clothes": {"tile_parts": {"2000": "Face"}}}},
            {"texture_sets": {"Clothes": {"mode": "body_part", "tile_parts": {"1001": "../Face"}}}},
        ]
        for override in invalid:
            with self.subTest(override=override), self.assertRaises(config.ConfigError):
                config.parse_config(self.settings(**override))

    def test_discovery_override_and_utf8_bom(self):
        default = self.root / ".stou_texture_sync" / "m1a_config.json"
        default.parent.mkdir()
        with patch("pathlib.Path.home", return_value=self.root), patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(config.discover_config_path())
            default.write_text(json.dumps(self.settings()), encoding="utf-8-sig")
            self.assertEqual(config.discover_config_path(), default)
            self.assertEqual(config.load_config(default).preset, "Demo")
            explicit = self.root / "other.json"
            with patch.dict(os.environ, STOU_TEXTURE_SYNC_CONFIG=str(explicit)):
                self.assertEqual(config.discover_config_path(), explicit)
                with self.assertRaises(config.ConfigError):
                    config.load_config(config.discover_config_path())


class NamingTests(unittest.TestCase):
    def test_keep_and_body_part_rules(self):
        keep = naming.TextureSetNaming("Coat_blue")
        body = naming.TextureSetNaming("Body", mode="body_part", character_prefix="C00",
                                       tile_parts={1011: "Arm"})
        self.assertEqual(naming.final_name("T_Coat_blue_Albedo_sRGB_1001.png", [TEMPLATE], keep),
                         "T_Coat_blue_Albedo_sRGB.png")
        keep.strip_tile_suffix = False
        self.assertEqual(naming.final_name("T_Coat_blue_Albedo_1001.png", [TEMPLATE], keep),
                         "T_Coat_blue_Albedo_1001.png")
        self.assertEqual(naming.final_name("T_Body_Albedo_1011.png", [TEMPLATE], body),
                         "T_C00_Arm_Albedo.png")
        self.assertEqual(naming.udim_to_uv(1011), (0, 1))
        self.assertEqual(naming.uv_to_udim(0, 1), 1011)
        for source in ("T_Body_Albedo_1000.png", "T_Body_Albedo_1001.png", "T_Body_Albedo.png"):
            with self.subTest(source=source), self.assertRaises(naming.NamingError):
                naming.final_name(source, [TEMPLATE], body)

    def test_reject_paths_invalid_tiles_and_collisions(self):
        spec = naming.TextureSetNaming("Body")
        for value in ("../a.png", r"x\a.png", "C:bad.png", ".", ".."):
            with self.subTest(value=value), self.assertRaises(naming.NamingError):
                naming.final_name(value, [TEMPLATE], spec)
        with self.assertRaises(naming.NamingError):
            naming.TextureSetNaming("Body", tile_parts={1000: "Face"}).validate()
        with self.assertRaises(naming.NamingError):
            naming.plan_names({"Body": ["T_Body_Albedo_1001.png", "T_Body_Albedo_1002.png"]},
                              [TEMPLATE], {"Body": spec})
        with self.assertRaises(naming.NamingError):
            naming.plan_names({"Body": ["T_Body_Albedo.png", "T_Body_Albedo.png"]},
                              [TEMPLATE], {"Body": spec})
        with self.assertRaises(naming.NamingError):
            naming.plan_names({"Body": ["T_Body_Albedo.png"], "body": ["T_body_Albedo.png"]},
                              [TEMPLATE], {"Body": spec, "body": naming.TextureSetNaming("body")})
        with self.assertRaises(naming.NamingError):
            naming.final_name("T_Body_Albedo.png", [TEMPLATE, TEMPLATE], spec)


class SchedulerTests(unittest.TestCase):
    def test_debounce_whole_serial_and_changes_during_export(self):
        scheduler = Scheduler(enabled=True)
        self.assertFalse(scheduler.record("Body", (0, 0), 0))
        self.assertTrue(scheduler.set_project("a"))
        self.assertFalse(scheduler.set_project("a"))
        for tile, timestamp in (((0, 0), 0.0), (None, 0.25), ((1, 0), 0.5)):
            self.assertTrue(scheduler.record("Body", tile, timestamp))
        self.assertIsNone(scheduler.next_batch(1.49))
        batch = scheduler.next_batch(1.5)
        self.assertEqual(batch.scope, {"Body": None})
        scheduler.record("Body", (2, 0), 2)
        self.assertIsNone(scheduler.next_batch(3))
        scheduler.finish(batch)
        later = scheduler.next_batch(3)
        self.assertEqual(later.scope, {"Body": {(2, 0)}})

    def test_disable_keeps_scope_and_manual_does_not_enable_auto(self):
        scheduler = Scheduler(enabled=True, project_id="a")
        scheduler.record("Body", (0, 0), 1)
        running = scheduler.next_batch(2)
        self.assertTrue(scheduler.set_enabled(False))
        self.assertFalse(scheduler.is_current(running))
        scheduler.finish(running, failed=True)
        self.assertIsNone(scheduler.paused_reason)
        self.assertIsNone(scheduler.next_batch(10))
        self.assertEqual(scheduler.request_immediate(), (True, None))
        manual = scheduler.next_batch(10)
        self.assertEqual((manual.trigger, manual.scope), ("immediate", {"Body": {(0, 0)}}))
        scheduler.finish(manual)
        self.assertFalse(scheduler.enabled)

    def test_project_change_discards_queued_work_and_stale_failure(self):
        scheduler = Scheduler(enabled=True, project_id="a")
        scheduler.record("Body", None, 0)
        batch = scheduler.next_batch(1)
        scheduler.record("Body", None, 2)
        scheduler.request_full()
        scheduler.invalidate_project(None)
        self.assertEqual(scheduler.request_full()[0], False)
        scheduler.set_project("b")
        scheduler.record("Clothes", (1, 0), 3)
        scheduler.finish(batch, failed=True, pause_reason="old error", require_full_recovery=True)
        self.assertIsNone(scheduler.paused_reason)
        self.assertFalse(scheduler.requires_full_recovery)
        new_batch = scheduler.next_batch(4)
        self.assertEqual((new_batch.project_id, new_batch.trigger, new_batch.scope),
                         ("b", "auto", {"Clothes": {(1, 0)}}))

    def test_paused_jobs_require_manual_retry_and_partial_requires_full(self):
        scheduler = Scheduler(enabled=True, project_id="a")
        scheduler.record("Body", (0, 0), 0)
        batch = scheduler.next_batch(1)
        scheduler.record("Body", (1, 0), 2)
        scheduler.finish(batch, failed=True, pause_reason="locked")
        self.assertEqual(scheduler.pending(), {"Body": {(0, 0), (1, 0)}})
        self.assertIsNone(scheduler.next_batch(30))
        self.assertEqual(scheduler.request_immediate(), (True, None))
        retry = scheduler.next_batch(30)
        scheduler.finish(retry, failed=True, pause_reason="partial", require_full_recovery=True)
        self.assertFalse(scheduler.request_immediate()[0])
        scheduler.record("Clothes", None, 30)
        self.assertIsNone(scheduler.next_batch(32))
        self.assertEqual(scheduler.request_full(), (True, None))
        full = scheduler.next_batch(32)
        self.assertTrue(full.full)
        self.assertEqual(full.trigger, "full")
        self.assertFalse(scheduler.has_pending())


class TargetAndPublishTests(WorkspaceTest):
    def test_lock_contention_reacquisition_and_diagnostic_failure(self):
        first = TargetLock(self.target)
        second = TargetLock(self.target)
        with self.assertRaises(FileNotFoundError):
            TargetLock(self.target / "missing").acquire("test")
        with patch("plugins.stou_texture_sync.target_lock.json.dumps", side_effect=RuntimeError("fault")):
            with self.assertRaisesRegex(RuntimeError, "fault"):
                first.acquire("test")
        first.acquire("first")
        try:
            with self.assertRaises(RuntimeError):
                first.acquire("again")
            with self.assertRaises(LockBusy):
                second.acquire("second")
            result = publish.publish_batch(7, {}, self.target, lock=second)
            self.assertEqual(result.state, "target_locked")
        finally:
            first.release()
        second.acquire("second")
        second.release()
        self.assertTrue((self.target / LOCK_NAME).is_file())

    def test_stage_reset_only_removes_marked_current(self):
        current = self.stage / "current"
        current.mkdir()
        unowned = current / "user.png"
        unowned.write_bytes(b"user")
        with self.assertRaises(RuntimeError):
            publish.prepare_stage_dir(self.stage)
        self.assertEqual(unowned.read_bytes(), b"user")
        current.rename(self.stage / "unowned")
        prepared = Path(publish.prepare_stage_dir(self.stage))
        self.assertEqual(prepared, current)
        (prepared / "old.png").write_bytes(b"staged")
        publish.prepare_stage_dir(self.stage)
        self.assertEqual([path.name for path in prepared.iterdir()], [publish.STAGE_MARKER])
        self.assertTrue((self.stage / "unowned" / "user.png").is_file())

    def test_dedup_hashes_each_file_once_and_keeps_target_bytes(self):
        a = self.stage / "a.png"
        b = self.stage / "b.png"
        a.write_bytes(b"same")
        b.write_bytes(b"new texture")
        (self.target / a.name).write_bytes(a.read_bytes())
        (self.target / b.name).write_bytes(b"old")
        lock = TargetLock(self.target)
        with patch.object(publish, "sha256", wraps=publish.sha256) as hashed:
            result = publish.publish_batch(1, {a.name: str(a), b.name: str(b)}, self.target, lock=lock)
        self.assertEqual(result.state, "file_published")
        self.assertEqual([(f.status, f.size_bytes) for f in result.files],
                         [("skipped_same", 4), ("published", 11)])
        self.assertEqual([Path(call.args[0]) for call in hashed.call_args_list],
                         [a, self.target / a.name, b, self.target / b.name])
        self.assertEqual((self.target / b.name).read_bytes(), b.read_bytes())
        self.assertFalse(list(self.target.glob("*.stou-tmp")))

    def test_stale_between_retry_attempts_preserves_target_and_cleans_temp(self):
        staged = self.stage / "color.png"
        staged.write_bytes(b"new")
        target = self.target / staged.name
        target.write_bytes(b"old")
        cancelled = threading.Event()
        attempts = []

        def locked(temp, dest):
            attempts.append(dest)
            self.assertEqual(Path(temp).read_bytes(), b"new")
            raise PermissionError("locked")

        result = publish.publish_file(staged, target, target_dir=self.target, replace=locked,
                                      is_current=lambda: not cancelled.is_set(),
                                      wait_fn=lambda _: cancelled.set())
        self.assertEqual((result.status, result.attempts, result.error), ("failed", 1, publish.STALE_ERROR))
        self.assertEqual(attempts, [str(target)])
        self.assertEqual(target.read_bytes(), b"old")
        self.assertFalse(list(self.target.glob("*.stou-tmp")))
        self.assertEqual(publish.publish_file(staged, self.root / target.name, target_dir=self.target)
                         .status, "failed")
        self.assertFalse((self.root / target.name).exists())


class PipelineTests(WorkspaceTest):
    def engine(self, *, replace=None, **changes):
        settings = config.parse_config(self.settings(sample_name="clothing", **changes))
        statuses = []
        engine = SyncEngine(settings, worker=PublishWorker(settings, replace=replace),
                            on_status_changed=lambda state, text: statuses.append((state, text)))
        self.addCleanup(engine.close)
        engine.on_project_changed("project-a")
        return engine, statuses

    def staged(self, stem="Albedo"):
        path = self.stage / f"T_Clothes_{stem}.png"
        path.write_bytes(stem.encode())
        return str(path)

    def submit(self, engine, batch, actual, planned=None, status="Success", message=""):
        return engine.submit_export_result(
            batch, preset_templates=[TEMPLATE, "T_$textureSet_Normal"], stage_dir=self.stage,
            export_status=status, export_message=message, planned_textures=actual if planned is None else planned,
            actual_textures=actual, list_s=0.02, export_s=0.2, stall_ms=31, start_wall=time.time())

    def wait_result(self, engine):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            result = engine.poll_publish_result()
            if result is not None:
                return result
            time.sleep(0.005)
        self.fail("no background publication result")

    def test_config_precedence_and_scope_for_mixed_1k_2k(self):
        settings = config.parse_config(self.settings(texture_sets={
            "Body": {"size_log2": 10, "tile_size_log2": {"1002": 11, "1003": 11}},
            "Clothes": {"size_log2": 11},
            "Disabled": {"enabled": False},
        }))
        available = {"Body": [(0, 0), (1, 0)], "Clothes": []}
        full = Batch(1, 0, "a", {}, full=True, trigger="full")
        settings_list = build_export_configs(settings, full, "resource://Demo", str(self.stage), available)
        self.assertEqual(len(settings_list), 1)
        (full_export,) = settings_list
        self.assertEqual(full_export["exportList"], [{"rootPath": "Body"}, {"rootPath": "Clothes"}])
        rules = full_export["exportParameters"]
        self.assertEqual(rules[-2], {"filter": {"dataPaths": ["Body"], "uvTiles": [[1, 0]]},
                                     "parameters": {"sizeLog2": 11}})
        self.assertEqual(rules[-1]["parameters"]["sizeLog2"], 11)
        partial = Batch(2, 0, "a", {"Body": {(0, 0)}, "Disabled": None})
        (partial_export,) = build_export_configs(settings, partial, "resource://Demo", str(self.stage))
        self.assertEqual(partial_export["exportList"], [{"rootPath": "Body", "filter": {"uvTiles": [[0, 0]]}}])
        self.assertFalse(any("uvTiles" in rule.get("filter", {}) for rule in partial_export["exportParameters"]))

    def test_success_path_and_jsonl_metrics(self):
        engine, statuses = self.engine(enabled=True)
        wall = time.time() - 1.1
        engine.record_texture_change("Clothes", None, 10, wall)
        self.assertIsNone(engine.begin_next_batch(10.9))
        batch = engine.begin_next_batch(11)
        color = {("Clothes", ""): [self.staged()]}
        optional = self.stage / "T_Clothes_Emissive.png"
        self.submit(engine, batch, color, {("Clothes", ""): [*color[("Clothes", "")], str(optional)]})
        self.assertEqual(self.wait_result(engine).state, "file_published")
        self.assertEqual([s for s, _ in statuses], ["waiting_idle", "exporting", "publishing", "file_published"])
        self.assertNotIn("UE", format_status("file_published"))
        raw = Path(engine.config.log_path).read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        record = json.loads(raw)
        self.assertEqual((record["schema"], record["sample"], record["scope"]),
                         ("stou.sync.v1", "clothing", {"Clothes": "whole"}))
        self.assertEqual(record["not_written"], [optional.name])
        self.assertEqual(record["first_change_wall"], wall)
        self.assertEqual(record["stall_ms"], 31)
        self.assertEqual(record["files"][0]["name"], Path(color[("Clothes", "")][0]).name)
        self.assertEqual(len(record["files"][0]["sha"]), 64)
        self.assertGreater(record["published_wall"], record["start_wall"])
        self.assertNotIn(str(self.root), raw.decode())

    def test_export_blocks_unexpected_paths_names_and_failure(self):
        settings = config.parse_config(self.settings())
        staged = self.staged()
        other = self.root / Path(staged).name
        other.write_bytes(b"user")
        old_status = validate_export_and_plan(settings, [TEMPLATE], self.stage, "Warning", "warning", {}, {})
        self.assertEqual(old_status.outcome, "export_not_success")
        unexpected = validate_export_and_plan(settings, [TEMPLATE], self.stage, "Success", "",
                                              {"Clothes": [staged]}, {"Clothes": [other]})
        self.assertEqual(unexpected.outcome, "unexpected_outputs")
        naming_error = validate_export_and_plan(settings, ["$mesh_$textureSet"], self.stage, "Success", "",
                                                {"Clothes": [staged]}, {"Clothes": [staged]})
        self.assertEqual(naming_error.outcome, "naming_error")
        engine, _ = self.engine()
        engine.request_full_sync()
        self.submit(engine, engine.begin_next_batch(10), {}, status="Error",
                    message=r"Output failed on E:\Private Assets\secret.png or /Users/private/file")
        raw = Path(engine.config.log_path).read_text(encoding="utf-8")
        self.assertIn("<local path>", raw)
        self.assertNotIn("Private Assets", raw)
        self.assertNotIn("/Users/", raw)
        self.assertFalse(list(self.target.iterdir()))

    def test_background_permission_retries_and_partial_recovery(self):
        background_calls = []

        def replace(temp, target):
            background_calls.append(threading.current_thread() is not threading.main_thread())
            if Path(target).name.endswith("_Normal.png"):
                raise PermissionError(r"E:\private\Normal.png is locked")
            os.replace(temp, target)

        engine, _ = self.engine(replace=replace, enabled=True, publish_retry_delay_s=0.01,
                                publish_retries=2)
        engine.record_texture_change("Clothes", None, 1, time.time() - 2)
        batch = engine.begin_next_batch(2)
        self.submit(engine, batch, {"Clothes": [self.staged(), self.staged("Normal")]})
        self.assertEqual(engine.state, "publishing")
        result = self.wait_result(engine)
        self.assertEqual(result.state, "partial_publication")
        self.assertEqual(background_calls, [True, True, True])
        self.assertTrue(engine.scheduler.requires_full_recovery)
        self.assertFalse(engine.request_immediate_sync()[0])
        self.assertTrue(engine.request_full_sync()[0])

    def test_project_switch_wakes_worker_and_cannot_pause_new_project(self):
        waiting = threading.Event()
        attempts = []

        def locked(temp, target):
            attempts.append(target)
            waiting.set()
            raise PermissionError("reader")

        engine, _ = self.engine(replace=locked, publish_retry_delay_s=30.0, publish_retries=5)
        engine.request_full_sync()
        self.submit(engine, engine.begin_next_batch(0), {"Clothes": [self.staged()]})
        self.assertTrue(waiting.wait(2))
        engine.on_project_changed("project-b")
        self.wait_result(engine)
        self.assertEqual(len(attempts), 1)
        self.assertFalse(list(self.target.glob("*.png")))
        self.assertFalse(list(self.target.glob("*.stou-tmp")))
        self.assertIsNone(engine.scheduler.paused_reason)
        self.assertEqual(json.loads(Path(engine.config.log_path).read_text(encoding="utf-8"))["outcome"],
                         "stale_during_publish")
        self.assertEqual(engine.state, "idle")
