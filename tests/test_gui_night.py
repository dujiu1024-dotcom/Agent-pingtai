#!/usr/bin/env python3
"""电脑操作层：夜间策略、预授权、回执（分项 T-0024）"""

import datetime as dt
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gui  # noqa: E402
from tests.test_gui_actions import SAMPLE_APP, FakeGui  # noqa: E402


class WindowTest(unittest.TestCase):
    def test_parse_window(self):
        self.assertEqual(gui.parse_window("23:00-08:00"), (1380, 480))
        with self.assertRaises(gui.GuiError):
            gui.parse_window("23:00")
        with self.assertRaises(gui.GuiError):
            gui.parse_window("25:00-08:00")

    def test_cross_midnight(self):
        window = "23:00-08:00"
        self.assertTrue(gui.in_window(dt.datetime(2026, 11, 11, 23, 30), window))
        self.assertTrue(gui.in_window(dt.datetime(2026, 11, 11, 7, 59), window))
        self.assertFalse(gui.in_window(dt.datetime(2026, 11, 11, 8, 0), window))
        self.assertFalse(gui.in_window(dt.datetime(2026, 11, 11, 12, 0), window))

    def test_same_day(self):
        window = "09:00-18:00"
        self.assertTrue(gui.in_window(dt.datetime(2026, 11, 11, 10, 0), window))
        self.assertFalse(gui.in_window(dt.datetime(2026, 11, 11, 19, 0), window))

    def test_allow_patterns(self):
        self.assertTrue(gui.night_allowed("demo", "set", ["demo:set"]))
        self.assertTrue(gui.night_allowed("demo", "click", ["demo:*"]))
        self.assertFalse(gui.night_allowed("demo", "click", ["demo:set"]))
        self.assertFalse(gui.night_allowed("demo", "set", []))


class NightGateTest(unittest.TestCase):
    def setUp(self):
        self.platform_guard = patch("gui.require_platform")
        self.platform_guard.start()
        self.addCleanup(self.platform_guard.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.apps = self.root / "apps"
        self.apps.mkdir()
        self.base = self.root / "gui"
        (self.apps / "demo.json").write_text(
            json.dumps(SAMPLE_APP, ensure_ascii=False), encoding="utf-8")
        self.night = dt.datetime(2026, 11, 11, 23, 30)

    def tearDown(self):
        self.tmp.cleanup()

    def run_plan(self, plan, fake, **kw):
        return gui.run_plan("demo", plan, apps_dir=self.apps, base=self.base,
                            run=fake, **kw)

    def test_write_blocked_at_night(self):
        fake = FakeGui()
        result = self.run_plan(
            {"steps": [{"action": "set",
                        "selector": {"role": "AXTextField", "title": "输入"},
                        "value": "x"}]},
            fake, night_window="23:00-08:00", night_allow=[], now=self.night)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["stop_reason"], "夜间未授权")
        self.assertEqual(fake.named("set-value"), [])
        pending = gui.read_pending(self.base)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["app"], "demo")

    def test_allow_list_lets_write_through(self):
        fake = FakeGui()
        result = self.run_plan(
            {"steps": [{"action": "set",
                        "selector": {"role": "AXTextField", "title": "输入"},
                        "value": "x"}]},
            fake, night_window="23:00-08:00", night_allow=["demo:set"],
            now=self.night)
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(fake.named("set-value")), 1)

    def test_read_only_passes_at_night(self):
        result = self.run_plan({"steps": [{"action": "observe"}]},
                               FakeGui(), night_window="23:00-08:00",
                               night_allow=[], now=self.night)
        self.assertEqual(result["status"], "done")

    def test_day_not_blocked(self):
        result = self.run_plan(
            {"steps": [{"action": "set",
                        "selector": {"role": "AXTextField", "title": "输入"},
                        "value": "x"}]},
            FakeGui(), night_window="23:00-08:00", night_allow=[],
            now=dt.datetime(2026, 11, 11, 12, 0))
        self.assertEqual(result["status"], "done")


class BlockedSessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def checks(self, ok):
        return [{"key": "lock", "label": "是否锁屏", "ok": ok,
                 "detail": "已锁屏" if not ok else "已解锁", "fix": "解锁"}]

    def test_blocked_session_is_traceable(self):
        sid = gui.blocked_session("demo", self.checks(False), base=self.base)
        state = gui.load_session(sid, self.base)
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["stop_reason"], "权限缺失")
        self.assertTrue((self.base / sid / "summary.md").exists())
        self.assertEqual(len(gui.read_actions(sid, self.base)), 1)

    def test_receipt(self):
        sid = gui.blocked_session("demo", self.checks(False), base=self.base)
        gui.append_pending({"session": sid, "app": "demo"}, self.base)
        info = gui.receipt(sid, self.base)
        self.assertEqual(info["session"], sid)
        self.assertEqual(info["stop_reason"], "权限缺失")
        self.assertEqual(info["screenshots"], 0)
        self.assertEqual(info["pending"], 1)


if __name__ == "__main__":
    unittest.main()
