from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.verify_sqlite_postgres_parity import verify_parity


class SQLitePostgresParityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.export_dir = self.root / "source"
        self.target_dir = self.root / "target"
        self.export_dir.mkdir()
        self.target_dir.mkdir()
        self.source_rows = [
            {"id": 1, "name": "alpha", "payload": {"$binary_base64": "AQI="}},
            {"id": 2, "name": "beta", "payload": None},
        ]
        self._write_source()
        self.mapping_path = self.root / "mapping.json"
        self.mapping_path.write_text(json.dumps({
            "version": 1,
            "tables": [{
                "source": "users",
                "target_schema": "public",
                "target": "platform_users",
                "columns": {"id": "id", "name": "display_name", "payload": "payload"},
                "primary_key": ["id"],
            }],
        }), encoding="utf-8")
        self._write_target([
            {"id": 1, "display_name": "alpha", "payload": {"$binary_base64": "AQI="}},
            {"id": 2, "display_name": "beta", "payload": None},
        ])

    def tearDown(self):
        self.temp.cleanup()

    def _write_source(self):
        records = [
            {"table": "users", "primary_key": {"id": row["id"]}, "row": row}
            for row in self.source_rows
        ]
        content = "".join(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                          for record in records).encode()
        (self.export_dir / "users.jsonl").write_bytes(content)
        manifest = {
            "format": "legacy-sqlite-jsonl-v1",
            "read_only": True,
            "tables": [{
                "name": "users",
                "file": "users.jsonl",
                "rows_exported": len(self.source_rows),
                "primary_key": ["id"],
                "sha256": hashlib.sha256(content).hexdigest(),
                "columns": [{"name": "id"}, {"name": "name"}, {"name": "payload"}],
            }],
        }
        (self.export_dir / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    def _write_target(self, rows):
        path = self.target_dir / "public__platform_users.jsonl"
        path.write_text(
            "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                    for row in rows),
            encoding="utf-8",
        )

    def verify(self):
        return verify_parity(self.export_dir, self.target_dir, self.mapping_path)

    def test_zero_difference_requires_matching_rows_and_checksums(self):
        report = self.verify()
        self.assertTrue(report["matched"])
        self.assertFalse(report["writes_performed"])
        self.assertEqual(report["tables"], 1)
        self.assertEqual(report["matched_tables"], 1)
        self.assertEqual(report["rows_source"], 2)
        self.assertEqual(report["rows_target"], 2)
        table = report["table_reports"][0]
        self.assertEqual(table["source_checksum"], table["target_checksum"])
        self.assertEqual(table["missing_in_target"], 0)
        self.assertEqual(table["extra_in_target"], 0)
        self.assertEqual(table["changed_rows"], 0)

    def test_row_order_does_not_change_parity_or_checksum(self):
        self._write_target([
            {"id": 2, "display_name": "beta", "payload": None},
            {"id": 1, "display_name": "alpha", "payload": {"$binary_base64": "AQI="}},
        ])
        self.assertTrue(self.verify()["matched"])

    def test_reports_missing_extra_and_changed_rows(self):
        self._write_target([
            {"id": 1, "display_name": "renamed", "payload": {"$binary_base64": "AQI="}},
            {"id": 3, "display_name": "extra", "payload": None},
        ])
        report = self.verify()
        self.assertFalse(report["matched"])
        self.assertEqual(report["missing_in_target"], 1)
        self.assertEqual(report["extra_in_target"], 1)
        self.assertEqual(report["changed_rows"], 1)

    def test_rejects_duplicate_target_primary_keys(self):
        self._write_target([
            {"id": 1, "display_name": "alpha", "payload": {"$binary_base64": "AQI="}},
            {"id": 1, "display_name": "duplicate", "payload": None},
        ])
        with self.assertRaisesRegex(ValueError, "duplicate primary key"):
            self.verify()

    def test_rejects_target_column_drift(self):
        self._write_target([{"id": 1, "display_name": "alpha", "unexpected": "x"}])
        with self.assertRaisesRegex(ValueError, "columns differ"):
            self.verify()

    def test_rejects_missing_or_extra_table_snapshots(self):
        (self.target_dir / "unexpected.jsonl").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "inventory mismatch"):
            self.verify()

    def test_rejects_manifest_checksum_drift_before_comparison(self):
        manifest_path = self.export_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["tables"][0]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
