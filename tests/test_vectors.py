"""向量检索降级与错误路径。"""

import array
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db as dbm
import vectors


class VectorDegradeTest(unittest.TestCase):
    def test_dense_failure_falls_back_and_surfaces_reason(self):
        with mock.patch.object(vectors, "pick_provider", return_value="ollama"), \
             mock.patch.object(vectors, "_search_dense",
                               return_value=([], "Ollama 不可用：boom")), \
             mock.patch.object(vectors, "_search_sparse",
                               return_value=[{"score": 1.0, "source_id": "x"}]):
            hits = vectors.search("测试")
        self.assertEqual(len(hits), 1)
        status = vectors.last_status()
        self.assertTrue(status["degraded"])
        self.assertIn("boom", status["reason"])

    def test_dense_hit_is_not_degraded(self):
        with mock.patch.object(vectors, "pick_provider", return_value="ollama"), \
             mock.patch.object(vectors, "_search_dense",
                               return_value=([{"score": 0.9}], "")):
            hits = vectors.search("测试")
        self.assertEqual(len(hits), 1)
        self.assertFalse(vectors.last_status()["degraded"])

    def test_empty_query_returns_empty(self):
        self.assertEqual(vectors.search("   "), [])
        self.assertFalse(vectors.last_status()["degraded"])

    def test_sparse_provider_skips_dense(self):
        with mock.patch.object(vectors, "pick_provider", return_value="hash"), \
             mock.patch.object(vectors, "_search_sparse",
                               return_value=[{"score": 0.5}]):
            self.assertEqual(len(vectors.search("测试")), 1)
        self.assertFalse(vectors.last_status()["degraded"])


class DenseMatrixScopeTest(unittest.TestCase):
    """项目视图必须同时包含本项目与 project_id IS NULL 的公司级资产。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_db_path = dbm.DB_PATH
        dbm.DB_PATH = Path(self.tmp.name) / "agency.db"
        vectors.clear_matrix_cache()
        blob = array.array("f", [1.0, 0.0]).tobytes()
        with contextlib.closing(dbm.connect()) as con, con:
            dbm.migrate(con, verbose=False)
            for code in ("P", "Q"):
                con.execute(
                    "INSERT INTO projects(id, code, name, status, created_at)"
                    " VALUES (?, ?, ?, 'active', ?)",
                    (code, code, code, dbm.now()))
            for sid, project in (("clients/P/doc.md", "P"),
                                 ("company/定位卡.md", None),
                                 ("clients/Q/other.md", "Q")):
                con.execute(
                    "INSERT INTO embeddings(project_id, source_type, source_id,"
                    " chunk_no, text, content_hash, model, dim, vector, state,"
                    " created_at) VALUES (?, 'doc', ?, 0, 'x', ?, ?, 2, ?,"
                    " 'active', ?)",
                    (project, sid, dbm.content_hash(sid), vectors.EMBED_MODEL,
                     blob, dbm.now()))

    def tearDown(self):
        vectors.clear_matrix_cache()
        dbm.DB_PATH = self.orig_db_path
        self.tmp.cleanup()

    def test_project_matrix_includes_company_assets(self):
        entry = vectors._matrix("P")
        sources = {row[0] for row in entry["rows"]}
        self.assertIn("clients/P/doc.md", sources)
        self.assertIn("company/定位卡.md", sources)
        self.assertNotIn("clients/Q/other.md", sources)


if __name__ == "__main__":
    unittest.main()
