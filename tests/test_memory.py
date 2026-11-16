"""wm_session 槽位写入与错误路径。"""

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import db as dbm
import memory


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = dbm.DB_PATH
        dbm.DB_PATH = Path(self.tmp.name) / "agency.db"
        with closing(dbm.connect()) as con, con:
            dbm.migrate(con)
            con.execute(
                "INSERT OR IGNORE INTO projects(id,code,name,status,created_at)"
                " VALUES('OPC-002','OPC-002','x','active',?)", (dbm.now(),))
            con.execute(
                "INSERT INTO tasks(id,project_id,title,kind,owner,status,done_when,"
                "evidence,created_at) VALUES('T-0001','OPC-002','x','general','pm',"
                "'todo','cmd: true','',?)", (dbm.now(),))

    def tearDown(self):
        dbm.DB_PATH = self.orig
        self.tmp.cleanup()

    def test_ensure_is_idempotent(self):
        a = memory.ensure("T-0001", "pm", goal="目标")
        b = memory.ensure("T-0001", "pm", goal="另一个目标")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(a["goal"], b["goal"])

    def test_list_slot_appends_without_overwrite(self):
        memory.ensure("T-0001", "pm")
        self.assertTrue(memory.set_slot("T-0001", "pm", "decisions", "决定A", "test"))
        self.assertTrue(memory.set_slot("T-0001", "pm", "decisions", "决定B", "test"))
        self.assertFalse(memory.set_slot("T-0001", "pm", "decisions", "决定A", "test"))
        with closing(memory.connect()) as con:
            row = memory._row(con, "T-0001", "pm")
        self.assertEqual(len(memory._load_list(row["decisions"])), 2)

    def test_slot_requires_source(self):
        memory.ensure("T-0001", "pm")
        with self.assertRaises(ValueError):
            memory.set_slot("T-0001", "pm", "decisions", "无来源", "")
        with self.assertRaises(ValueError):
            memory.set_slot("T-0001", "pm", "decisions", "", "test")

    def test_text_slot_overwrites(self):
        memory.ensure("T-0001", "pm")
        memory.set_slot("T-0001", "pm", "goal", "旧目标", "test")
        memory.set_slot("T-0001", "pm", "goal", "新目标", "test")
        with closing(memory.connect()) as con:
            row = memory._row(con, "T-0001", "pm")
        self.assertEqual(memory._load_scalar(row["goal"]), "新目标")


if __name__ == "__main__":
    unittest.main()
