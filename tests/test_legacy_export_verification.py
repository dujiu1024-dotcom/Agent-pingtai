from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.export_legacy_sqlite import export_database
from scripts.verify_legacy_export import verify_export


class LegacyExportVerificationTests(unittest.TestCase):
    def _export(self, root: Path) -> Path:
        database = root / "legacy.db"
        import sqlite3
        connection = sqlite3.connect(database)
        connection.executescript(
            "CREATE TABLE items(id INTEGER PRIMARY KEY, value TEXT);"
            "INSERT INTO items VALUES(1, 'one'); INSERT INTO items VALUES(2, 'two');"
        )
        connection.commit()
        connection.close()
        output = root / "export"
        export_database(database, output)
        return output

    def test_verifies_manifest_row_count_and_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self._export(Path(directory))
            report = verify_export(output)
            self.assertTrue(report["valid"])
            self.assertEqual(report["tables"], 1)
            self.assertEqual(report["rows"], 2)

    def test_rejects_tampered_rows_and_unexpected_files(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self._export(Path(directory))
            with (output / "items.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"table": "items", "row": {"id": 3}}) + "\n")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                verify_export(output)
        with tempfile.TemporaryDirectory() as directory:
            output = self._export(Path(directory))
            (output / "unexpected.txt").write_text("extra")
            with self.assertRaisesRegex(ValueError, "file set mismatch"):
                verify_export(output)

    def test_rejects_path_traversal_in_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self._export(Path(directory))
            manifest_path = output / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["tables"][0]["file"] = "../outside.jsonl"
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "unsafe export filename"):
                verify_export(output)


if __name__ == "__main__":
    unittest.main()
