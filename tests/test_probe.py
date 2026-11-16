#!/usr/bin/env python3
"""probe.py 测试（T-0026）：默认关闭、产物隔离、结论固化。"""

import json
import tempfile
import unittest
from pathlib import Path

import gui as guim
import probe as pm
from tests.test_gui import make_png

APP = {"id": "sample", "name": "示例应用", "bundle_id": "com.example.app"}
ELEMENTS = [
    {"role": "button", "ax_role": "AXButton", "title": "提交",
     "label": "提交", "is_actionable": True},
    {"role": "textfield", "ax_role": "AXTextField", "title": "",
     "label": "姓名", "is_actionable": True},
    {"role": "static", "ax_role": "AXStaticText", "title": "说明",
     "is_actionable": False},
]


class FakeCapture:
    def __init__(self, elements=None, ok=True, is_dialog=False, message=""):
        self.elements = elements if elements is not None else ELEMENTS
        self.ok = ok
        self.is_dialog = is_dialog
        self.message = message
        self.calls = 0

    def __call__(self, app, png=None, run=guim.run_cmd, timeout=45):
        self.calls += 1
        if png:
            make_png(Path(png), rgb=(180, 180, 180))
        return {"ok": self.ok, "argv": ["peekaboo", "see"], "ms": 10,
                "elements": self.elements, "window_title": "示例窗口",
                "is_dialog": self.is_dialog, "message": self.message}


def fake_agent(prompt, run=guim.run_cmd, timeout=60):
    return {"ok": True, "command": ["openclaw", "agent"], "ms": 5,
            "text": "可自动化，关键元素是提交按钮。", "error": ""}


class AnalyzeTest(unittest.TestCase):
    def test_yes(self):
        cap = FakeCapture()(APP)
        out = pm.analyze(APP, cap, cap)
        self.assertEqual(out["feasible"], "yes")
        self.assertEqual(len(out["key_elements"]), 2)
        self.assertEqual(out["key_elements"][0]["role"], "AXButton")

    def test_no_elements(self):
        cap = FakeCapture(elements=[])(APP)
        self.assertEqual(pm.analyze(APP, cap, cap)["feasible"], "no")

    def test_webview(self):
        cap = FakeCapture(elements=[
            {"role": "webarea", "ax_role": "AXWebArea", "title": "",
             "is_actionable": False},
            {"role": "button", "ax_role": "AXButton", "title": "关闭",
             "is_actionable": True},
        ])(APP)
        out = pm.analyze(APP, cap, cap)
        self.assertEqual(out["feasible"], "browser")
        self.assertTrue(out["counts"]["web_area"])

    def test_dialog_is_blocker(self):
        cap = FakeCapture(is_dialog=True)(APP)
        out = pm.analyze(APP, cap, cap)
        self.assertEqual(out["feasible"], "yes")
        self.assertTrue(out["blockers"])

    def test_capture_failed(self):
        cap = FakeCapture(ok=False, message="窗口不可见")(APP)
        out = pm.analyze(APP, cap, cap)
        self.assertEqual(out["feasible"], "unknown")
        self.assertIn("窗口不可见", out["summary"])


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.base = self.root / "probe"
        self.gui_base = self.root / "gui"

    def tearDown(self):
        self.tmp.cleanup()

    def test_artifacts_isolated(self):
        result = pm.run(APP, goal="填表", base=self.base,
                        capture=FakeCapture(), agent_fn=fake_agent)
        root = pm.probe_dir(result["probe"], self.base)
        for name in ("before.png", "after.png", "report.md", "result.json"):
            self.assertTrue((root / name).exists(), name)
        self.assertFalse(self.gui_base.exists())
        data = json.loads((root / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(data["conclusion"]["feasible"], "yes")
        self.assertTrue(data["agent"]["ok"])
        self.assertEqual(data["steps"], 3)

    def test_no_agent_with_two_steps(self):
        result = pm.run(APP, max_steps=2, base=self.base,
                        capture=FakeCapture(), agent_fn=fake_agent)
        self.assertEqual(result["steps"], 2)
        self.assertFalse(result["agent"]["ok"])
        self.assertIn("步数不足", result["agent"]["error"])

    def test_agent_failure_is_recorded(self):
        def bad_agent(prompt, run=guim.run_cmd, timeout=60):
            return {"ok": False, "command": ["openclaw", "agent"], "ms": 3,
                    "text": "", "error": "requires credentials"}

        result = pm.run(APP, base=self.base, capture=FakeCapture(),
                        agent_fn=bad_agent)
        self.assertFalse(result["agent"]["ok"])
        self.assertIn("credentials", result["agent"]["error"])
        self.assertEqual(result["conclusion"]["feasible"], "yes")

    def test_capture_failure_still_concludes(self):
        result = pm.run(APP, base=self.base,
                        capture=FakeCapture(ok=False, message="找不到窗口"),
                        agent_fn=fake_agent)
        self.assertEqual(result["conclusion"]["feasible"], "unknown")
        self.assertTrue((pm.probe_dir(result["probe"], self.base)
                         / "report.md").exists())


class PromoteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.base = self.root / "probe"
        self.apps = self.root / "apps"
        self.apps.mkdir()
        self.result = pm.run(APP, goal="填表", base=self.base,
                             capture=FakeCapture(), agent_fn=fake_agent)

    def tearDown(self):
        self.tmp.cleanup()

    def test_promote_draft(self):
        info = pm.promote(self.result["probe"], base=self.base,
                          apps_dir=self.apps)
        draft = json.loads(Path(info["path"]).read_text(encoding="utf-8"))
        self.assertEqual(draft["id"], "sample")
        self.assertEqual(draft["verified_at"], "")
        self.assertTrue(draft["interactive"])
        guim.validate_app(draft, "sample.json")
        with self.assertRaises(pm.ProbeError):
            pm.promote(self.result["probe"], base=self.base,
                       apps_dir=self.apps)

    def test_refuse_non_yes(self):
        result = pm.run(APP, base=self.base, capture=FakeCapture(elements=[]),
                        agent_fn=fake_agent)
        with self.assertRaises(pm.ProbeError):
            pm.promote(result["probe"], base=self.base, apps_dir=self.apps)


class LayoutTest(unittest.TestCase):
    def test_bad_id(self):
        with self.assertRaises(pm.ProbeError):
            pm.probe_dir("../../etc")

    def test_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "probe"
            pm.run(APP, base=base, capture=FakeCapture(),
                   agent_fn=fake_agent, probe_id="probe-20261111-120000-abcd")
            rows = pm.list_probes(base=base)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["feasible"], "yes")


if __name__ == "__main__":
    unittest.main()
