#!/usr/bin/env python3
"""tools/erd.py：从 SQLite 生成 Mermaid ERD（分项 19.3）"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db as dbm  # noqa: E402
from tools import erd  # noqa: E402


class ErdTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "agency.db"
        con = dbm.connect(self.db_path)
        dbm.migrate(con, verbose=False)
        con.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_generates_all_tables_and_foreign_keys(self):
        out = Path(self.tmp.name) / "docs" / "erd.md"
        path, text = erd.generate(self.db_path, out)
        self.assertTrue(path.exists())
        self.assertIn("erDiagram", text)
        for table in dbm.TABLES:
            self.assertIn("    %s {" % table, text)
        # tasks.project_id → projects.id
        self.assertIn('projects ||--o{ tasks : "project_id"', text)
        self.assertIn('invoices ||--o{ payments : "invoice_id"', text)
        self.assertIn("tasks", text.split("## 表清单")[0])

    def test_render_is_deterministic(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            first = erd.render(con)
            second = erd.render(con)
        finally:
            con.close()
        self.assertEqual(first, second)

    def test_missing_database_raises(self):
        with self.assertRaises(FileNotFoundError):
            erd.generate(Path(self.tmp.name) / "nope.db", Path(self.tmp.name) / "x.md")


if __name__ == "__main__":
    unittest.main()
