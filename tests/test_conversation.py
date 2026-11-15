"""多轮会话上下文：按人隔离、闲置重置、最近 10 条、待确认意图。"""

import os
import unittest
import uuid
from contextlib import closing

os.environ.setdefault("AGENCY_ALLOW_TEST_DB", "1")

import conversation  # noqa: E402


class ConversationTest(unittest.TestCase):
    def setUp(self):
        self.uid = "ou_test_" + uuid.uuid4().hex[:8]
        with closing(conversation._con()) as con, con:
            con.execute("DELETE FROM conversations WHERE open_id = ?", (self.uid,))
        conversation.clear_pending(self.uid)

    def tearDown(self):
        with closing(conversation._con()) as con, con:
            con.execute("DELETE FROM conversations WHERE open_id = ?", (self.uid,))
        conversation.clear_pending(self.uid)

    def test_history_keeps_last_10_and_order(self):
        for i in range(12):
            conversation.append(self.uid, "user", "第%d句" % i)
        hist = conversation.history(self.uid)
        self.assertEqual(len(hist), 10)
        self.assertEqual(hist[-1]["text"], "第11句")

    def test_idle_timeout_starts_new_session(self):
        conversation.append(self.uid, "user", "昨天的话")
        with closing(conversation._con()) as con, con:
            con.execute("UPDATE conversations SET created_at = ? WHERE open_id = ?",
                        ("2020-01-01T00:00:00+00:00", self.uid))
        self.assertEqual(conversation.history(self.uid), [])
        conversation.append(self.uid, "user", "新会话")
        self.assertEqual(len(conversation.history(self.uid)), 1)

    def test_pending_intent_roundtrip(self):
        conversation.set_pending(self.uid, {"goal": "帮我搞个诊断", "round": 1})
        self.assertEqual(conversation.get_pending(self.uid)["goal"], "帮我搞个诊断")
        conversation.clear_pending(self.uid)
        self.assertIsNone(conversation.get_pending(self.uid))

    def test_context_isolated_by_open_id(self):
        other = self.uid + "_other"
        conversation.append(self.uid, "user", "我的问题")
        self.assertEqual(conversation.context_text(other), "")
        self.assertIn("我的问题", conversation.context_text(self.uid))


if __name__ == "__main__":
    unittest.main()
