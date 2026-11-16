from __future__ import annotations

import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from agent_platform.extensions.package import extract_extension_zip


class ExtensionPackageExtractionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.archive = self.root / "extension.zip"
        self.destination = self.root / "installed" / "extension"

    def _write_zip(self, entries):
        with zipfile.ZipFile(self.archive, "w") as package:
            for name, content in entries:
                package.writestr(name, content)

    def test_extracts_files_with_owner_only_permissions_and_digest(self):
        self._write_zip([("manifest.json", b'{"id":"sample-extension"}'),
                         ("plugin/main.py", b"def run(): return 1")])
        report = extract_extension_zip(self.archive, self.destination)
        self.assertEqual(report.files_extracted, 2)
        self.assertEqual(report.uncompressed_bytes, 49)
        self.assertEqual(len(report.archive_sha256), 64)
        self.assertEqual(stat.S_IMODE((self.destination / "plugin/main.py").stat().st_mode),
                         0o600)

    def test_rejects_traversal_absolute_and_drive_paths(self):
        for name in ("../escape.py", "/tmp/escape.py", "C:/escape.py", "plugin/../escape.py"):
            with self.subTest(name=name):
                self._write_zip([(name, b"bad")])
                with self.assertRaisesRegex(ValueError, "unsafe|absolute|drive"):
                    extract_extension_zip(self.archive, self.destination)
                self.assertFalse(self.destination.exists())

    def test_rejects_symlinks_duplicate_paths_and_case_collisions(self):
        with zipfile.ZipFile(self.archive, "w") as package:
            symlink = zipfile.ZipInfo("plugin/link")
            symlink.create_system = 3
            symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
            package.writestr(symlink, "target")
        with self.assertRaisesRegex(ValueError, "symlink"):
            extract_extension_zip(self.archive, self.destination)

        self._write_zip([("Plugin/main.py", b"a"), ("plugin/MAIN.py", b"b")])
        with self.assertRaisesRegex(ValueError, "case-colliding"):
            extract_extension_zip(self.archive, self.destination)

    def test_enforces_file_and_uncompressed_size_limits(self):
        self._write_zip([("one.txt", b"1234"), ("two.txt", b"5678")])
        with self.assertRaisesRegex(ValueError, "limits"):
            extract_extension_zip(self.archive, self.destination, max_files=1)
        with self.assertRaisesRegex(ValueError, "limits"):
            extract_extension_zip(self.archive, self.destination, max_uncompressed_bytes=4)

    def test_rejects_existing_destination_and_archive_symlink(self):
        self._write_zip([("main.py", b"ok")])
        self.destination.parent.mkdir(parents=True)
        self.destination.mkdir()
        with self.assertRaises(FileExistsError):
            extract_extension_zip(self.archive, self.destination)


if __name__ == "__main__":
    unittest.main()
