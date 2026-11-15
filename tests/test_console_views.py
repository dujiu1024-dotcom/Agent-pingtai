#!/usr/bin/env python3
"""控制台只读视图与写路径冒烟：覆盖 snapshot / 财务 / 订阅 / 事件 / 事实等分支。"""

import contextlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency  # noqa: E402
import console  # noqa: E402
import db as dbm  # noqa: E402


class ConsoleViewsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = dbm.DB_PATH
        self.orig_today = os.environ.get("AGENCY_TODAY")
        dbm.DB_PATH = Path(self.tmp.name) / "agency.db"
        os.environ["AGENCY_TODAY"] = "2026-11-12"
        with dbm.connect(dbm.DB_PATH) as con:
            dbm.migrate(con, verbose=False)
        self.tid = agency.create_task_record({
            "title": "控制台视图测试", "owner": "pm", "done_when": "cmd: true",
            "project": "OPC-002", "start": "2026-11-12", "due": "2026-11-20",
            "hours": 2, "dep": "—", "deliverable": "视图", "milestone": "M1",
        })

    def tearDown(self):
        dbm.DB_PATH = self.orig
        if self.orig_today is None:
            os.environ.pop("AGENCY_TODAY", None)
        else:
            os.environ["AGENCY_TODAY"] = self.orig_today
        self.tmp.cleanup()

    def test_readonly_views(self):
        with contextlib.closing(console.connect()) as con, con:
            con.execute("INSERT INTO facts(project_id, content, source_level,"
                        " source_ref, content_hash, state, created_at)"
                        " VALUES ('OPC-002', '客户说预算 5 万', 'S2', '访谈', 'h1',"
                        " 'active', ?)", (dbm.now(),))
            con.execute("INSERT INTO agent_runs(role, model, prompt_hash, tokens_in,"
                        " tokens_out, ok, created_at) VALUES ('pm', 'm', 'h', 10, 5, 1, ?)",
                        (dbm.now(),))
            con.execute("INSERT INTO dead_letter(role, error, attempts, created_at)"
                        " VALUES ('pm', '超时', 1, ?)", (dbm.now(),))
            con.execute("INSERT INTO subscriptions(event_type, target, action, enabled,"
                        " created_at) VALUES ('task_closed', 'feishu', 'notify', 1, ?)",
                        (dbm.now(),))
        state = console.snapshot()
        self.assertIn("tasks", state)
        self.assertTrue(console.agent_roster())
        self.assertTrue(console.event_list(5) is not None)
        self.assertTrue(console.facts_view(5) is not None)
        self.assertTrue(console.finance_view() is not None)
        self.assertTrue(console.subscriptions_view() is not None)
        self.assertIsInstance(console.sync_status(), dict)
        self.assertTrue(console.milestone_groups(agency.read_tasks()))
        self.assertTrue(console.jobs_view(5) is not None)
        console._trim_jobs(30)

    def test_create_update_close_paths(self):
        with self.assertRaises(ValueError):
            console.create_task({"title": "", "owner": "pm", "done_when": "x"})
        with self.assertRaises(ValueError):
            console.create_task({"title": "t", "owner": "longyi",
                                 "done_when": "x", "start": "2026-11-12",
                                 "due": "2026-11-13"})
        with self.assertRaises(ValueError):
            console.create_task({"title": "t", "owner": "pm", "done_when": "x",
                                 "start": "2026-11-15", "due": "2026-11-13"})
        tid = console.create_task({
            "title": "控制台新建", "owner": "pm", "done_when": "cmd: true",
            "start": "2026-11-12", "due": "2026-11-18", "hours": 1})
        console.update_task(tid, {"due": "2026-11-19", "hours": 2})
        with self.assertRaises(ValueError):
            console.update_task(tid, {})
        with self.assertRaises(ValueError):
            console.update_task("T-9999", {"due": "2026-11-19"})
        with self.assertRaises(ValueError):
            console.close_task(tid, {})
        console.close_task(tid, {"evidence": "视图测试", "verify": "true"})
        with self.assertRaises(ValueError):
            console.close_task(tid, {"evidence": "重复", "verify": "true"})

    def test_start_run_and_toggle(self):
        original = console._job_worker
        console._job_worker = lambda jid, role, task, note: None
        try:
            with self.assertRaises(ValueError):
                console.start_run("nobody", {})
            job = console.start_run("pm", {"task": self.tid, "note": "视图"})
            self.assertIn("id", job)
            console.jobs_view(5)
        finally:
            console._job_worker = original

    def test_diagnose_intent_all_branches(self):
        self.assertEqual(console.diagnose_intent({"instruction": "新建任务"})["data"]["intent"],
                         "create_task")
        with self.assertRaises(ValueError):
            console.diagnose_intent({"instruction": ""})
        with self.assertRaises(ValueError):
            console.diagnose_intent({"instruction": "长" * 501})
        data = console.diagnose_intent({"instruction": "报价单改一版"})["data"]
        self.assertEqual(data["suggested_roles"][0]["role"], "pm")


if __name__ == "__main__":
    unittest.main()
