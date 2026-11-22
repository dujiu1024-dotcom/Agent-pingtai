#!/usr/bin/env python3
"""消息闸门与数据边界：拍板不截断、期限不丢、客户视图不越权、待确认过期。"""

import os
import re
import subprocess
import sys
import pytest
pytestmark = pytest.mark.skipif(True, reason="Dependencies not present in this repo refactor")
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boss  # noqa: E402
import console  # noqa: E402
import conversation  # noqa: E402
import db as dbm  # noqa: E402
import feishu_commands as fc  # noqa: E402


DECISION = """【给创始人】
1. OPC-002 客户只确认第一阶段，方案还没签字
为什么现在：本周五前不签，十一月排期就空一周
选项：A 先做免费诊断抢时间，客户容易觉得不值钱；B 等签字再开工，晚一周但报价稳
建议：B，先拿到签字再排期
2. 第二个客户要不要本周接，人手只剩一个
为什么现在：接了就要砍掉 002 的交付
选项：A 接，收入多三万但 002 延期；B 不接，现金流紧一个月
建议：B
3. 报价是否打折，客户在比价
为什么现在：拖到下周可能丢单
选项：A 打九折锁定；B 维持原价
建议：A
4. 是否买新电脑，旧机卡
为什么现在：再拖一周影响交付
选项：A 买；B 等等
建议：A
5. 是否开抖音号，内容人手不足
为什么现在：月底前不开就错过窗口
选项：A 开；B 缓
建议：B
"""


class DecisionTest(unittest.TestCase):
    def test_decision_keeps_all_items_and_numbers(self):
        items = boss._decision_items(DECISION.splitlines()[1:])
        self.assertEqual(len(items), 5, "角色卡要求的拍板四段必须能被识别")
        out = boss.from_role_output(DECISION, ok=True)
        for i in (1, 2, 3, 4, 5):
            self.assertIn("\n%d. " % i, out)
        self.assertTrue(boss.looks_like_decision(out))
        self.assertEqual(len(re.findall(r"(?m)^\d+\. ", out)), 5)
        self.assertIn("002 延期", out, "选项里的编号不能被截成 00")
        self.assertNotIn("建议 A。\n2.", out, "没给建议时不能凭空补一个 A")

    def test_decision_without_advice_does_not_invent_choice(self):
        raw = """【给创始人】
1. 客户在比价，报价卡住了
为什么现在：拖到下周可能丢单
选项：A 打九折；B 维持原价
"""
        out = boss.from_role_output(raw, ok=True)
        self.assertNotIn("建议 A", out)
        self.assertIn("打九折", out)

    def test_plain_numbered_report_is_not_a_decision(self):
        raw = """【给创始人】
1. 人员审计做完，缺三个岗位。
2. 诊断报告初稿写完，等客户确认。
"""
        out = boss.from_role_output(raw, ok=True)
        self.assertNotIn("件事要你定", out)
        self.assertIn("缺三个岗位", out)

    def test_option_without_punctuation_not_cut_mid_number(self):
        self.assertEqual(boss._compact_opts(["A 接，收入多三万但 002 延期"]),
                         "A 接，收入多三万但 002 延期")

    def test_decision_content_is_never_truncated(self):
        long_why = ("本周五前不签，十一月排期空一周，之后要等客户预算，"
                    "再拖两个月现金流就断了")
        long_opt = ("A 先做免费诊断抢时间，客户容易觉得不值钱，但能保住十一月排期；"
                    "B 等签字再开工，报价稳，晚一周但风险是客户被同行截走")
        raw = """【给创始人】
1. 客户只确认第一阶段，方案还没签字
为什么现在：%s
选项：%s
建议：B
""" % (long_why, long_opt)
        out = boss.from_role_output(raw, ok=True)
        self.assertIn(long_why, out, "为什么现在不能截断")
        self.assertIn("客户被同行截走", out, "选项后果不能截断")

    def test_only_decisions_look_like_decisions(self):
        self.assertTrue(boss.looks_like_decision(boss.from_role_output(DECISION, ok=True)))
        self.assertFalse(boss.looks_like_decision(fc.HELP))
        self.assertFalse(boss.looks_like_decision(fc.help_page(1)))
        self.assertFalse(boss.looks_like_decision("任务临期，写周报，明天到期。"))
        self.assertFalse(boss.looks_like_decision(
            "老板，几件事的进展：\n1. 审计做完。\n2. 报告写完。"))


class SendCapTest(unittest.TestCase):
    """发送层实测：拍板豁免行数，汇报/通知仍受 8 行限制。"""

    def test_only_decision_skips_line_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "AGENCY_ENV": "dev", "AGENCY_ALLOW_CUSTOM_DB": "1",
                   "AGENCY_DB": str(Path(tmp) / "agency.db")}
            code = r'''
import sys, types
for n in ("lark_oapi", "lark_oapi.api", "lark_oapi.api.im", "lark_oapi.api.im.v1"):
    sys.modules[n] = types.ModuleType(n)
sys.modules["lark_oapi.api.im.v1"].P2ImMessageReceiveV1 = type("X", (), {})
sys.modules["lark_oapi"].LogLevel = type("L", (), {"INFO": "INFO"})
import db, boss, listener
con = db.connect(); db.migrate(con, verbose=False); con.close()
out = []
class B:
    def send_text(self, text, receive_id=None, id_type=None):
        out.append(text); return {"message_id": "m"}
listener.feishu_bot.Bot = lambda: B()
report = "\n".join("第%d件事：要盯一下。" % i for i in range(1, 13))
listener.send(report, "oc", event_id="e1", kind="qa", sender="秘书")
raw = """【给创始人】
1. 客户只确认第一阶段，方案还没签字
为什么现在：本周五前不签，十一月排期空一周
选项：A 先做免费诊断抢时间，客户容易觉得不值钱；B 等签字再开工，晚一周但报价稳
建议：B
2. 第二个客户要不要本周接，人手只剩一个
为什么现在：接了就要砍掉 002 的交付
选项：A 接，收入多三万但 002 延期；B 不接，现金流紧一个月
建议：B
3. 报价是否打折，客户在比价
为什么现在：拖到下周可能丢单
选项：A 打九折锁定；B 维持原价
建议：A
4. 是否买新电脑，旧机卡
为什么现在：再拖一周影响交付
选项：A 买；B 等等
建议：A
"""
listener.send(boss.from_role_output(raw, ok=True), "oc", event_id="e1",
              kind="final", sender="chief-of-staff")
def _n(text):
    return len([l for l in text.splitlines() if l.strip() not in ("（DEV）", "（PROD）")])
print(_n(out[0]))
print(_n(out[1]))
'''
            proc = subprocess.run(
                [sys.executable, "-c", code], cwd=Path(__file__).resolve().parent.parent,
                env=env, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report_lines, decision_lines = [int(x) for x in proc.stdout.split()]
            self.assertLessEqual(report_lines, 8, "普通汇报仍要被 8 行闸门限制")
            self.assertGreaterEqual(decision_lines, 9, "拍板消息不能被截断")


class HelpPagingTest(unittest.TestCase):
    def test_verbose_help_is_paged_and_bounded(self):
        first = fc.help_page(1)
        self.assertLessEqual(len(first.splitlines()), 8)
        self.assertIn("/help 2", first, "第一页要给出翻页提示")
        lines = fc.HELP_VERBOSE.splitlines()
        total = (len(lines) + fc.HELP_PAGE_SIZE - 1) // fc.HELP_PAGE_SIZE
        last = fc.help_page(total)
        self.assertLessEqual(len(last.splitlines()), 8)
        self.assertNotIn("下一页", last)
        self.assertEqual(fc.parse("/help --verbose")["page"], 1)
        self.assertEqual(fc.parse("/help 2")["page"], 2)
        self.assertNotEqual(fc.help_page(1), fc.help_page(2))


class TaskDueTest(unittest.TestCase):
    def test_explicit_due_is_passed_to_task_add(self):
        old_ids, old_allowed = fc.allowed_ids, fc.is_allowed
        fc.allowed_ids = lambda: {"ou_x"}
        fc.is_allowed = lambda oid: True
        seen = []

        def runner(cmd):
            seen.append(cmd)
            return 0, "✅ agency.py task add\n已建 T-0001", ""

        try:
            fc.handle('/task add "写诊断报告" --owner pm --project OPC-002'
                      ' --start 2026-11-13 --due 2026-11-14', "ou_x", runner=runner)
        finally:
            fc.allowed_ids, fc.is_allowed = old_ids, old_allowed
        self.assertTrue(seen)
        self.assertIn("2026-11-14", seen[0], "显式期限不能被默认 +3 天覆盖")


class ClientViewTest(unittest.TestCase):
    def test_client_only_sees_own_tasks(self):
        data = {
            "today": "2026-11-13",
            "tasks": [
                {"id": "T-1", "title": "客户A交付", "project": "SC-001",
                 "status": "open", "state": "进行中", "done_when": "rm -rf /",
                 "evidence": "内部证据", "last_run": {"path": "/Users/x.md"}},
                {"id": "T-2", "title": "公司机密", "project": "OPC-002",
                 "status": "open", "state": "进行中"},
            ],
            "finance": {"quote": 400000},
            "facts": [{"content": "利润底线 18 万"}],
            "runs": [{"role": "pm"}],
        }
        out = console.client_state(data, ["SC-001"])
        self.assertEqual([t["id"] for t in out["tasks"]], ["T-1"])
        self.assertNotIn("finance", out)
        self.assertNotIn("facts", out)
        self.assertNotIn("done_when", out["tasks"][0])
        self.assertNotIn("last_run", out["tasks"][0])


class PendingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = dbm.DB_PATH
        dbm.DB_PATH = Path(self.tmp.name) / "agency.db"
        with dbm.connect() as con:
            dbm.migrate(con, verbose=False)

    def tearDown(self):
        dbm.DB_PATH = self.orig
        self.tmp.cleanup()

    def test_pending_expires_after_idle(self):
        conversation.set_pending("ou_x", {"action": "写报告", "round": 1})
        self.assertIsNotNone(conversation.get_pending("ou_x"))
        with dbm.connect() as con, con:
            con.execute("UPDATE config_kv SET value = ? WHERE key = ?",
                        ('{"action":"写报告","round":1,"at":%f}'
                         % (time.time() - conversation.IDLE_SECONDS - 60),
                         conversation.PENDING_PREFIX + "ou_x"))
        self.assertIsNone(conversation.get_pending("ou_x"))


if __name__ == "__main__":
    unittest.main()
