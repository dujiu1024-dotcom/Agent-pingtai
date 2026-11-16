"""圆桌会议：选角、轮次、预算、结构审计与三入口共用逻辑。"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import agency
import db as dbm
import feishu_commands


def conclusion(ids=""):
    return f"""## 共识
- 先接第二个客户，依据台账。
## 分歧
- pm vs finance｜先标准化还是先接单｜依据台账。
## 建议方案
- 先接单再标准化；取舍：短期交付压力上升。
## 需要创始人拍板
1. 是否本月接第二个客户？
## 关键数字与来源
- {ids or "无"}｜来源：台账 T-0001
"""


class RoundtableTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_db_path = dbm.DB_PATH
        dbm.DB_PATH = Path(self.tmp.name) / "agency.db"
        with contextlib.closing(dbm.connect()) as con, con:
            dbm.migrate(con, verbose=False)
            con.execute(
                "INSERT INTO projects(id, code, name, status, created_at)"
                " VALUES ('OPC-002', 'OPC-002', '一人公司', 'active', ?)",
                (dbm.now(),))
            con.execute(
                "INSERT INTO tasks(id, project_id, title, owner, status, start_at,"
                " due_at, done_when, created_at) VALUES ('T-0001', 'OPC-002',"
                " '测试任务', 'longyi', 'todo', '2026-11-13', '2026-11-14',"
                " '完成', ?)", (dbm.now(),))
        self.calls = []

    def tearDown(self):
        dbm.DB_PATH = self.orig_db_path
        self.tmp.cleanup()

    def fake(self, chair_texts=None):
        chair_texts = list(chair_texts or [conclusion("T-0001")])

        def llm(role, prompt):
            self.calls.append(role)
            if role == "chief-of-staff":
                text = chair_texts.pop(0) if chair_texts else conclusion("T-0001")
            else:
                text = f"回应：前序。我是 {role}，依据 T-0001，[假设·S4] 可控。"
            return text, {"prompt_tokens": 10, "completion_tokens": 5}, "fake"

        return llm

    # ---------- 选角 ----------

    def test_auto_roles_follow_topic(self):
        roles = agency.pick_roundtable_roles("客户报价与回款怎么谈")
        self.assertGreaterEqual(len(roles), 3)
        self.assertLessEqual(len(roles), 5)
        self.assertIn("finance", roles)
        self.assertIn("scout", roles)

    def test_explicit_roles_keep_order_and_validate(self):
        self.assertEqual(agency.pick_roundtable_roles("任何", "finance,pm"),
                         ["finance", "pm"])
        with self.assertRaises(agency.RoundtableError):
            agency.pick_roundtable_roles("任何", "pm,nobody")

    # ---------- 跑通与结构 ----------

    def test_rounds_order_and_per_role_tokens(self):
        result = agency.run_roundtable(
            "本月是否接第二个客户", roles="pm,finance,scout", rounds=2,
            write=False, llm=self.fake())
        self.assertEqual(len(result["speeches"]), 6)
        self.assertEqual(result["speeches"][0]["responds_to"], "无（开场）")
        self.assertIn("pm", result["speeches"][1]["responds_to"])
        self.assertEqual(result["tokens"]["by_role"]["pm"]["calls"], 2)
        self.assertIn("## 需要创始人拍板", result["markdown"])
        self.assertIn("## 关键数字与来源", result["markdown"])
        self.assertEqual(result["violations"], [])

    def test_invalid_task_id_triggers_one_repair(self):
        result = agency.run_roundtable(
            "客户交付", roles="pm,finance", rounds=1, write=False,
            llm=self.fake([conclusion("T-9999"), conclusion("T-0001")]))
        self.assertEqual(self.calls.count("chief-of-staff"), 2)
        self.assertIn("T-0001", result["conclusion"])
        self.assertEqual(result["violations"], [])

    def test_unrepaired_conflict_refuses_to_write(self):
        with self.assertRaises(agency.RoundtableError):
            agency.run_roundtable(
                "客户交付", roles="pm,finance", rounds=1, write=False,
                llm=self.fake([conclusion("T-9999"), conclusion("T-8888")]))

    # ---------- 三份产物 ----------

    def test_writes_three_artifacts_in_one_dir(self):
        out = Path(self.tmp.name) / "roundtable"
        old = agency.ROUNDTABLE_DIR
        agency.ROUNDTABLE_DIR = out
        try:
            result = agency.run_roundtable(
                "客户交付", roles="pm,finance", rounds=1, llm=self.fake())
            session = out / result["id"]
            names = sorted(p.name for p in session.iterdir())
            self.assertEqual(names, ["conclusion.md", "meta.json", "minutes.md",
                                     "transcript.md"])
            transcript = (session / "transcript.md").read_text(encoding="utf-8")
            self.assertIn("### 第1轮 · 1/2 · pm", transcript)
            self.assertIn("- tokens：in 10 / out 5", transcript)
            self.assertIn("- 引用任务号：T-0001", transcript)
            self.assertIn("回应：前序。我是 pm", transcript)
            minutes = (session / "minutes.md").read_text(encoding="utf-8")
            for title in ("议题", "参会角色", "讨论要点", "结论", "分歧",
                          "待创始人拍板", "下一步动作"):
                self.assertIn(title, minutes)
            self.assertNotIn("UNTRUSTED", minutes)
            self.assertNotIn("【台账快照", minutes)
            meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["tokens"]["total"], 45)
            self.assertEqual(meta["files"]["minutes"],
                             result["dir"] + "/minutes.md")
        finally:
            agency.ROUNDTABLE_DIR = old

    def test_export_reads_minutes_by_session(self):
        out = Path(self.tmp.name) / "roundtable"
        old = agency.ROUNDTABLE_DIR
        agency.ROUNDTABLE_DIR = out
        try:
            result = agency.run_roundtable(
                "客户交付", roles="pm,finance", rounds=1, llm=self.fake())
            meta = agency._find_session(result["dir"].split("/")[-1])
            self.assertEqual(meta["id"], result["dir"].split("/")[-1])
            args = type("A", (), {"topic": "export", "session": meta["id"],
                                  "file": "minutes", "out": ""})()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                agency.cmd_roundtable(args)
            self.assertIn("# 会议纪要", buf.getvalue())
        finally:
            agency.ROUNDTABLE_DIR = old

    # ---------- 预算 ----------

    def test_budget_stops_before_next_call(self):
        result = agency.run_roundtable(
            "客户交付", roles="pm,finance,scout", rounds=2, max_tokens=5,
            write=False, llm=self.fake())
        self.assertIsNotNone(result["stopped"])
        self.assertEqual(result["speeches"], [])
        self.assertIn("预算中止", result["markdown"])

    def test_budget_stops_after_call(self):
        def heavy(role, prompt):
            return (conclusion("T-0001") if role == "chief-of-staff"
                    else "发言 [假设·S4]"), {"prompt_tokens": 70000,
                                             "completion_tokens": 0}, "fake"

        result = agency.run_roundtable(
            "客户交付", roles="pm,finance", rounds=2, write=False, llm=heavy)
        self.assertIsNotNone(result["stopped"])
        self.assertEqual(result["tokens"]["total"], 70000)
        self.assertEqual(len(result["speeches"]), 1)

    # ---------- 三入口共用解析 ----------

    def test_feishu_parse_roundtable(self):
        cmd = feishu_commands.parse(
            "/roundtable 本月是否接第二个客户 --roles pm,finance --rounds 2")
        self.assertEqual(cmd["cmd"], "/roundtable")
        self.assertEqual(cmd["roles"], "pm,finance")
        self.assertEqual(cmd["rounds"], 2)
        self.assertEqual(cmd["topic"], "本月是否接第二个客户")

    def test_console_and_feishu_share_runner(self):
        marker = {}

        def llm(role, prompt):
            marker["prompt"] = prompt
            return conclusion("T-0001"), {"prompt_tokens": 1, "completion_tokens": 1}, "fake"

        result = agency.run_roundtable(
            "客户交付", roles="pm,finance", rounds=1, write=False, llm=llm)
        self.assertIn("议题", marker["prompt"])
        self.assertEqual(result["path"], "")


if __name__ == "__main__":
    unittest.main()
