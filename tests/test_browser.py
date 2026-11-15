#!/usr/bin/env python3
"""browser.py 测试（T-0025）：选择器、计划校验、命令脱敏、会话执行。"""

import json
import tempfile
import unittest
from pathlib import Path

import browser as bm
import gui as guim
from tests.test_gui import make_png


class SelectorTest(unittest.TestCase):
    def test_ref(self):
        self.assertEqual(bm.normalize_selector({"ref": "e5"}),
                         {"kind": "ref", "ref": "e5"})

    def test_role_name(self):
        self.assertEqual(
            bm.normalize_selector({"role": "textbox", "name": "Name:"}),
            {"kind": "role_name", "role": "textbox", "name": "Name:"})

    def test_role_index(self):
        self.assertEqual(
            bm.normalize_selector({"role": "button", "index": 1}),
            {"kind": "role_index", "role": "button", "index": 1})

    def test_rejects(self):
        for bad in ({"role": "Button"}, {"role": "textbox"}, {"x": 1},
                    {"ref": ""}, {"role": "textbox", "index": -1}):
            with self.assertRaises(bm.BrowserError):
                bm.normalize_selector(bad)

    def test_resolve_exact_then_fuzzy(self):
        refs = {"e1": {"role": "textbox", "name": "Customer name:"},
                "e2": {"role": "textbox", "name": "Customer name (optional)"},
                "e3": {"role": "button", "name": "Submit order"}}
        self.assertEqual(bm.resolve_ref(refs, bm.normalize_selector(
            {"role": "textbox", "name": "Customer name:"}))[0], "e1")
        self.assertEqual(bm.resolve_ref(refs, bm.normalize_selector(
            {"role": "textbox", "name": "optional"}))[0], "e2")
        self.assertEqual(bm.resolve_ref(refs, bm.normalize_selector(
            {"role": "button", "index": 0}))[0], "e3")
        ref, err = bm.resolve_ref(refs, bm.normalize_selector(
            {"role": "button", "index": 2}))
        self.assertIsNone(ref)
        self.assertIn("越界", err)
        ref, err = bm.resolve_ref(refs, bm.normalize_selector(
            {"role": "link", "name": "x"}))
        self.assertIsNone(ref)


class PlanTest(unittest.TestCase):
    def test_valid(self):
        plan = {"url": "https://example.test/f",
                "steps": [{"action": "open", "url": "https://example.test/f"},
                          {"action": "click",
                           "selector": {"role": "button", "name": "Go"}},
                          {"action": "fill", "fields": [
                              {"selector": {"ref": "e1"}, "value": "x"}]},
                          {"action": "assert",
                           "expect": {"text_contains": "ok"}}]}
        self.assertEqual(len(bm.validate_plan(plan)), 4)

    def test_rejects(self):
        bad = [
            {"steps": [{"action": "eval"}]},
            {"steps": [{"action": "open", "url": "file:///etc/passwd"}]},
            {"steps": [{"action": "click"}]},
            {"steps": [{"action": "type", "selector": {"ref": "e1"},
                        "text": ""}]},
            {"steps": [{"action": "fill", "fields": []}]},
            {"steps": [{"action": "wait", "ms": 999999}]},
            {"steps": [{"action": "assert", "expect": {}}]},
            {"steps": [{"action": "click", "selector": {"ref": "e1"},
                        "extra": 1}]},
            {"steps": []},
            {"unknown": 1, "steps": [{"action": "snapshot"}]},
        ]
        for plan in bad:
            with self.assertRaises(bm.BrowserError):
                bm.validate_plan(plan)


class FakeOpenClaw:
    """按子命令返回固定 JSON，记录每次 argv。"""

    def __init__(self, shot_dir):
        self.calls = []
        self.shot_dir = Path(shot_dir)
        self.url = "https://example.test/form"
        self.refs = {"e5": {"role": "textbox", "name": "Customer name:"},
                     "e7": {"role": "button", "name": "Submit order"}}
        self.click_race = True
        self.shot_missing = False

    def __call__(self, argv, timeout=45):
        self.calls.append(argv)
        cmd = argv[2]
        if cmd == "open":
            return 0, json.dumps({"suggestedTargetId": "t1",
                                  "url": argv[3]}), ""
        if cmd == "snapshot":
            refs = ({"e9": {"role": "generic"}} if self.url.endswith("/done")
                    else self.refs)
            return 0, json.dumps({"ok": True, "url": self.url,
                                  "targetId": "t1", "refs": refs,
                                  "snapshot": "- textbox [ref=e5]"}), ""
        if cmd == "click":
            if self.click_race:
                self.url = "https://example.test/done"
                return 1, "", ('Error: Unknown ref "%s". Run a new snapshot'
                               % argv[3])
            return 0, json.dumps({"ok": True, "url": self.url}), ""
        if cmd == "fill":
            return 0, json.dumps({"ok": True, "url": self.url}), ""
        if cmd == "evaluate":
            text = "submitted 龙奕" if self.url.endswith("/done") else "form"
            return 0, json.dumps({"ok": True, "url": self.url,
                                  "result": text}), ""
        if cmd == "screenshot":
            if self.shot_missing:
                return 0, json.dumps({"ok": True,
                                      "path": str(self.shot_dir / "nope.png")}), ""
            return 0, json.dumps({"ok": True,
                                  "path": str(self.shot_dir / "shot.png")}), ""
        if cmd == "tabs":
            return 0, json.dumps({"tabs": [{"tabId": "t1",
                                            "suggestedTargetId": "t1"}]}), ""
        return 0, json.dumps({"ok": True}), ""


class CallTest(unittest.TestCase):
    def setUp(self):
        self._token = bm.TOKEN
        bm.TOKEN = "tok-123"

    def tearDown(self):
        bm.TOKEN = self._token

    def test_masks_token(self):
        seen = {}

        def run(argv, timeout=45):
            seen["argv"] = argv
            return 0, json.dumps({"ok": True}), ""

        res = bm.status(run)
        self.assertTrue(res["ok"])
        self.assertIn("--token", seen["argv"])
        self.assertNotIn("tok-123", res["argv"])
        self.assertIn("***", res["argv"])

    def test_missing_token(self):
        bm.TOKEN = ""
        with self.assertRaises(bm.BrowserError):
            bm.status(lambda a, t=45: (0, "{}", ""))

    def test_error_propagates(self):
        def run(argv, timeout=45):
            return 1, json.dumps({"ok": False,
                                  "error": {"message": "boom"}}), ""

        res = bm.status(run)
        self.assertFalse(res["ok"])
        self.assertEqual(res["message"], "boom")


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name) / "gui"
        self._token = bm.TOKEN
        bm.TOKEN = "tok-123"
        make_png(Path(self.tmp.name) / "shot.png", rgb=(200, 200, 200))
        self.fake = FakeOpenClaw(self.tmp.name)

    def tearDown(self):
        bm.TOKEN = self._token
        self.tmp.cleanup()

    def _plan(self):
        return {"url": "https://example.test/form", "steps": [
            {"action": "open", "url": "https://example.test/form"},
            {"action": "snapshot"},
            {"action": "fill", "fields": [
                {"selector": {"role": "textbox", "name": "Customer name:"},
                 "value": "龙奕"}]},
            {"action": "click", "selector": {"role": "button",
                                             "name": "Submit order"}},
            {"action": "assert", "expect": {"text_contains": "龙奕"}},
            {"action": "screenshot"},
        ]}

    def test_happy_path_with_navigation_race(self):
        result = bm.run_plan(self._plan(), base=self.base, run=self.fake)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["url"], "https://example.test/done")
        actions = guim.read_actions(result["session"], base=self.base)
        self.assertEqual([a["action"] for a in actions],
                         ["open", "snapshot", "fill", "click", "assert",
                          "screenshot"])
        self.assertTrue(all(a["ok"] for a in actions))
        rel = [a.get("screenshot") for a in actions if a.get("screenshot")]
        self.assertTrue(rel)
        shot = guim.session_dir(result["session"], self.base) / rel[0]
        self.assertTrue(shot.exists())
        recorded = json.dumps(actions, ensure_ascii=False)
        self.assertNotIn("tok-123", recorded)

    def test_click_isolation(self):
        """点击不触发导航时按真实返回判定，不做 URL 兜底。"""
        self.fake.click_race = False
        plan = {"url": "https://example.test/form", "steps": [
            {"action": "open", "url": "https://example.test/form"},
            {"action": "click", "selector": {"role": "button",
                                             "name": "Submit order"}}]}
        result = bm.run_plan(plan, base=self.base, run=self.fake)
        self.assertEqual(result["status"], "done")

    def test_selector_miss_stops(self):
        plan = {"url": "https://example.test/form", "steps": [
            {"action": "open", "url": "https://example.test/form"},
            {"action": "click", "selector": {"role": "link",
                                             "name": "不存在"}}]}
        result = bm.run_plan(plan, base=self.base, run=self.fake)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["stop_reason"], "选择器未命中")

    def test_night_block_writes_pending(self):
        import datetime as dt
        bm.TOKEN = "tok-123"
        plan = {"url": "https://example.test/form", "steps": [
            {"action": "open", "url": "https://example.test/form"},
            {"action": "fill", "fields": [
                {"selector": {"ref": "e5"}, "value": "x"}]}]}
        result = bm.run_plan(plan, base=self.base, run=self.fake,
                             night_window="23:00-08:00",
                             now=dt.datetime(2026, 11, 11, 23, 30))
        self.assertEqual(result["stop_reason"], "夜间未授权")
        pending = guim.read_pending(self.base)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["app"], "browser")

    def test_screenshot_missing_fails(self):
        self.fake.shot_missing = True
        plan = {"url": "https://example.test/form", "steps": [
            {"action": "open", "url": "https://example.test/form"},
            {"action": "screenshot"}]}
        result = bm.run_plan(plan, base=self.base, run=self.fake)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["stop_reason"], "动作失败")

    def test_sessions_isolated(self):
        result = bm.run_plan(self._plan(), base=self.base, run=self.fake)
        root = guim.session_dir(result["session"], self.base)
        state = guim.load_session(result["session"], self.base)
        self.assertEqual(state["kind"], "browser")
        self.assertEqual(state["app"], "browser")
        for path in state["artifacts"]:
            self.assertTrue(str(root) in str(path) or not Path(path).is_absolute())
        self.assertEqual(len(bm.list_sessions(base=self.base)), 1)


if __name__ == "__main__":
    unittest.main()
