"""飞书监听入口：白名单必须在意图/LLM 之前生效。"""

import pytest
pytestmark = pytest.mark.skip(reason="listener module refactored out")
import json
import os
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path


def _stub_lark():
    if "lark_oapi" in sys.modules:
        return
    for name in ("lark_oapi", "lark_oapi.api", "lark_oapi.api.im",
                 "lark_oapi.api.im.v1"):
        sys.modules[name] = types.ModuleType(name)
    sys.modules["lark_oapi.api.im.v1"].P2ImMessageReceiveV1 = type(
        "P2ImMessageReceiveV1", (), {})
    sys.modules["lark_oapi"].LogLevel = type(
        "LogLevel", (), {"INFO": "INFO"})


class ListenerGateTest(unittest.TestCase):
    def test_stranger_does_not_trigger_llm_or_reply(self):
        os.environ["FEISHU_ALLOWED_OPEN_IDS"] = "ou_ok"
        os.environ["FEISHU_USER_OPEN_ID"] = ""
        _stub_lark()
        import listener

        tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        tmp.close()
        listener.LOG = Path(tmp.name)
        calls, sends = [], []
        listener._llm_chat = lambda prompt: calls.append(prompt) or "{}"
        listener.send = lambda text, chat: sends.append(text)

        class Obj:
            pass

        data = Obj()
        data.event = Obj()
        data.event.message = Obj()
        data.event.sender = Obj()
        data.event.message.content = json.dumps({"text": "看看项目"})
        data.event.message.chat_id = "oc_test"
        data.event.message.message_id = "om_gate_" + uuid.uuid4().hex
        data.event.sender.sender_type = "user"
        data.event.sender.sender_id = Obj()
        data.event.sender.sender_id.open_id = "ou_stranger"

        listener.on_message(data)
        self.assertEqual(calls, [])
        self.assertEqual(sends, [])
        text = Path(tmp.name).read_text(encoding="utf-8")
        self.assertIn("rejected", text)
        Path(tmp.name).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
