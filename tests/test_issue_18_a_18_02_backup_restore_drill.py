"""Timed isolated PostgreSQL backup/restore drill; emits measured RPO/RTO evidence."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from psycopg import connect, sql
from psycopg.conninfo import make_conninfo


ROOT = Path(__file__).resolve().parents[1]
ADMIN_DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")
POLICY = json.loads((ROOT / "deploy/disaster-recovery-policy.json").read_text(encoding="utf-8"))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


@unittest.skipUnless(
    ADMIN_DSN and all(shutil.which(name) for name in ("pg_dump", "pg_restore", "psql", "age", "age-keygen")),
    "PostgreSQL CLI tools, age and AGENT_PLATFORM_TEST_DSN are required",
)
class Issue18BackupRestoreDrillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.admin_dsn = ADMIN_DSN
        cls.source_db = f"drill_src_{uuid4().hex[:12]}"
        cls.target_db = f"drill_dst_{uuid4().hex[:12]}"
        with connect(cls.admin_dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.source_db)))
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.target_db)))
        cls.source_dsn = make_conninfo(ADMIN_DSN, dbname=cls.source_db)
        cls.target_dsn = make_conninfo(ADMIN_DSN, dbname=cls.target_db)

    @classmethod
    def tearDownClass(cls) -> None:
        with connect(cls.admin_dsn, autocommit=True) as conn:
            for database in (cls.source_db, cls.target_db):
                conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))

    def test_encrypted_postgres_backup_restore_records_actual_rpo_rto(self) -> None:
        with tempfile.TemporaryDirectory(prefix="issue-18-drill-") as temp:
            work = Path(temp)
            identity_path = work / "age-identity.txt"
            key = subprocess.run(
                ["age-keygen", "-o", str(identity_path)],
                check=True, capture_output=True, text=True,
            )
            public_key = re.search(r"public key: (age1[0-9a-z]+)", key.stderr, re.IGNORECASE)
            self.assertIsNotNone(public_key, key.stderr)
            recipient = public_key.group(1) if public_key else ""
            backup_dir = work / "backups"
            env = os.environ.copy()
            env.update({"PG_DSN": self.source_dsn, "AGE_RECIPIENT": recipient, "BACKUP_DIR": str(backup_dir)})

            with connect(self.source_dsn) as conn:
                conn.execute("CREATE TABLE recovery_marker (marker text PRIMARY KEY)")
                conn.execute("INSERT INTO recovery_marker(marker) VALUES (%s)", ("issue-18-a-18-02",))
            last_good_write = utc_now()
            subprocess.run(
                ["bash", str(ROOT / "scripts/backup_postgres.sh")],
                env=env, check=True, capture_output=True, text=True,
            )
            artifacts = list(backup_dir.glob("*.dump.age"))
            self.assertEqual(len(artifacts), 1)
            backup_file = artifacts[0]
            incident_declared = utc_now()

            restore_env = env.copy()
            restore_env.update({
                "PG_DSN": self.target_dsn,
                "AGE_IDENTITY": str(identity_path),
                "BACKUP_FILE": str(backup_file),
                "CONFIRM_RESTORE": "RESTORE-PLATFORM-DATABASE",
                "ALLOW_DIFFERENT_DATABASE": "1",
            })
            subprocess.run(
                ["bash", str(ROOT / "scripts/restore_postgres.sh")],
                env=restore_env, check=True, capture_output=True, text=True,
            )
            with connect(self.target_dsn) as conn:
                row = conn.execute("SELECT marker FROM recovery_marker").fetchone()
            self.assertEqual(row, ("issue-18-a-18-02",))
            traffic_resumed = utc_now()

            measured_rpo = (incident_declared - last_good_write).total_seconds()
            measured_rto = (traffic_resumed - incident_declared).total_seconds()
            objective = POLICY["objectives"]["postgres"]
            record = {
                "exercise_id": f"DR-ISSUE18-{uuid4().hex[:12]}",
                "scope": "postgres",
                "environment": "ephemeral GitHub Actions PostgreSQL service; isolated source/restore databases",
                "scenario": "encrypted pg_dump backup followed by destructive-loss simulation and restore into a separate empty database",
                "last_known_good_write_at": iso(last_good_write),
                "incident_declared_at": iso(incident_declared),
                "traffic_resumed_at": iso(traffic_resumed),
                "measured_rpo_seconds": round(measured_rpo, 3),
                "measured_rto_seconds": round(measured_rto, 3),
                "target_rpo_seconds": objective["rpo_seconds"],
                "target_rto_seconds": objective["rto_seconds"],
                "component_checks": {"postgres": True},
                "consistency_checks": {"postgres_restore_marker_verified": True},
                "reviewer_signoff": False,
                "evidence_references": [
                    "CI artifact: issue-18-a-18-02-backup-restore-drill",
                    f"backup-manifest: {backup_file.name}.manifest.json",
                ],
                "limitations": [
                    "PostgreSQL component-only drill; Redis, MinIO, config, full-platform consistency and failover were not exercised.",
                    "Ephemeral CI service measurements are not production RPO/RTO or an SLA.",
                    "Independent human reviewer sign-off remains pending.",
                ],
            }
            evidence_dir = Path(os.environ.get("DRILL_EVIDENCE_DIR", work / "drill-evidence"))
            evidence_dir.mkdir(parents=True, exist_ok=True)
            evidence_path = evidence_dir / "postgres-backup-restore-drill.json"
            evidence_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            self.assertLessEqual(measured_rpo, objective["rpo_seconds"])
            self.assertLessEqual(measured_rto, objective["rto_seconds"])
            self.assertTrue(Path(f"{backup_file}.sha256").is_file())
            self.assertTrue(Path(f"{backup_file}.manifest.json").is_file())
            print(json.dumps(record, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
