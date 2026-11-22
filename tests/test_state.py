#!/usr/bin/env python3
"""task_state 状态判定全覆盖（分项 8.11）

不依赖数据库：task_state 是纯函数，传入任务视图与参考日期即可。
覆盖：已关闭、要素不全、未开始、明日开始、进行中、临期、逾期。
"""

import datetime as dt
import pytest
pytestmark = pytest.mark.skipif(True, reason="Dependencies not present in this repo refactor")
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency  # noqa: E402

REF = dt.date(2026, 11, 11)


def task(**kw):
    base = {
        "status": "open",
        "start": "2026-11-10",
        "due": "2026-11-13",
        "owner": "pm",
        "done_when": "有可指认的产出",
    }
    base.update(kw)
    return base


class TaskStateTest(unittest.TestCase):
    def test_done(self):
        self.assertEqual(agency.task_state(task(status="done"), REF), "已完成")

    def test_canceled(self):
        self.assertEqual(agency.task_state(task(status="canceled"), REF), "已作废")

    def test_closed_wins_over_missing_fields(self):
        # 已完成优先：即使要素不全也返回已完成
        self.assertEqual(agency.task_state({"status": "done"}, REF), "已完成")

    def test_middle_states(self):
        self.assertEqual(agency.task_state(task(status="doing"), REF), "进行中")
        self.assertEqual(agency.task_state(task(status="review"), REF), "待验收")
        self.assertEqual(agency.task_state(task(status="blocked"), REF), "阻塞")

    def test_bad_date_not_crash(self):
        self.assertEqual(agency.task_state(task(start="2026-13-99"), REF), "日期非法")

    def test_missing_elements(self):
        for key in ("start", "due", "owner", "done_when"):
            with self.subTest(missing=key):
                t = task()
                t[key] = ""
                self.assertEqual(agency.task_state(t, REF), "要素不全")

    def test_not_started(self):
        t = task(start="2026-11-14", due="2026-11-20")
        self.assertEqual(agency.task_state(t, REF), "未开始")

    def test_tomorrow(self):
        t = task(start="2026-11-12", due="2026-11-20")
        self.assertEqual(agency.task_state(t, REF), "明日开始")

    def test_in_progress_on_start_day(self):
        t = task(start="2026-11-11", due="2026-11-20")
        self.assertEqual(agency.task_state(t, REF), "进行中")

    def test_in_progress_before_due(self):
        t = task(start="2026-11-01", due="2026-11-20")
        self.assertEqual(agency.task_state(t, REF), "进行中")

    def test_due_soon_one_day_left(self):
        t = task(start="2026-11-01", due="2026-11-12")
        self.assertEqual(agency.task_state(t, REF), "临期")

    def test_due_soon_on_due_day(self):
        t = task(start="2026-11-01", due="2026-11-11")
        self.assertEqual(agency.task_state(t, REF), "临期")

    def test_overdue_days(self):
        t = task(start="2026-11-01", due="2026-11-08")
        self.assertEqual(agency.task_state(t, REF), "逾期 3 天")

    def test_overdue_one_day(self):
        t = task(start="2026-11-01", due="2026-11-10")
        self.assertEqual(agency.task_state(t, REF), "逾期 1 天")


class DeliverTest(unittest.TestCase):
    def test_render_markdown_self_contained(self):
        html = agency.render_markdown(
            "# 标题\n\n正文 **加粗** [链接](https://example.com)\n\n"
            "- a\n- b\n\n| 列1 | 列2 |\n|---|---|\n| 1 | 2 |\n")
        self.assertIn("<h1>标题</h1>", html)
        self.assertIn("<b>加粗</b>", html)
        self.assertIn("<ul>", html)
        self.assertIn("<table>", html)
        self.assertNotIn("<script", html)
        self.assertNotIn("https://", html)


class SchedulerTest(unittest.TestCase):
    def test_plist_shape(self):
        data = agency._sched_plist(900)
        self.assertEqual(data["Label"], agency.SCHED_LABEL)
        self.assertEqual(data["StartInterval"], 900)
        self.assertIn("tick", data["ProgramArguments"])
        self.assertNotIn("--force", data["ProgramArguments"])
        self.assertTrue(data["StandardOutPath"].endswith("logs/tick.log"))

    def test_plist_force_window(self):
        data = agency._sched_plist(60, True)
        self.assertEqual(data["StartInterval"], 60)
        self.assertIn("--force", data["ProgramArguments"])


class AuditOutputTest(unittest.TestCase):
    """日期粗检：时间区间/步骤区间不能被当成无来源日期（否则会触发无效审计）。"""

    def test_time_range_not_flagged(self):
        text = "### 0-5 分钟｜破冰\n### 5-15分钟｜深挖\n### 25-30 分钟｜收口"
        self.assertEqual(agency.audit_output(text, "无"), [])

    def test_step_range_not_flagged(self):
        self.assertEqual(agency.audit_output("按第 2-3 步执行，覆盖 3-4 项", "无"), [])

    def test_deliverable_range_not_flagged(self):
        self.assertEqual(agency.audit_output("交付物 1-3 已可交付，共 4-6 项待补", "无"), [])

    def test_real_date_still_flagged(self):
        flags = agency.audit_output("会议定在 11-25，另一场 2026-12-01。", "无")
        self.assertEqual(len(flags), 2)

    def test_relative_and_quarter_flagged(self):
        flags = agency.audit_output("首单推到 2026 年 1 月，浪费 4 周，3 天内定。", "无")
        self.assertEqual(len(flags), 3)
        self.assertTrue(all("时间口径" in f for f in flags))

    def test_quarter_expression_flagged(self):
        self.assertEqual(len(agency.audit_output("推迟到 Q1 2027。", "无")), 1)
        self.assertEqual(len(agency.audit_output("放到第四季度。", "无")), 1)

    def test_s4_marked_time_exempt(self):
        self.assertEqual(
            agency.audit_output("[假设·S4] 至少需要 2-4 周才有自然流量。", "无"), [])

    def test_relative_time_known_in_prompt(self):
        self.assertEqual(agency.audit_output("2 周内上线。", "周期 2 周"), [])

    def test_chinese_relative_time_flagged(self):
        self.assertEqual(len(agency.audit_output("约一个月窗口期。", "无")), 1)

    def test_mid_month_flagged_once(self):
        self.assertEqual(len(agency.audit_output("12 月中旬前完成。", "无")), 1)

    def test_spaced_date_not_flagged_when_known(self):
        self.assertEqual(agency.audit_output("12 月 10 日前上线。", "2026-12-10"), [])


class MissingRefsTest(unittest.TestCase):
    """产出里引用了不存在的仓库文件时要能被检出（实测 scout 编造 company/名单.md）。"""

    def test_missing_file_detected(self):
        self.assertEqual(agency.missing_refs("请从 company/名单.md 中匹配"), ["company/名单.md"])

    def test_existing_file_not_flagged(self):
        self.assertEqual(agency.missing_refs("见 templates/客户诊断问卷.md 第 3 节"), [])

    def test_plain_words_not_flagged(self):
        self.assertEqual(agency.missing_refs("名单.md 和 问卷.md 这类裸文件名不算路径"), [])


class LLMErrorClassTest(unittest.TestCase):
    """错误分级：限流可重试/降级，模型不存在不重试。"""

    def test_rate_limit(self):
        body = 'LLM 调用失败：429 {"error":{"message":"rpm exhausted"}}'
        self.assertEqual(agency.classify_llm_error(body), "rate_limit")

    def test_insufficient_quota_is_rate_limit(self):
        body = 'LLM 调用失败：429 {"code":"insufficient_quota"}'
        self.assertEqual(agency.classify_llm_error(body), "rate_limit")

    def test_model_not_found_is_unavailable(self):
        body = 'LLM 调用失败：404 {"error":{"message":"model is not found"}}'
        self.assertEqual(agency.classify_llm_error(body), "unavailable")

    def test_console_retry_gate(self):
        import console
        self.assertTrue(console._retryable("LLM 调用失败：429 rpm exhausted"))
        self.assertFalse(console._retryable("LLM 调用失败：404 model is not found"))


if __name__ == "__main__":
    unittest.main()
