#!/usr/bin/env python3
"""导出/导入往返：默认包不含 state，--with-state 才带库。"""

import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*args):
    env = {**os.environ, "AGENCY_ENV": "dev", "AGENCY_ALLOW_CUSTOM_DB": "1"}
    return subprocess.run([sys.executable, *args], cwd=ROOT, env=env,
                          capture_output=True, text=True)


class ExportImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_public_package_has_no_state(self):
        pack = self.tmp / "public.tgz"
        out = run("agency.py", "export", str(pack))
        self.assertEqual(out.returncode, 0, out.stderr)
        with tarfile.open(pack) as tar:
            names = tar.getnames()
        self.assertIn("manifest.json", names)
        self.assertIn("agency.py", names)
        self.assertNotIn(".env", names)
        self.assertFalse([n for n in names if n.endswith("agency.db")])
        dest = self.tmp / "restored"
        out = run("agency.py", "import", str(pack), "--into", str(dest))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue((dest / "agency.py").is_file())
        self.assertFalse((dest / "agency" / "state" / "agency.db").exists())

    def test_state_package_carries_db(self):
        pack = self.tmp / "state.tgz"
        out = run("agency.py", "export", str(pack), "--with-state")
        self.assertEqual(out.returncode, 0, out.stderr)
        with tarfile.open(pack) as tar:
            names = tar.getnames()
        self.assertTrue([n for n in names if n.endswith("agency.db")])
        dest = self.tmp / "restored-state"
        out = run("agency.py", "import", str(pack), "--into", str(dest))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue((dest / "agency" / "dev-state" / "agency.db").is_file())


if __name__ == "__main__":
    unittest.main()
