import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from stou_m0.naming import (NamingError, TextureSetNaming, final_name, map_type,  # noqa: E402
                            plan_names, udim_to_uv, uv_to_udim)
from stou_m0.publish import TEMP_SUFFIX, publish_batch, publish_file  # noqa: E402
from stou_m0.scheduler import WHOLE, Scheduler  # noqa: E402
from stou_m0.target_lock import LockBusy, TargetLock  # noqa: E402

TEMPLATES = ["T_$textureSet_Albedo(_$colorSpace)(_$udim)", "T_$textureSet_ARM(_$colorSpace)(_$udim)",
             "T_$textureSet_Normal(_$colorSpace)(_$udim)", "T_$textureSet_Emissive(_$colorSpace)(_$udim)"]
BODY = TextureSetNaming("C00_Body", "body_part", character_prefix="C00",
                        tile_parts={1001: "Face", 1002: "Body", 1003: "Arm", 1004: "Leg", 1005: "Nail"})


class NamingTest(unittest.TestCase):
    def test_issue_examples(self):
        self.assertEqual(final_name("T_C00_Body_Albedo_1001.png", TEMPLATES, BODY), "T_C00_Face_Albedo.png")
        self.assertEqual(final_name("T_C00_Body_ARM_1003.png", TEMPLATES, BODY), "T_C00_Arm_ARM.png")
        self.assertEqual(final_name("T_C00_Body_Albedo_sRGB_1001.png", TEMPLATES, BODY),
                         "T_C00_Face_Albedo_sRGB.png")

    def test_empty_prefix_drops_separator(self):
        naming = TextureSetNaming("Body", "body_part", tile_parts={1002: "Torso"})
        self.assertEqual(final_name("T_Body_Normal_1002.png", TEMPLATES, naming), "T_Torso_Normal.png")

    def test_texture_set_with_underscores_is_bound_not_guessed(self):
        naming = TextureSetNaming("C01_Clothes_02_Top")
        self.assertEqual(final_name("T_C01_Clothes_02_Top_ARM.png", TEMPLATES, naming),
                         "T_C01_Clothes_02_Top_ARM.png")
        self.assertEqual(final_name("T_C01_Clothes_02_Top_ARM_1001.png", TEMPLATES, naming),
                         "T_C01_Clothes_02_Top_ARM.png")

    def test_unknown_tile_and_wrong_set_are_rejected(self):
        with self.assertRaisesRegex(NamingError, "1006"):
            final_name("T_C00_Body_Albedo_1006.png", TEMPLATES, BODY)
        with self.assertRaisesRegex(NamingError, "does not match"):
            final_name("T_Other_Albedo_1001.png", TEMPLATES, BODY)

    def test_duplicate_final_names_block_the_plan(self):
        naming = TextureSetNaming("Coat")  # keep mode strips tiles: 1001 and 1002 collide
        with self.assertRaisesRegex(NamingError, "produced by both"):
            plan_names({"Coat": ["a/T_Coat_ARM_1001.png", "a/T_Coat_ARM_1002.png"]}, TEMPLATES,
                       {"Coat": naming})

    def test_duplicate_part_mapping_is_invalid(self):
        naming = TextureSetNaming("B", "body_part", tile_parts={1001: "Arm", 1002: "Arm"})
        with self.assertRaises(NamingError):
            final_name("T_B_ARM_1001.png", TEMPLATES, naming)

    def test_map_type_and_udim(self):
        self.assertEqual(map_type(TEMPLATES[1]), "ARM")
        self.assertEqual(udim_to_uv(1012), (1, 1))
        self.assertEqual(uv_to_udim(1, 0), 1002)


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.stage = os.path.join(self.tmp.name, "stage")
        self.target = os.path.join(self.tmp.name, "target")
        os.makedirs(self.stage)
        os.makedirs(self.target)

    def tearDown(self):
        self.tmp.cleanup()

    def staged(self, name, data):
        path = os.path.join(self.stage, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_publish_dedup_and_republish_after_external_change(self):
        files = {"T_A.png": self.staged("a", b"one"), "T_B.png": self.staged("b", b"two")}
        first = publish_batch(1, files, self.target)
        self.assertEqual(first.state, "file_published")
        second = publish_batch(2, files, self.target)
        self.assertEqual([f.status for f in second.files], ["skipped_same", "skipped_same"])
        with open(os.path.join(self.target, "T_A.png"), "wb") as handle:
            handle.write(b"edited outside")
        os.remove(os.path.join(self.target, "T_B.png"))
        third = publish_batch(3, files, self.target)
        self.assertEqual([f.status for f in third.files], ["published", "published"])
        self.assertEqual(sorted(os.listdir(self.target)), ["T_A.png", "T_B.png"])

    def test_partial_publication_is_reported_and_leaves_no_temp(self):
        calls = []

        def flaky_replace(src, dst):
            calls.append(dst)
            if dst.endswith("T_B.png"):
                raise PermissionError("in use")
            os.replace(src, dst)

        files = {"T_A.png": self.staged("a", b"one"), "T_B.png": self.staged("b", b"two")}
        result = publish_batch(1, files, self.target, replace=flaky_replace, retries=2, delay=0)
        self.assertEqual(result.state, "partial_publication")
        self.assertEqual(result.files[1].attempts, 2)
        self.assertFalse([n for n in os.listdir(self.target) if n.endswith(TEMP_SUFFIX)])

    def test_stale_batch_stops_before_publishing(self):
        files = {"T_A.png": self.staged("a", b"one")}
        result = publish_batch(1, files, self.target, is_current=lambda: False)
        self.assertEqual(result.state, "failed")
        self.assertFalse(os.path.exists(os.path.join(self.target, "T_A.png")))

    def test_replace_keeps_previous_target_on_failure(self):
        target = os.path.join(self.target, "T_A.png")
        with open(target, "wb") as handle:
            handle.write(b"old")

        def broken(src, dst):
            raise OSError("disk full")

        result = publish_file(self.staged("a", b"new"), target, replace=broken)
        self.assertEqual(result.status, "failed")
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), b"old")


class SchedulerTest(unittest.TestCase):
    def test_debounce_merge_and_serial(self):
        s = Scheduler(debounce=1.0)
        s.record("Body", (0, 0), now=0.0)
        s.record("Body", (1, 0), now=0.5)
        self.assertIsNone(s.next_batch(1.2))
        batch = s.next_batch(1.6)
        self.assertEqual(batch.scope, {"Body": {(0, 0), (1, 0)}})
        s.record("Body", (0, 1), now=1.7)
        self.assertIsNone(s.next_batch(5.0))  # one batch at a time
        s.finish(batch)
        self.assertEqual(s.next_batch(5.0).scope, {"Body": {(0, 1)}})

    def test_whole_set_absorbs_tiles(self):
        s = Scheduler(debounce=0)
        s.record("Coat", (0, 0), 0)
        s.record("Coat", None, 0)
        s.record("Coat", (1, 0), 0)
        self.assertEqual(s.next_batch(1).scope, {"Coat": WHOLE})

    def test_disable_invalidates_running_batch_and_stops_auto(self):
        s = Scheduler(debounce=0)
        s.record("Coat", (0, 0), 0)
        batch = s.next_batch(1)
        s.set_enabled(False)
        self.assertFalse(s.is_current(batch))
        s.finish(batch, failed=True)          # stale scope is not resurrected
        s.record("Coat", (0, 0), 2)
        self.assertIsNone(s.next_batch(3))     # automatic work stopped
        self.assertIsNotNone(s.next_batch(3, immediate=True))  # manual still works

    def test_full_sync_supersedes_pending(self):
        s = Scheduler(debounce=0)
        s.record("Coat", (0, 0), 0)
        s.request_full()
        batch = s.next_batch(1)
        self.assertTrue(batch.full)
        self.assertEqual(s.pending(), {})


class LockTest(unittest.TestCase):
    def test_second_process_blocked_then_reusable_after_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = ("import sys,time; sys.path.insert(0, %r); from stou_m0.target_lock import TargetLock;"
                    "l=TargetLock(%r); l.acquire('child'); print('locked', flush=True); time.sleep(30)"
                    % (os.path.dirname(HERE), tmp))
            child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "locked")
                with self.assertRaises(LockBusy):
                    TargetLock(tmp).acquire("parent")
            finally:
                child.kill()
                child.wait()
            lock = TargetLock(tmp)  # killed holder: the OS released the lock
            lock.acquire("parent")
            lock.release()


if __name__ == "__main__":
    unittest.main()
