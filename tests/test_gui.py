#!/usr/bin/env python3
"""电脑操作层：注册表、会话、预检、只读感知（分项 T-0022）"""

import json
import plistlib
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gui  # noqa: E402


def make_png(path, width=4, height=4, rgb=(0, 0, 0)):
    """生成 8bit RGB、filter=0 的最小 PNG，用于亮度测试。"""
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                           + chunk(b"IDAT", zlib.compress(raw))
                           + chunk(b"IEND", b""))
    return path


def fake_run_factory(shot_locked=False, sleep_blocked=True,
                     ax_granted=True, elements=None, see_ok=True,
                     shot_rgb=(20, 20, 20)):
    """构造只读命令替身：按 argv 前缀返回预置结果。"""

    def fake(argv, timeout=25):
        if argv[0].endswith("peekaboo") and argv[1] == "permissions":
            payload = {"success": True, "data": {"permissions": [
                {"name": "Accessibility", "isGranted": ax_granted},
                {"name": "Screen Recording", "isGranted": True},
            ]}}
            return 0, json.dumps(payload), ""
        if argv[0] == "screencapture":
            make_png(argv[2], 8, 8, shot_rgb)
            return 0, "", ""
        if argv[0] == "sips":
            make_png(argv[-1], 4, 4, shot_rgb)
            return 0, "", ""
        if argv[0] == "ioreg":
            data = {"IOConsoleLocked": shot_locked,
                    "IOConsoleUsers": [{"CGSSessionScreenIsLocked": shot_locked}]}
            return 0, plistlib.dumps(data).decode("utf-8"), ""
        if argv[0] == "pmset":
            value = 1 if sleep_blocked else 0
            text = ("   PreventUserIdleDisplaySleep    1\n"
                    "   PreventUserIdleSystemSleep    %d\n" % value)
            return 0, text, ""
        if argv[0].endswith("peekaboo") and argv[1] == "see":
            png = Path(argv[argv.index("--path") + 1])
            png.parent.mkdir(parents=True, exist_ok=True)
            if not see_ok:
                payload = {"success": False,
                           "data": None,
                           "error": {"code": "CAPTURE_FAILED",
                                     "message": "session is locked"}}
                return 0, json.dumps(payload), ""
            png.write_bytes(b"\x89PNG\r\n\x1a\n")
            payload = {"success": True,
                       "data": {"snapshot_id": "snap-1",
                                "ui_elements": elements or []}}
            return 0, json.dumps(payload), ""
        return 1, "", "unexpected: %s" % " ".join(argv[:3])

    return fake


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


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data, name="demo.json"):
        path = self.dir / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def test_valid_app_loads(self):
        self.write(SAMPLE_APP)
        app = gui.load_app("demo", self.dir)
        self.assertTrue(app["verified"])
        self.assertEqual(app["name"], "演示应用")

    def test_unknown_key_rejected(self):
        self.write(dict(SAMPLE_APP, extra="x"))
        with self.assertRaises(gui.RegistryError):
            gui.load_app("demo", self.dir)

    def test_filename_mismatch_rejected(self):
        self.write(SAMPLE_APP, "other.json")
        with self.assertRaises(gui.RegistryError):
            gui.load_app("other", self.dir)

    def test_bad_role_and_duplicate_key_rejected(self):
        bad = dict(SAMPLE_APP, interactive=[
            {"key": "a", "role": "Button", "title": "t"},
            {"key": "a", "role": "AXButton", "title": "t"},
        ])
        self.write(bad)
        with self.assertRaises(gui.RegistryError):
            gui.load_app("demo", self.dir)

    def test_unverified_listed(self):
        self.write(dict(SAMPLE_APP, verified_at=""))
        rows = gui.list_apps(self.dir)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["ok"])
        self.assertFalse(rows[0]["verified"])

    def test_missing_app_rejected(self):
        with self.assertRaises(gui.RegistryError):
            gui.load_app("ghost", self.dir)


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_and_record(self):
        state = gui.create_session("demo", "读界面", base=self.base)
        root = self.base / state["session"]
        self.assertTrue((root / "state.json").exists())
        self.assertTrue((root / "steps").is_dir())
        item = gui.record_action(state, "observe", {"app": "demo"},
                                 result={"element_count": 3}, base=self.base)
        self.assertEqual(item["step"], 1)
        lines = (root / "actions.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["result"]["element_count"], 3)

    def test_stop_and_resume(self):
        state = gui.create_session("demo", "g", base=self.base)
        gui.set_status(state, "stopped", "未知弹窗", base=self.base)
        self.assertEqual(state["stop_reason"], "未知弹窗")
        gui.set_status(state, "running", base=self.base)
        self.assertEqual(state["stop_reason"], "")
        gui.finish_session(state, "完成", base=self.base)
        self.assertEqual(state["status"], "done")
        with self.assertRaises(gui.GuiError):
            gui.set_status(state, "running", base=self.base)

    def test_max_steps_stops(self):
        state = gui.create_session("demo", "g", max_steps=1, base=self.base)
        gui.record_action(state, "observe", base=self.base)
        with self.assertRaises(gui.GuiError):
            gui.record_action(state, "observe", base=self.base)
        self.assertEqual(gui.load_session(state["session"], self.base)["status"],
                         "stopped")

    def test_action_after_finish_rejected(self):
        state = gui.create_session("demo", "g", base=self.base)
        gui.finish_session(state, "完成", base=self.base)
        with self.assertRaises(gui.GuiError):
            gui.record_action(state, "observe", base=self.base)

    def test_list_sessions_sorted(self):
        first = gui.create_session("demo", "a", base=self.base)
        old = dict(first, updated_at="2026-01-01T00:00:00+08:00")
        (self.base / first["session"] / "state.json").write_text(
            json.dumps(old, ensure_ascii=False), encoding="utf-8")
        second = gui.create_session("demo", "b", base=self.base)
        rows = gui.list_sessions(base=self.base)
        self.assertEqual(rows[0]["session"], second["session"])

    def test_illegal_session_id(self):
        with self.assertRaises(gui.GuiError):
            gui.load_session("../etc/passwd", self.base)


class PreflightTest(unittest.TestCase):
    def test_all_pass(self):
        report = gui.preflight(fake_run_factory(elements=[]))
        self.assertTrue(report["ok"])
        self.assertEqual([c["key"] for c in report["checks"]],
                         ["accessibility", "screen", "lock", "sleep"])

    def test_locked_blocks(self):
        report = gui.preflight(fake_run_factory(shot_locked=True))
        self.assertFalse(report["ok"])
        lock = [c for c in report["checks"] if c["key"] == "lock"][0]
        self.assertFalse(lock["ok"])

    def test_missing_accessibility(self):
        report = gui.preflight(fake_run_factory(ax_granted=False))
        self.assertFalse(report["ok"])

    def test_sleep_not_blocked(self):
        report = gui.preflight(fake_run_factory(sleep_blocked=False))
        sleep = [c for c in report["checks"] if c["key"] == "sleep"][0]
        self.assertFalse(sleep["ok"])

    def test_black_screen_detected(self):
        report = gui.preflight(fake_run_factory(shot_rgb=(0, 0, 0)))
        screen = [c for c in report["checks"] if c["key"] == "screen"][0]
        self.assertFalse(screen["ok"])

    def test_png_luma(self):
        with tempfile.TemporaryDirectory() as tmp:
            black = make_png(Path(tmp) / "b.png", rgb=(0, 0, 0))
            white = make_png(Path(tmp) / "w.png", rgb=(255, 255, 255))
            self.assertLess(gui.png_luma(black), 1)
            self.assertGreater(gui.png_luma(white), 250)


class ObserveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.apps = self.root / "apps"
        self.apps.mkdir()
        self.base = self.root / "gui"

    def tearDown(self):
        self.tmp.cleanup()

    def add_app(self, **over):
        data = dict(SAMPLE_APP, **over)
        (self.apps / "demo.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_unregistered_rejected(self):
        with self.assertRaises(gui.RegistryError):
            gui.observe("demo", apps_dir=self.apps, base=self.base,
                        run=fake_run_factory())

    def test_unverified_rejected(self):
        self.add_app(verified_at="")
        with self.assertRaises(gui.GuiError):
            gui.observe("demo", apps_dir=self.apps, base=self.base,
                        run=fake_run_factory())

    def test_observe_writes_session(self):
        self.add_app()
        elements = [{"id": "e1", "ax_role": "AXButton", "is_actionable": True},
                    {"id": "e2", "ax_role": "AXTextArea", "is_actionable": True},
                    {"id": "e3", "ax_role": "AXGroup", "is_actionable": False}]
        result = gui.observe("demo", apps_dir=self.apps, base=self.base,
                             run=fake_run_factory(elements=elements))
        self.assertEqual(result["element_count"], 3)
        self.assertEqual(result["actionable"], 2)
        session = self.base / result["session"]
        self.assertTrue((session / "steps" / "001-observe.png").exists())
        self.assertTrue((session / "see.json").exists())
        self.assertTrue((session / "summary.md").exists())
        state = gui.load_session(result["session"], self.base)
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["step"], 1)

    def test_observe_failure_stops(self):
        self.add_app()
        with self.assertRaises(gui.GuiError):
            gui.observe("demo", apps_dir=self.apps, base=self.base,
                        run=fake_run_factory(see_ok=False))
        state = gui.list_sessions(base=self.base)[0]
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["stop_reason"], "感知失败")

    def test_reuse_session(self):
        self.add_app()
        first = gui.observe("demo", apps_dir=self.apps, base=self.base,
                            run=fake_run_factory(elements=[]))
        sid = first["session"]
        state = gui.load_session(sid, self.base)
        state["status"] = "running"
        gui.save_session(state, self.base)
        second = gui.observe("demo", session_id=sid, apps_dir=self.apps,
                             base=self.base, run=fake_run_factory(elements=[]))
        self.assertEqual(second["session"], sid)
        self.assertEqual(gui.read_actions(sid, self.base)[-1]["step"], 2)


if __name__ == "__main__":
    unittest.main()
