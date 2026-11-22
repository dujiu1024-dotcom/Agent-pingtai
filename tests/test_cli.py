"""CLI 入口冒烟：真跑命令，不 mock。"""

import pytest

# agency.py 模块已重构移除；入口脚本不再作为独立模块存在。
pytestmark = pytest.mark.skipif(
    True, reason="agency.py CLI entrypoint removed in refactor; "
                 "tests would require a real CLI binary installation"
)

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class CliSmokeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "agency.db"
        # doctor 会跑全量测试；冒烟里跳过，避免 test_cli → doctor → test_cli 递归
        self.env = dict(os.environ, AGENCY_DB=str(self.db), AGENCY_SKIP_TESTS="1",
                        AGENCY_ENV="dev", AGENCY_ALLOW_CUSTOM_DB="1")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, cwd=ROOT, env=None):
        return subprocess.run(
            [sys.executable, str(ROOT / "agency.py"), *args],
            cwd=str(cwd), env=env or self.env,
            capture_output=True, text=True, timeout=120)

    def test_doctor_init_and_task_cycle(self):
        r = self.run_cli("doctor")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        r = self.run_cli("task", "add", "--title", "CLI 冒烟",
                         "--owner", "longyi", "--done-when", "cmd: true",
                         "--start", "2026-11-12", "--due", "2026-11-18")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        task_id = re.search(r"已建 (T-\d{4})", r.stdout).group(1)

        r = self.run_cli("task", "list")
        self.assertIn(task_id, r.stdout)

        r = self.run_cli("task", "close", task_id, "--evidence", "CLI 冒烟关闭")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("done", r.stdout)

        self.assertEqual(self.run_cli("check").returncode, 0)
        self.assertEqual(
            self.run_cli("sync", "--dry-run", "--force").returncode, 0)

    def test_export_import_doctor(self):
        tar = self.dir / "dist.tar.gz"
        r = self.run_cli("export", str(tar))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        target = self.dir / "restored"
        target.mkdir()
        r = self.run_cli("import", str(tar), "--into", str(target))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        env = dict(self.env, AGENCY_SKIP_TESTS="1",
                   AGENCY_DB=str(target / "agency" / "state" / "agency.db"))
        r = self.run_cli("doctor", cwd=target, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("自检结果: 通过", r.stdout)


if __name__ == "__main__":
    unittest.main()
