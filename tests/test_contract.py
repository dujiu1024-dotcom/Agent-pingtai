#!/usr/bin/env python3
"""真实模型 / 真实飞书契约测试（默认跳过，手动开启）。

运行：
    AGENCY_REAL_TEST=1 python3 -m unittest tests.test_contract -v

约束：只做单次最小调用，不循环；真实花费以 stdout 的 token 统计为准。
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency  # noqa: E402

REAL = os.environ.get("AGENCY_REAL_TEST") == "1"


class ContractTest(unittest.TestCase):
    @unittest.skipUnless(REAL, "真实模型调用需 AGENCY_REAL_TEST=1")
    def test_model_roundtrip(self):
        client = agency.LLMClient("pm")
        text, usage = client.chat("只回复两个字：ok")
        print("[contract:model] model=%s usage=%s text=%r"
              % (client.model, usage, text[:80]))
        self.assertTrue(text.strip())
        self.assertTrue(usage.get("total_tokens"))

    @unittest.skipUnless(REAL, "真实飞书发送需 AGENCY_REAL_TEST=1")
    def test_feishu_send(self):
        chat_id = os.environ.get("FEISHU_USER_OPEN_ID")
        if not chat_id:
            self.skipTest("未配置 FEISHU_USER_OPEN_ID")
        import feishu_bot
        data = feishu_bot.Bot().send_text(
            "FDE 契约测试（单次，可忽略）", receive_id=chat_id, id_type="open_id")
        msg_id = str((data or {}).get("message_id", ""))
        print("[contract:feishu] chat=%s message_id=%s" % (chat_id[-6:], msg_id[:12]))
        self.assertTrue(msg_id)


if __name__ == "__main__":
    unittest.main()
