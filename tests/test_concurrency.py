#!/usr/bin/env python3
"""并发写回归：两进程同时关同一任务、同时追加记忆。

判据（T-0083）：
- 同一条任务只能被一个进程关闭成功，另一个必须非零退出；
- task_closed 事件只能有 1 条；
- 两个进程追加的记忆都要在，不能丢更新。
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db as dbm  # noqa: E402


class ConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "agency.db"
        if dbm.DB_PATH.exists():
            shutil.copy2(dbm.DB_PATH, self.db)
        self.env = {**os.environ, "AGENCY_DB": str(self.db),
                    "AGENCY_ENV": "dev", "AGENCY_ALLOW_CUSTOM_DB": "1"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _add_task(self):
        cmd = [sys.executable, "agency.py", "task", "add", "--title", "并发关闭测试",
               "--owner", "pm", "--start", "2026-11-12", "--due", "2026-11-13",
               "--hours", "1", "--done-when", "cmd:true", "--project", "OPC-002"]
        out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                             env=self.env)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out.stdout.split("已建 ")[1].split("｜")[0].strip()

    def test_two_processes_close_same_task(self):
        tid = self._add_task()
        procs = [
            subprocess.Popen(
                [sys.executable, "agency.py", "task", "close", tid,
                 "--evidence", f"并发证据-{i}", "--verify", "true"],
                cwd=ROOT, env=self.env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True)
            for i in range(2)
        ]
        results = [p.communicate() for p in procs]
        codes = [p.returncode for p in procs]
        self.assertEqual(sorted(codes), [0, 1], results)

        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        status = con.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()[0]
        events = con.execute(
            "SELECT COUNT(*) FROM events WHERE type = 'task_closed' AND task_id = ?",
            (tid,)).fetchone()[0]
        con.close()
        self.assertEqual(status, "done")
        self.assertEqual(events, 1, "同一条任务被重复写入关闭事件")

    def test_two_processes_append_memory(self):
        con = sqlite3.connect(self.db)
        row = con.execute("SELECT id FROM tasks ORDER BY id LIMIT 1").fetchone()
        con.close()
        if row is None:
            self.skipTest("空库无任务（交付包默认状态），跳过并发写入用例")
        tid = row[0]
        code = (
            "import memory, sys\n"
            "memory.set_slot(sys.argv[2], 'pm', 'constraints', sys.argv[1], '并发测试')\n"
        )
        procs = [
            subprocess.Popen([sys.executable, "-c", code, f"约束-{i}", tid],
                             cwd=ROOT, env=self.env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
            for i in range(2)
        ]
        results = [p.communicate() for p in procs]
        self.assertEqual([p.returncode for p in procs], [0, 0], results)

        con = sqlite3.connect(self.db)
        row = con.execute(
            "SELECT constraints FROM wm_session WHERE task_id = ? AND role = ?",
            (tid, "pm")).fetchone()
        con.close()
        self.assertIsNotNone(row, "wm_session 未创建")
        items = json.loads(row[0])
        texts = {i.get("text") for i in items}
        self.assertEqual(texts, {"约束-0", "约束-1"}, "并发追加丢失更新")


if __name__ == "__main__":
    unittest.main()
