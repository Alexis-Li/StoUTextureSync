"""Validate the standalone Painter scaffold and its local documentation links."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate(root: Path = ROOT) -> list[str]:
    errors = []
    for name in ("README.md", "README_CN.md", "AGENTS.md", "CONTEXT.md",
                 "CHANGELOG.md", "LICENSE", "plugins/stou_texture_sync/__init__.py"):
        if not (root / name).is_file():
            errors.append(f"missing {name}")
    entry = root / "plugins/stou_texture_sync/__init__.py"
    if entry.is_file():
        try:
            tree = ast.parse(entry.read_text(encoding="utf-8-sig"))
            functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
            for name in ("start_plugin", "close_plugin"):
                if name not in functions:
                    errors.append(f"missing Painter entry point: {name}")
            versions = [node.value.value for node in tree.body
                        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                        and any(isinstance(t, ast.Name) and t.id == "__version__"
                                for t in node.targets)]
            if len(versions) != 1 or not re.fullmatch(r"\d+\.\d+\.\d+", str(versions[0])):
                errors.append("entry point must declare one semantic version")
        except (SyntaxError, UnicodeError) as exc:
            errors.append(f"invalid Painter entry point: {exc}")
    for path in root.rglob("*.md"):
        if any(part in {".git", ".tmp", ".venv"} for part in path.relative_to(root).parts):
            continue
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8-sig")):
            if "://" in target or target.startswith("#"):
                continue
            destination = (path.parent / target.split("#", 1)[0]).resolve()
            if not destination.is_relative_to(root.resolve()) or not destination.exists():
                errors.append(f"broken or external local link in {path.relative_to(root)}: {target}")
    return errors


if __name__ == "__main__":
    problems = validate()
    print("\n".join(problems) if problems else "Project scaffold: OK (host behavior unverified)")
    raise SystemExit(bool(problems))
