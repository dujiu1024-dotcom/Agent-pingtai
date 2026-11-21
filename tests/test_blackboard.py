#!/usr/bin/env python3
"""黑板与到期信号：同事结论注入、唤醒原因、事件按日去重。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db as dbm  # noqa: E402

import agency  # noqa: E402


class LogEventDedupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = dbm.DB_PATH
        dbm.DB_PATH = os.path.join(self.tmp.name, "t.db")
        con = dbm.connect(dbm.DB_PATH)
        dbm.migrate(con, verbose=False)
        con.close()

    def tearDown(self):
        dbm.DB_PATH = self.orig
        self.tmp.cleanup()

    def test_same_dedup_key_writes_once(self):
        for _ in range(3):
            agency.log_event("task_due_soon", "", "x", dedup="due:T-0001:2026-11-12")
        con = dbm.connect(dbm.DB_PATH)
        n = con.execute("select count(*) from events where type='task_due_soon'").fetchone()[0]
        con.close()
        self.assertEqual(n, 1)

    def test_different_day_writes_again(self):
        agency.log_event("task_due_soon", "", "x", dedup="due:T-0001:2026-11-12")
        agency.log_event("task_due_soon", "", "x", dedup="due:T-0001:2026-11-13")
        con = dbm.connect(dbm.DB_PATH)
        n = con.execute("select count(*) from events where type='task_due_soon'").fetchone()[0]
        con.close()
        self.assertEqual(n, 2)


class BlackboardPromptTest(unittest.TestCase):
    def test_prompt_has_peer_section_and_wake_reason(self):
        task = {"id": "T-9999", "title": "t", "project": "P", "owner": "auditor",
                "start": "2026-11-12", "due": "2026-11-14", "done_when": "x",
                "hours": 1, "status": "open"}
        text = agency.build_prompt("auditor", "ROLE", task, [], "", "task_due_soon：临近到期")
        self.assertIn("【同事结论】", text)
        self.assertIn("【为什么叫醒你】task_due_soon", text)

    def test_peer_outputs_skips_other_roles_echo(self):
        # 复读型产出（全是角色规则）不应被当成同事结论
        self.assertEqual(agency._brief_text("/nonexistent.md"), "")


if __name__ == "__main__":
    unittest.main()
