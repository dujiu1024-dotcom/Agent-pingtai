#!/usr/bin/env python3
"""导出/导入往返：默认包不含 state，--with-state 才带库。"""

import pytest

# agency.py 模块已移除/重构，shell 脚本入口不存在。
pytestmark = pytest.mark.skipif(
    True, reason="agency.py CLI entrypoint removed; tests launch non-existent script"
)

import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*args):
    # 显式指向 dev 库：父进程（如 doctor 以 prod 运行）可能已设置 AGENCY_DB，
    # 不覆盖会让 init/export 读写不同环境的库。
    env = {**os.environ, "AGENCY_ENV": "dev", "AGENCY_ALLOW_CUSTOM_DB": "1",
           "AGENCY_DB": str(ROOT / "agency" / "dev-state" / "agency.db")}
    return subprocess.run([sys.executable, *args], cwd=ROOT, env=env,
                          capture_output=True, text=True)


class ExportImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # dev 库是未跟踪的运行数据；干净检出（CI/交付包）里不存在，
        # 先初始化，避免用例依赖开发机遗留文件。
        run("agency.py", "init")

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
