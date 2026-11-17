from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "migrate_sqlite_to_pg.py"


class Issue665S66504MigrationAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "legacy-redacted.sqlite"
        self.target = self.root / "postgres-snapshot"
        self.target.mkdir()
        self.mapping = self.root / "mapping.json"
        self.mapping.write_text(json.dumps({
            "chains": [
                {"name": "project_delivery", "tables": [
                    {"source_table": "projects", "target_table": "projects", "target_file": "projects.jsonl",
                     "key": "id", "columns": {"id": "id", "name": "name"}},
                    {"source_table": "tasks", "target_table": "tasks", "target_file": "tasks.jsonl",
                     "key": "id", "columns": {"id": "id", "project_id": "project_id", "status": "status"}},
                    {"source_table": "events", "target_table": "events", "target_file": "events.jsonl",
                     "key": "id", "columns": {"id": "id", "task_id": "task_id", "kind": "kind"}}
                ]},
                {"name": "commercial_finance", "tables": [
                    {"source_table": "quotes", "target_table": "quotes", "target_file": "quotes.jsonl",
                     "key": "id", "columns": {"id": "id", "project_id": "project_id", "amount": "amount"}},
                    {"source_table": "contracts", "target_table": "contracts", "target_file": "contracts.jsonl",
                     "key": "id", "columns": {"id": "id", "quote_id": "quote_id", "amount": "amount"}},
                    {"source_table": "invoices", "target_table": "invoices", "target_file": "invoices.jsonl",
                     "key": "id", "columns": {"id": "id", "contract_id": "contract_id", "amount": "amount"}},
                    {"source_table": "payments", "target_table": "payments", "target_file": "payments.jsonl",
                     "key": "id", "columns": {"id": "id", "invoice_id": "invoice_id", "amount": "amount"}}
                ]},
                {"name": "knowledge_assets", "tables": [
                    {"source_table": "facts", "target_table": "facts", "target_file": "facts.jsonl",
                     "key": "id", "columns": {"id": "id", "project_id": "project_id", "content_hash": "content_hash"}},
                    {"source_table": "docs", "target_table": "docs", "target_file": "docs.jsonl",
                     "key": "id", "columns": {"id": "id", "content_hash": "content_hash", "state": "state"}},
                    {"source_table": "embeddings", "target_table": "embeddings", "target_file": "embeddings.jsonl",
                     "key": "id", "columns": {"id": "id", "source_type": "source_type", "source_id": "source_id"}}
                ]}
            ]
        }), encoding="utf-8")
        rows = {
            "projects": [(1, "project-redacted")],
            "tasks": [(2, 1, "done")],
            "events": [(3, 2, "task_closed")],
            "quotes": [(4, 1, 1200)],
            "contracts": [(5, 4, 1200)],
            "invoices": [(6, 5, 1200)],
            "payments": [(7, 6, 1200)],
            "facts": [(8, 1, "sha-fact")],
            "docs": [(9, "sha-doc", "L1")],
            "embeddings": [(10, "fact", "8")]
        }
        schemas = {
            "projects": "id INTEGER PRIMARY KEY, name TEXT",
            "tasks": "id INTEGER PRIMARY KEY, project_id INTEGER, status TEXT",
            "events": "id INTEGER PRIMARY KEY, task_id INTEGER, kind TEXT",
            "quotes": "id INTEGER PRIMARY KEY, project_id INTEGER, amount INTEGER",
            "contracts": "id INTEGER PRIMARY KEY, quote_id INTEGER, amount INTEGER",
            "invoices": "id INTEGER PRIMARY KEY, contract_id INTEGER, amount INTEGER",
            "payments": "id INTEGER PRIMARY KEY, invoice_id INTEGER, amount INTEGER",
            "facts": "id INTEGER PRIMARY KEY, project_id INTEGER, content_hash TEXT",
            "docs": "id INTEGER PRIMARY KEY, content_hash TEXT, state TEXT",
            "embeddings": "id INTEGER PRIMARY KEY, source_type TEXT, source_id TEXT"
        }
        with sqlite3.connect(self.source) as db:
            for table, schema in schemas.items():
                db.execute(f"CREATE TABLE {table} ({schema})")
                db.executemany(
                    f"INSERT INTO {table} VALUES ({','.join('?' for _ in rows[table][0])})",
                    rows[table],
                )
        self.target_rows = {
            "projects": [{"id": 1, "name": "project-redacted"}],
            "tasks": [{"id": 2, "project_id": 1, "status": "done"}],
            "events": [{"id": 3, "task_id": 2, "kind": "task_closed"}],
            "quotes": [{"id": 4, "project_id": 1, "amount": 1200}],
            "contracts": [{"id": 5, "quote_id": 4, "amount": 1200}],
            "invoices": [{"id": 6, "contract_id": 5, "amount": 1200}],
            "payments": [{"id": 7, "invoice_id": 6, "amount": 1200}],
            "facts": [{"id": 8, "project_id": 1, "content_hash": "sha-fact"}],
            "docs": [{"id": 9, "content_hash": "sha-doc", "state": "L1"}],
            "embeddings": [{"id": 10, "source_type": "fact", "source_id": "8"}]
        }
        for table, records in self.target_rows.items():
            (self.target / f"{table}.jsonl").write_text(
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in records),
                encoding="utf-8",
            )

    def tearDown(self):
        self.temp.cleanup()

    def _run(self):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--source", str(self.source),
             "--target-snapshot-dir", str(self.target), "--mapping", str(self.mapping),
             "--dry-run"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )

    def test_three_business_chains_match_without_database_writes(self):
        before = self.source.read_bytes()
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["matched"])
        self.assertEqual(report["business_chains"], 3)
        self.assertEqual(report["tables"], 10)
        self.assertEqual(report["rows_source"], 10)
        self.assertEqual(report["rows_target"], 10)
        self.assertTrue(all(item["matched"] for item in report["chain_reports"]))
        self.assertFalse(report["writes_performed"])
        self.assertFalse(report["postgres_connection_opened"])
        self.assertEqual(self.source.read_bytes(), before)

    def test_detects_missing_extra_and_changed_target_rows(self):
        target_file = self.target / "payments.jsonl"
        target_file.write_text(
            json.dumps({"id": 7, "invoice_id": 6, "amount": 999}) + "\n"
            + json.dumps({"id": 99, "invoice_id": 6, "amount": 1}) + "\n",
            encoding="utf-8",
        )
        result = self._run()
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        payment = next(item for item in report["chain_reports"] if item["source_table"] == "payments")
        self.assertEqual(payment["changed_rows"], 1)
        self.assertEqual(payment["extra_in_target"], 1)
        self.assertFalse(report["matched"])

    def test_rejects_mapping_that_omits_three_chains_or_contains_secret_columns(self):
        payload = json.loads(self.mapping.read_text(encoding="utf-8"))
        payload["chains"] = payload["chains"][:2]
        self.mapping.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(self._run().returncode, 2)
        payload["chains"].append({"name": "third", "tables": [
            {"source_table": "projects", "target_table": "projects", "target_file": "x.jsonl",
             "key": "id", "columns": {"id": "id", "secret_token": "secret_token"}}
        ]})
        self.mapping.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(self._run().returncode, 2)


if __name__ == "__main__":
    unittest.main()
