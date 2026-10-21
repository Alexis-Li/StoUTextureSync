"""Create the 12 source images the Auto Reimport probes start from.

Usage: python ue_make_sources.py <source_image_dir> [--size 1024]

Writes T_C00_<Part>_<Map>.png for the parts Face / Body / Arm / Leg and the maps
Albedo / ARM / Normal (the names the live loop publishes for the `body_udim`
sample) as near-solid RGBA8 PNGs. Run it before the first editor start, which
imports these files into /Game/StoU.
"""

from __future__ import annotations

import argparse
import os

from png_tools import solid_png

PARTS = ("Face", "Body", "Arm", "Leg")
MAPS = ("Albedo", "ARM", "Normal")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir")
    parser.add_argument("--size", type=int, default=1024)
    args = parser.parse_args()
    os.makedirs(args.source_dir, exist_ok=True)
    for index, (part, map_name) in enumerate((p, m) for p in PARTS for m in MAPS):
        data = solid_png(args.size, args.size, (40 + index * 17, 90, 160, 255), noise_seed=index + 1)
        with open(os.path.join(args.source_dir, f"T_C00_{part}_{map_name}.png"), "wb") as handle:
            handle.write(data)
    print(f"wrote {len(PARTS) * len(MAPS)} images to {args.source_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
