#!/usr/bin/env python3
"""CLI 离线子命令冒烟：brief/check/task/memory/facts/model/policy/gui/vector/wbs。"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency  # noqa: E402
import db as dbm  # noqa: E402


class CliOfflineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = dbm.DB_PATH
        self.orig_today = os.environ.get("AGENCY_TODAY")
        self.orig_env = {k: os.environ.get(k) for k in
                         ("AGENCY_ENV", "AGENCY_ALLOW_CUSTOM_DB")}
        os.environ["AGENCY_ENV"] = "dev"
        os.environ["AGENCY_ALLOW_CUSTOM_DB"] = "1"
        dbm.DB_PATH = Path(self.tmp.name) / "agency.db"
        os.environ["AGENCY_TODAY"] = "2026-11-12"
        with dbm.connect(dbm.DB_PATH) as con:
            dbm.migrate(con, verbose=False)
        self.tid = agency.create_task_record({
            "title": "CLI 冒烟", "owner": "pm", "done_when": "cmd: true",
            "project": "OPC-002", "start": "2026-11-12", "due": "2026-11-20",
            "hours": 2, "dep": "—", "deliverable": "冒烟", "milestone": "M1",
        })

    def tearDown(self):
        dbm.DB_PATH = self.orig
        for key, value in self.orig_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if self.orig_today is None:
            os.environ.pop("AGENCY_TODAY", None)
        else:
            os.environ["AGENCY_TODAY"] = self.orig_today
        self.tmp.cleanup()

    def run_cli(self, *argv):
        buf = io.StringIO()
        old = sys.argv
        sys.argv = ["agency.py", *argv]
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                agency.main()
        except SystemExit as exc:
            self.assertIn(exc.code, (None, 0), buf.getvalue()[-500:])
        finally:
            sys.argv = old
        return buf.getvalue()

    def test_views_and_stats(self):
        self.run_cli("brief")
        self.run_cli("check")
        self.run_cli("task", "list")
        self.run_cli("task", "stats")
        self.run_cli("wbs", "--file", str(Path(self.tmp.name) / "wbs.csv"))
        self.run_cli("model", "list")
        self.run_cli("model", "select", "--purpose", "generate")
        self.run_cli("policy", "show")
        self.run_cli("policy", "check")
        self.run_cli("policy", "explain", "--app", "com.apple.Safari",
                     "--action", "click")
        self.run_cli("gui", "list")
        self.run_cli("gui", "apps")
        self.run_cli("vector", "status")
        self.run_cli("deadletter", "list")
        self.run_cli("subscription", "list")

    def test_task_mutations(self):
        self.run_cli("task", "update", self.tid, "--due", "2026-11-25",
                     "--hours", "3")
        self.run_cli("task", "cancel", self.tid, "--reason", "冒烟作废")
        self.run_cli("task", "reopen", self.tid, "--reason", "冒烟重开")
        self.run_cli("task", "close", self.tid, "--evidence", "冒烟通过",
                     "--verify", "true")

    def test_idempotent_add_five_times(self):
        outs = [self.run_cli(
            "task", "add", "--title", "同日重复建单", "--owner", "pm",
            "--start", "2026-11-12", "--due", "2026-11-20",
            "--done-when", "cmd: true", "--project", "OPC-002")
            for _ in range(5)]
        self.assertIn("已建", outs[0])
        for line in outs[1:]:
            self.assertIn("已存在", line)
            self.assertIn("内容幂等", line)
        with dbm.connect(dbm.DB_PATH) as con:
            rows = con.execute(
                "SELECT id, COUNT(*) AS n FROM tasks WHERE title = '同日重复建单'"
                " GROUP BY id").fetchall()
        self.assertEqual(len(rows), 1, "重复请求不应生成多条任务")
        self.assertEqual(rows[0]["n"], 1)

    def test_memory_and_facts(self):
        self.run_cli("memory", "set", self.tid, "pm", "--slot", "goal",
                     "--text", "目标 A", "--source", "冒烟")
        self.run_cli("memory", "show", self.tid, "pm")
        self.run_cli("facts", "add", "客户预算 5 万", "--level", "S2",
                     "--source", "冒烟访谈")
        self.run_cli("facts", "list")
        self.run_cli("subscription", "add", "task_closed",
                     "--target", "feishu", "--action", "notify")
        self.run_cli("archive", "--days", "30", "--dry-run")
        self.run_cli("export", str(Path(self.tmp.name) / "pack.tgz"))


if __name__ == "__main__":
    unittest.main()
