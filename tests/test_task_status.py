#!/usr/bin/env python3
"""状态模型：close 验收门禁 / cancel / reopen / 完成率口径"""

import argparse
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

TODAY = "2026-11-11"


class TaskStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_db_path = dbm.DB_PATH
        self.orig_today = os.environ.get("AGENCY_TODAY")
        dbm.DB_PATH = Path(self.tmp.name) / "agency.db"
        os.environ["AGENCY_TODAY"] = TODAY
        with contextlib.closing(agency.db()) as con, con:
            con.execute(
                "INSERT INTO tasks(id, title, owner, status, start_at, due_at,"
                " done_when, evidence, kind, dep, deliverable, milestone, created_at)"
                " VALUES ('T-0001', '测试任务', 'longyi', 'todo', ?, ?, ?, '',"
                " 'general', '—', '产出.md', '—', ?)",
                (TODAY, TODAY, "cmd: true", dbm.now()))

    def tearDown(self):
        dbm.DB_PATH = self.orig_db_path
        if self.orig_today is None:
            os.environ.pop("AGENCY_TODAY", None)
        else:
            os.environ["AGENCY_TODAY"] = self.orig_today
        self.tmp.cleanup()

    def call(self, func, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                func(argparse.Namespace(**kw))
                code = 0
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
                if isinstance(exc.code, str):
                    buf.write(exc.code)
        return code, buf.getvalue()

    def status(self):
        with contextlib.closing(agency.db()) as con:
            return con.execute("SELECT * FROM tasks WHERE id='T-0001'").fetchone()

    def test_close_requires_verify(self):
        with contextlib.closing(agency.db()) as con, con:
            con.execute("UPDATE tasks SET done_when='有产出' WHERE id='T-0001'")
        code, out = self.call(agency.cmd_task_close, id="T-0001",
                              evidence="已交付", verify=None)
        self.assertEqual(code, 1)
        self.assertIn("可执行验收命令", out)
        self.assertEqual(self.status()["status"], "todo")

    def test_close_rejects_failed_verify(self):
        code, out = self.call(agency.cmd_task_close, id="T-0001",
                              evidence="已交付", verify="exit 3")
        self.assertEqual(code, 1)
        self.assertIn("退出码 3", out)
        self.assertEqual(self.status()["status"], "todo")

    def test_close_with_passing_verify(self):
        code, out = self.call(agency.cmd_task_close, id="T-0001",
                              evidence="已交付", verify="true")
        self.assertEqual(code, 0, out)
        row = self.status()
        self.assertEqual(row["status"], "done")
        self.assertIn("exit 0", row["evidence"])

    def test_close_echoes_command_and_exit_code(self):
        code, out = self.call(agency.cmd_task_close, id="T-0001",
                              evidence="已交付", verify="true")
        self.assertEqual(code, 0, out)
        self.assertIn("验收命令：true", out)
        self.assertIn("退出码：0", out)

    def test_add_rejects_weak_test_s_command(self):
        code, out = self.call(
            agency.cmd_task_add, title="弱验收", project="", owner="longyi",
            start=TODAY, due=TODAY, done_when="cmd: test -s README.md",
            kind="general", hours=None, dep="", deliverable="", milestone="")
        self.assertEqual(code, 1)
        self.assertIn("test -s", out)
        self.assertEqual(len(agency.read_tasks()), 1)

    def test_close_rejects_glob_matching_multiple_files(self):
        code, out = self.call(agency.cmd_task_close, id="T-0001",
                              evidence="已交付", verify="grep -q x tests/*.py")
        self.assertEqual(code, 1)
        self.assertIn("glob", out)
        self.assertEqual(self.status()["status"], "todo")

    def test_cancel_requires_reason_and_is_not_done(self):
        code, out = self.call(agency.cmd_task_cancel, id="T-0001", reason="")
        self.assertEqual(code, 1)
        self.assertIn("必须写原因", out)
        code, out = self.call(agency.cmd_task_cancel, id="T-0001",
                              reason="范围变更，由新方案替代")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status()["status"], "canceled")

    def test_reopen_requires_reason_and_counts(self):
        self.call(agency.cmd_task_close, id="T-0001", evidence="已交付", verify="true")
        code, out = self.call(agency.cmd_task_reopen, id="T-0001", reason="")
        self.assertEqual(code, 1)
        code, out = self.call(agency.cmd_task_reopen, id="T-0001",
                              reason="验收发现缺项")
        self.assertEqual(code, 0, out)
        row = self.status()
        self.assertEqual(row["status"], "todo")
        self.assertEqual(row["reopen_count"], 1)
        self.assertIn("验收发现缺项", row["reopen_reason"])

    def test_completion_rate_excludes_canceled(self):
        self.call(agency.cmd_task_cancel, id="T-0001", reason="作废")
        stats = agency.completion_stats(agency.read_tasks())
        self.assertEqual(stats["canceled"], 1)
        self.assertEqual(stats["base"], 0)
        self.assertEqual(stats["rate"], 0.0)


class TaskDedupProjectTest(unittest.TestCase):
    """去重指纹必须含项目：不同项目的同名任务不能互相吞并。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_db_path = dbm.DB_PATH
        dbm.DB_PATH = Path(self.tmp.name) / "agency.db"

    def tearDown(self):
        dbm.DB_PATH = self.orig_db_path
        self.tmp.cleanup()

    def add(self, project):
        return agency.create_task_record({
            "title": "整理需求说明", "owner": "pm", "project": project,
            "start": TODAY, "due": "2026-11-18", "done_when": "完成",
        })

    def test_same_title_different_projects_do_not_collide(self):
        first = self.add("SC-001")
        second = self.add("SC-002")
        self.assertNotEqual(first, second)
        self.assertEqual(len(agency.read_tasks()), 2)

    def test_same_project_same_content_is_idempotent(self):
        first = self.add("SC-001")
        again = self.add("SC-001")
        self.assertEqual(first, again)
        self.assertEqual(len(agency.read_tasks()), 1)


class ProjectScopeStatsTest(unittest.TestCase):
    """完成率按项目口径：计划外项目不能混进公司完成率。"""

    @staticmethod
    def row(project, status, hours=1.0):
        return {"project": project, "db_status": status, "hours": hours,
                "reopen_count": 0}

    def test_unmanaged_projects_are_split_out(self):
        tasks = [self.row("OPC-002", "done"), self.row("OPC-002", "todo"),
                 self.row("SC-001", "done"), self.row("SC-002", "todo")]
        inside, outside = agency.split_by_scope(tasks)
        self.assertEqual({t["project"] for t in inside}, {"OPC-002"})
        self.assertEqual({t["project"] for t in outside}, {"SC-001", "SC-002"})

        stats = agency.completion_stats(inside)
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["rate"], 50.0)


if __name__ == "__main__":
    unittest.main()
