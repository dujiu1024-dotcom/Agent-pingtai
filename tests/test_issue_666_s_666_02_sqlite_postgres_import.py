from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.migrate_sqlite_to_pg import load_export, load_mapping, migrate


class Cursor:
    def __init__(self, rows=(), one=(False,)):
        self._rows = list(rows)
        self._one = one

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one


class FakeConnection:
    def __init__(self, *, target_columns=("id", "name", "payload"), nonempty=False, fail_insert=False):
        self.target_columns = target_columns
        self.nonempty = nonempty
        self.fail_insert = fail_insert
        self.commands = []
        self.committed = False
        self.rolled_back = False

    def execute(self, query, params=None):
        self.commands.append((query, params))
        if "information_schema.columns" in query:
            return Cursor([(column,) for column in self.target_columns])
        if "SELECT EXISTS" in query:
            return Cursor(one=(self.nonempty,))
        if query.startswith("INSERT") and self.fail_insert:
            raise RuntimeError("simulated insert failure")
        return Cursor()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class SQLitePostgresImportTests(unittest.TestCase):
    def _export(self, directory: Path):
        rows = [
            {"table": "records", "primary_key": {"id": 1},
             "row": {"id": 1, "name": "one", "payload": {"$binary_base64": "AQI="}}},
            {"table": "records", "primary_key": {"id": 2},
             "row": {"id": 2, "name": "two", "payload": None}},
        ]
        content = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows).encode()
        (directory / "records.jsonl").write_bytes(content)
        manifest = {
            "format": "legacy-sqlite-jsonl-v1", "read_only": True,
            "tables": [{
                "name": "records", "file": "records.jsonl", "rows_exported": 2,
                "sha256": hashlib.sha256(content).hexdigest(),
                "columns": [{"name": "id"}, {"name": "name"}, {"name": "payload"}],
            }],
        }
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        mapping = {"version": 1, "tables": [{
            "source": "records", "target_schema": "public", "target": "records",
            "columns": {"id": "id", "name": "name", "payload": "payload"},
            "primary_key": ["id"],
        }]}
        mapping_path = directory / "mapping.json"
        mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
        return mapping_path

    def test_manifest_checksum_and_binary_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            mapping_path = self._export(directory)
            manifest, rows = load_export(directory)
            mappings = load_mapping(mapping_path, manifest)
            self.assertEqual(rows["records"][0]["payload"], b"\x01\x02")
            self.assertEqual(mappings[0]["primary_key"], ["id"])
            (directory / "records.jsonl").write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checksum"):
                load_export(directory)

    def test_dry_run_is_read_only_and_never_inserts(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            mapping_path = self._export(directory)
            manifest, rows = load_export(directory)
            connection = FakeConnection()
            report = migrate(connection, rows, load_mapping(mapping_path, manifest), dry_run=True)
            self.assertEqual(report["status"], "validated")
            self.assertEqual(report["total_rows"], 2)
            self.assertTrue(connection.commands[0][0].startswith("BEGIN READ ONLY"))
            self.assertFalse(any(query.startswith("INSERT") for query, _ in connection.commands))
            self.assertTrue(connection.rolled_back)
            self.assertFalse(connection.committed)

    def test_apply_inserts_all_rows_and_commits_once(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            mapping_path = self._export(directory)
            manifest, rows = load_export(directory)
            connection = FakeConnection()
            report = migrate(connection, rows, load_mapping(mapping_path, manifest), dry_run=False)
            inserts = [(query, params) for query, params in connection.commands if query.startswith("INSERT")]
            self.assertEqual(len(inserts), 2)
            self.assertEqual(inserts[0][1], (1, "one", b"\x01\x02"))
            self.assertEqual(report["status"], "committed")
            self.assertTrue(connection.committed)
            self.assertFalse(connection.rolled_back)

    def test_nonempty_target_fails_before_insert(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            mapping_path = self._export(directory)
            manifest, rows = load_export(directory)
            connection = FakeConnection(nonempty=True)
            with self.assertRaisesRegex(ValueError, "must be empty"):
                migrate(connection, rows, load_mapping(mapping_path, manifest), dry_run=False)
            self.assertFalse(any(query.startswith("INSERT") for query, _ in connection.commands))
            self.assertTrue(connection.rolled_back)

    def test_insert_failure_rolls_back_entire_import(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            mapping_path = self._export(directory)
            manifest, rows = load_export(directory)
            connection = FakeConnection(fail_insert=True)
            with self.assertRaisesRegex(RuntimeError, "simulated"):
                migrate(connection, rows, load_mapping(mapping_path, manifest), dry_run=False)
            self.assertFalse(connection.committed)
            self.assertTrue(connection.rolled_back)

    def test_mapping_must_cover_all_exported_tables_and_columns(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            mapping_path = self._export(directory)
            manifest, _ = load_export(directory)
            payload = json.loads(mapping_path.read_text(encoding="utf-8"))
            payload["tables"][0]["columns"].pop("payload")
            mapping_path.write_text(json.dumps(payload), encoding="utf-8")
            # The mapping may intentionally omit a source column; the export's
            # primary-key contract still has to be mapped.
            payload["tables"][0]["primary_key"] = ["missing"]
            mapping_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "primary_key"):
                load_mapping(mapping_path, manifest)


if __name__ == "__main__":
    unittest.main()
