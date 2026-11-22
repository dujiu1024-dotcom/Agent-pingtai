from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPORT_SCRIPT = ROOT / "scripts" / "export_legacy_sqlite.py"
VERIFY_SCRIPT = ROOT / "scripts" / "verify_legacy_export.py"
DIFF_SCRIPT = ROOT / "scripts" / "migration_diff.py"


class Issue665S66504MigrationAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "legacy-redacted.sqlite"
        self.export_dir = self.root / "legacy-export"
        self.target_dir = self.root / "postgres-snapshot"
        self.target_dir.mkdir()
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
            "embeddings": [(10, "fact", "8")],
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
            "embeddings": "id INTEGER PRIMARY KEY, source_type TEXT, source_id TEXT",
        }
        with sqlite3.connect(self.source) as db:
            for table, schema in schemas.items():
                db.execute(f"CREATE TABLE {table} ({schema})")
                db.executemany(
                    f"INSERT INTO {table} VALUES ({','.join('?' for _ in rows[table][0])})",
                    rows[table],
                )

    def tearDown(self):
        self.temp.cleanup()

    def _export(self):
        return subprocess.run(
            [sys.executable, str(EXPORT_SCRIPT), str(self.source), str(self.export_dir)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )

    def _verify(self):
        return subprocess.run(
            [sys.executable, str(VERIFY_SCRIPT), str(self.export_dir)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )

    def _rows(self, table: str) -> list[dict]:
        return [
            json.loads(line)["row"]
            for line in (self.export_dir / f"{table}.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _write_rows(self, path: Path, rows: list[dict]) -> None:
        path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )

    def _diff(self, source: Path, target: Path):
        return subprocess.run(
            [sys.executable, str(DIFF_SCRIPT), "--source", str(source), "--target", str(target)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )

    def test_three_business_chains_export_verify_and_match_all_ten_tables(self):
        before = self.source.read_bytes()
        exported = self._export()
        self.assertEqual(exported.returncode, 0, exported.stderr)

        verified = self._verify()
        self.assertEqual(verified.returncode, 0, verified.stderr)
        verification = json.loads(verified.stdout)
        self.assertTrue(verification["valid"])
        self.assertEqual(verification["tables"], 10)
        self.assertEqual(verification["rows"], 10)

        # Simulate independently exported PostgreSQL JSONL snapshots from the
        # same redacted fixture; compare every table with the read-only export.
        manifest = json.loads((self.export_dir / "manifest.json").read_text(encoding="utf-8"))
        for table in manifest["tables"]:
            source_rows = self._rows(table["name"])
            source_file = self.root / f"source-{table['name']}.jsonl"
            target_file = self.target_dir / f"{table['name']}.jsonl"
            self._write_rows(source_file, source_rows)
            self._write_rows(target_file, source_rows)
            result = self._diff(source_file, target_file)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["matched"], table["name"])

        self.assertEqual(self.source.read_bytes(), before)

    def test_diff_detects_changed_and_extra_rows_in_target_snapshot(self):
        exported = self._export()
        self.assertEqual(exported.returncode, 0, exported.stderr)
        rows = self._rows("payments")
        source_file = self.root / "payments-source.jsonl"
        target_file = self.target_dir / "payments-target.jsonl"
        self._write_rows(source_file, rows)
        changed = dict(rows[0], amount=999)
        extra = {"id": 99, "invoice_id": 6, "amount": 1}
        self._write_rows(target_file, [changed, extra])

        result = self._diff(source_file, target_file)
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["changed"], ["7"])
        self.assertEqual(report["missing_in_source"], ["99"])
        self.assertEqual(report["missing_in_target"], [])

    def test_export_redacts_sensitive_columns_and_preserves_source(self):
        with sqlite3.connect(self.source) as db:
            db.execute("ALTER TABLE projects ADD COLUMN secret_token TEXT")
            db.execute("UPDATE projects SET secret_token='never-export-this-value'")
        before = self.source.read_bytes()

        exported = self._export()
        self.assertEqual(exported.returncode, 0, exported.stderr)
        manifest = json.loads((self.export_dir / "manifest.json").read_text(encoding="utf-8"))
        project = next(item for item in manifest["tables"] if item["name"] == "projects")
        self.assertEqual(project["redacted_columns"], ["secret_token"])
        row = self._rows("projects")[0]
        self.assertEqual(row["secret_token"], "[REDACTED]")
        self.assertNotIn("never-export-this-value", (self.export_dir / "projects.jsonl").read_text())
        self.assertEqual(self._verify().returncode, 0)
        self.assertEqual(self.source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
