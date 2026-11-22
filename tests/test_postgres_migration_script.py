from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "migrate_postgres.sh"


class PostgresMigrationScriptTests(unittest.TestCase):
    def test_migration_and_checksum_ledger_share_one_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            migrations = base / "migrations"
            migrations.mkdir()
            (migrations / "001_atomicity_probe.sql").write_text(
                "-- migration comment\nBEGIN;\n"
                "CREATE TABLE atomicity_probe (id integer);\n"
                "CREATE FUNCTION atomicity_probe_fn() RETURNS void AS $fn$\n"
                "BEGIN;\nPERFORM 1;\nEND;\n$fn$ LANGUAGE plpgsql;\n"
                "COMMIT;\n",
                encoding="utf-8",
            )
            fake_bin = base / "bin"
            fake_bin.mkdir()
            capture = base / "captured"
            fake_psql = fake_bin / "psql"
            fake_psql.write_text(
                "#!/usr/bin/env bash\n"
                'set -euo pipefail\n'
                'mkdir -p "$CAPTURE_DIR"\n'
                'cp -R "$(dirname "$6")" "$CAPTURE_DIR/runner-dir"\n',
                encoding="utf-8",
            )
            fake_psql.chmod(0o755)
            env = os.environ.copy()
            env.update({
                "PG_DSN": "postgresql://unused",
                "MIGRATION_DIR": str(migrations),
                "CAPTURE_DIR": str(capture),
                "PATH": f"{fake_bin}:{env['PATH']}",
            })

            completed = subprocess.run(
                ["bash", str(RUNNER)], cwd=ROOT, env=env,
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

            runner_file = capture / "runner-dir" / "runner.sql"
            runner_sql = runner_file.read_text(encoding="utf-8")
            prepared_file = next(
                path for path in (capture / "runner-dir").glob("*.sql")
                if path.name != "runner.sql"
            )
            prepared_sql = prepared_file.read_text(encoding="utf-8")

            self.assertTrue(prepared_sql.startswith("-- migration comment\nCREATE TABLE"))
            self.assertNotIn("\nCOMMIT;\n", prepared_sql)
            self.assertIn("CREATE FUNCTION atomicity_probe_fn()", prepared_sql)
            self.assertIn("\nBEGIN;\nPERFORM 1;", prepared_sql)
            begin = runner_sql.index("  BEGIN;")
            include = runner_sql.index("  \\i '", begin)
            ledger = runner_sql.index("INSERT INTO platform_schema_migrations", include)
            commit = runner_sql.index("  COMMIT;", ledger)
            self.assertLess(begin, include)
            self.assertLess(include, ledger)
            self.assertLess(ledger, commit)

    def test_rejects_unexpected_migration_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            migrations = Path(tmp) / "migrations"
            migrations.mkdir()
            (migrations / "unsafe name.sql").write_text(
                "CREATE TABLE should_not_run (id integer);\n", encoding="utf-8"
            )
            env = os.environ.copy()
            env.update({
                "PG_DSN": "postgresql://unused",
                "MIGRATION_DIR": str(migrations),
            })
            completed = subprocess.run(
                ["bash", str(RUNNER)], cwd=ROOT, env=env,
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("invalid migration filename", completed.stderr)

    def test_rejects_duplicate_migration_versions_before_psql(self):
        with tempfile.TemporaryDirectory() as tmp:
            migrations = Path(tmp) / "migrations"
            migrations.mkdir()
            (migrations / "001_first.sql").write_text("SELECT 1;\n", encoding="utf-8")
            (migrations / "001_renamed.sql").write_text("SELECT 2;\n", encoding="utf-8")
            env = os.environ.copy()
            env.update({"PG_DSN": "postgresql://unused", "MIGRATION_DIR": str(migrations)})
            completed = subprocess.run(
                ["bash", str(RUNNER)], cwd=ROOT, env=env,
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("duplicate migration version 001", completed.stderr)

    def test_runner_refuses_to_drop_historical_migration_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            migrations = Path(tmp) / "migrations"
            migrations.mkdir()
            (migrations / "001_current.sql").write_text("SELECT 1;\n", encoding="utf-8")
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            capture = Path(tmp) / "captured"
            fake_psql = fake_bin / "psql"
            fake_psql.write_text(
                "#!/usr/bin/env bash\nset -euo pipefail\n"
                'mkdir -p "$CAPTURE_DIR"\n'
                'cp -R "$(dirname "$6")" "$CAPTURE_DIR/runner-dir"\n',
                encoding="utf-8",
            )
            fake_psql.chmod(0o755)
            env = os.environ.copy()
            env.update({"PG_DSN": "postgresql://unused", "MIGRATION_DIR": str(migrations),
                        "CAPTURE_DIR": str(capture), "PATH": f"{fake_bin}:{env['PATH']}"})
            completed = subprocess.run(
                ["bash", str(RUNNER)], cwd=ROOT, env=env,
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            runner_sql = (capture / "runner-dir" / "runner.sql").read_text(encoding="utf-8")
            self.assertIn("migration_name NOT IN ('001_current.sql')", runner_sql)
            self.assertIn("An applied migration is missing or was renamed", runner_sql)


if __name__ == "__main__":
    unittest.main()
