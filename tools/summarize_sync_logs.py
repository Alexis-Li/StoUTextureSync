"""Summarize Painter batch, UE reimport and idle heartbeat JSONL logs."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugins.stou_texture_sync.pipeline import LOCAL_PATH_PATTERN  # noqa: E402

TIMINGS = ("wait_after_last_change_s", "list_s", "export_s", "validate_s",
           "publish_s", "sp_side_s", "stall_ms")
FILE_STATUSES = ("published", "skipped_same", "failed")


def read_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8-sig") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{path.name}, line {number}: {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{path.name}, line {number}: expected a JSON object")
            records.append(record)
    return records


def find_local_path_leaks(records) -> list[str]:
    """Return locations of suspected local paths without echoing their values."""
    leaks = []

    def visit(value, location):
        if isinstance(value, str) and LOCAL_PATH_PATTERN.search(value):
            leaks.append(location)
        elif isinstance(value, dict):
            for index, (key, child) in enumerate(value.items()):
                key_location = f"{location}.<key {index}>"
                if LOCAL_PATH_PATTERN.search(key):
                    leaks.append(key_location)
                else:
                    key_location = f"{location}.{key}"
                visit(child, key_location)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                visit(child, f"{location}[{index}]")

    for index, record in enumerate(records, 1):
        visit(record, f"record {index}")
    return leaks


def _number(value) -> bool:
    return type(value) in (int, float) and -sys.float_info.max <= value <= sys.float_info.max


def _stats(values) -> dict:
    values = sorted(value for value in values if _number(value))
    n = len(values)
    if not n:
        return dict(n=0, median=None, p95=None, max=None)
    return dict(n=n, median=statistics.median(values),
                p95=values[math.ceil(n * 0.95) - 1], max=values[-1])


def _label(value, default="default") -> str:
    return LOCAL_PATH_PATTERN.sub("<local path>", str(value or default))


def _sample(record: dict) -> str:
    return _label(record.get("sample"))


def _files(record: dict) -> list[dict]:
    files = record.get("files", [])
    if isinstance(files, dict):
        return [{"name": name, "status": status} for name, status in files.items()]
    return [item for item in files if isinstance(item, dict)] if isinstance(files, list) else []


def _asset_name(value, *, source_image=False) -> str:
    name = PurePosixPath(str(value or "").replace("\\", "/")).name
    return (PurePosixPath(name).stem if source_image else name.split(".", 1)[0]).casefold()


def _registry_hash(event) -> tuple[str, ...]:
    value = event.get("registry_md5")
    return tuple(str(item) for item in value) if isinstance(value, list) else (str(value or ""),)


def _correlate(batches, events) -> dict[int, float]:
    """Match first reimport receipts for each published asset before its next publication."""
    by_asset = defaultdict(list)
    for event in events:
        wall = event.get("wall")
        asset = _asset_name(event.get("asset"))
        if event.get("kind") == "reimport" and _number(wall) and asset:
            by_asset[asset].append(event)

    publications = defaultdict(list)
    expected = {}
    for index, batch in enumerate(batches):
        published = batch.get("published_wall")
        if not _number(published):
            continue
        assets = {_asset_name(file.get("name"), source_image=True) for file in _files(batch)
                  if file.get("status") == "published" and file.get("name")}
        if assets:
            expected[index] = assets
        for asset in assets:
            publications[asset].append((published, index, _sample(batch)))

    matched = defaultdict(dict)
    for asset, starts in publications.items():
        starts.sort()
        events = sorted(by_asset.get(asset, ()), key=lambda event: event["wall"])
        cursor = 0
        for index, (start, batch_index, sample) in enumerate(starts):
            end = starts[index + 1][0] if index + 1 < len(starts) else math.inf
            distinct = {}
            while cursor < len(events) and events[cursor]["wall"] < end:
                event = events[cursor]
                cursor += 1
                if event["wall"] >= start and (not event.get("sample") or _sample(event) == sample):
                    identity = (event["asset"], _registry_hash(event))
                    distinct.setdefault(identity, event["wall"])
            if distinct:
                matched[batch_index][asset] = min(distinct.values())

    return {index: max(matched[index].values()) for index, assets in expected.items()
            if matched[index].keys() == assets}


def summarize(sync_records, ue_records=None, idle_records=None, target_s: float = 5.0) -> dict:
    batches = [record for record in sync_records if record.get("kind") == "batch"]
    samples = defaultdict(list)
    for index, batch in enumerate(batches):
        samples[_sample(batch)].append(index)
    include_idle = idle_records is not None or any(r.get("kind") == "idle" for r in sync_records)
    idle_by_sample = defaultdict(list)
    for record in idle_records if idle_records is not None else sync_records:
        if idle_records is None and record.get("kind") != "idle":
            continue
        gaps = record.get("stall_ms")
        idle_by_sample[_sample(record)].extend(gaps if isinstance(gaps, list) else [gaps])
        samples.setdefault(_sample(record), [])

    completion = _correlate(batches, ue_records) if ue_records is not None else {}
    result = {"sanitized": not any(find_local_path_leaks(records)
                                   for records in (sync_records, ue_records or [], idle_records or [])),
              "target_s": target_s, "samples": {}}
    for sample, indices in sorted(samples.items()):
        records = [batches[index] for index in indices]
        counts = Counter(file.get("status") for record in records for file in _files(record))
        summary = {
            "batches": len(records),
            "outcomes": dict(sorted(Counter(_label(record.get("outcome"), "unknown")
                                            for record in records).items())),
            "timings": {key: _stats(record.get(key) for record in records) for key in TIMINGS},
            "files": {key: counts[key] for key in FILE_STATUSES},
        }
        if include_idle:
            summary["idle_stall_ms"] = _stats(idle_by_sample[sample])
        if ue_records is not None:
            matches = [index for index in indices if index in completion]
            ue_times = [completion[index] - batches[index]["published_wall"] for index in matches]
            e2e = [completion[index] - batches[index]["last_change_wall"] for index in matches
                   if _number(batches[index].get("last_change_wall"))]
            eligible = sum(any(file.get("status") == "published" for file in _files(record))
                           for record in records)
            passed = sum(value <= target_s for value in e2e)
            summary["ue_reimport_s"] = _stats(ue_times)
            summary["e2e_s"] = _stats(e2e)
            summary["target"] = {"pass": passed, "fail": len(e2e) - passed,
                                 "measured": len(e2e), "unmatched": eligible - len(matches)}
        result["samples"][sample] = summary
    return result


def _md(value) -> str:
    return str(value).replace("|", r"\|").replace("\n", " ").replace("\r", " ")


def render_markdown(summary: dict) -> str:
    lines = ["# Texture sync summary", "",
             f"Local path scan: {'passed' if summary['sanitized'] else 'paths found'}"]
    for sample, info in summary["samples"].items():
        outcomes = ", ".join(f"{_md(key)} {count}" for key, count in info["outcomes"].items())
        files = ", ".join(f"{key} {count}" for key, count in info["files"].items())
        lines.extend(["", f"## {_md(sample)}", "", f"Batches: {info['batches']}",
                      f"Outcomes: {outcomes or 'none'}", f"Files: {files}", "",
                      "| Metric | n | Median | P95 | Max |", "| --- | ---: | ---: | ---: | ---: |"])
        timings = info["timings"].copy()
        for key in ("idle_stall_ms", "ue_reimport_s", "e2e_s"):
            if key in info:
                timings[key] = info[key]
        for key, values in timings.items():
            row = ["—" if value is None else f"{value:.4g}" for value in values.values()]
            lines.append(f"| {key} | {' | '.join(row)} |")
        if "target" in info:
            target = info["target"]
            lines.extend(["", f"Within {summary['target_s']:g} s: {target['pass']}/{target['measured']} "
                          f"({target['fail']} above target, {target['unmatched']} unmatched batches)"])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sync-log", required=True, type=Path, help="Painter sync JSONL file")
    parser.add_argument("--ue-log", type=Path, help="optional UE reimport JSONL file")
    parser.add_argument("--idle-log", type=Path, help="optional idle heartbeat JSONL file")
    parser.add_argument("--target-s", type=float, default=5.0, help="end-to-end target in seconds")
    parser.add_argument("--out", type=Path, help="output file (default: stdout)")
    parser.add_argument("--markdown", action="store_true", help="write Markdown instead of JSON")
    parser.add_argument("--strict-sanitize", action="store_true",
                        help="exit with status 1 when any input contains a local path")
    args = parser.parse_args(argv)
    if not math.isfinite(args.target_s) or args.target_s < 0:
        parser.error("--target-s must be finite and >= 0")
    try:
        inputs = [args.sync_log, args.ue_log, args.idle_log]
        if args.out and any(args.out.resolve() == path.resolve() for path in inputs if path):
            raise ValueError("--out must be separate from the input logs")
        records = [read_jsonl(path) if path is not None else None for path in inputs]
        summary = summarize(*records, target_s=args.target_s)
        output = (render_markdown(summary) if args.markdown
                  else json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
        if args.out is not None:
            args.out.write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    return int(args.strict_sanitize and not summary["sanitized"])


if __name__ == "__main__":
    raise SystemExit(main())
