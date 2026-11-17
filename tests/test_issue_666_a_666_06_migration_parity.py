from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_sqlite_to_pg.py"
SPEC = importlib.util.spec_from_file_location("migrate_sqlite_to_pg", SCRIPT)
assert SPEC and SPEC.loader
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


class Issue666MigrationParityAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "agency.snapshot.sqlite"
        self.target = self.root / "postgres-export"
        self.target.mkdir()
        self.mapping = self.root / "mapping.json"
        self.chains = [
            ("identity", "users", "user_id", ["user_id", "display_name"]),
            ("work", "tasks", "task_id", ["task_id", "title"]),
            ("knowledge", "documents", "document_id", ["document_id", "source_uri"]),
        ]
        with sqlite3.connect(self.source) as db:
            db.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, display_name TEXT)")
            db.execute("CREATE TABLE tasks (task_id INTEGER PRIMARY KEY, title TEXT)")
            db.execute("CREATE TABLE documents (document_id INTEGER PRIMARY KEY, source_uri TEXT)")
            db.executemany("INSERT INTO users VALUES (?, ?)", [(1, "Ada"), (2, "Lin")])
            db.executemany("INSERT INTO tasks VALUES (?, ?)", [(10, "Plan"), (11, "Ship")])
            db.executemany("INSERT INTO documents VALUES (?, ?)", [(20, "s3://docs/a"), (21, "s3://docs/b")])
        mapping = {"chains": []}
        for chain, table, key, columns in self.chains:
            mapping["chains"].append({
                "name": chain,
                "tables": [{
                    "source_table": table,
                    "target_table": table,
                    "key": key,
                    "columns": {column: column for column in columns},
                    "target_file": f"{table}.jsonl",
                }],
            })
        self.mapping.write_text(json.dumps(mapping), encoding="utf-8")
        self._write_target()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_target(self, overrides: dict[str, list[dict]] | None = None) -> None:
        overrides = overrides or {}
        rows = {
            "users": [{"user_id": 1, "display_name": "Ada"}, {"user_id": 2, "display_name": "Lin"}],
            "tasks": [{"task_id": 10, "title": "Plan"}, {"task_id": 11, "title": "Ship"}],
            "documents": [{"document_id": 20, "source_uri": "s3://docs/a"}, {"document_id": 21, "source_uri": "s3://docs/b"}],
        }
        rows.update(overrides)
        for table, table_rows in rows.items():
            (self.target / f"{table}.jsonl").write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in table_rows),
                encoding="utf-8",
            )

    def test_exact_parity_across_all_business_chains_and_read_only_contract(self) -> None:
        before = self.source.read_bytes()
        report = MIGRATION.dry_run(self.source, self.target, self.mapping)
        self.assertTrue(report["matched"])
        self.assertFalse(report["writes_performed"])
        self.assertFalse(report["postgres_connection_opened"])
        self.assertEqual(report["business_chains"], 3)
        self.assertEqual(report["tables"], 3)
        self.assertEqual(report["rows_source"], 6)
        self.assertEqual(report["rows_target"], 6)
        self.assertEqual(report["missing_in_target"], 0)
        self.assertEqual(report["extra_in_target"], 0)
        self.assertEqual(report["changed_rows"], 0)
        self.assertEqual(self.source.read_bytes(), before)

    def test_missing_extra_and_changed_rows_are_counted_and_fail(self) -> None:
        self._write_target({
            "users": [{"user_id": 1, "display_name": "Ada Lovelace"},
                      {"user_id": 3, "display_name": "Grace"}],
        })
        report = MIGRATION.dry_run(self.source, self.target, self.mapping)
        self.assertFalse(report["matched"])
        self.assertEqual(report["missing_in_target"], 1)
        self.assertEqual(report["extra_in_target"], 1)
        self.assertEqual(report["changed_rows"], 1)
        self.assertEqual(report["chain_reports"][0]["matched"], False)

    def test_duplicate_target_primary_key_is_rejected(self) -> None:
        (self.target / "users.jsonl").write_text(
            '{"user_id":1,"display_name":"Ada"}\n{"user_id":1,"display_name":"Duplicate"}\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "duplicate primary key"):
            MIGRATION.dry_run(self.source, self.target, self.mapping)

    def test_sensitive_column_mapping_is_rejected(self) -> None:
        mapping = json.loads(self.mapping.read_text(encoding="utf-8"))
        mapping["chains"][0]["tables"][0]["columns"]["api_token"] = "api_token"
        self.mapping.write_text(json.dumps(mapping), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "sensitive columns"):
            MIGRATION.dry_run(self.source, self.target, self.mapping)

    def test_missing_target_snapshot_fails_closed(self) -> None:
        (self.target / "tasks.jsonl").unlink()
        with self.assertRaisesRegex(ValueError, "regular, non-symlink file"):
            MIGRATION.dry_run(self.source, self.target, self.mapping)


if __name__ == "__main__":
    unittest.main()
