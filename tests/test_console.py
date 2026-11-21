#!/usr/bin/env python3
"""控制台 HTTP 安全用例：无 token、路径穿越、跨站 Origin、并发建单、产出跨项目。"""

import http.client
import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db as dbm  # noqa: E402

import agency  # noqa: E402
import console  # noqa: E402

TOKEN = "test-token"


class ConsoleHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.orig_db = dbm.DB_PATH
        cls.orig_out = agency.OUTPUT_DIR
        cls.orig_token = console.CONSOLE_TOKEN
        cls.orig_legacy_auth_enabled = console.LEGACY_CONSOLE_AUTH_ENABLED
        dbm.DB_PATH = cls.tmp / "agency.db"
        agency.OUTPUT_DIR = cls.tmp / "outputs"
        agency.OUTPUT_DIR.mkdir(parents=True)
        console.CONSOLE_TOKEN = TOKEN
        with dbm.connect(dbm.DB_PATH) as con:
            dbm.migrate(con, verbose=False)
        agency._MIGRATED_DBS.add(str(dbm.DB_PATH))
        cls.server = console.ConsoleServer(("127.0.0.1", 0), console.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        dbm.DB_PATH = cls.orig_db
        agency.OUTPUT_DIR = cls.orig_out
        console.CONSOLE_TOKEN = cls.orig_token
        console.LEGACY_CONSOLE_AUTH_ENABLED = cls.orig_legacy_auth_enabled
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        conn.request(method, path, body=json.dumps(body or {}), headers=h)
        resp = conn.getresponse()
        data = resp.read().decode("utf-8")
        conn.close()
        return resp.status, data

    def test_missing_token_rejected(self):
        status, _ = self.request("GET", "/api/state")
        self.assertEqual(status, 401)

    def test_legacy_auth_can_be_disabled_at_identity_cutover(self):
        original = console.LEGACY_CONSOLE_AUTH_ENABLED
        console.LEGACY_CONSOLE_AUTH_ENABLED = False
        try:
            status, _ = self.request("GET", "/api/state?token=" + TOKEN)
            self.assertEqual(status, 401)
        finally:
            console.LEGACY_CONSOLE_AUTH_ENABLED = original

    def test_path_traversal_rejected(self):
        status, _ = self.request(
            "GET", "/api/output?token=%s&path=../requirements.txt" % TOKEN)
        self.assertEqual(status, 403)

    def test_cross_site_origin_rejected(self):
        status, _ = self.request(
            "POST", "/api/tasks?token=" + TOKEN,
            {"title": "跨站", "owner": "pm", "done_when": "cmd: true"},
            {"Origin": "http://evil.example.com"})
        self.assertEqual(status, 403)

    def test_concurrent_task_create(self):
        results = []

        def worker(i):
            results.append(self.request(
                "POST", "/api/tasks?token=" + TOKEN,
                {"title": "并发建单-%d" % i, "owner": "pm",
                 "done_when": "cmd: true", "start": "2026-11-12",
                 "due": "2026-11-20", "hours": 1}))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual([s for s, _ in results], [201] * 8)
        with dbm.connect(dbm.DB_PATH) as con:
            count = con.execute(
                "SELECT COUNT(*) FROM tasks WHERE title LIKE '并发建单-%'").fetchone()[0]
        self.assertEqual(count, 8)

    def test_output_project_isolation(self):
        a = agency.OUTPUT_DIR / "OPC-001" / "a.txt"
        b = agency.OUTPUT_DIR / "OPC-002" / "b.txt"
        a.parent.mkdir(parents=True, exist_ok=True)
        b.parent.mkdir(parents=True, exist_ok=True)
        a.write_text("A", encoding="utf-8")
        b.write_text("B", encoding="utf-8")
        same, body = self.request(
            "GET", "/api/output?token=%s&project=OPC-001&path=OPC-001/a.txt" % TOKEN)
        self.assertEqual(same, 200)
        self.assertEqual(body, "A")
        cross, _ = self.request(
            "GET", "/api/output?token=%s&project=OPC-002&path=OPC-001/a.txt" % TOKEN)
        self.assertEqual(cross, 403)


if __name__ == "__main__":
    unittest.main()
