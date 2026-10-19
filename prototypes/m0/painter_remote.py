"""Send Python to a Painter started with --enable-remote-scripting.

Usage: python painter_remote.py <script.py> [--port 60041]
       python painter_remote.py -c "<code>"

Painter executes the code on its main thread. Results are not returned
reliably, so probes write JSON lines to their own log file.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.request


def run(code: str, port: int = 60041, timeout: float = 600.0) -> str:
    payload = json.dumps({"python": base64.b64encode(code.encode("utf-8")).decode()})
    request = urllib.request.Request(
        f"http://localhost:{port}/run.json", payload.encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("script", nargs="?")
    parser.add_argument("-c", dest="code")
    parser.add_argument("--port", type=int, default=60041)
    args = parser.parse_args()
    code = args.code if args.code is not None else open(args.script, encoding="utf-8").read()
    print(run(code, args.port))
    return 0


if __name__ == "__main__":
    sys.exit(main())
