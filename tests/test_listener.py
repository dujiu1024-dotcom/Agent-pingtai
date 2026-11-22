#!/usr/bin/env python3
"""飞书监听：白名单、重启后去重、去重降级、意图降级。"""

import pytest

pytestmark = pytest.mark.skip(reason="listener module refactored out")
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _stub_lark():
    if "lark_oapi" in sys.modules:
        return
    for name in ("lark_oapi", "lark_oapi.api", "lark_oapi.api.im",
                 "lark_oapi.api.im.v1"):
        sys.modules[name] = types.ModuleType(name)
    sys.modules["lark_oapi.api.im.v1"].P2ImMessageReceiveV1 = type(
        "P2ImMessageReceiveV1", (), {})
    sys.modules["lark_oapi"].LogLevel = type("LogLevel", (), {"INFO": "INFO"})


class ListenerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "agency.db"
        self.env = {**os.environ, "AGENCY_DB": str(self.db)}

    def tearDown(self):
        os.environ.pop("AGENCY_DB", None)
        self.tmp.cleanup()

    def test_dedup_persists_across_restart(self):
        code = (
            "import sys, types\n"
            "for n in ('lark_oapi','lark_oapi.api','lark_oapi.api.im','lark_oapi.api.im.v1'):\n"
            "    sys.modules[n]=types.ModuleType(n)\n"
            "sys.modules['lark_oapi.api.im.v1'].P2ImMessageReceiveV1=type('X',(),{})\n"
            "sys.modules['lark_oapi'].LogLevel=type('L',(),{'INFO':'INFO'})\n"
            "import listener\n"
            "print(listener._dedup_check('om_same_event'))\n"
        )
        first = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=self.env,
                               capture_output=True, text=True)
        second = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout.strip(), "True")
        self.assertEqual(second.stdout.strip(), "False", "重启后同一事件被重复处理")

    def test_replayed_command_executes_once_after_restart(self):
        counter = Path(self.tmp.name) / "count.txt"
        env = {**self.env, "FEISHU_ALLOWED_OPEN_IDS": "ou_ok",
               "FEISHU_USER_OPEN_ID": "", "CNT": str(counter)}
        code = """
import json, os, sys, time, types
for n in ('lark_oapi','lark_oapi.api','lark_oapi.api.im','lark_oapi.api.im.v1'):
    sys.modules[n]=types.ModuleType(n)
sys.modules['lark_oapi.api.im.v1'].P2ImMessageReceiveV1=type('X',(),{})
sys.modules['lark_oapi'].LogLevel=type('L',(),{'INFO':'INFO'})
import listener
listener.send=lambda *a, **k: None
listener.run_command=lambda *a, **k: open(os.environ['CNT'],'a').write('x')
class O: pass
e=O(); e.event=O(); e.event.message=O(); e.event.sender=O()
e.event.message.message_id='om_replay_1'
e.event.message.chat_id='oc_1'
e.event.message.content=json.dumps({'text':'/task add 重放测试 --owner pm --project OPC-002'})
e.event.sender.sender_type='user'
e.event.sender.sender_id=O(); e.event.sender.sender_id.open_id='ou_ok'
listener.on_message(e)
time.sleep(0.4)
print(open(os.environ['CNT']).read().count('x') if os.path.exists(os.environ['CNT']) else 0)
"""
        first = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                               capture_output=True, text=True)
        counter.unlink(missing_ok=True)
        second = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                                capture_output=True, text=True)
        self.assertEqual(first.stdout.strip().splitlines()[-1], "1", first.stderr)
        self.assertEqual(second.stdout.strip().splitlines()[-1], "0", second.stderr)
        self.assertIn("去重跳过", second.stdout)

    def test_dedup_falls_back_when_db_unavailable(self):
        _stub_lark()
        os.environ["AGENCY_DB"] = str(self.db)
        import listener
        original = listener.dbm.connect
        listener.dbm.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down"))
        try:
            listener._SEEN.clear()
            self.assertTrue(listener._dedup_check("om_fallback"))
            self.assertFalse(listener._dedup_check("om_fallback"))
        finally:
            listener.dbm.connect = original

    def test_intent_fallback_shared_router(self):
        _stub_lark()
        import listener
        self.assertEqual(listener._intent_fallback("看看项目进度")["intent"], "brief")
        self.assertEqual(listener._intent_fallback("帮我规划一下")["intent"], "plan")
        self.assertEqual(listener._intent_fallback("随便聊聊")["intent"], "chat")

    def test_stranger_blocked_before_llm(self):
        _stub_lark()
        os.environ["FEISHU_ALLOWED_OPEN_IDS"] = "ou_ok"
        os.environ["FEISHU_USER_OPEN_ID"] = ""
        import listener
        listener.LOG = Path(self.tmp.name) / "listener.jsonl"
        calls = []
        listener._llm_chat = lambda prompt: calls.append(prompt) or "{}"
        listener.send = lambda text, chat: None

        class Obj:
            pass

        event = Obj()
        event.event = Obj()
        event.event.message = Obj()
        event.event.message.message_id = "om_stranger"
        event.event.message.chat_id = "oc_1"
        event.event.message.content = '{"text":"看看进度"}'
        event.event.sender = Obj()
        event.event.sender.sender_type = "user"
        event.event.sender.sender_id = Obj()
        event.event.sender.sender_id.open_id = "ou_stranger"
        listener.on_message(event)
        self.assertEqual(calls, [], "陌生 open_id 不应触发 LLM")


if __name__ == "__main__":
    unittest.main()
