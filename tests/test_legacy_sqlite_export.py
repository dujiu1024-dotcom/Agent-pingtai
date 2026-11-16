from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.export_legacy_sqlite import export_database


class LegacySQLiteExportTests(unittest.TestCase):
    def test_exports_schema_and_rows_read_only_with_sensitive_column_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "legacy.db"
            connection = sqlite3.connect(database)
            connection.executescript("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    email TEXT NOT NULL,
                    api_key TEXT,
                    profile BLOB
                );
                INSERT INTO users VALUES (2, 'b@example.test', 'secret-b', X'0102');
                INSERT INTO users VALUES (1, 'a@example.test', 'secret-a', X'0304');
            """)
            connection.commit()
            connection.close()

            output = root / "export"
            manifest = export_database(database, output)
            table = manifest["tables"][0]
            self.assertEqual(table["name"], "users")
            self.assertEqual(table["rows_exported"], 2)
            self.assertEqual(table["redacted_columns"], ["api_key"])
            rows = [json.loads(line) for line in (output / "users.jsonl").read_text().splitlines()]
            self.assertEqual([row["primary_key"]["id"] for row in rows], [1, 2])
            self.assertEqual(rows[0]["row"]["api_key"], "[REDACTED]")
            self.assertEqual(rows[0]["row"]["profile"], {"$binary_base64": "AwQ="})
            self.assertTrue((output / "manifest.json").is_file())

            reopened = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
            self.assertEqual(reopened.execute("SELECT COUNT(*) FROM users").fetchone()[0], 2)
            reopened.close()

    def test_table_filter_and_nonempty_output_are_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "legacy.db"
            connection = sqlite3.connect(database)
            connection.executescript("CREATE TABLE a(id INTEGER); CREATE TABLE b(id INTEGER);")
            connection.commit()
            connection.close()
            output = root / "export"
            manifest = export_database(database, output, ["b"])
            self.assertEqual([table["name"] for table in manifest["tables"]], ["b"])
            with self.assertRaises(FileExistsError):
                export_database(database, output)


if __name__ == "__main__":
    unittest.main()
