import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from package_sp import PackageError, archive_name, build  # noqa: E402
from version import VersionError, read_version, release_changelog, set_version  # noqa: E402

ENTRY = '"""Entry."""\n\n__version__ = "0.1.0"\n\n\ndef start_plugin():\n    pass\n\n\ndef close_plugin():\n    pass\n'
CHANGELOG = "# Changelog\n\n## Unreleased\n\n- Add packaging.\n\n## 0.1.0 - 2026-01-01\n\n- First.\n"


def make_project(root: Path) -> Path:
    for name in ("README.md", "README_CN.md", "AGENTS.md", "CONTEXT.md"):
        (root / name).write_text("# Doc\n", encoding="utf-8")
    (root / "LICENSE").write_text("GPL\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    package = root / "plugins" / "stou_texture_sync"
    package.mkdir(parents=True)
    (package / "__init__.py").write_bytes(ENTRY.encode("utf-8"))
    return package


class VersionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.package = make_project(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_repository_version_is_readable(self):
        self.assertRegex(read_version(REPO), r"^\d+\.\d+\.\d+$")

    def test_set_updates_entry_and_releases_changelog(self):
        self.assertEqual(set_version("0.2.0", self.root, date="2026-10-20"), "0.1.0")
        self.assertEqual(read_version(self.root), "0.2.0")
        self.assertEqual((self.root / "CHANGELOG.md").read_text(encoding="utf-8"),
                         "# Changelog\n\n## Unreleased\n\n## 0.2.0 - 2026-10-20\n\n- Add packaging.\n\n"
                         "## 0.1.0 - 2026-01-01\n\n- First.\n")

    def test_invalid_or_lower_versions_write_nothing(self):
        for bad in ("0.2", "v0.2.0", "0.02.0", "0.1.0", "0.0.9"):
            with self.subTest(bad), self.assertRaises(VersionError):
                set_version(bad, self.root)
        self.assertEqual(read_version(self.root), "0.1.0")
        self.assertEqual((self.root / "CHANGELOG.md").read_text(encoding="utf-8"), CHANGELOG)

    def test_empty_unreleased_section_is_refused(self):
        (self.root / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n## 0.1.0\n", encoding="utf-8")
        with self.assertRaises(VersionError):
            set_version("0.2.0", self.root)
        self.assertEqual(read_version(self.root), "0.1.0")

    def test_changelog_needs_one_unreleased_heading(self):
        with self.assertRaises(VersionError):
            release_changelog("# Changelog\n\n- loose\n", "0.2.0", "2026-10-20")

    def test_last_section_release(self):
        self.assertEqual(release_changelog("# C\n\n## Unreleased\n\n- A.\n", "1.0.0", "2026-10-20"),
                         "# C\n\n## Unreleased\n\n## 1.0.0 - 2026-10-20\n\n- A.\n")

    def test_duplicate_version_line_is_rejected(self):
        (self.package / "__init__.py").write_text(ENTRY + '__version__ = "0.1.0"\n', encoding="utf-8")
        with self.assertRaises(VersionError):
            read_version(self.root)


class PackageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.package = make_project(self.root)
        (self.package / "core").mkdir()
        (self.package / "core" / "__init__.py").write_text("", encoding="utf-8")
        (self.package / "__pycache__").mkdir()
        (self.package / "__pycache__" / "x.cpython-311.pyc").write_bytes(b"\0")
        self.out = Path(self.temp.name) / "dist"

    def tearDown(self):
        self.temp.cleanup()

    def test_archive_holds_runtime_files_and_license(self):
        target = build(self.out, self.root)
        self.assertEqual(target.name, archive_name("0.1.0"))
        self.assertEqual(target.name, "StoUTextureSync-0.1.0-SP.zip")
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(archive.namelist(), ["stou_texture_sync/LICENSE", "stou_texture_sync/__init__.py",
                                                  "stou_texture_sync/core/__init__.py"])
            self.assertEqual(archive.read("stou_texture_sync/__init__.py").decode("utf-8"), ENTRY)
        self.assertEqual([p.name for p in self.out.iterdir()], [target.name])

    def test_archive_is_reproducible(self):
        first = build(self.out, self.root).read_bytes()
        second = build(self.out, self.root).read_bytes()
        self.assertEqual(first, second)

    def test_unexpected_runtime_file_stops_the_build(self):
        (self.package / "notes.txt").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(PackageError, "notes.txt"):
            build(self.out, self.root)
        self.assertFalse(self.out.exists())

    def test_syntax_error_stops_the_build(self):
        (self.package / "broken.py").write_text("def (:\n", encoding="utf-8")
        with self.assertRaisesRegex(PackageError, "broken.py"):
            build(self.out, self.root)

    def test_invalid_project_stops_the_build(self):
        (self.root / "CONTEXT.md").unlink()
        with self.assertRaisesRegex(PackageError, "CONTEXT.md"):
            build(self.out, self.root)


class CommandLineTest(unittest.TestCase):
    def test_version_command_prints_repository_version(self):
        result = subprocess.run([sys.executable, str(REPO / "tools" / "version.py")],
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), read_version(REPO))

    def test_package_command_writes_archive(self):
        with tempfile.TemporaryDirectory() as out:
            result = subprocess.run([sys.executable, str(REPO / "tools" / "package_sp.py"), "--out", out],
                                    capture_output=True, text=True, check=True)
            name = archive_name(read_version(REPO))
            self.assertIn(name, result.stdout)
            self.assertTrue((Path(out) / name).is_file())


if __name__ == "__main__":
    unittest.main()
