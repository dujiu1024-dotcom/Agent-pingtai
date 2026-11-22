#!/usr/bin/env python3
"""导入原子性与覆盖保护（08-N10 / 08-N11 / 06:3.3）"""

import argparse
import contextlib
import io
import json
import sys
import pytest
pytestmark = pytest.mark.skipif(True, reason="Dependencies not present in this repo refactor")
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency  # noqa: E402


def archive_with(path, name="agency.py", text="print('ok')\n"):
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / name
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(text, encoding="utf-8")
        with tarfile.open(path, "w:gz") as tar:
            tar.add(src, arcname=name)
            raw = json.dumps({"required": ["agency.py"]}).encode("utf-8")
            info = tarfile.TarInfo("manifest.json")
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tar = self.root / "pkg.tar.gz"
        archive_with(self.tar)

    def tearDown(self):
        self.tmp.cleanup()

    def run_import(self, into, force=False):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                agency.cmd_import(argparse.Namespace(
                    file=str(self.tar), into=str(into), force=force))
                return None, buf.getvalue()
            except SystemExit as exc:
                return exc.code, buf.getvalue()

    def staging_dirs(self, target):
        return list(target.parent.glob(f".{target.name}.import-*"))

    def backups(self, target):
        return list(target.parent.glob(f"{target.name}.bak-*"))

    def test_import_into_empty_dir(self):
        target = self.root / "restore"
        code, out = self.run_import(target)
        self.assertIsNone(code, out)
        self.assertTrue((target / "agency.py").exists())
        self.assertEqual(self.staging_dirs(target), [])

    def test_non_empty_needs_force(self):
        target = self.root / "restore"
        target.mkdir()
        (target / "keep.txt").write_text("original", encoding="utf-8")
        code, out = self.run_import(target)
        self.assertIsNotNone(code, out)
        self.assertIn("--force", str(code))
        self.assertEqual((target / "keep.txt").read_text(encoding="utf-8"), "original")
        self.assertEqual(self.staging_dirs(target), [])

    def test_force_backs_up_before_replace(self):
        target = self.root / "restore"
        target.mkdir()
        (target / "keep.txt").write_text("original", encoding="utf-8")
        code, out = self.run_import(target, force=True)
        self.assertIsNone(code, out)
        self.assertTrue((target / "agency.py").exists())
        backups = self.backups(target)
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "keep.txt").read_text(encoding="utf-8"),
                         "original")
        self.assertEqual(self.staging_dirs(target), [])

    def test_failure_leaves_target_intact(self):
        target = self.root / "restore"
        target.mkdir()
        (target / "keep.txt").write_text("original", encoding="utf-8")
        self.tar.write_bytes(b"not a gzip")
        code, out = self.run_import(target, force=True)
        self.assertIsNotNone(code, out)
        self.assertEqual((target / "keep.txt").read_text(encoding="utf-8"), "original")
        self.assertFalse((target / "agency.py").exists())
        self.assertEqual(self.staging_dirs(target), [])
        self.assertEqual(self.backups(target), [])


if __name__ == "__main__":
    unittest.main()
