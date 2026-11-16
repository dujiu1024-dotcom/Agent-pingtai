#!/usr/bin/env python3
"""财务台账与商务闸门（分项 17.1~17.3）

走真实 CLI（agency.main），只把数据库指向临时目录，
覆盖：五表可写可查、毛利率/预付/账期/节点/开票五项、
回款不超发票、未过闸门 code 任务被拒。
"""

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

PROJECT = "TEST-FIN"


class FinanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_db_path = dbm.DB_PATH
        self.orig_env = {k: os.environ.get(k) for k in
                         ("AGENCY_ENV", "AGENCY_ALLOW_CUSTOM_DB")}
        os.environ["AGENCY_ENV"] = "dev"
        os.environ["AGENCY_ALLOW_CUSTOM_DB"] = "1"
        dbm.DB_PATH = Path(self.tmp.name) / "agency.db"
        self.cli("finance", "client", "add", "测试客户",
                 "--contact", "王工", "--phone", "13900000000")

    def tearDown(self):
        dbm.DB_PATH = self.orig_db_path
        for key, value in self.orig_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    # ---------- 工具 ----------

    def cli(self, *argv):
        """执行 agency.py 命令，返回 (退出码, 输出)。"""
        old_argv = sys.argv
        sys.argv = ["agency.py", *argv]
        buf, err = io.StringIO(), io.StringIO()
        code = None
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                try:
                    agency.main()
                except SystemExit as e:
                    code = e.code
                    if isinstance(e.code, str):
                        buf.write(e.code)
        finally:
            sys.argv = old_argv
        return code, buf.getvalue() + err.getvalue()

    def quote(self, amount=1000, cost=500, client="CL-0001"):
        argv = ["finance", "quote", "add", PROJECT,
                "--amount", str(amount), "--cost", str(cost)]
        if client:
            argv += ["--client-id", client]
        return self.cli(*argv)

    def contract(self, prepay="0.3", term="30", amount=1000,
                 quote="Q-0001", diff=None):
        argv = ["finance", "contract", "add", PROJECT,
                "--amount", str(amount), "--quote-id", quote,
                "--prepay-ratio", prepay, "--term-days", term,
                "--payment-nodes", "签约30%，验收后7天70%"]
        if diff:
            argv += ["--diff-reason", diff]
        return self.cli(*argv)

    def billing(self):
        return self.cli("finance", "billing", "set", PROJECT,
                        "--title", "龍奕科技", "--tax-no", "91310000TEST",
                        "--type", "增值税专用发票")

    def invoice(self, amount="300"):
        return self.cli("finance", "invoice", "add", "C-0001", "--no", "INV-001",
                        "--amount", amount, "--issued-at", "2026-11-11",
                        "--due-at", "2026-12-11")

    def seed_deal(self):
        self.quote()
        self.contract()

    # ---------- 17.2 五表读写与校验 ----------

    def test_five_tables_writable_and_listable(self):
        self.seed_deal()
        self.billing()
        self.invoice()
        self.cli("finance", "payment", "add", "INV-0001",
                 "--amount", "100", "--paid-at", "2026-11-12")
        self.cli("finance", "cost", "add", PROJECT, "--category", "cloud",
                 "--amount", "50", "--occurred-at", "2026-11-12")

        for name, prefix in [("quote", "Q-0001"), ("contract", "C-0001"),
                             ("invoice", "INV-0001"), ("payment", "PAY-0001"),
                             ("cost", "COST-0001")]:
            code, out = self.cli("finance", name, "list")
            self.assertIsNone(code, out)
            self.assertIn(prefix, out)

    def test_amount_must_be_positive(self):
        code, out = self.quote(amount=0)
        self.assertNotEqual(code, 0)
        self.assertIn("必须大于 0", out)

    def test_invoice_requires_existing_contract(self):
        code, out = self.cli("finance", "invoice", "add", "C-9999",
                             "--no", "INV-X", "--amount", "100")
        self.assertNotEqual(code, 0)
        self.assertIn("合同不存在", out)

    def test_payment_cannot_exceed_invoice(self):
        self.seed_deal()
        self.billing()
        self.invoice(amount="300")
        code, out = self.cli("finance", "payment", "add", "INV-0001",
                             "--amount", "200", "--paid-at", "2026-11-12")
        self.assertIsNone(code, out)
        code, out = self.cli("finance", "payment", "add", "INV-0001",
                             "--amount", "200", "--paid-at", "2026-11-13")
        self.assertNotEqual(code, 0)
        self.assertIn("回款超额", out)

        code, out = self.cli("finance", "payment", "add", "INV-0001",
                             "--amount", "100", "--paid-at", "2026-11-14")
        self.assertIsNone(code, out)
        with contextlib.closing(agency.db()) as con:
            status = con.execute(
                "SELECT status FROM invoices WHERE id = 'INV-0001'").fetchone()["status"]
        self.assertEqual(status, "paid")

    # ---------- 17.3 商务闸门五项 ----------

    def test_margin_below_floor_blocks_gate(self):
        code, out = self.quote(amount=1000, cost=900)
        self.assertIsNone(code, out)
        self.contract()
        code, out = self.cli("gate", "finance", "confirm", PROJECT, "--by", "longyi")
        self.assertNotEqual(code, 0)
        self.assertIn("毛利率", out)
        self.assertFalse(agency.finance_gate_ok(PROJECT))

    def test_prepay_below_floor_blocks_gate(self):
        self.quote()
        self.contract(prepay="0.1")
        code, out = self.cli("gate", "finance", "confirm", PROJECT, "--by", "longyi")
        self.assertNotEqual(code, 0)
        self.assertIn("预付比例", out)

    def test_term_over_limit_is_warning_only(self):
        self.quote()
        self.contract(term="45")
        code, out = self.cli("gate", "finance", "confirm", PROJECT, "--by", "longyi")
        self.assertIsNone(code, out)
        self.assertIn("商务闸门已开", out)
        self.assertIn("警告", out)
        self.assertIn("账期", out)

    def test_missing_nodes_blocks_gate(self):
        self.quote()
        self.cli("finance", "contract", "add", PROJECT, "--amount", "1000",
                 "--quote-id", "Q-0001", "--prepay-ratio", "0.3",
                 "--term-days", "30", "--payment-nodes", " ")
        code, out = self.cli("gate", "finance", "confirm", PROJECT, "--by", "longyi")
        self.assertNotEqual(code, 0)
        self.assertIn("验收与付款节点", out)

    def test_confirm_then_check_passes(self):
        self.seed_deal()
        self.billing()
        code, out = self.cli("gate", "finance", "confirm", PROJECT, "--by", "longyi")
        self.assertIsNone(code, out)
        self.assertIn("商务闸门已开", out)
        self.assertTrue(agency.gate_check(PROJECT, "finance"))

        code, out = self.cli("gate", "finance", "check", PROJECT)
        self.assertIsNone(code, out)
        self.assertIn("通过", out)

        self.cli("gate", "finance", "revoke", PROJECT)
        self.assertFalse(agency.gate_check(PROJECT, "finance"))

    def test_code_task_requires_finance_gate(self):
        self.cli("gate", "confirm", PROJECT, "--by", "longyi", "--scope", "测试范围")
        self.cli("finance", "config", "--gate-mode", "block")
        code, out = self.cli(
            "task", "add", "--title", "测试任务", "--project", PROJECT,
            "--owner", "longyi", "--start", "2026-11-11", "--due", "2026-11-12",
            "--done-when", "命令执行成功", "--kind", "code")
        self.assertNotEqual(code, 0)
        self.assertIn("商务闸门未通过", out)

        self.seed_deal()
        self.cli("gate", "finance", "confirm", PROJECT, "--by", "longyi")
        code, out = self.cli(
            "task", "add", "--title", "测试任务", "--project", PROJECT,
            "--owner", "longyi", "--start", "2026-11-11", "--due", "2026-11-12",
            "--done-when", "命令执行成功", "--kind", "code")
        self.assertIsNone(code, out)
        self.assertIn("已建", out)

    def test_threshold_configurable(self):
        self.quote(amount=1000, cost=600)  # 1.67 倍
        self.contract()
        code, out = self.cli("finance", "config", "--margin-min", "2.0")
        self.assertIsNone(code, out)
        code, out = self.cli("gate", "finance", "confirm", PROJECT, "--by", "longyi")
        self.assertNotEqual(code, 0)
        self.assertIn("毛利率", out)

    # ---------- 商务单据链路 ----------

    def test_quote_requires_client(self):
        code, out = self.quote(client="")
        self.assertNotEqual(code, 0)
        self.assertIn("必须挂客户", out)

    def test_contract_requires_quote(self):
        self.quote()
        code, out = self.cli("finance", "contract", "add", PROJECT,
                             "--amount", "1000", "--prepay-ratio", "0.5",
                             "--term-days", "15", "--payment-nodes", "预付50%")
        self.assertNotEqual(code, 0)
        self.assertIn("必须引用报价单", out)

    def test_contract_amount_mismatch_needs_diff_reason(self):
        self.quote(amount=1000, cost=500)
        code, out = self.contract(amount=900)
        self.assertNotEqual(code, 0)
        self.assertIn("不一致", out)
        code, out = self.contract(amount=900, diff="客户砍价 100")
        self.assertIsNone(code, out)
        self.assertIn("差异原因", out)

    def test_gate_rejects_detached_quote_client(self):
        self.quote()
        self.contract()
        with contextlib.closing(agency.db()) as con, con:
            con.execute("UPDATE quotes SET client_id = NULL WHERE id = 'Q-0001'")
        code, out = self.cli("gate", "finance", "confirm", PROJECT, "--by", "longyi")
        self.assertNotEqual(code, 0)
        self.assertIn("报价挂客户", out)


if __name__ == "__main__":
    unittest.main()
