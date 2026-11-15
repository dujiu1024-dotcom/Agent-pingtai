#!/usr/bin/env python3
"""电脑操作层：动作执行、校验与停手（分项 T-0023）"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gui  # noqa: E402

SAMPLE_APP = {
    "id": "demo",
    "name": "演示应用",
    "bundle_id": "com.example.demo",
    "launch": "open -b com.example.demo",
    "window_match": "演示应用",
    "interactive": [{"key": "input", "role": "AXTextArea", "title": "输入"}],
    "dialogs": ["登录"],
    "dangerous": ["删除"],
    "verified_at": "2026-11-11",
}


class FakeGui:
    """动作层替身：模拟 peekaboo 的 see / set-value / 动作命令。"""

    def __init__(self, elements=None, title="演示", dialog=False, see_ok=True,
                 fail_times=0):
        self.elements = elements if elements is not None else [
            {"id": "elem_1", "ax_role": "AXTextField", "label": "输入",
             "value": "", "is_actionable": True},
            {"id": "elem_2", "ax_role": "AXButton", "label": "发送",
             "is_actionable": True},
        ]
        self.title = title
        self.dialog = dialog
        self.see_ok = see_ok
        self.fail_times = fail_times
        self.calls = []

    def __call__(self, argv, timeout=25):
        self.calls.append(list(argv))
        if argv[0].endswith("peekaboo") and argv[1] == "see":
            png = Path(argv[argv.index("--path") + 1])
            png.parent.mkdir(parents=True, exist_ok=True)
            png.write_bytes(b"\x89PNG\r\n\x1a\n")
            if "--no-elements" in argv:
                return 0, "", ""
            if not self.see_ok:
                return 0, json.dumps({"success": False,
                                      "error": {"message": "session locked"}}), ""
            payload = {"success": True, "data": {
                "window_title": self.title, "is_dialog": self.dialog,
                "snapshot_id": "snap-1", "ui_elements": self.elements}}
            return 0, json.dumps(payload), ""
        if argv[0].endswith("peekaboo") and argv[1] == "set-value":
            value, elem_id = argv[2], argv[argv.index("--on") + 1]
            for e in self.elements:
                if e.get("id") == elem_id:
                    e["value"] = value
            return 0, "", ""
        if self.fail_times > 0 and argv[0].endswith("peekaboo"):
            self.fail_times -= 1
            return 1, "", "boom"
        return 0, "", ""

    def named(self, action):
        return [c for c in self.calls
                if c[0].endswith("peekaboo") and len(c) > 1 and c[1] == action]


class PlanTest(unittest.TestCase):
    def test_reject_unknown_action(self):
        with self.assertRaises(gui.GuiError):
            gui.validate_plan({"steps": [{"action": "exec"}]})

    def test_reject_unknown_key(self):
        with self.assertRaises(gui.GuiError):
            gui.validate_plan({"steps": [{"action": "click",
                                          "selector": {"id": "e1"},
                                          "extra": 1}]})

    def test_reject_missing_selector(self):
        with self.assertRaises(gui.GuiError):
            gui.validate_plan({"steps": [{"action": "click"}]})

    def test_reject_coordinate_without_reason(self):
        with self.assertRaises(gui.GuiError):
            gui.normalize_selector({"x": 10, "y": 20})

    def test_reject_key_without_focus_consent(self):
        with self.assertRaises(gui.GuiError):
            gui.validate_plan({"steps": [{"action": "key", "combo": "cmd+c"}]})

    def test_selector_priority(self):
        self.assertEqual(gui.normalize_selector({"id": "elem_9"})["kind"], "id")
        self.assertEqual(
            gui.normalize_selector({"role": "AXButton", "title": "发送"})["kind"],
            "role_title")
        self.assertEqual(
            gui.normalize_selector({"role": "AXButton", "index": 1})["kind"],
            "role_index")

    def test_resolve_selectors(self):
        elements = [{"id": "e1", "ax_role": "AXButton", "label": "发送"},
                    {"id": "e2", "ax_role": "AXButton", "label": "删除"},
                    {"id": "e3", "ax_role": "AXTextField", "label": "输入"}]
        self.assertEqual(gui.resolve_selector(
            elements, gui.normalize_selector({"id": "e2"}))["id"], "e2")
        self.assertEqual(gui.resolve_selector(
            elements, gui.normalize_selector(
                {"role": "AXTextField", "title": "输入"}))["id"], "e3")
        self.assertEqual(gui.resolve_selector(
            elements, gui.normalize_selector(
                {"role": "AXButton", "index": 1}))["id"], "e2")
        self.assertIsNone(gui.resolve_selector(
            elements, gui.normalize_selector({"id": "e9"})))

    def test_resolve_selector_matches_overwritten_value(self):
        elements = [{"id": "e1", "ax_role": "AXTextField", "label": "hello",
                     "value": "hello"}]
        selector = gui.normalize_selector(
            {"role": "AXTextField", "title": "hello"})
        self.assertEqual(gui.resolve_selector(elements, selector)["id"], "e1")


class ActionRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.apps = self.root / "apps"
        self.apps.mkdir()
        self.base = self.root / "gui"
        (self.apps / "demo.json").write_text(
            json.dumps(SAMPLE_APP, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_plan(self, plan, fake):
        return gui.run_plan("demo", plan, apps_dir=self.apps, base=self.base,
                            run=fake)

    def test_full_plan_done(self):
        fake = FakeGui()
        plan = {"goal": "输入并校验", "steps": [
            {"action": "launch"},
            {"action": "observe"},
            {"action": "set", "selector": {"role": "AXTextField",
                                           "title": "输入"},
             "value": "hello"},
            {"action": "assert",
             "expect": {"kind": "text_contains",
                        "selector": {"role": "AXTextField", "title": "输入"},
                        "text": "hello"}},
            {"action": "screenshot"},
        ]}
        result = self.run_plan(plan, fake)
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(result["steps"]), 5)
        session = self.base / result["session"]
        shots = list((session / "steps").glob("*.png"))
        self.assertGreaterEqual(len(shots), 3)
        self.assertTrue((session / "summary.md").exists())
        self.assertEqual(len(fake.named("set-value")), 1)
        self.assertIn("hello", [c[2] for c in fake.named("set-value")])

    def test_dialog_stops(self):
        result = self.run_plan({"steps": [{"action": "observe"}]},
                               FakeGui(dialog=True))
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["stop_reason"], "未知弹窗")
        self.assertTrue(list((self.base / result["session"] / "steps")
                             .glob("*stop*")))

    def test_captcha_title_stops(self):
        result = self.run_plan({"steps": [{"action": "observe"}]},
                               FakeGui(title="安全验证码"))
        self.assertEqual(result["stop_reason"], "验证码")

    def test_dangerous_title_stops(self):
        result = self.run_plan({"steps": [{"action": "observe"}]},
                               FakeGui(title="确认删除"))
        self.assertEqual(result["stop_reason"], "危险动作")

    def test_two_validation_failures_stop(self):
        plan = {"steps": [
            {"action": "assert", "expect": {"kind": "window_title_contains",
                                            "text": "不存在"}},
            {"action": "assert", "expect": {"kind": "window_title_contains",
                                            "text": "不存在"}},
        ]}
        result = self.run_plan(plan, FakeGui())
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["stop_reason"], "校验失败")

    def test_command_retried_once(self):
        fake = FakeGui(fail_times=1)
        plan = {"steps": [
            {"action": "click", "selector": {"role": "AXButton",
                                             "title": "发送"}},
        ]}
        result = self.run_plan(plan, fake)
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(fake.named("click")), 2)

    def test_command_failure_stops(self):
        fail_fake = FakeGui(fail_times=99)
        result = self.run_plan({"steps": [
            {"action": "click", "selector": {"id": "elem_2"}},
        ]}, fail_fake)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["stop_reason"], "动作失败")

    def test_selector_miss_stops(self):
        result = self.run_plan(
            {"steps": [{"action": "click", "selector": {"id": "elem_9"}}]},
            FakeGui())
        self.assertEqual(result["stop_reason"], "选择器未命中")

    def test_unverified_app_rejected(self):
        (self.apps / "demo.json").write_text(
            json.dumps(dict(SAMPLE_APP, verified_at=""), ensure_ascii=False),
            encoding="utf-8")
        with self.assertRaises(gui.GuiError):
            gui.run_plan("demo", {"steps": [{"action": "observe"}]},
                         apps_dir=self.apps, base=self.base, run=FakeGui())


class StopRuleTest(unittest.TestCase):
    def test_sensitive_titles(self):
        app = dict(SAMPLE_APP)
        for title, reason in (("请输入支付密码", "支付页"),
                              ("微博授权", "授权页"),
                              ("登录 - 豆包", "登录失效")):
            self.assertEqual(
                gui.detect_stop(app, {"window_title": title,
                                      "is_dialog": False}), reason)

    def test_registry_dialog_and_dangerous(self):
        app = dict(SAMPLE_APP)
        self.assertEqual(
            gui.detect_stop(app, {"window_title": "登录", "is_dialog": False}),
            "登录失效")
        self.assertEqual(
            gui.detect_stop(app, {"window_title": "删除", "is_dialog": False}),
            "危险动作")

    def test_clean_window(self):
        self.assertEqual(
            gui.detect_stop(dict(SAMPLE_APP),
                            {"window_title": "轻量决策模型 - 豆包",
                             "is_dialog": False}), "")


if __name__ == "__main__":
    unittest.main()
