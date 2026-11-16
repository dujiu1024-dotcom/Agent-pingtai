"""Real PostgreSQL encrypted backup and isolated restore integration test."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from psycopg import connect, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


ROOT = Path(__file__).resolve().parents[1]
ADMIN_DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")


@unittest.skipUnless(
    ADMIN_DSN and all(shutil.which(name) for name in ("pg_dump", "pg_restore", "psql", "age", "age-keygen")),
    "PostgreSQL CLI tools, age and AGENT_PLATFORM_TEST_DSN are required",
)
class PostgresEncryptedBackupRestoreIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
            cls.admin_dsn = ADMIN_DSN
        cls.source_db = f"backup_src_{uuid4().hex[:12]}"
        cls.target_db = f"backup_dst_{uuid4().hex[:12]}"
        with connect(cls.admin_dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.source_db)))
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.target_db)))
        cls.source_dsn = make_conninfo(ADMIN_DSN, dbname=cls.source_db)
        cls.target_dsn = make_conninfo(ADMIN_DSN, dbname=cls.target_db)

    @classmethod
    def tearDownClass(cls) -> None:
        with connect(cls.admin_dsn, autocommit=True) as conn:
            for database in (cls.source_db, cls.target_db):
                conn.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(database)
                    )
                )

    def test_encrypted_backup_restores_marker_into_separate_database(self) -> None:
        with tempfile.TemporaryDirectory(prefix="platform-pg-restore-") as temp:
            work = Path(temp)
            key_path = work / "age-identity.txt"
            key = subprocess.run(
                ["age-keygen", "-o", str(key_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            public_key = re.search(r"public key: (age1[0-9a-z]+)", key.stderr, re.IGNORECASE)
            self.assertIsNotNone(public_key, key.stderr)
            identity = key_path.read_text(encoding="utf-8")
            self.assertIn("AGE-SECRET-KEY-", identity)
            backup_dir = work / "backups"
            env = os.environ.copy()
            env.update(
                {
                    "PG_DSN": self.source_dsn,
                    "AGE_RECIPIENT": public_key.group(1),
                    "BACKUP_DIR": str(backup_dir),
                }
            )
            with connect(self.source_dsn) as conn:
                conn.execute("CREATE TABLE recovery_marker (marker text PRIMARY KEY)")
                conn.execute(
                    "INSERT INTO recovery_marker(marker) VALUES (%s)",
                    ("release-gate-backup-roundtrip",),
                )

            subprocess.run(
                ["bash", str(ROOT / "scripts" / "backup_postgres.sh")],
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            artifacts = list(backup_dir.glob("*.dump.age"))
            self.assertEqual(len(artifacts), 1)
            backup_file = artifacts[0]
            self.assertTrue(Path(f"{backup_file}.sha256").is_file())
            self.assertTrue(Path(f"{backup_file}.manifest.json").is_file())

            restore_env = env.copy()
            restore_env.update(
                {
                    "PG_DSN": self.target_dsn,
                    "AGE_IDENTITY": str(key_path),
                    "BACKUP_FILE": str(backup_file),
                    "CONFIRM_RESTORE": "RESTORE-PLATFORM-DATABASE",
                    "ALLOW_DIFFERENT_DATABASE": "1",
                }
            )
            subprocess.run(
                ["bash", str(ROOT / "scripts" / "restore_postgres.sh")],
                env=restore_env,
                check=True,
                capture_output=True,
                text=True,
            )
            with connect(self.target_dsn) as conn:
                row = conn.execute(
                    "SELECT marker FROM recovery_marker"
                ).fetchone()
            self.assertEqual(row, ("release-gate-backup-roundtrip",))


if __name__ == "__main__":
    unittest.main()
