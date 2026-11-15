#!/usr/bin/env python3
"""ratelimit：本地预算/速率/并发闸门（边界值来自 2026-11-12 实测）。"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ratelimit  # noqa: E402


class EstimateTest(unittest.TestCase):
    def test_chinese_ratio(self):
        self.assertTrue(1400 < ratelimit.estimate_tokens("测" * 3000) < 2100)

    def test_empty(self):
        self.assertEqual(ratelimit.estimate_tokens(""), 1)


class LimitsTest(unittest.TestCase):
    def setUp(self):
        self.orig = ratelimit.LIMITS
        self.orig_window = ratelimit.WINDOW
        ratelimit.WINDOW = 2.0  # 测试用短窗口，避免单测等 60s
        ratelimit.LIMITS = {
            "tiny": {"callable": True, "max_prompt_tokens": 1000, "tpm": 2000,
                     "rpm": 2, "concurrency": 1, "recovery_s": 60},
            "dead": {"callable": False, "max_prompt_tokens": 0, "tpm": 0,
                     "rpm": 0, "concurrency": 0, "recovery_s": 0},
        }
        ratelimit._LOCKS.clear()

    def tearDown(self):
        ratelimit.LIMITS = self.orig
        ratelimit.WINDOW = self.orig_window
        ratelimit._LOCKS.clear()

    def test_prompt_too_long(self):
        with self.assertRaises(ratelimit.PromptTooLong):
            with ratelimit.slot("tiny", 5000):
                pass

    def test_rpm_gate_waits(self):
        # rpm=2：连续占 2 个槽后，第 3 个必须等待
        with ratelimit.slot("tiny", 500):
            pass
        with ratelimit.slot("tiny", 500):
            pass
        start = time.time()
        with ratelimit.slot("tiny", 500):
            pass
        self.assertGreater(time.time() - start, 0.5)

    def test_unavailable_filtered(self):
        self.assertEqual(ratelimit.callable_models(["dead", "tiny"]), ["tiny"])

    def test_unknown_model_uses_default(self):
        conf = ratelimit.conf("never-seen")
        self.assertEqual(conf["tpm"], ratelimit.DEFAULT["tpm"])


class RolePrefsTest(unittest.TestCase):
    def setUp(self):
        self.orig = ratelimit.ROLE_PREFS
        ratelimit.ROLE_PREFS = {"pm": ["m2"]}

    def tearDown(self):
        ratelimit.ROLE_PREFS = self.orig

    def test_pref_first_then_rest(self):
        self.assertEqual(ratelimit.role_models("pm", ["m1", "m2", "m3"]),
                         ["m2", "m1", "m3"])

    def test_unknown_role_keeps_order(self):
        self.assertEqual(ratelimit.role_models("nobody", ["m1", "m2"]), ["m1", "m2"])


class DegenerateTest(unittest.TestCase):
    def test_repeated_line_detected(self):
        import agency
        text = "\n".join(["禁止输出空话"] * 8 + ["结论：不做"])
        self.assertIn("连续重复", agency.degenerate_reason(text))

    def test_normal_text_passes(self):
        import agency
        text = "\n".join(f"第 {i} 条：不同的结论内容 {i}" for i in range(30))
        self.assertEqual(agency.degenerate_reason(text), "")

    def test_empty_body_detected(self):
        import agency
        self.assertEqual(agency.degenerate_reason(" \n\t "), "正文为空")

    def test_short_body_detected_when_min_chars(self):
        import agency
        self.assertIn("正文过短", agency.degenerate_reason("太短了", min_chars=20))
        self.assertEqual(agency.degenerate_reason("太短了"), "")

    def test_mostly_duplicate_detected(self):
        import agency
        text = "\n".join(["同一段话"] * 45)
        self.assertNotEqual(agency.degenerate_reason(text), "")


if __name__ == "__main__":
    unittest.main()
