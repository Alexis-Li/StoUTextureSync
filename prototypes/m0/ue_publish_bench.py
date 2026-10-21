"""Measure route A: publish source images and time UE Auto Reimport (starts the editor with ue_probe.py).

Usage: python ue_publish_bench.py --source <dir> --log <ue_probe.jsonl> --commands <dir>
           --uproject <StoUProbe.uproject> --ue-exe <UnrealEditor.exe> --out <results.jsonl>
           [--plan default|quick|focus|serve|offline] [--focus-log <foreground.jsonl>]

Each trial publishes new content for a set of source images and waits for the
matching OnAssetReimport records. It reports per-trial first/last reimport
latency from the last file replacement, missing or duplicate imports, whether
the recorded source MD5 equals the published file, and the foreground window
before and after (to detect focus theft): `ue_foreground_before` is UE already in
front when the trial starts, `ue_became_foreground` is UE in front afterwards but
not before, and `ue_foreground_seen` is UE in front at any 50 ms sample between
publication and the end of the trial. The `focus` plan holds Painter in front
before each trial; `serve` with `--focus-log` records every foreground change
while an end-to-end run is active.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import statistics
import sys
import threading
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from png_tools import solid_png  # noqa: E402
from stou_m0.publish import publish_file  # noqa: E402
from ue_make_project import write_project  # noqa: E402

import subprocess  # noqa: E402

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def window_of(process_name: str):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if process_of_pid(pid.value).lower() == process_name.lower():
            found.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    return found[0] if found else None


def process_of_pid(pid: int) -> str:
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(512)
        kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        return os.path.basename(buf.value)
    finally:
        kernel32.CloseHandle(handle)


def foreground_process() -> str:
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return process_of_pid(pid.value)


def bring_to_front(hwnd) -> None:
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.keybd_event(0x12, 0, 0, 0)  # ALT lets a background process change the foreground
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0x12, 0, 2, 0)


def minimize(hwnd) -> None:
    user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE


UE = "UnrealEditor.exe"
PAINTER = "Adobe Substance 3D Painter.exe"


def set_window_state(state: str) -> None:
    """Put UE in a state without taking focus from whoever is using the machine.

    "ue_background": UE visible but not the foreground window (focus untouched);
    "ue_minimized": UE minimized; "ue_restore": UE restored, focus untouched.
    Only "ue_foreground" takes focus, and only when explicitly planned.
    """
    ue = window_of(UE)
    if state == "ue_foreground":
        bring_to_front(ue)
    elif state in ("ue_background", "ue_restore"):
        if user32.IsIconic(ue):
            user32.ShowWindow(ue, 4)  # SW_SHOWNOACTIVATE
    elif state == "ue_minimized":
        user32.ShowWindow(ue, 7)  # SW_SHOWMINNOACTIVE
    time.sleep(1.5)


class FocusSampler:
    """Sample the foreground process every 50 ms in a background thread."""

    def __init__(self, path=None):
        self.path = path
        self.samples = 0
        self.seen = []
        self.changes = []
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self):
        last = None
        while not self._stop.is_set():
            current = foreground_process()
            self.samples += 1
            if current not in self.seen:
                self.seen.append(current)
            if current != last:
                change = {"wall": round(time.time(), 3), "foreground": current, "previous": last}
                self.changes.append(change)
                if self.path:
                    with open(self.path, "a", encoding="utf-8") as handle:
                        handle.write(json.dumps(change, ensure_ascii=False) + "\n")
                last = current
            time.sleep(0.05)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(2)
        return self


class ProbeLog:
    def __init__(self, path):
        self.path = path
        self.offset = 0

    def read_new(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as handle:
            handle.seek(self.offset)
            data = handle.read()
            self.offset = handle.tell()
        return [json.loads(line) for line in data.splitlines() if line.strip()]

    def skip_to_end(self):
        self.read_new()


def send(commands, cmd, **data):
    path = os.path.join(commands, f"{time.time():.6f}_{cmd}.json")
    with open(path + ".part", "w", encoding="utf-8") as handle:
        json.dump({"cmd": cmd, **data}, handle)
    os.replace(path + ".part", path)


def wait_for(log, predicate, timeout):
    deadline = time.time() + timeout
    seen = []
    while time.time() < deadline:
        seen.extend(log.read_new())
        if predicate(seen):
            return seen
        time.sleep(0.05)
    return seen


_SEED = [int(time.time()) % 1000]


def make_content(size):
    _SEED[0] += 1
    seed = _SEED[0]
    color = ((seed * 53) % 256, (seed * 97) % 256, (seed * 31) % 256, 255)
    return solid_png(size, size, color, noise_seed=seed)


def trial(args, log, label, names, mode="atomic", size=1024, timeout=40.0, stage_dir=None,
          holder=None):
    stage_dir = stage_dir or os.path.join(os.path.dirname(args.log), "bench_stage")
    os.makedirs(stage_dir, exist_ok=True)
    staged, md5 = {}, {}
    for name in names:
        data = make_content(size)
        path = os.path.join(stage_dir, name)
        with open(path, "wb") as handle:
            handle.write(data)
        staged[name] = path
        md5[name] = hashlib.md5(data).hexdigest()
    if holder:
        hwnd = window_of(holder)
        if hwnd:
            bring_to_front(hwnd)
        time.sleep(1.0)
    log.skip_to_end()
    fg_before = foreground_process()
    sampler = FocusSampler().start()
    try:
        start = time.time()
        if mode == "atomic":
            for name in names:
                publish_file(staged[name], os.path.join(args.source, name))
        elif mode == "direct_slow":
            # A writer that pauses mid-file longer than the import threshold.
            for name in names:
                with open(staged[name], "rb") as handle:
                    data = handle.read()
                with open(os.path.join(args.source, name), "wb") as handle:
                    handle.write(data[: len(data) // 2])
                    handle.flush()
                    time.sleep(args.slow_pause)
                    handle.write(data[len(data) // 2:])
        published = time.time()
        assets = {os.path.splitext(n)[0] for n in names}

        def complete(records):
            done = {r["asset"] for r in records if r["kind"] == "reimport"}
            return assets <= done

        records = wait_for(log, complete, timeout)
        time.sleep(1.0)
        records.extend(log.read_new())
        fg_after = foreground_process()
    finally:
        sampler.stop()
    # UE 5.7 broadcasts OnAssetReimport twice per texture import; count distinct imports.
    unique = {}
    for r in records:
        if r["kind"] == "reimport" and r.get("asset") in assets:
            unique.setdefault((r["asset"], tuple(r.get("registry_md5") or ())), r)
    reimports = list(unique.values())
    per_asset = {}
    for r in reimports:
        per_asset.setdefault(r["asset"], []).append(r)
    md5_match = {}
    for asset, items in per_asset.items():
        recorded = (items[-1].get("registry_md5") or [None])[0]
        md5_match[asset] = (recorded == md5[asset + ".png"]) if recorded else None
    latencies = [round(r["wall"] - published, 3) for r in reimports]
    result = {
        "label": label, "mode": mode, "files": len(names), "size": size,
        "publish_s": round(published - start, 3),
        "first_s": min(latencies, default=None), "last_s": max(latencies, default=None),
        "missing": sorted(assets - set(per_asset)),
        "duplicates": {a: len(v) for a, v in per_asset.items() if len(v) > 1},
        "md5_match": None if not md5_match else
        ("all" if all(md5_match.values()) else
         "unknown" if all(v is None for v in md5_match.values()) else md5_match),
        "ue_foreground_before": fg_before == UE,
        "ue_became_foreground": fg_before != UE and fg_after == UE,
        "ue_foreground_seen": UE in sampler.seen,
        "foreground_before": fg_before, "foreground_after": fg_after,
        "foreground_seen": sampler.seen, "foreground_samples": sampler.samples,
        "holder": holder, "holder_front_before": bool(holder) and fg_before == holder,
        "errors": [r for r in records if r["kind"] not in ("reimport", "post_import", "command")][:5],
        "settings_kept": sorted({(r.get("srgb"), r.get("compression_settings")) for r in reimports}),
        "dirty_count": len(reimports[-1].get("dirty", [])) if reimports else None,
    }
    with open(args.out, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in ("errors",)}, ensure_ascii=False))
    return result


class Editor:
    """Restart the official editor with a given Auto Reimport configuration."""

    def __init__(self, args, log):
        self.args, self.log = args, log
        self.process = None

    def start(self, threshold=1.0, throttle=True, detect_on_startup=True):
        self.stop()
        project_dir = os.path.dirname(self.args.uproject)
        write_project(project_dir, self.args.source, threshold, detect_on_startup, throttle)
        saved = os.path.join(project_dir, "Saved", "Config", "WindowsEditor",
                             "EditorPerProjectUserSettings.ini")
        if os.path.exists(saved):
            os.remove(saved)
        env = dict(os.environ, STOU_PROBE_SOURCE=self.args.source, STOU_PROBE_LOG=self.args.log,
                   STOU_PROBE_COMMANDS=self.args.commands)
        probe = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ue_probe.py")
        self.log.skip_to_end()
        started = time.time()
        self.process = subprocess.Popen(
            [self.args.ue_exe, self.args.uproject, f'-ExecCmds=py {probe}', "-nosplash"], env=env)
        records = wait_for(self.log, lambda rs: any(r["kind"] == "ready" for r in rs), 300)
        ready = [r for r in records if r["kind"] == "ready"]
        startup = [r for r in records if r["kind"] == "reimport"]
        info = {"threshold": threshold, "throttle": throttle, "detect_on_startup": detect_on_startup,
                "ready": bool(ready), "startup_s": round(time.time() - started, 1),
                "reimports_before_ready": [r["asset"] for r in startup]}
        print("EDITOR", json.dumps(info), flush=True)
        time.sleep(5)  # let startup scanning and async texture builds settle
        late = [r["asset"] for r in self.log.read_new() if r["kind"] == "reimport"]
        if late:
            print("EDITOR startup reimports", late)
        return info

    def stop(self):
        if self.process is None:
            return
        send(self.args.commands, "quit")
        try:
            self.process.wait(120)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.process = None


def _write(args, name, data):
    stage = os.path.join(os.path.dirname(args.log), "bench_stage")
    os.makedirs(stage, exist_ok=True)
    path = os.path.join(stage, name)
    with open(path, "wb") as handle:
        handle.write(data)
    publish_file(path, os.path.join(args.source, name))
    return hashlib.md5(data).hexdigest()


def _report(args, log):
    send(args.commands, "report")
    records = wait_for(log, lambda rs: any(r["kind"] == "report" for r in rs), 30)
    report = [r for r in records if r["kind"] == "report"][-1]
    return {t["source"]: (t.get("registry_md5") or [None])[0] for t in report["textures"]}, report


def offline_plan(args, log, editor, names):
    """POC-05: changes while UE is closed, restart detection, dirty state and full-sync replay."""
    target = [n for n in names if "_Leg_" in n][:2]
    out = {}
    for detect in (True, False):
        editor.start(threshold=1.0, throttle=True, detect_on_startup=detect)
        md5_online = {n: _write(args, n, make_content(1024)) for n in target}
        wait_for(log, lambda rs: sum(r["kind"] == "reimport" for r in rs) >= 2 * len(target), 30)
        time.sleep(2)
        _, report = _report(args, log)
        dirty_before_quit = report["dirty"]
        quit_started = time.time()
        editor.stop()  # quits without saving the reimported packages
        quit_s = round(time.time() - quit_started, 1)
        md5_offline = {n: _write(args, n, make_content(1024)) for n in target}
        info = editor.start(threshold=1.0, throttle=True, detect_on_startup=detect)
        time.sleep(8)
        recorded, report = _report(args, log)
        state = {n: ("offline" if recorded.get(n) == md5_offline[n] else
                     "online_content" if recorded.get(n) == md5_online[n] else "older")
                 for n in target}
        # Full sync: republish byte-identical content (dedup bypassed).
        log.skip_to_end()
        for n in target:
            with open(os.path.join(args.source, n), "rb") as handle:
                data = handle.read()
            _write(args, n, data)
        replay = wait_for(log, lambda rs: sum(r["kind"] == "reimport" for r in rs) >= 2 * len(target), 20)
        replay_assets = sorted({r["asset"] for r in replay if r["kind"] == "reimport"})
        time.sleep(1)
        recorded_after, _ = _report(args, log)
        out[f"detect_on_startup={detect}"] = {
            "dirty_before_quit": dirty_before_quit, "quit_s": quit_s,
            "startup_reimports": info["reimports_before_ready"],
            "recorded_after_restart": state,
            "identical_republish_reimported": replay_assets,
            "after_full_sync_matches_disk": all(
                recorded_after.get(n) == md5_offline[n] for n in target),
        }
        print("OFFLINE", json.dumps(out[f"detect_on_startup={detect}"]), flush=True)
    editor.stop()
    with open(args.out, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"offline": out}) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--commands", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--plan", default="default")
    parser.add_argument("--uproject", required=True)
    parser.add_argument("--ue-exe", required=True)
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--slow-pause", type=float, default=2.5)
    parser.add_argument("--threshold", type=float, default=1.0)
    parser.add_argument("--no-throttle", action="store_true")
    parser.add_argument("--focus-log", help="serve: record foreground changes to this JSONL file")
    args = parser.parse_args()
    log = ProbeLog(args.log)
    names = sorted(n for n in os.listdir(args.source) if n.lower().endswith(".png"))
    one_part = [n for n in names if "_Face_" in n] or names[:3]
    single = one_part[:1]

    editor = Editor(args, log)

    def settings(threshold=1.0, throttle=True):
        editor.start(threshold=threshold, throttle=throttle)

    summary = {}

    def series(label, files, state, reps, **kwargs):
        set_window_state(state)
        results = [trial(args, log, f"{label}#{i}", files, **kwargs) for i in range(reps)]
        lasts = [r["last_s"] for r in results if r["last_s"] is not None]
        summary[label] = {
            "n": len(results), "complete": sum(1 for r in results if not r["missing"]),
            "median_last_s": statistics.median(lasts) if lasts else None,
            "max_last_s": max(lasts, default=None),
            "focus_changed": sum(1 for r in results if r["foreground_before"] != r["foreground_after"]),
            "ue_became_foreground": sum(1 for r in results if r["ue_became_foreground"]),
            "ue_foreground_seen": sum(1 for r in results if r["ue_foreground_seen"]),
            "holder_front_before": sum(1 for r in results if r["holder_front_before"]),
        }
        print("SERIES", label, json.dumps(summary[label]))

    if args.plan == "serve":
        # Keep one configured editor running for end-to-end runs until <commands>/stop_serve exists.
        editor.start(threshold=args.threshold, throttle=not args.no_throttle)
        sampler = FocusSampler(args.focus_log).start() if args.focus_log else None
        stop_file = os.path.join(args.commands, "stop_serve")
        try:
            while not os.path.exists(stop_file):
                time.sleep(1)
            os.remove(stop_file)
        finally:
            if sampler:
                sampler.stop()
        if sampler:
            print("FOCUS", json.dumps({"samples": sampler.samples, "seen": sampler.seen,
                                       "changes": len(sampler.changes)}))
        editor.stop()
    elif args.plan == "offline":
        offline_plan(args, log, editor, names)
    elif args.plan == "focus":
        # R-004: does a background reimport take the foreground from Painter?
        # Painter is brought to front before each trial; UE stays visible behind it or
        # minimized. The UE-foreground series is the control.
        settings(threshold=args.threshold, throttle=not args.no_throttle)
        for files_label, files, reps in (("part3", one_part, args.reps), ("all12", names, 5)):
            series(f"focus_{files_label}_bg", files, "ue_background", reps, holder=PAINTER)
            series(f"focus_{files_label}_min", files, "ue_minimized", reps, holder=PAINTER)
            series(f"focus_{files_label}_uefront", files, "ue_foreground", reps)
        editor.stop()
    elif args.plan == "quick":
        settings(threshold=1.0, throttle=True)
        series("quick_part_bg", one_part, "ue_background", 2)
        editor.stop()
    elif args.plan == "default":
        settings(threshold=1.0, throttle=True)
        series("part3_bg_thr1_throttle", one_part, "ue_background", args.reps)
        series("part3_min_thr1_throttle", one_part, "ue_minimized", args.reps)
        series("direct_slow_bg_throttle", single, "ue_background", 2, mode="direct_slow")
        settings(threshold=1.0, throttle=False)
        series("part3_bg_thr1_nothrottle", one_part, "ue_background", args.reps)
        series("part3_min_thr1_nothrottle", one_part, "ue_minimized", args.reps)
        series("all12_bg_thr1_nothrottle", names, "ue_background", args.reps)
        series("all12_2k_bg_thr1_nothrottle", names, "ue_background", 3, size=2048)
        settings(threshold=0.3, throttle=False)
        series("part3_bg_thr03_nothrottle", one_part, "ue_background", args.reps)
        series("single_bg_thr03_nothrottle", single, "ue_background", args.reps)
        editor.stop()
    print("SUMMARY", json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
