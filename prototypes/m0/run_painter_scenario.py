"""Start a painter_probe scenario through remote scripting and wait for it.

Usage: python run_painter_scenario.py <scenario> --scratch <dir> [--timeout 900]
Prints the scenario's JSON lines when it finishes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from painter_remote import run

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario")
    parser.add_argument("--scratch", required=True)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--module", default="painter_probe")
    parser.add_argument("--kw", default="{}", help="JSON keyword arguments for the scenario")
    parser.add_argument("--log-name")
    args = parser.parse_args()
    scratch = os.path.abspath(args.scratch)
    os.makedirs(scratch, exist_ok=True)
    log_path = os.path.join(scratch, f"{args.log_name or args.scenario}.jsonl")
    if os.path.exists(log_path):
        os.remove(log_path)
    code = (
        "import sys, importlib\n"
        f"sys.path.insert(0, {HERE!r}) if {HERE!r} not in sys.path else None\n"
        f"import {args.module} as m\n"
        "importlib.reload(m)\n"
        "import json\n"
        f"m.start({args.scenario!r}, {scratch!r}, {log_path!r}, **json.loads({args.kw!r}))\n")
    run(code)
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        time.sleep(2)
        if not os.path.exists(log_path):
            continue
        with open(log_path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        kinds = [json.loads(line)["kind"] for line in lines if line.strip()]
        if "done" in kinds or "error" in kinds:
            print("\n".join(lines))
            return 0 if "error" not in kinds else 1
    print("timed out", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
