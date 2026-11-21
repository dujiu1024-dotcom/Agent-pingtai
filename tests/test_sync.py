#!/usr/bin/env python3
"""事件总线：订阅、时段过滤、去重、死信（分项 19.1/19.2）"""

import contextlib
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db as dbm  # noqa: E402

import agency  # noqa: E402

TODAY = "2026-11-11"
DAYTIME = dt.datetime(2026, 11, 11, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
NIGHT = dt.datetime(2026, 11, 11, 3, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_db_path = dbm.DB_PATH
        self.orig_today = os.environ.get("AGENCY_TODAY")
        dbm.DB_PATH = Path(self.tmp.name) / "agency.db"
        os.environ["AGENCY_TODAY"] = TODAY
        self.seq = 0

    def tearDown(self):
        dbm.DB_PATH = self.orig_db_path
        if self.orig_today is None:
            os.environ.pop("AGENCY_TODAY", None)
        else:
            os.environ["AGENCY_TODAY"] = self.orig_today
        self.tmp.cleanup()

    # ---------- 工具 ----------

    def add_task(self, tid="T-0001"):
        with contextlib.closing(agency.db()) as con, con:
            con.execute(
                "INSERT INTO tasks(id, title, owner, status, start_at, due_at,"
                " done_when, evidence, kind, created_at)"
                " VALUES (?, '事件用例', 'planner', 'todo', ?, ?, '完成标准', '',"
                " 'general', ?)",
                (tid, TODAY, TODAY, dbm.now()))

    def add_event(self, kind="task_created", task_id=None, detail="事件"):
        self.seq += 1
        with contextlib.closing(agency.db()) as con, con:
            con.execute(
                "INSERT INTO events(type, task_id, payload, dedup_key, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (kind, task_id, json.dumps({"detail": detail}, ensure_ascii=False),
                 "k-%d" % self.seq, dbm.now()))

    def events(self):
        with contextlib.closing(agency.db()) as con:
            return [dict(r) for r in con.execute(
                "SELECT id, type, delivered_at FROM events ORDER BY id")]

    def dead_letters(self):
        with contextlib.closing(agency.db()) as con:
            return [dict(r) for r in con.execute(
                "SELECT event_id, role, error, attempts FROM dead_letter ORDER BY id")]

    # ---------- 归档与去重 ----------

    def test_no_subscription_archives(self):
        self.add_event()
        stats = agency.sync_pending(now=DAYTIME)
        self.assertEqual(stats["archived"], 1)
        self.assertIsNotNone(self.events()[0]["delivered_at"])

        stats = agency.sync_pending(now=DAYTIME)
        self.assertEqual(stats["pending"], 0)

    def test_notify_delivers_once(self):
        agency.sub_upsert("task_created", "secretary", "notify")
        self.add_event()
        sent = []
        stats = agency.sync_pending(now=DAYTIME, sender=sent.append)
        self.assertEqual(stats["delivered"], 1)
        self.assertEqual(len(sent), 1)
        self.assertIn("已建任务", sent[0])
        self.assertNotIn("task_created", sent[0], "通知正文不出现内部事件名")
        self.assertIsNotNone(self.events()[0]["delivered_at"])

        stats = agency.sync_pending(now=DAYTIME, sender=sent.append)
        self.assertEqual(stats["pending"], 0)
        self.assertEqual(len(sent), 1, "重复 sync 不应重投")

    def test_time_window_blocks_then_force(self):
        agency.sub_upsert("task_created", "secretary", "notify")
        self.add_event()
        sent = []
        stats = agency.sync_pending(now=NIGHT, sender=sent.append)
        self.assertTrue(stats["skipped"])
        self.assertEqual(sent, [])
        self.assertIsNone(self.events()[0]["delivered_at"])

        stats = agency.sync_pending(now=NIGHT, sender=sent.append, force=True)
        self.assertEqual(stats["delivered"], 1)
        self.assertEqual(len(sent), 1)

    def test_dry_run_does_not_write(self):
        agency.sub_upsert("task_created", "secretary", "notify")
        self.add_event()
        sent = []
        stats = agency.sync_pending(now=DAYTIME, sender=sent.append, dry_run=True)
        self.assertEqual(stats["would_send"], 1)
        self.assertEqual(stats["delivered"], 0)
        self.assertEqual(sent, [])
        self.assertIsNone(self.events()[0]["delivered_at"])

    # ---------- 失败与重试 ----------

    def test_failure_keeps_event_and_counts_attempts(self):
        agency.sub_upsert("task_created", "secretary", "notify")

        def boom(_text):
            raise RuntimeError("飞书不可达")

        self.add_event()
        stats = agency.sync_pending(now=DAYTIME, sender=boom)
        self.assertEqual(stats["failed"], 1)
        self.assertIsNone(self.events()[0]["delivered_at"])
        letters = self.dead_letters()
        self.assertEqual(len(letters), 1)
        self.assertEqual(letters[0]["attempts"], 1)
        self.assertIn("飞书不可达", letters[0]["error"])

        # 60s 退避窗口内不重试，避免失败风暴
        stats = agency.sync_pending(now=DAYTIME, sender=boom)
        self.assertEqual(stats["deferred"], 1)
        self.assertEqual(self.dead_letters()[0]["attempts"], 1)

        # 退避时间过了才重试，attempts 累加
        later = DAYTIME + dt.timedelta(seconds=61)
        agency.sync_pending(now=later, sender=boom)
        letters = self.dead_letters()
        self.assertEqual(len(letters), 1, "同一事件同一订阅只保留一条死信")
        self.assertEqual(letters[0]["attempts"], 2)

    # ---------- 开关 ----------

    def test_disabled_subscription_is_ignored(self):
        agency.sub_upsert("task_created", "secretary", "notify", enabled=False)
        self.add_event()
        sent = []
        stats = agency.sync_pending(now=DAYTIME, sender=sent.append)
        self.assertEqual(sent, [])
        self.assertEqual(stats["archived"], 1)

        agency.sub_toggle("task_created", "secretary", "notify", True)
        self.add_event()
        stats = agency.sync_pending(now=DAYTIME, sender=sent.append)
        self.assertEqual(stats["delivered"], 1)

    # ---------- Agent 动作 ----------

    def test_run_action_executes_and_records_spend(self):
        self.add_task()
        agency.sub_upsert("task_created", "planner", "run", budget=5000)
        self.add_event(task_id="T-0001")
        calls = []

        def runner(role, task):
            calls.append((role, task["id"]))
            return 1200, None

        stats = agency.sync_pending(now=DAYTIME, runner=runner)
        self.assertEqual(stats["delivered"], 1)
        self.assertEqual(calls, [("planner", "T-0001")])
        spend = agency.sync_spend_today(TODAY)
        self.assertEqual(spend["task_created|planner|run"], 1200)

    def test_run_action_budget_blocks(self):
        self.add_task()
        agency.sub_upsert("task_created", "planner", "run", budget=1)
        self.add_event(task_id="T-0001")
        calls = []
        stats = agency.sync_pending(now=DAYTIME,
                                    runner=lambda r, t: calls.append(t["id"]) or (0, None))
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(calls, [])
        self.assertIn("预算不足", self.dead_letters()[0]["error"])

    def test_run_action_without_task_fails(self):
        agency.sub_upsert("task_created", "planner", "run")
        self.add_event(task_id=None)
        stats = agency.sync_pending(now=DAYTIME, runner=lambda r, t: (0, None))
        self.assertEqual(stats["failed"], 1)
        self.assertIn("未绑定任务", self.dead_letters()[0]["error"])

    def test_invalid_subscription_rejected(self):
        with self.assertRaises(SystemExit):
            agency.sub_upsert("not_an_event", "secretary", "notify")
        with self.assertRaises(SystemExit):
            agency.sub_upsert("task_created", "nobody", "run")


if __name__ == "__main__":
    unittest.main()
