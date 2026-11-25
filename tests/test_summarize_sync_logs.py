"""Exercise the log reporter with synthetic Painter and UE samples."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.summarize_sync_logs import find_local_path_leaks, summarize

TOOL = Path(__file__).resolve().parents[1] / "tools" / "summarize_sync_logs.py"


def batch(published, last_change, names, *, sample="", export_s=0.4):
    return {
        "schema": "stou.sync.v1", "kind": "batch", "sample": sample, "outcome": "file_published",
        "published_wall": published, "last_change_wall": last_change, "export_s": export_s,
        "wait_after_last_change_s": 1.0, "list_s": 0.05, "validate_s": 0.01,
        "publish_s": 0.1, "sp_side_s": 1.56, "stall_ms": 30,
        "files": [{"name": name, "status": status} for name, status in names],
    }


class SummaryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, name, records, encoding="utf-8"):
        path = self.root / name
        path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding=encoding)
        return path

    def run_tool(self, *args):
        return subprocess.run([sys.executable, str(TOOL), *map(str, args)],
                              capture_output=True, encoding="utf-8",
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})

    def test_json_markdown_and_ue_correlation(self):
        a, b = "T_Body_Color.png", "T_Body_Normal.png"
        sync = self.write("sync.jsonl", [
            batch(102, 100, [(a, "published"), (b, "published")], sample="body", export_s=0.5),
            batch(114, 110, [(a, "published"), (b, "skipped_same")], sample="body", export_s=1.1),
            batch(None, None, [("T_Coat.png", "skipped_same")]),
            {**batch(124, 122, [(a, "failed")], sample="body", export_s=0.7),
             "outcome": "failed"},
        ], encoding="utf-8-sig")
        ue = self.write("ue.jsonl", [
            {"kind": "reimport", "wall": 101, "asset": "T_Body_Color", "registry_md5": ["old"]},
            {"kind": "reimport", "wall": 104, "asset": "/Game/Test/T_Body_Color.T_Body_Color",
             "registry_md5": ["a"]},
            {"kind": "reimport", "wall": 104.25, "asset": "T_Body_Normal", "registry_md5": ["b"]},
            {"kind": "reimport", "wall": 105.25, "asset": "T_Body_Normal", "registry_md5": ["b"]},
            {"kind": "reimport", "wall": 116, "asset": "T_Body_Color", "registry_md5": ["c"]},
            {"kind": "reimport", "wall": 130, "asset": "T_Unrelated", "registry_md5": ["z"]},
        ])
        idle = self.write("idle.jsonl", [{"kind": "idle", "sample": "body", "stall_ms": [10, 15, 21]},
                                        {"kind": "idle", "sample": "body", "stall_ms": 12}])
        output = self.root / "summary.json"
        args = ["--sync-log", sync, "--ue-log", ue, "--idle-log", idle, "--strict-sanitize"]
        result = self.run_tool(*args, "--out", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        data = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(data["sanitized"])
        self.assertEqual(data["target_s"], 5.0)
        info = data["samples"]["body"]
        self.assertEqual(info["outcomes"], {"file_published": 2, "failed": 1})
        self.assertEqual(info["timings"]["export_s"], {"n": 3, "median": 0.7, "p95": 1.1, "max": 1.1})
        self.assertEqual(info["idle_stall_ms"], {"n": 4, "median": 13.5, "p95": 21, "max": 21})
        self.assertEqual(info["files"], {"published": 3, "skipped_same": 1, "failed": 1})
        self.assertEqual(info["ue_reimport_s"], {"n": 2, "median": 2.125, "p95": 2.25, "max": 2.25})
        self.assertEqual(info["e2e_s"], {"n": 2, "median": 5.125, "p95": 6, "max": 6})
        self.assertEqual(info["target"], {"pass": 1, "fail": 1, "measured": 2, "unmatched": 0})
        self.assertEqual(data["samples"]["default"]["batches"], 1)

        result = self.run_tool(*args, "--markdown")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("## body", result.stdout)
        self.assertIn("| e2e_s | 2 | 5.125 | 6 | 6 |", result.stdout)
        self.assertIn("Within 5 s: 1/2", result.stdout)

    def test_later_reimport_cannot_complete_an_earlier_batch(self):
        a, b = "T_Body.png", "T_Arm.png"
        batches = [batch(10, 8, [(a, "published"), (b, "published")]),
                   batch(20, 17, [(a, "published")])]
        events = [{"kind": "reimport", "wall": 11, "asset": "T_Arm", "registry_md5": ["1"]},
                  {"kind": "reimport", "wall": 21, "asset": "T_Body", "registry_md5": ["2"]}]
        info = summarize(batches, events, target_s=4.0)["samples"]["default"]
        self.assertEqual(info["e2e_s"]["n"], 1)
        self.assertEqual(info["e2e_s"]["max"], 4)
        self.assertEqual(info["target"], {"pass": 1, "fail": 0, "measured": 1, "unmatched": 1})

    def test_each_file_matches_from_its_own_publication_time(self):
        batch_record = batch(12.0, 9.0, [("T_Body.png", "published"), ("T_Arm.png", "published")])
        for file, wall in zip(batch_record["files"], (10.0, 12.0)):
            file["published_at"] = wall
        events = [{"kind": "reimport", "wall": 11.0, "asset": "T_Body"},
                  {"kind": "reimport", "wall": 13.5, "asset": "T_Arm"}]
        info = summarize([batch_record], events)["samples"]["default"]
        self.assertEqual(info["target"]["unmatched"], 0)
        self.assertEqual(info["ue_reimport_s"]["max"], 1.5)

    def test_duplicate_receipt_does_not_confirm_a_changed_file(self):
        name = "T_Body.png"
        first = batch(10, 9, [(name, "published")], sample="body")
        second = batch(20, 19, [(name, "published")], sample="body")
        first["files"][0]["sha"] = "sha_1"
        second["files"][0]["sha"] = "sha_2"
        events = [{"kind": "reimport", "wall": 11, "asset": "T_Body", "registry_md5": "M1"},
                  {"kind": "reimport", "wall": 21, "asset": "/Game/Test/T_Body.T_Body",
                   "registry_md5": ["M1"]},
                  {"kind": "reimport", "wall": 23, "asset": "T_Body", "registry_md5": "M2"}]
        info = summarize([first, second], events)["samples"]["body"]
        self.assertEqual(info["ue_reimport_s"], {"n": 2, "median": 2, "p95": 3, "max": 3})
        info = summarize([first, second], events[:2])["samples"]["body"]
        self.assertEqual(info["target"]["unmatched"], 1)
        self.assertEqual(info["ue_reimport_s"]["max"], 1)
        del events[1]["registry_md5"]
        info = summarize([first, second], events[:2])["samples"]["body"]
        self.assertEqual(info["target"]["unmatched"], 1)

    def test_receipts_are_isolated_by_sample_for_the_same_asset(self):
        first = batch(10, 9, [("T_Body.png", "published")], sample="A")
        second = batch(11, 10, [("T_Body.png", "published")], sample="B")
        event = {"kind": "reimport", "wall": 12, "asset": "T_Body", "sample": "A"}
        samples = summarize([first, second], [event])["samples"]
        self.assertEqual(samples["A"]["ue_reimport_s"]["max"], 2)
        self.assertEqual(samples["A"]["target"]["unmatched"], 0)
        self.assertEqual(samples["B"]["target"]["unmatched"], 1)
        del event["sample"]
        samples = summarize([first, second], [event])["samples"]
        self.assertEqual([samples[sample]["target"]["unmatched"] for sample in ("A", "B")], [1, 1])

    def test_older_registry_hash_only_confirms_a_republication_of_the_same_bytes(self):
        batches = [batch(wall, wall - 1, [("T_Body.png", "published")], sample="body")
                   for wall in (10, 20, 30)]
        for record, sha in zip(batches, ("S1", "S2", "S3")):
            record["files"][0]["sha"] = sha
        events = [{"kind": "reimport", "wall": wall, "asset": "T_Body", "registry_md5": registry}
                  for wall, registry in ((11, "X"), (22, "Y"), (33, "X"), (34, "Z"))]

        info = summarize(batches, events[:3])["samples"]["body"]
        self.assertEqual(info["target"]["unmatched"], 1)
        self.assertEqual(info["ue_reimport_s"], {"n": 2, "median": 1.5, "p95": 2, "max": 2})

        info = summarize(batches, events)["samples"]["body"]
        self.assertEqual(info["target"]["unmatched"], 0)
        self.assertEqual(info["ue_reimport_s"]["max"], 4)

        batches[2]["files"][0]["sha"] = "S1"
        info = summarize(batches, events[:3])["samples"]["body"]
        self.assertEqual(info["target"]["unmatched"], 0)
        self.assertEqual(info["ue_reimport_s"]["max"], 3)

    def test_unlabeled_receipt_routes_only_to_a_sample_already_published(self):
        batches = [batch(10, 9, [("T_Body.png", "published")], sample="A"),
                   batch(20, 19, [("T_Body.png", "published")], sample="B")]
        early = {"kind": "reimport", "wall": 11, "asset": "T_Body"}
        late = {**early, "wall": 22}
        for events, unmatched, latest in (
            ([early], (0, 1), (1, None)),
            ([late], (1, 1), (None, None)),
            ([early, {**late, "sample": "B"}], (0, 0), (1, 2)),
        ):
            with self.subTest(events=events):
                samples = summarize(batches, events)["samples"]
                self.assertEqual(tuple(samples[s]["target"]["unmatched"] for s in ("A", "B")), unmatched)
                self.assertEqual(tuple(samples[s]["ue_reimport_s"]["max"] for s in ("A", "B")), latest)

    def test_strict_sanitize_checks_nested_keys_and_values_without_echoing_paths(self):
        clean = {"kind": "batch", "sample": "clean", "path": "/Game/StoU/T_Test"}
        leaks = {"kind": "batch", "sample": "/Users/Private Name/project",
                 "details": [{r"C:\secret\file": r"failed opening E:\secret\Texture.png"},
                             {"source": r"\\fileserver\user\project.png"}]}
        self.assertEqual(find_local_path_leaks([clean]), [])
        self.assertEqual(len(find_local_path_leaks([leaks])), 4)
        sync = self.write("sync.jsonl", [leaks])
        result = self.run_tool("--sync-log", sync, "--strict-sanitize")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["sanitized"])
        self.assertNotIn("Private Name", result.stdout)
        self.assertNotIn("secret", result.stdout)

    def test_malformed_jsonl_and_output_over_input_are_errors(self):
        path = self.root / "bad.jsonl"
        path.write_text('{}\n{broken}\n', encoding="utf-8")
        result = self.run_tool("--sync-log", path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("line 2", result.stderr)
        result = self.run_tool("--sync-log", path, "--out", path)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(path.read_text(encoding="utf-8"), '{}\n{broken}\n')
