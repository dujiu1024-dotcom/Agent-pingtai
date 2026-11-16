from __future__ import annotations
import json, sqlite3, tempfile, unittest
from pathlib import Path
from scripts.export_legacy_sqlite import export_database

class LegacySQLiteExportTests(unittest.TestCase):
    def test_exports_schema_and_rows_read_only_with_sensitive_column_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); database=root/"legacy.db"; connection=sqlite3.connect(database)
            connection.executescript("""
                PRAGMA user_version=7;
                CREATE TABLE teams(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE users(id INTEGER PRIMARY KEY, team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE,
                    email TEXT NOT NULL, api_key TEXT, profile BLOB);
                CREATE INDEX users_email_idx ON users(email);
                CREATE VIEW active_users AS SELECT id,email FROM users;
                CREATE TRIGGER users_insert_guard AFTER INSERT ON users BEGIN SELECT 1; END;
                INSERT INTO teams VALUES(1,'platform');
                INSERT INTO users VALUES(2,1,'b@example.test','secret-b',X'0102');
                INSERT INTO users VALUES(1,1,'a@example.test','secret-a',X'0304');
            """); connection.commit(); connection.close()
            output=root/"export"; manifest=export_database(database,output)
            table=next(item for item in manifest["tables"] if item["name"]=="users")
            schema=manifest["schema"]; users=next(item for item in schema["tables"] if item["name"]=="users")
            self.assertEqual(schema["user_version"],7)
            self.assertEqual(users["foreign_keys"][0]["on_delete"],"CASCADE")
            self.assertIn("users_email_idx",[item["name"] for item in users["indexes"]])
            self.assertEqual(schema["views"][0]["name"],"active_users")
            self.assertEqual(schema["triggers"][0]["name"],"users_insert_guard")
            self.assertEqual(table["rows_exported"],2); self.assertEqual(table["redacted_columns"],["api_key"])
            rows=[json.loads(line) for line in (output/"users.jsonl").read_text().splitlines()]
            self.assertEqual([row["primary_key"]["id"] for row in rows],[1,2])
            self.assertEqual(rows[0]["row"]["api_key"],"[REDACTED]")
            self.assertEqual(rows[0]["row"]["profile"],{"$binary_base64":"AwQ="})
            reopened=sqlite3.connect(f"file:{database.as_posix()}?mode=ro",uri=True)
            self.assertEqual(reopened.execute("SELECT COUNT(*) FROM users").fetchone()[0],2); reopened.close()

    def test_table_filter_and_nonempty_output_are_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); database=root/"legacy.db"; connection=sqlite3.connect(database)
            connection.executescript("CREATE TABLE a(id INTEGER); CREATE TABLE b(id INTEGER);")
            connection.commit(); connection.close(); output=root/"export"
            manifest=export_database(database,output,["b"])
            self.assertEqual([table["name"] for table in manifest["tables"]],["b"])
            self.assertEqual([table["name"] for table in manifest["schema"]["tables"]],["b"])
            with self.assertRaises(FileExistsError): export_database(database,output)

    def test_rejects_symlinked_database_and_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); database=root/"legacy.db"; sqlite3.connect(database).close()
            db_link=root/"db-link"
            target=root/"target"; target.mkdir(); out_link=root/"out-link"
            try:
                db_link.symlink_to(database); out_link.symlink_to(target,target_is_directory=True)
            except (NotImplementedError,OSError): self.skipTest("symlinks unavailable")
            with self.assertRaisesRegex(ValueError,"database must be a regular"):
                export_database(db_link,root/"export")
            with self.assertRaisesRegex(ValueError,"output directory"):
                export_database(database,out_link)

if __name__=="__main__": unittest.main()
