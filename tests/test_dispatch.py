#!/usr/bin/env python3
"""调度层 dispatch：ready 判定、预算熔断、Agent 无关闭权限（分项 18.3）"""

import argparse
import contextlib
import datetime as dt
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


def day(n):
    return (dt.date(2026, 11, 11) + dt.timedelta(days=n)).isoformat()


def args(**kw):
    base = dict(project=None, limit=5, budget=None, dry_run=False)
    base.update(kw)
    return argparse.Namespace(**base)


class DispatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_db_path = dbm.DB_PATH
        self.orig_today = os.environ.get("AGENCY_TODAY")
        dbm.DB_PATH = Path(self.tmp.name) / "agency.db"
        os.environ["AGENCY_TODAY"] = TODAY

    def tearDown(self):
        dbm.DB_PATH = self.orig_db_path
        if self.orig_today is None:
            os.environ.pop("AGENCY_TODAY", None)
        else:
            os.environ["AGENCY_TODAY"] = self.orig_today
        self.tmp.cleanup()

    # ---------- 工具 ----------

    def add_task(self, tid, title, **kw):
        with contextlib.closing(agency.db()) as con, con:
            con.execute(
                "INSERT INTO tasks(id, title, owner, status, start_at, due_at,"
                " done_when, evidence, kind, dep, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, '', 'general', ?, ?)",
                (tid, title, kw.get("owner", "planner"), kw.get("status", "todo"),
                 kw.get("start", day(0)), kw.get("due", day(2)),
                 "完成标准", kw.get("dep", ""), dbm.now()))

    def run_dispatch(self, runner, **kw):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            try:
                agency.cmd_dispatch(args(**kw), runner=runner)
                code = None
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue() + err.getvalue()

    def snapshot(self):
        with contextlib.closing(agency.db()) as con:
            return {r["id"]: r["status"] for r in con.execute(
                "SELECT id, status FROM tasks")}

    def dead_letters(self):
        with contextlib.closing(agency.db()) as con:
            return con.execute("SELECT COUNT(*) AS n FROM dead_letter").fetchone()["n"]

    # ---------- ready 判定 ----------

    def test_ready_rules(self):
        self.add_task("T-0001", "窗口内无依赖")
        self.add_task("T-0002", "窗口内但依赖未关", dep="T-0003")
        self.add_task("T-0003", "依赖本身未到窗口", start=day(3), due=day(4))
        self.add_task("T-0004", "明天才开始", start=day(1), due=day(3))
        self.add_task("T-0005", "已完成", status="done")
        ready = agency.dispatch_ready(agency.read_tasks(),
                                      dt.date.fromisoformat(TODAY))
        self.assertEqual([t["id"] for t in ready], ["T-0001"])

    def test_dep_closed_unlocks(self):
        self.add_task("T-0001", "前置", status="done")
        self.add_task("T-0002", "后置", dep="T-0001")
        ready = agency.dispatch_ready(agency.read_tasks(),
                                      dt.date.fromisoformat(TODAY))
        self.assertEqual([t["id"] for t in ready], ["T-0002"])

    def test_dep_placeholders_are_not_dependencies(self):
        """回归 08-N04：dep 写 —/-/无/空 时不应被当成未关闭依赖。"""
        for index, placeholder in enumerate(("—", "-", "无", "空", "none", "null"), 1):
            self.add_task("T-%04d" % index, "占位符 %s" % placeholder,
                          dep=placeholder, due=day(index))
        ready = agency.dispatch_ready(agency.read_tasks(),
                                      dt.date.fromisoformat(TODAY))
        self.assertEqual([t["id"] for t in ready],
                         ["T-0001", "T-0002", "T-0003", "T-0004", "T-0005", "T-0006"])

    # ---------- 执行 ----------

    def test_success_runs_but_never_closes(self):
        self.add_task("T-0001", "派活用例")
        calls = []

        def runner(role, task):
            calls.append((role, task["id"]))
            return 120, None

        code, out = self.run_dispatch(runner)
        self.assertIsNone(code, out)
        self.assertEqual(calls, [("planner", "T-0001")])
        self.assertEqual(self.snapshot()["T-0001"], "review")
        self.assertIn("待验收", out)
        self.assertIn("成功 1", out)

    def test_dry_run_does_not_execute(self):
        self.add_task("T-0001", "预演用例")
        calls = []
        code, out = self.run_dispatch(lambda r, t: calls.append(t["id"]) or (1, None),
                                      dry_run=True)
        self.assertIsNone(code, out)
        self.assertEqual(calls, [])
        self.assertIn("预估", out)

    def test_budget_stops_before_execution(self):
        for i in range(1, 4):
            self.add_task("T-%04d" % i, "任务 %d" % i, due=day(i))
        calls = []

        def runner(role, task):
            calls.append(task["id"])
            return 2000, None

        code, out = self.run_dispatch(runner, budget=2500)
        self.assertIsNone(code, out)
        self.assertEqual(calls, ["T-0001"])
        self.assertIn("停止", out)
        self.assertIn("卡点", out)

    def test_failure_writes_dead_letter(self):
        self.add_task("T-0001", "失败用例")

        def runner(role, task):
            raise RuntimeError("模型超时")

        code, out = self.run_dispatch(runner)
        self.assertIsNone(code, out)
        self.assertEqual(self.dead_letters(), 1)
        self.assertIn("模型超时", out)
        self.assertEqual(self.snapshot()["T-0001"], "todo")

    def test_estimate_uses_role_history(self):
        with contextlib.closing(agency.db()) as con, con:
            for tokens in (500, 1500):
                con.execute(
                    "INSERT INTO agent_runs(role, model, prompt_hash, tokens_in,"
                    " tokens_out, ok, created_at) VALUES ('planner', 'm', 'h', ?, 0, 1, ?)",
                    (tokens, dbm.now()))
        self.assertEqual(agency.estimate_tokens("planner"), 1000)
        self.assertEqual(agency.estimate_tokens("coach"), 2000)


if __name__ == "__main__":
    unittest.main()
