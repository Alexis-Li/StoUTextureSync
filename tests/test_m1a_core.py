"""M1a scheduling, source-image publication and diagnostic contracts."""

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from plugins import stou_texture_sync as plugin
from plugins.stou_texture_sync import config, naming, publish
from plugins.stou_texture_sync.pipeline import (
    PublishWorker, SyncEngine, SyncLogWriter, build_export_configs, format_status,
    sanitize_local_paths, validate_binding_uniqueness, validate_export_and_plan,
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
    def test_final_name_patterns_cover_optional_color_space_with_or_without_a_tile(self):
        def stems(template, spec, udim):
            return {(p.prefix, p.suffix) for p in naming.final_name_patterns(template, spec, udim)}

        shirt = naming.TextureSetNaming("Shirt")
        self.assertEqual(stems(TEMPLATE, shirt, 1001),
                         {("t_shirt_albedo", None), ("t_shirt_albedo_", "")})
        self.assertEqual(stems(TEMPLATE, shirt, None),
                         {("t_shirt_albedo", None), ("t_shirt_albedo_", "")})
        self.assertEqual(stems("T_$textureSet_Albedo(_$colorSpace)", shirt, None),
                         {("t_shirt_albedo", None), ("t_shirt_albedo_", "")})
        shirt.strip_tile_suffix = False
        self.assertEqual(stems(TEMPLATE, shirt, 1001),
                         {("t_shirt_albedo_1001", None), ("t_shirt_albedo_", "_1001")})
        for bad_udim in (None, 1002):
            body = naming.TextureSetNaming("Body", mode="body_part", tile_parts={1001: "Arm"})
            with self.subTest(udim=bad_udim), self.assertRaises(naming.NamingError):
                naming.final_name_patterns(TEMPLATE, body, bad_udim)
        with self.assertRaises(naming.NamingError):
            naming.final_name_patterns("T_$textureSet_Albedo_$udim", shirt, None)

    def test_color_space_and_tile_need_a_separator_in_precheck_and_runtime(self):
        shirt = naming.TextureSetNaming("Shirt")
        for template in ("U_$textureSet_Base(_$colorSpace)($udim)",
                         "U_$textureSet_Base(_$colorSpace)(-$udim)",
                         "U_$textureSet_Base($udim)($colorSpace)"):
            with self.subTest(template=template):
                for call in (lambda: naming.template_regex(template, "Shirt"),
                             lambda: naming.final_name_patterns(template, shirt, 1001),
                             lambda: naming.final_name("U_Shirt_Base_linear1001.png", [template], shirt)):
                    with self.assertRaises(naming.NamingError):
                        call()
        naming.template_regex("T_$textureSet_Albedo(_$colorSpace)(_$udim)", "Shirt")
        naming.template_regex("T_$textureSet_Albedo(_$colorSpace)(.$udim)", "Shirt")

        settings = config.parse_config({
            "preset": "Demo", "target_dir": "t", "stage_dir": "s",
            "texture_sets": {"Shirt": {}, "Body": {"mode": "body_part", "tile_parts": {"1001": "linear1001"}}}})
        templates = ["U_$textureSet_Base(_$colorSpace)($udim)", "V_$textureSet_linear1001(_$udim)"]
        with self.assertRaises(naming.NamingError):
            validate_binding_uniqueness(settings, templates, {"Shirt": [(0, 0)], "Body": [(0, 0)]})
        with self.assertRaises(naming.NamingError):
            naming.plan_names({"Shirt": ["U_Shirt_Base_linear1001.png"]}, templates,
                              {"Shirt": shirt})

    def test_precheck_covers_templates_that_omit_an_optional_tile_group(self):
        settings = config.parse_config({
            "preset": "Demo", "target_dir": "t", "stage_dir": "s", "texture_sets": {"Body": {}}})
        templates = ["T_$textureSet_Base(_$udim)", "T_Body_Base_1001(_$udim)"]
        spec = naming.TextureSetNaming("Body")
        with self.assertRaises(naming.NamingError):
            naming.final_name("T_Body_Base_1001.png", templates, spec)
        with self.assertRaisesRegex(naming.NamingError, "can match the same exported file"):
            validate_binding_uniqueness(settings, templates, {"Body": [(0, 0)]})
        with self.assertRaises(naming.NamingError):
            validate_binding_uniqueness(settings, templates[::-1], {"Body": [(0, 0)]})
        accepted = {(p.prefix, p.suffix) for p in naming.source_name_patterns(
            templates[1], "Body", 1001, include_optional_udim_omit=True)}
        self.assertEqual(accepted, {("t_body_base_1001_1001", None), ("t_body_base_1001", None)})
        # Another Tile cannot be confused with the literal 1001, and distinct map types never clash.
        validate_binding_uniqueness(settings, templates, {"Body": [(1, 0)]})

    def test_precheck_matches_the_runtime_regex_for_every_tile_number(self):
        settings = config.parse_config({
            "preset": "Demo", "target_dir": "t", "stage_dir": "s", "texture_sets": {"Body": {}}})
        available = {"Body": [(0, 0)]}
        spec = naming.TextureSetNaming("Body")
        for templates in (["T_$textureSet_Base(_$udim)", "T_Body_Base_1002"],
                          ["T_$textureSet_Base(_$colorSpace)_1002",
                           "T_$textureSet_Base(_$colorSpace)(_$udim)"]):
            for ordered in (templates, templates[::-1]):
                with self.subTest(templates=ordered):
                    with self.assertRaisesRegex(naming.NamingError, "can match the same exported file"):
                        validate_binding_uniqueness(settings, ordered, available)
        with self.assertRaisesRegex(naming.NamingError, "matches several preset templates"):
            naming.final_name("T_Body_Base_1002.png", ["T_$textureSet_Base(_$udim)", "T_Body_Base_1002"],
                              spec)
        validate_binding_uniqueness(settings, ["T_$textureSet_Base(_$udim)", "T_Body_Other_1002"], available)

    def test_precheck_matches_runtime_with_original_texture_set_spelling(self):
        settings = config.parse_config({
            "preset": "Demo", "target_dir": "t", "stage_dir": "s", "texture_sets": {"Straße": {}}})
        templates = ["T_$textureSet_Base(_$udim)", "T_$textureSet_Base_1002"]
        with self.assertRaisesRegex(naming.NamingError, "matches several preset templates"):
            naming.final_name("T_Straße_Base_1002.png", templates, naming.TextureSetNaming("Straße"))
        with self.assertRaisesRegex(naming.NamingError, "can match the same exported file"):
            validate_binding_uniqueness(settings, templates, {"Straße": [(0, 0)]})

    def test_precheck_matches_runtime_for_overlapping_udim_digits(self):
        settings = config.parse_config({
            "preset": "Demo", "target_dir": "t", "stage_dir": "s", "texture_sets": {"Body": {}}})
        templates = ["$colorSpace_T_$textureSet_Base11002", "$colorSpace_T_$textureSet_Base1($udim)"]
        with self.assertRaisesRegex(naming.NamingError, "matches several preset templates"):
            naming.final_name("sRGB_T_Body_Base11002.png", templates, naming.TextureSetNaming("Body"))
        with self.assertRaisesRegex(naming.NamingError, "can match the same exported file"):
            validate_binding_uniqueness(settings, templates, {"Body": [(0, 0)]})

    def test_source_name_patterns_follow_export_names(self):
        def stems(template, name, udim):
            return {(p.prefix, p.suffix) for p in naming.source_name_patterns(template, name, udim)}

        self.assertEqual(stems("T_$textureSet_Base(_raw_$colorSpace)(_$udim)", "Body", 1002),
                         {("t_body_base_1002", None), ("t_body_base_raw_", "_1002")})
        self.assertEqual(stems("T_$textureSet_Base(_raw_$colorSpace)(_$udim)", "Body", None),
                         {("t_body_base", None), ("t_body_base_raw_", "")})
        with self.assertRaises(naming.NamingError):
            naming.source_name_patterns("T_$textureSet_Base_$udim", "Body", None)

    def test_pattern_overlap_is_exact(self):
        lit, hole = (lambda p: naming.FinalNamePattern(p)), naming.FinalNamePattern
        cases = [
            (lit("a_b"), lit("a_b"), True), (lit("a_b"), lit("a_c"), False),
            (lit("a_linear_1"), hole("a_", "_1"), True), (lit("a_1_1"), hole("a_", "_1"), False),
            (lit("a_x_y_1"), hole("a_", "_1"), False), (lit("a_"), hole("a_", ""), False),
            (hole("a_", ""), hole("a_", "_1"), False), (hole("a_", ""), hole("a_b", ""), True),
            (hole("a_", ""), hole("a_b_", ""), False), (hole("a-", "_1"), hole("a-b", "_1"), True),
            (hole("x", "_c"), hole("xa_", ""), True), (hole("a_", "_1"), hole("a_", "_2"), False),
            (hole("a", "1-"), hole("a", "-"), True), (hole("a", "-1"), hole("a", "-"), False),
        ]
        for first, second, expected in cases:
            with self.subTest(first=first, second=second):
                self.assertEqual(naming.patterns_overlap(first, second), expected)
                self.assertEqual(naming.patterns_overlap(second, first), expected)

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

    def test_disable_discards_pending_scope_and_queued_requests(self):
        for trigger in (None, "immediate", "full"):
            with self.subTest(trigger=trigger):
                scheduler = Scheduler(enabled=True, project_id="a")
                scheduler.record("Body", (0, 0), 1)
                if trigger is not None:
                    self.assertTrue(getattr(scheduler, f"request_{trigger}")()[0])
                self.assertTrue(scheduler.set_enabled(False))
                self.assertEqual(scheduler.pending(), {})
                self.assertFalse(scheduler.has_pending())
                self.assertIsNone(scheduler.next_batch(10))
                self.assertFalse(scheduler.request_immediate()[0])
                scheduler.set_enabled(True)
                self.assertIsNone(scheduler.next_batch(11))

    def test_disable_discards_running_auto_scope_and_records_new_changes(self):
        scheduler = Scheduler(enabled=True, project_id="a")
        scheduler.record("Body", (0, 0), 1)
        running = scheduler.next_batch(2)
        scheduler.record("Clothes", None, 3)
        self.assertTrue(scheduler.set_enabled(False))
        self.assertFalse(scheduler.is_current(running))
        self.assertEqual(scheduler.generation, running.generation)
        self.assertTrue(scheduler.record("Body", (1, 0), 4))
        scheduler.finish(running, failed=True)
        self.assertIsNone(scheduler.paused_reason)
        self.assertEqual(scheduler.pending(), {"Body": {(1, 0)}})
        self.assertIsNone(scheduler.next_batch(10))
        self.assertEqual(scheduler.request_immediate(), (True, None))
        manual = scheduler.next_batch(10)
        self.assertEqual((manual.trigger, manual.scope), ("immediate", {"Body": {(1, 0)}}))
        scheduler.finish(manual)
        self.assertFalse(scheduler.enabled)

    def test_realtime_toggle_preserves_running_manual_batches(self):
        for trigger in ("immediate", "full"):
            with self.subTest(trigger=trigger):
                scheduler = Scheduler(enabled=True, project_id="a")
                scheduler.record("Body", (0, 0), 1)
                self.assertTrue(getattr(scheduler, f"request_{trigger}")()[0])
                batch = scheduler.next_batch(2)
                self.assertEqual(batch.trigger, trigger)
                scheduler.set_enabled(False)
                self.assertTrue(scheduler.is_current(batch))
                self.assertEqual(scheduler.generation, batch.generation)
                scheduler.finish(batch)
                self.assertFalse(scheduler.requires_full_recovery)

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
        self.assertTrue(scheduler.requires_full_recovery)
        self.assertEqual(scheduler.paused_reason, "partial")
        scheduler.record("Body", (1, 0), 33)
        self.assertFalse(scheduler.request_immediate()[0])
        scheduler.finish(full, failed=True, pause_reason="export failed")
        self.assertTrue(scheduler.requires_full_recovery)
        self.assertFalse(scheduler.request_immediate()[0])
        self.assertIsNone(scheduler.next_batch(34))

        scheduler.request_full()
        recovery = scheduler.next_batch(34)
        scheduler.record("Body", (2, 0), 35)
        scheduler.finish(recovery)
        self.assertFalse(scheduler.requires_full_recovery)
        self.assertIsNone(scheduler.paused_reason)
        self.assertTrue(scheduler.request_immediate()[0])


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

    def submit(self, engine, batch, actual, planned=None, status="Success", message="", **kwargs):
        return engine.submit_export_result(
            batch, preset_templates=[TEMPLATE, "T_$textureSet_Normal"], stage_dir=self.stage,
            export_status=status, export_message=message, planned_textures=actual if planned is None else planned,
            actual_textures=actual, list_s=0.02, export_s=0.2, stall_ms=31, start_wall=time.time(), **kwargs)

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
        self.assertGreaterEqual(record["stall_ms"], 31)
        self.assertEqual(record["files"][0]["name"], Path(color[("Clothes", "")][0]).name)
        self.assertEqual(len(record["files"][0]["sha"]), 64)
        self.assertGreater(record["published_wall"], record["start_wall"])
        self.assertEqual(record["files"][0]["published_at"], record["published_wall"])
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
        self.assertTrue(engine.status_text.startswith("已暂停"))
        self.assertIn("Painter 导出未成功", engine.status_text)
        self.assertIn("完整同步", engine.status_text)
        self.assertNotIn("立即同步", engine.status_text)
        self.assertEqual(engine.request_immediate_sync(), (False, "请先执行完整同步以恢复"))
        self.assertIn("已暂停", engine.status_text)
        self.assertIn("Painter 导出未成功", engine.status_text)
        self.assertIn("完整同步", engine.status_text)
        self.assertFalse(list(self.target.iterdir()))

    def test_single_tile_export_rejects_binding_wide_collision(self):
        engine, _ = self.engine(enabled=True)
        engine.record_texture_change("Clothes", (0, 0), 1, 10)
        batch = engine.begin_next_batch(2)
        path = self.stage / "T_Clothes_Albedo_1001.png"
        path.write_bytes(b"first tile")
        outputs = {"Clothes": [str(path)]}
        outcome = engine.submit_export_result(
            batch, preset_templates=[TEMPLATE], stage_dir=self.stage,
            export_status="Success", export_message="", planned_textures=outputs,
            actual_textures=outputs, list_s=0, export_s=0, stall_ms=0,
            start_wall=12, available_tiles={"Clothes": [(0, 0), (1, 0)]})
        self.assertEqual(outcome.outcome, "naming_error")
        self.assertIn("1002", outcome.error)
        self.assertTrue(engine.status_text.startswith("已暂停"))
        self.assertIn("立即同步", engine.status_text)
        self.assertNotIn("完整同步", engine.status_text)
        self.assertFalse(list(self.target.iterdir()))

        settings = config.parse_config(self.settings(texture_sets={
            "Clothes": {"strip_tile_suffix": False},
        }))
        ready = validate_export_and_plan(settings, [TEMPLATE], self.stage, "Success", "",
                                         outputs, outputs, {"Clothes": [(0, 0), (1, 0)]})
        self.assertEqual(ready.staged, {path.name: str(path)})

    def test_single_tile_export_rejects_collision_without_optional_color_space(self):
        engine, _ = self.engine(enabled=True, texture_sets={
            "Shirt": {"mode": "keep", "strip_tile_suffix": False},
            "Body": {"mode": "body_part", "tile_parts": {"1002": "Shirt"}},
        })
        engine.record_texture_change("Shirt", (0, 0), 1, 10)
        batch = engine.begin_next_batch(2)
        self.assertEqual(batch.scope, {"Shirt": {(0, 0)}})
        path = self.stage / "T_Shirt_Base_1001.png"
        path.write_bytes(b"shirt tile")
        outputs = {"Shirt": [str(path)]}
        templates = ["T_$textureSet_Base(_$colorSpace)(_$udim)", "U_$textureSet_Base_1001(_$udim)"]
        with patch.object(engine.worker, "submit", wraps=engine.worker.submit) as publish_outputs:
            outcome = engine.submit_export_result(
                batch, preset_templates=templates, stage_dir=self.stage,
                export_status="Success", export_message="", planned_textures=outputs,
                actual_textures=outputs, list_s=0, export_s=0, stall_ms=0,
                start_wall=12, available_tiles={"Shirt": [(0, 0)], "Body": [(1, 0)]})
            self.assertEqual(outcome.outcome, "naming_error")
            publish_outputs.assert_not_called()
        self.assertIn(path.name, outcome.error)
        self.assertIn("Body", outcome.error)
        self.assertIn("1002", outcome.error)
        self.assertFalse(list(self.target.iterdir()))

    def test_single_tile_export_rejects_collision_through_any_color_space_value(self):
        templates = ["T_$textureSet_Base(_$colorSpace)(_$udim)", "U_$textureSet_Base_linear_1001(_$udim)"]
        for name in ("T_Shirt_Base_linear_1001.png", "T_Shirt_Base_1001.png"):
            with self.subTest(file=name):
                engine, _ = self.engine(enabled=True, texture_sets={
                    "Shirt": {"mode": "keep", "strip_tile_suffix": False},
                    "Body": {"mode": "body_part", "tile_parts": {"1002": "Shirt"}},
                })
                engine.record_texture_change("Shirt", (0, 0), 1, 10)
                batch = engine.begin_next_batch(2)
                path = self.stage / name
                path.write_bytes(b"shirt tile")
                outputs = {"Shirt": [str(path)]}
                with patch.object(engine.worker, "submit", wraps=engine.worker.submit) as publish_outputs:
                    outcome = engine.submit_export_result(
                        batch, preset_templates=templates, stage_dir=self.stage,
                        export_status="Success", export_message="", planned_textures=outputs,
                        actual_textures=outputs, list_s=0, export_s=0, stall_ms=0,
                        start_wall=12, available_tiles={"Shirt": [(0, 0)], "Body": [(1, 0)]})
                    self.assertEqual(outcome.outcome, "naming_error")
                    publish_outputs.assert_not_called()
                self.assertIn("Body", outcome.error)
                self.assertIn("1002", outcome.error)
                self.assertFalse(list(self.target.iterdir()))

    def test_preset_templates_with_overlapping_export_names_are_rejected_before_publishing(self):
        templates = ["T_$textureSet_Base(_raw_$colorSpace)(_$udim)", "T_Body_Base_raw_sRGB(_$udim)"]
        engine, _ = self.engine(enabled=True, texture_sets={
            "Shirt": {"mode": "keep"}, "Body": {"mode": "keep"}})
        engine.record_texture_change("Shirt", (0, 0), 1, 10)
        batch = engine.begin_next_batch(2)
        path = self.stage / "T_Shirt_Base_raw_sRGB_1001.png"
        path.write_bytes(b"shirt tile")
        outputs = {"Shirt": [str(path)]}
        with patch.object(engine.worker, "submit", wraps=engine.worker.submit) as publish_outputs:
            outcome = engine.submit_export_result(
                batch, preset_templates=templates, stage_dir=self.stage,
                export_status="Success", export_message="", planned_textures=outputs,
                actual_textures=outputs, list_s=0, export_s=0, stall_ms=0,
                start_wall=12, available_tiles={"Shirt": [(0, 0)], "Body": [(1, 0)]})
            self.assertEqual(outcome.outcome, "naming_error")
            publish_outputs.assert_not_called()
        self.assertIn("Body", outcome.error)
        self.assertIn("1002", outcome.error)
        self.assertFalse(list(self.target.iterdir()))

    def test_regular_presets_and_body_part_mappings_have_no_name_collisions(self):
        templates = [f"T_$textureSet_{kind}(_$colorSpace)(_$udim)"
                     for kind in ("Albedo", "ARM", "Normal", "Emissive")]
        settings = config.parse_config(self.settings(texture_sets={
            "Body": {"mode": "body_part", "character_prefix": "C00",
                     "tile_parts": {"1001": "Torso", "1002": "Arm", "1011": "Leg"}},
            "Shirt": {"strip_tile_suffix": False},
            "Coat": {},
            "Hair": {"mode": "body_part", "tile_parts": {"1001": "Hair"}},
        }))
        available = {"Body": [(0, 0), (1, 0), (0, 1)], "Shirt": [(0, 0), (1, 0)],
                     "Coat": [(0, 0)], "Hair": [(0, 0)]}
        validate_binding_uniqueness(settings, templates, available)
        validate_binding_uniqueness(settings, templates)
        settings.texture_sets["Coat"].strip_tile_suffix = False
        validate_binding_uniqueness(settings, templates, available)

    def test_configured_tiles_detect_collisions_across_texture_sets(self):
        settings = config.parse_config(self.settings(texture_sets={
            "Body": {"mode": "body_part", "tile_parts": {"1001": "Torso"}},
            "Clothes": {"mode": "body_part", "tile_parts": {"1002": "Torso"}},
        }))
        path = self.stage / "T_Body_Albedo_1001.png"
        path.write_bytes(b"body tile")
        outputs = {"Body": [str(path)]}
        outcome = validate_export_and_plan(settings, [TEMPLATE], self.stage, "Success", "",
                                           outputs, outputs)
        self.assertEqual(outcome.outcome, "naming_error")
        self.assertIn("T_Torso_Albedo", outcome.error)
        self.assertIn("Clothes", outcome.error)

        settings.texture_sets["Clothes"].enabled = False
        ready = validate_export_and_plan(settings, [TEMPLATE], self.stage, "Success", "",
                                         outputs, outputs)
        self.assertEqual(ready.staged, {"T_Torso_Albedo.png": str(path)})

    def test_log_writer_and_strict_scan_share_path_rules(self):
        from tools.summarize_sync_logs import find_local_path_leaks

        paths = ["/data/private/a.png", "/work/asset.spp", r"C:\Users\x\a.png",
                 r"\\srv\share\a.png", "file:///C:/a.png", "~/project/image.png",
                 "/Game/private/a.png", "/Engine/private/project.uproject",
                 "/Script/private/log.jsonl"]
        public = ["/Game/Characters/Coat/T_Coat_Albedo.T_Coat_Albedo",
                  "/Engine/Transient", "/Script/Engine.Texture2D",
                  "https://example.com/a/b", "http://example.com/private.spp",
                  "resource://library/resources/a"]
        path = self.root / "sanitize.jsonl"
        self.assertEqual(len(find_local_path_leaks([{"fields": paths}])), len(paths))
        SyncLogWriter(path).write("test", fields=paths, public=public,
                                  message=f"see {public[3]} failed at {paths[0]}")
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["fields"], ["<local path>"] * len(paths))
        self.assertEqual(record["public"], public)
        self.assertEqual(record["message"], f"see {public[3]} failed at <local path>")
        self.assertEqual(find_local_path_leaks([record]), [])

    def test_log_writer_and_strict_scan_distinguish_local_paths_from_public_tokens(self):
        from tools.summarize_sync_logs import find_local_path_leaks

        paths = {
            "path:/data/private/a.png": "path:<local path>",
            "/private.png": "<local path>",
            "/Users/Private Name/image.png": "<local path>",
            "file:///Projects/My Scene/test.png": "<local path>",
            "/@private/a.png": "<local path>",
            "//server/share/image.png": "<local path>",
            r"C:\Users\a\b.png": "<local path>",
            r"\\srv\share\a.png": "<local path>",
            "file:///C:/a.png": "<local path>",
            "/Game/private/a.uasset": "<local path>",
            "/Game/private/a.png": "<local path>",
            "error /Users/Private Name/image.png /Game/角色/T_Body": "error <local path>",
        }
        public = ("/Game/Characters/Coat/T_Coat_Albedo.T_Coat_Albedo", "/Game/角色/T_Body",
                  "/Game/StoU/T_Face.T_Face", "/Engine/Transient", "/Script/Engine.Texture2D",
                  "https://example.com/a/b", "resource://shelf/preset?version=1",
                  "实时/立即/完整", "a / b", "1/2")
        path = self.root / "path-boundaries.jsonl"
        writer = SyncLogWriter(path)
        for value in (*paths, *public):
            writer.write("test", value=value)
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        for record, value in zip(records, (*paths, *public), strict=True):
            with self.subTest(value=value):
                self.assertEqual(bool(find_local_path_leaks([{"value": value}])), value in paths)
                self.assertEqual(record["value"], paths.get(value, value))
                self.assertEqual(find_local_path_leaks([record]), [])

    def test_local_paths_with_spaces_apostrophes_and_adjacent_public_urls(self):
        from tools.summarize_sync_logs import find_local_path_leaks

        redacted = {
            "/game/private/asset": "<local path>",
            "/Game/Private Name/image.png": "<local path>",
            "/Game/My Private Name/image.png": "<local path>",
            "/Game/O'Brien/image.png": "<local path>",
            "/Game/Private O'Brien/image.png": "<local path>",
            "/Users/O'Brien/image.png": "<local path>",
            "/Users/Artists' Files/image.png": "<local path>",
            "cannot open /Game/Private/image.png.": "cannot open <local path>.",
            "open '/Users/O'Brien/image.png', retry": "open '<local path>', retry",
            "open '/Users/a/b.png'": "open '<local path>'",
            "open '/Users/a/b.png'. Next": "open '<local path>'. Next",
            "open '/Users/Artists' Files/image.png'. Next": "open '<local path>'. Next",
            "open '/Users/a/b.png' and 'done'": "open '<local path>' and 'done'",
            "open '/Users/a/b.png' and '/Users/c/d.png'":
                "open '<local path>' and '<local path>'",
            "/private.png,https://example.com/a": "<local path>,https://example.com/a",
            r"https://example.com/a,C:\Private\image.png": "https://example.com/a,<local path>",
            "/Users/Private Name/image.png,https://example.com/a":
                "<local path>,https://example.com/a",
            "/Game/角色/T_Body in /private.png": "<local path>",
        }
        kept = (
            "实时/立即/完整", "a / b", "1/2",
            "/Game/角色/T_Body", "/Game/StoU/T_Face.T_Face", "/Engine/Transient",
            "/Script/Engine.Texture2D", "/Engine/BasicShapes/Cube", "/Script/Engine/Texture2D",
            "https://example.com/a/b", "resource://shelf/preset?version=1",
        )
        path = self.root / "edges.jsonl"
        writer = SyncLogWriter(path)
        for value in (*redacted, *kept):
            writer.write("test", value=value)
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        for record, value in zip(records, (*redacted, *kept), strict=True):
            with self.subTest(value=value):
                self.assertEqual(sanitize_local_paths(value), redacted.get(value, value))
                self.assertEqual(record["value"], redacted.get(value, value))
                self.assertEqual(find_local_path_leaks([{"value": value}]),
                                 ["record 1.value"] if value in redacted else [])
                self.assertEqual(find_local_path_leaks([record]), [])

    def test_stall_includes_validation_and_worker_submission_even_on_failure(self):
        engine, _ = self.engine()
        engine.request_full_sync()
        batch = engine.begin_next_batch(2)
        outputs = {"Clothes": [self.staged()]}
        with (patch.object(engine.worker, "submit", side_effect=RuntimeError("worker unavailable")),
              patch("plugins.stou_texture_sync.pipeline.time.perf_counter", side_effect=[2, 2.02, 2.07])):
            self.submit(engine, batch, outputs)
        record = json.loads(Path(engine.config.log_path).read_text(encoding="utf-8"))
        self.assertEqual(record["outcome"], "failed")
        self.assertAlmostEqual(record["validate_s"], 0.02)
        self.assertEqual(record["stall_ms"], 70)

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
        status = engine.status_text
        self.assertTrue(status.startswith("已暂停"))
        self.assertIn("部分文件已发布", status)
        self.assertIn("完整同步", status)
        self.assertNotIn("立即同步", status)
        engine.set_realtime_enabled(False)
        self.assertEqual(engine.status_text, status)
        self.assertEqual(engine.request_immediate_sync(), (False, "请先执行完整同步以恢复"))
        self.assertEqual(engine.status_text, status)
        self.assertTrue(engine.request_full_sync()[0])
        self.submit(engine, engine.begin_next_batch(3), {}, status="Error")
        self.assertTrue(engine.scheduler.requires_full_recovery)
        self.assertTrue(engine.status_text.startswith("已暂停"))
        self.assertIn("Painter 导出未成功", engine.status_text)
        self.assertIn("完整同步", engine.status_text)

    def test_target_lock_status_explains_recovery(self):
        for full in (False, True):
            with self.subTest(full=full):
                engine, _ = self.engine(enabled=True)
                if full:
                    engine.request_full_sync()
                else:
                    engine.record_texture_change("Clothes", None, 0, 10)
                target_lock = TargetLock(self.target)
                target_lock.acquire("other writer")
                try:
                    self.submit(engine, engine.begin_next_batch(1), {"Clothes": [self.staged()]})
                    self.assertEqual(self.wait_result(engine).state, "target_locked")
                finally:
                    target_lock.release()
                self.assertTrue(engine.status_text.startswith("已暂停"))
                self.assertIn("释放目标目录", engine.status_text)
                self.assertIn("完整同步" if full else "立即同步", engine.status_text)
                self.assertNotIn("立即同步" if full else "完整同步", engine.status_text)
                self.assertNotIn("UE", engine.status_text)
                self.assertEqual(engine.scheduler.requires_full_recovery, full)
                self.assertEqual(engine.request_immediate_sync()[0], not full)

    def test_realtime_toggle_does_not_cancel_manual_worker(self):
        entered, proceed = threading.Event(), threading.Event()

        def replace(temp, target):
            entered.set()
            if not proceed.wait(3):
                raise RuntimeError("publication test did not release worker")
            os.replace(temp, target)

        engine, _ = self.engine(replace=replace, enabled=True)
        engine.request_full_sync()
        batch = engine.begin_next_batch(0)
        self.submit(engine, batch, {"Clothes": [self.staged(), self.staged("Normal")]})
        try:
            self.assertTrue(entered.wait(2))
            self.assertTrue(engine.set_realtime_enabled(False))
            self.assertTrue(engine.scheduler.is_current(batch))
        finally:
            proceed.set()
        self.assertEqual(self.wait_result(engine).state, "file_published")
        self.assertEqual(engine.state, "file_published")
        self.assertFalse(engine.scheduler.requires_full_recovery)

    def test_cancel_mid_publish_keeps_partial_result_and_limits_recovery_to_its_project(self):
        for switch_project in (False, True):
            with self.subTest(switch_project=switch_project), TemporaryDirectory(dir=self.root) as sandbox:
                locked = threading.Event()
                scratch = Path(sandbox)
                target = scratch / "target"
                target.mkdir()

                def replace(temp, destination):
                    if Path(destination).name.endswith("_Normal.png"):
                        locked.set()
                        raise PermissionError("source busy")
                    os.replace(temp, destination)

                engine, _ = self.engine(replace=replace, enabled=True, target_dir=str(target),
                                        log_path=str(scratch / "sync.jsonl"), publish_retry_delay_s=30)
                engine.record_texture_change("Clothes", (0, 0), 1, 10)
                batch = engine.begin_next_batch(2)
                self.submit(engine, batch, {"Clothes": [self.staged(), self.staged("Normal")]})
                self.assertTrue(locked.wait(2))
                if switch_project:
                    engine.on_project_changed("project-b")
                else:
                    engine.set_realtime_enabled(False)
                self.assertEqual(self.wait_result(engine).state, "partial_publication")
                record = json.loads(Path(engine.config.log_path).read_text(encoding="utf-8"))
                self.assertEqual(record["outcome"], "partial_publication")
                self.assertIsNotNone(record["files"][0]["published_at"])
                self.assertIsNone(record["files"][1]["published_at"])
                self.assertFalse(list(target.glob("*.stou-tmp")))
                self.assertEqual(engine.scheduler.requires_full_recovery, not switch_project)
                if switch_project:
                    self.assertIsNone(engine.scheduler.paused_reason)
                    self.assertEqual(engine.state, "idle")
                else:
                    self.assertEqual(engine.state, "paused")
                    self.assertIn("完整同步", engine.status_text)
                    self.assertFalse(engine.request_immediate_sync()[0])
                    engine.request_full_sync()
                    self.assertTrue(engine.scheduler.requires_full_recovery)

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


class PainterControllerTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        package = plugin.__name__
        painter = ModuleType("substance_painter")
        painter.__path__ = []
        modules = {painter.__name__: painter}
        for name in ("event", "export", "project", "textureset", "ui"):
            module = Mock()
            setattr(painter, name, module)
            modules[f"substance_painter.{name}"] = module
        self.painter = painter
        self.qt = SimpleNamespace(
            QObject=object, QTimer=Mock(), QCoreApplication=Mock(),
            QEventLoop=SimpleNamespace(ProcessEventsFlag=SimpleNamespace(AllEvents=0)),
            Qt=SimpleNamespace(TimerType=SimpleNamespace(PreciseTimer=0)),
        )
        modules["PySide6"] = SimpleNamespace(QtCore=self.qt)
        modules[f"{package}.panel"] = SimpleNamespace(SyncPanel=Mock())
        self.enterContext(patch.dict(sys.modules, modules))
        spec = importlib.util.spec_from_file_location(
            f"{package}.host_painter", Path(config.__file__).with_name("host_painter.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[spec.name] = module

        painter.project.get_uuid.return_value = "project-a"
        painter.project.is_busy.return_value = False
        painter.textureset.all_texture_sets.return_value = []
        painter.export.list_project_textures.side_effect = self._list
        painter.export.export_project_textures.side_effect = self._export
        settings = config.parse_config(self.settings(enabled=True, throttle_ms=None))
        self.enterContext(patch.object(plugin, "_STATE", SimpleNamespace(config_override=settings)))
        self.addCleanup(plugin.close_plugin)
        plugin.start_plugin()
        self.controller = plugin.get_controller()
        self.controller._resolve_preset = Mock(return_value=("resource://Demo", [TEMPLATE]))
        self.controller._refresh_project()

    def _list(self, export_config):
        path = Path(export_config["exportPath"]) / "T_Clothes_Albedo.png"
        return {("Clothes", ""): [str(path)]}

    def _export(self, export_config):
        outputs = self._list(export_config)
        Path(outputs["Clothes", ""][0]).write_bytes(b"texture")
        status = self.painter.export.ExportStatus.Success
        status.name = "Success"
        return SimpleNamespace(status=status, message="", textures=outputs)

    def test_queued_toggle_runs_before_submit_and_tick_cannot_reenter(self):
        controller = self.controller
        texture_set = self.painter.textureset.Stack.return_value.material.return_value
        texture_set.name.return_value = "Clothes"
        texture_set.has_uv_tiles.return_value = True
        controller.engine.record_texture_change("Clothes", None, 1, 10)
        batch = controller.engine.begin_next_batch(2)
        controller.engine.record_texture_change("Clothes", (1, 0), 3, 12)
        controller._on_texture(SimpleNamespace(stack_id=1, tile_indices=(2, 0)))

        def process_events(_flags):
            self.assertFalse(controller._in_export_call)
            self.assertIs(controller._pending_submission[0], batch)
            controller.set_realtime_enabled(False)
            controller._tick()
            self.assertIs(controller.engine.scheduler.running_batch(), batch)

        self.qt.QCoreApplication.processEvents.side_effect = process_events
        with patch.object(controller.engine.worker, "submit") as submit:
            controller._run_export(batch)
            submit.assert_not_called()
        self.qt.QCoreApplication.processEvents.assert_called_once()
        self.painter.export.export_project_textures.assert_called_once()
        self.assertIsNone(controller._pending_submission)
        record = json.loads(Path(controller.config.log_path).read_text(encoding="utf-8"))
        self.assertEqual(record["outcome"], "stale_before_publish")
        controller._tick()
        self.assertEqual(controller.engine.scheduler.pending(), {})
        self.assertFalse(controller.request_immediate_sync()[0])
        with patch("plugins.stou_texture_sync.host_painter.time.time", return_value=14):
            controller._on_texture(SimpleNamespace(stack_id=1, tile_indices=(3, 0)))
        controller._tick()
        self.assertTrue(controller.request_immediate_sync()[0])
        manual = controller.engine.begin_next_batch(4)
        self.assertEqual((manual.trigger, manual.scope), ("immediate", {"Clothes": {(3, 0)}}))
        self.assertEqual((controller.engine._metrics.first_change_wall,
                          controller.engine._metrics.last_change_wall), (14, 14))
        self.assertFalse(list(self.target.iterdir()))

    def test_queued_project_switch_discards_old_export(self):
        controller = self.controller
        self.assertTrue(controller.request_full_sync()[0])
        batch = controller.engine.begin_next_batch(0)

        def switch(_flags):
            controller._on_project_left(None)
            self.painter.project.get_uuid.return_value = "project-b"
            controller._on_project_ready(None)

        self.qt.QCoreApplication.processEvents.side_effect = switch
        with patch.object(controller.engine.worker, "submit") as submit:
            controller._run_export(batch)
            submit.assert_not_called()
        self.assertEqual(controller.engine.scheduler.project_id, "project-b")
        self.assertIsNone(controller.engine.scheduler.paused_reason)
        self.assertFalse(list(self.target.iterdir()))

    def test_close_and_restart_discard_pending_submission(self):
        old = self.controller
        old.request_full_sync()
        batch = old.engine.begin_next_batch(0)
        dispatcher = self.painter.event.DISPATCHER
        registered = dispatcher.connect.call_count

        def restart(_flags):
            stage = Path(old._pending_submission[1]["stage_dir"])
            self.assertTrue(stage.is_dir())
            plugin.close_plugin()
            self.assertFalse(stage.exists())
            plugin.start_plugin()

        self.qt.QCoreApplication.processEvents.side_effect = restart
        with patch.object(old.engine, "submit_export_result") as submit:
            old._run_export(batch)
            submit.assert_not_called()
        self.assertIsNone(old._pending_submission)
        self.assertIsNot(plugin.get_controller(), old)
        self.assertEqual(dispatcher.disconnect.call_count, registered)
        self.assertEqual(dispatcher.connect.call_count, 2 * registered)
        self.assertFalse(list(self.target.iterdir()))

    def test_waiting_for_edits_and_host_busy_have_distinct_statuses(self):
        controller = self.controller
        controller.engine.record_texture_change("Clothes", None, time.perf_counter(), 10)
        self.assertEqual(controller.engine.status_text, "等待修改结束")
        controller._busy = True
        controller._tick()
        self.assertEqual(controller.panel.set_status.call_args.args[0], "待同步：Painter 正忙")
