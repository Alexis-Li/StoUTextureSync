"""Show or set the product version.

The Painter entry point's ``__version__`` is the single product version.
``set`` rewrites it and moves the CHANGELOG ``Unreleased`` entries under a
heading for the new version, leaving an empty ``Unreleased`` section on top.
"""

from __future__ import annotations

import argparse
import datetime
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = Path("plugins/stou_texture_sync/__init__.py")
CHANGELOG = Path("CHANGELOG.md")
SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
VERSION_LINE = re.compile(r'^__version__ = "([^"]*)"$', re.MULTILINE)
UNRELEASED = "## Unreleased"


class VersionError(Exception):
    pass


def parse(version: str) -> tuple[int, int, int]:
    match = SEMVER.fullmatch(version)
    if not match:
        raise VersionError(f"not a MAJOR.MINOR.PATCH version: {version!r}")
    return tuple(int(part) for part in match.groups())


def read_version(root: Path = ROOT) -> str:
    text = (root / ENTRY).read_text(encoding="utf-8")
    found = VERSION_LINE.findall(text)
    if len(found) != 1:
        raise VersionError(f"{ENTRY.as_posix()} must contain exactly one __version__ line")
    parse(found[0])
    return found[0]


def release_changelog(text: str, version: str, date: str) -> str:
    """Move the Unreleased entries under ``## <version> - <date>``."""
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip() == UNRELEASED]
    if len(starts) != 1:
        raise VersionError(f"CHANGELOG.md must contain exactly one '{UNRELEASED}' heading")
    start = starts[0]
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    entries = "".join(lines[start + 1:end]).strip("\n")
    if not entries.strip():
        raise VersionError("CHANGELOG.md has no Unreleased entries to release")
    if any(re.match(rf"## {re.escape(version)}\b", line) for line in lines):
        raise VersionError(f"CHANGELOG.md already has a section for {version}")
    section = f"{UNRELEASED}\n\n## {version} - {date}\n\n{entries}\n"
    if end < len(lines):
        section += "\n"
    return "".join(lines[:start]) + section + "".join(lines[end:])


def set_version(new: str, root: Path = ROOT, date: str | None = None) -> str:
    """Set the product version; returns the previous one. Writes nothing on error."""
    old = read_version(root)
    if parse(new) <= parse(old):
        raise VersionError(f"new version {new} must be greater than {old}")
    entry_text = (root / ENTRY).read_text(encoding="utf-8")
    changelog_text = (root / CHANGELOG).read_text(encoding="utf-8")
    changelog_text = release_changelog(
        changelog_text, new, date or datetime.date.today().isoformat())
    entry_text = VERSION_LINE.sub(f'__version__ = "{new}"', entry_text)
    (root / ENTRY).write_text(entry_text, encoding="utf-8", newline="\n")
    (root / CHANGELOG).write_text(changelog_text, encoding="utf-8", newline="\n")
    return old


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command")
    setter = commands.add_parser("set", help="set a new version and release the changelog")
    setter.add_argument("version")
    args = parser.parse_args(argv)
    try:
        if args.command == "set":
            old = set_version(args.version)
            print(f"{old} -> {args.version}")
        else:
            print(read_version())
    except VersionError as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
