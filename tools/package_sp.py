"""Build the Painter installation archive StoUTextureSync-<version>-SP.zip.

The archive holds one top-level ``stou_texture_sync/`` folder, ready to be
extracted into Painter's ``python/plugins`` directory. It contains the runtime
Python files and the project LICENSE, nothing else. Bytecode caches are
skipped; any other file in the runtime folder, or syntax newer than Painter's
Python 3.11, stops the build so that nothing is shipped or dropped unnoticed.
Entries carry a fixed timestamp, and LICENSE is checked out with LF endings,
so the same sources produce the same archive bytes.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validate_project import validate  # noqa: E402
from version import ROOT, VersionError, read_version  # noqa: E402

PRODUCT = "StoUTextureSync"
PACKAGE = "stou_texture_sync"
RUNTIME_SUFFIXES = {".py"}
CACHE_DIRS = {"__pycache__"}
CACHE_SUFFIXES = {".pyc", ".pyo"}
ZIP_DATE = (1980, 1, 1, 0, 0, 0)
# Painter 11.0 runs Python 3.11; newer syntax is rejected even when the build
# interpreter accepts it (ast feature_version is best effort).
PAINTER_PYTHON = (3, 11)


class PackageError(Exception):
    pass


def archive_name(version: str) -> str:
    return f"{PRODUCT}-{version}-SP.zip"


def runtime_files(root: Path = ROOT) -> dict[str, Path]:
    """Map archive member names to source files; raise on unexpected files."""
    package_dir = root / "plugins" / PACKAGE
    members, unexpected = {}, []
    for path in sorted(package_dir.rglob("*")):
        relative = path.relative_to(package_dir)
        if path.is_dir() or CACHE_DIRS & set(relative.parts) or path.suffix in CACHE_SUFFIXES:
            continue
        if path.suffix not in RUNTIME_SUFFIXES:
            unexpected.append(relative.as_posix())
            continue
        try:
            source = path.read_bytes()
            compile(source, str(path), "exec", dont_inherit=True)
            ast.parse(source, str(path), feature_version=PAINTER_PYTHON)
        except (SyntaxError, ValueError) as exc:
            raise PackageError(f"cannot compile {relative.as_posix()}: {exc}") from exc
        members[f"{PACKAGE}/{relative.as_posix()}"] = path
    if unexpected:
        raise PackageError("unexpected files in the runtime folder: " + ", ".join(unexpected))
    members[f"{PACKAGE}/LICENSE"] = root / "LICENSE"
    return dict(sorted(members.items()))


def build(out_dir: Path, root: Path = ROOT) -> Path:
    """Write the archive into ``out_dir`` and return its path."""
    problems = validate(root)
    if problems:
        raise PackageError("project validation failed:\n" + "\n".join(problems))
    try:
        version = read_version(root)
    except VersionError as exc:
        raise PackageError(str(exc)) from exc
    members = runtime_files(root)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / archive_name(version)
    handle, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=out_dir)
    os.close(handle)
    try:
        with zipfile.ZipFile(temp_name, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, source in members.items():
                info = zipfile.ZipInfo(name, ZIP_DATE)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, source.read_bytes())
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist",
                        help="output directory (default: dist/)")
    args = parser.parse_args(argv)
    try:
        target = build(args.out)
    except PackageError as exc:
        parser.exit(1, f"error: {exc}\n")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
    print(f"{target.name}  sha256 {digest}")
    print("\n".join(f"  {name}" for name in names))
    print("Installation in Painter is not verified by this command.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
