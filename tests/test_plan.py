#!/usr/bin/env python3
"""调度层 plan：草案校验与入库（分项 18.2）

用临时数据库隔离，日期固定为 AGENCY_TODAY，避免窗口随真实日期漂移。
"""

import argparse
import contextlib
import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency  # noqa: E402
import db as dbm  # noqa: E402

PROJECT = "TEST-PLAN"
TODAY = "2026-11-11"


def day(n):
    return (dt.date(2026, 11, 11) + dt.timedelta(days=n)).isoformat()


def args(**kw):
    base = dict(goal="测试目标", days=5, project=PROJECT, hours=40.0,
                file=None, apply=False)
    base.update(kw)
    return argparse.Namespace(**base)


def task(title="写测试用例", **kw):
    base = {
        "title": title,
        "owner": "planner",
        "kind": "general",
        "start": day(0),
        "due": day(2),
        "done_when": "用例全部通过",
        "hours": 2,
        "dep": [],
        "deliverable": "tests/",
        "milestone": "M1",
    }
    base.update(kw)
    return base


class PlanTest(unittest.TestCase):
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

    def run_cmd(self, draft, **kw):
        path = Path(self.tmp.name) / "draft.json"
        path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            try:
                agency.cmd_plan(args(file=str(path), **kw))
                code = None
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue() + err.getvalue()

    def add_existing(self, tid, title, **kw):
        with contextlib.closing(agency.db()) as con, con:
            con.execute(
                "INSERT INTO tasks(id, title, owner, status, start_at, due_at,"
                " done_when, evidence, kind, dep, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, '', 'general', ?, ?)",
                (tid, title, kw.get("owner", "planner"), kw.get("status", "todo"),
                 kw.get("start", day(0)), kw.get("due", day(2)),
                 "已有完成标准", kw.get("dep", ""), dbm.now()))

    def check(self, draft, **kw):
        return agency.plan_check(draft, kw.get("days", 5),
                                 kw.get("project", PROJECT),
                                 kw.get("hours", 40.0))

    # ---------- 合法草案 ----------

    def test_valid_draft_applies_with_ids_and_dep(self):
        draft = {"goal": "测试目标", "project": PROJECT,
                 "tasks": [task("第一条"), task("第二条", dep=[0])]}
        code, out = self.run_cmd(draft, apply=True)
        self.assertIsNone(code, out)
        tasks = agency.read_tasks()
        self.assertEqual([t["id"] for t in tasks], ["T-0001", "T-0002"])
        self.assertEqual(tasks[1]["dep"], "T-0001")
        self.assertIn("已入库 2 条", out)
        self.assertEqual(tasks[0]["status"], "open")

    def test_validate_only_does_not_write(self):
        code, out = self.run_cmd({"tasks": [task()]})
        self.assertIsNone(code, out)
        self.assertEqual(agency.read_tasks(), [])
        self.assertIn("加 --apply 写入台账", out)

    # ---------- 6 类非法草案 ----------

    def test_missing_required_field(self):
        result = self.check({"tasks": [task(done_when="")]})
        self.assertIn("缺少必填字段", result["items"][0]["errors"][0])

    def test_bad_owner(self):
        result = self.check({"tasks": [task(owner="intern")]})
        self.assertIn("owner 不合法", result["items"][0]["errors"][0])

    def test_start_after_due(self):
        result = self.check({"tasks": [task(start=day(3), due=day(1))]})
        self.assertIn("开始日期晚于截止日期", "".join(result["items"][0]["errors"]))

    def test_out_of_window(self):
        result = self.check({"tasks": [task(start=day(10), due=day(12))]})
        self.assertIn("日期超出窗口", "".join(result["items"][0]["errors"]))

    def test_unknown_dep(self):
        result = self.check({"tasks": [task(dep=["T-9999"])]})
        self.assertIn("依赖任务不存在", "".join(result["items"][0]["errors"]))

    def test_forward_dep_in_batch(self):
        result = self.check({"tasks": [task("第一条", dep=[1]), task("第二条")]})
        self.assertIn("不是本批前序", "".join(result["items"][0]["errors"]))

    def test_partial_apply_keeps_good_items(self):
        draft = {"tasks": [task("好条目"), task("坏条目", owner="nobody")]}
        code, out = self.run_cmd(draft, apply=True)
        self.assertEqual(code, 1)
        self.assertEqual([t["title"] for t in agency.read_tasks()], ["好条目"])

    # ---------- 整批拒绝 ----------

    def test_code_task_without_gates_rejects_whole_batch(self):
        draft = {"tasks": [task("普通条目"), task("写代码", kind="code")]}
        result = self.check(draft)
        self.assertTrue(result["batch_errors"])
        self.assertIn("整批拒绝", " ".join(result["batch_errors"]))
        code, out = self.run_cmd(draft, apply=True)
        self.assertNotEqual(code, 0)
        self.assertIn("整批拒绝", out)
        self.assertEqual(agency.read_tasks(), [])

    def test_dependency_cycle_rejects_whole_batch(self):
        self.add_existing("T-0001", "已有 A", dep="T-0002")
        self.add_existing("T-0002", "已有 B", dep="T-0001")
        result = self.check({"tasks": [task()]})
        self.assertTrue(any("依赖环" in e for e in result["batch_errors"]))

    # ---------- 警告 ----------

    def test_duplicate_title_warns(self):
        self.add_existing("T-0001", "写测试用例")
        result = self.check({"tasks": [task("写测试用例")]})
        self.assertEqual(result["items"][0]["errors"], [])
        self.assertIn("重复", result["items"][0]["warnings"][0])

    def test_duplicate_title_in_batch_warns(self):
        result = self.check({"tasks": [task("同名"), task("同名")]})
        self.assertIn("重复", result["items"][1]["warnings"][0])

    def test_hours_budget_warns(self):
        result = self.check({"tasks": [task(hours=30), task("第二条", hours=20)]},
                            hours=40.0)
        self.assertIn("超过预算", " ".join(result["warnings"]))


if __name__ == "__main__":
    unittest.main()
