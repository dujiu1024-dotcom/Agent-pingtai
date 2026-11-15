#!/usr/bin/env python3
"""开工闸门三态：未确认 / 已确认 / 已撤销（分项 8.12）

用临时数据库隔离：每个用例把 db.DB_PATH 指向临时目录，跑完还原，
不会碰到 agency/state/agency.db。
"""

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency  # noqa: E402
import db as dbm  # noqa: E402

PROJECT = "TEST-GATE"


def args(**kw):
    return argparse.Namespace(**kw)


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_db_path = dbm.DB_PATH
        dbm.DB_PATH = Path(self.tmp.name) / "agency.db"

    def tearDown(self):
        dbm.DB_PATH = self.orig_db_path
        self.tmp.cleanup()

    # ---------- 工具 ----------

    def run_quiet(self, fn, a):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            fn(a)
        return buf.getvalue() + err.getvalue()

    def run_capture(self, fn, a):
        """执行命令并捕获退出码；正常返回记为 None。stderr 一并收集（sys.exit 走 stderr）。"""
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            try:
                fn(a)
                code = None
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue() + err.getvalue()

    def gate(self, cmd, **kw):
        return self.run_quiet(
            agency.cmd_gate,
            args(gate_cmd=cmd, project=kw.get("project", PROJECT),
                 by=kw.get("by", "longyi"), scope=kw.get("scope", "验证闸门")),
        )

    def add_task(self, kind="code"):
        return self.run_capture(
            agency.cmd_task_add,
            args(title="闸门用例任务", project=PROJECT, owner="longyi",
                 start="2026-11-11", due="2026-11-12",
                 done_when="命令执行成功", kind=kind, hours=1.0,
                 dep="", deliverable="", milestone=""),
        )

    def open_finance_gate(self):
        """补一单合规报价与合同，并确认商务闸门（code 任务需两道闸门）。"""
        with contextlib.closing(agency.db()) as con, con:
            con.execute(
                "INSERT INTO clients(id, name, created_at)"
                " VALUES ('CL-T', '闸门客户', '2026-11-11T00:00:00+00:00')")
            con.execute(
                "INSERT INTO quotes(id, project_id, client_id, amount, cost, status,"
                " created_at) VALUES ('Q-T', ?, 'CL-T', 1000, 500, 'draft',"
                " '2026-11-11T00:00:00+00:00')",
                (PROJECT,))
            con.execute(
                "INSERT INTO contracts(id, project_id, quote_id, amount, payment_terms,"
                " created_at) VALUES ('C-T', ?, 'Q-T', 1000, ?,"
                " '2026-11-11T00:00:00+00:00')",
                (PROJECT, json.dumps(
                    {"prepay_ratio": 0.3, "term_days": 30, "nodes": "验收后7天"},
                    ensure_ascii=False)))
        agency_out = io.StringIO()
        with contextlib.redirect_stdout(agency_out):
            agency.finance_gate_confirm(PROJECT, "longyi")

    # ---------- 三态 ----------

    def test_default_is_blocked(self):
        self.assertFalse(agency.gate_ok(PROJECT))
        code, out = self.add_task(kind="code")
        self.assertNotEqual(code, 0)
        self.assertIn("闸门未通过", str(code))

    def test_check_command_fails_when_not_confirmed(self):
        code, out = self.run_capture(
            agency.cmd_gate, args(gate_cmd="check", project=PROJECT, by=None, scope=None))
        self.assertEqual(code, 1)
        self.assertIn("未通过", out)

    def test_confirm_opens_gate(self):
        out = self.gate("confirm", scope="OPC-002 诊断包")
        self.assertIn("闸门已开", out)
        self.assertTrue(agency.gate_ok(PROJECT))

        info = agency.read_gates()[PROJECT]
        self.assertTrue(info["confirmed"])
        self.assertEqual(info["by"], "longyi")
        self.assertEqual(info["scope"], "OPC-002 诊断包")
        self.assertEqual(info["revoked_at"], "")

        out = self.run_quiet(
            agency.cmd_gate, args(gate_cmd="check", project=PROJECT, by=None, scope=None))
        self.assertIn("通过", out)

    def test_start_gate_alone_rejects_code_task(self):
        self.gate("confirm")
        con = agency.db()
        try:
            with con:
                agency.cfg_set(con, "finance.gate_mode", "block")
        finally:
            con.close()
        code, out = self.add_task(kind="code")
        self.assertNotEqual(code, 0)
        self.assertIn("商务闸门未通过", str(code))

    def test_finance_gate_warn_allows_code_task(self):
        """默认 warn：商务闸门只告警，不把正常建单锁死。"""
        self.gate("confirm")
        code, out = self.add_task(kind="code")
        self.assertIsNone(code)
        self.assertIn("已建", out)

    def test_confirm_allows_code_task(self):
        self.gate("confirm")
        self.open_finance_gate()
        code, out = self.add_task(kind="code")
        self.assertIsNone(code)
        self.assertIn("已建", out)
        self.assertEqual(len(agency.read_tasks()), 1)

    def test_revoke_closes_gate(self):
        self.gate("confirm")
        out = self.gate("revoke")
        self.assertIn("闸门已关", out)
        self.assertFalse(agency.gate_ok(PROJECT))

        info = agency.read_gates()[PROJECT]
        self.assertFalse(info["confirmed"])
        self.assertTrue(info["revoked_at"])

        code, out = self.add_task(kind="code")
        self.assertNotEqual(code, 0)
        self.assertIn("闸门未通过", str(code))

    def test_revoke_keeps_general_task_allowed(self):
        self.gate("confirm")
        self.gate("revoke")
        code, out = self.add_task(kind="general")
        self.assertIsNone(code)
        self.assertIn("已建", out)

    def test_reconfirm_clears_revoked_at(self):
        self.gate("confirm")
        self.gate("revoke")
        self.gate("confirm")
        info = agency.read_gates()[PROJECT]
        self.assertTrue(info["confirmed"])
        self.assertEqual(info["revoked_at"], "")

    # ---------- plan 与 task add 同模式一致 ----------

    def set_mode(self, mode):
        with contextlib.closing(agency.db()) as con, con:
            agency.cfg_set(con, "finance.gate_mode", mode)

    def plan_result(self, kind="code"):
        import datetime as _dt
        day = _dt.date.today()
        draft = {"tasks": [{
            "title": "批量闸门用例", "owner": "pm",
            "start": day.isoformat(), "due": (day + _dt.timedelta(days=3)).isoformat(),
            "done_when": "完成", "kind": kind, "hours": 1.0, "dep": "—",
        }]}
        return agency.plan_check(draft, days=30, project=PROJECT)

    def test_warn_mode_plan_and_task_add_both_warn(self):
        """warn 下商务闸门只告警：plan 不再整批拒绝，与 task add 一致。"""
        self.gate("confirm")
        self.set_mode("warn")
        result = self.plan_result("code")
        self.assertEqual(result["batch_errors"], [])
        self.assertTrue(any("商务闸门未通过" in w for w in result["warnings"]))
        code, out = self.add_task(kind="code")
        self.assertIsNone(code)
        self.assertIn("已建", out)

    def test_block_mode_plan_and_task_add_both_reject(self):
        """block 下商务闸门阻断：plan 整批拒绝，task add 也拒绝。"""
        self.gate("confirm")
        self.set_mode("block")
        result = self.plan_result("code")
        self.assertTrue(any("商务闸门未通过" in e for e in result["batch_errors"]))
        code, out = self.add_task(kind="code")
        self.assertNotEqual(code, 0)
        self.assertIn("商务闸门未通过", str(code))

    def test_start_gate_rejects_plan_in_both_modes(self):
        """开工闸门始终阻断：warn 下 plan 也不能绕过。"""
        self.set_mode("warn")
        result = self.plan_result("code")
        self.assertTrue(any("开工闸门未通过" in e for e in result["batch_errors"]))


if __name__ == "__main__":
    unittest.main()
