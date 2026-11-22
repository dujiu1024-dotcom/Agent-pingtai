#!/usr/bin/env python3
"""Run an isolated SQLite -> PostgreSQL staging import, zero-diff check and rollback drill.

This deliberately uses synthetic data and a disposable CI PostgreSQL database. It
never connects to production and never claims a production cutover was performed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.export_legacy_sqlite import export_database  # noqa: E402
from scripts.migrate_sqlite_to_pg import load_export, load_mapping, migrate  # noqa: E402
from scripts.rollback_cutover import REQUIRED_STEPS, load_plan, run_plan  # noqa: E402

TABLES: dict[str, tuple[str, ...]] = {
    "users": ("user_id", "display_name"),
    "tasks": ("task_id", "title", "status"),
    "documents": ("document_id", "source_uri", "payload"),
}


def _canonical(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=lambda v: {"$binary_hex": v.hex()})
         + "\n").encode("utf-8")
        for row in sorted(rows, key=lambda row: tuple(str(row[key]) for key in row if key.endswith("_id")))
    )


def _sha(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(_canonical(rows)).hexdigest()


def _sqlite_rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    cursor = connection.execute(f'SELECT * FROM "{table}" ORDER BY 1')
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _postgres_rows(connection: Any, table: str) -> list[dict[str, Any]]:
    cursor = connection.execute(f'SELECT * FROM public."{table}" ORDER BY 1')
    names = [item.name for item in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _mapping_file(path: Path) -> None:
    entries = []
    for table, columns in TABLES.items():
        entries.append({
            "source": table,
            "target_schema": "public",
            "target": table,
            "columns": {column: column for column in columns},
            "primary_key": [columns[0]],
        })
    path.write_text(json.dumps({"version": 1, "tables": entries}, indent=2), encoding="utf-8")


def _create_source(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE users (user_id INTEGER PRIMARY KEY, display_name TEXT NOT NULL);
            CREATE TABLE tasks (task_id INTEGER PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL);
            CREATE TABLE documents (document_id INTEGER PRIMARY KEY, source_uri TEXT NOT NULL, payload BLOB NOT NULL);
        """)
        db.executemany("INSERT INTO users VALUES (?, ?)", [(1, "Ada"), (2, "Lin")])
        db.executemany("INSERT INTO tasks VALUES (?, ?, ?)", [(10, "Prepare", "done"), (11, "Release", "ready")])
        db.executemany(
            "INSERT INTO documents VALUES (?, ?, ?)",
            [(20, "s3://staging/a", b"\x00alpha"), (21, "s3://staging/b", b"\xffbeta")],
        )


def _create_postgres_schema(connection: Any) -> None:
    connection.execute("DROP TABLE IF EXISTS public.documents, public.tasks, public.users CASCADE")
    connection.execute("CREATE TABLE public.users (user_id BIGINT PRIMARY KEY, display_name TEXT NOT NULL)")
    connection.execute("CREATE TABLE public.tasks (task_id BIGINT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL)")
    connection.execute("CREATE TABLE public.documents (document_id BIGINT PRIMARY KEY, source_uri TEXT NOT NULL, payload BYTEA NOT NULL)")
    connection.commit()


def _init_rollback_sqlite(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE users (user_id INTEGER PRIMARY KEY, display_name TEXT NOT NULL);
            CREATE TABLE tasks (task_id INTEGER PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL);
            CREATE TABLE documents (document_id INTEGER PRIMARY KEY, source_uri TEXT NOT NULL, payload BLOB NOT NULL);
        """)


def _step_main(args: argparse.Namespace) -> int:
    import psycopg

    marker_dir = args.marker_dir
    marker_dir.mkdir(parents=True, exist_ok=True)
    if args.step == "freeze_writes":
        (marker_dir / "writes-frozen").write_text("frozen\n", encoding="utf-8")
    elif args.step == "reverse_sync":
        with psycopg.connect(args.dsn) as pg, sqlite3.connect(args.rollback_db) as legacy:
            for table, columns in TABLES.items():
                rows = _postgres_rows(pg, table)
                legacy.execute(f'DELETE FROM "{table}"')
                placeholders = ",".join("?" for _ in columns)
                legacy.executemany(
                    f'INSERT INTO "{table}" ({",".join(chr(34)+c+chr(34) for c in columns)}) VALUES ({placeholders})',
                    [tuple(row[column] for column in columns) for row in rows],
                )
            legacy.commit()
    elif args.step == "verify_parity":
        with psycopg.connect(args.dsn) as pg, sqlite3.connect(args.rollback_db) as legacy:
            for table in TABLES:
                if _canonical(_postgres_rows(pg, table)) != _canonical(_sqlite_rows(legacy, table)):
                    raise RuntimeError(f"reverse-sync parity mismatch: {table}")
    elif args.step == "switch_gateway_to_legacy":
        if not (marker_dir / "writes-frozen").is_file():
            raise RuntimeError("cannot switch gateway unless writes are frozen")
        (marker_dir / "gateway-target").write_text("legacy\n", encoding="utf-8")
    elif args.step == "verify_legacy_health":
        if (marker_dir / "gateway-target").read_text(encoding="utf-8").strip() != "legacy":
            raise RuntimeError("legacy gateway is not active")
        with sqlite3.connect(args.rollback_db) as legacy:
            for table in TABLES:
                legacy.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
    elif args.step == "unfreeze_legacy_writes":
        if not (marker_dir / "gateway-target").is_file():
            raise RuntimeError("cannot unfreeze before legacy gateway health check")
        (marker_dir / "writes-frozen").unlink(missing_ok=True)
    else:
        raise ValueError(f"unknown rollback step: {args.step}")
    return 0


def run_drill(dsn: str, evidence_path: Path) -> dict[str, Any]:
    import psycopg

    with tempfile.TemporaryDirectory(prefix="issue-666-l4-t08-") as temp:
        root = Path(temp)
        source_db = root / "legacy-source.sqlite"
        export_dir = root / "source-export"
        mapping_path = root / "mapping.json"
        rollback_db = root / "legacy-rollback.sqlite"
        marker_dir = root / "rollback-state"
        _create_source(source_db)
        manifest = export_database(source_db, export_dir)
        _mapping_file(mapping_path)
        loaded_manifest, source_rows = load_export(export_dir)
        mappings = load_mapping(mapping_path, loaded_manifest)

        with psycopg.connect(dsn) as pg:
            _create_postgres_schema(pg)
            dry_run = migrate(pg, source_rows, mappings, dry_run=True)
            if dry_run.get("status") != "validated":
                raise RuntimeError("migration dry-run did not validate")
            applied = migrate(pg, source_rows, mappings, dry_run=False)
            if applied.get("status") != "committed":
                raise RuntimeError("staging migration did not commit")
            pre_rollback: dict[str, Any] = {}
            for table in TABLES:
                src = _sqlite_rows(sqlite3.connect(source_db), table)
                dst = _postgres_rows(pg, table)
                if _canonical(src) != _canonical(dst):
                    raise RuntimeError(f"source-to-staging zero-diff check failed: {table}")
                pre_rollback[table] = {
                    "rows_source": len(src),
                    "rows_staging": len(dst),
                    "source_sha256": _sha(src),
                    "staging_sha256": _sha(dst),
                    "zero_diff": True,
                }
            # Model one post-cutover write. The reverse-sync must bring this row
            # back before the gateway can safely point at the legacy database.
            pg.execute("INSERT INTO public.tasks (task_id, title, status) VALUES (12, 'Post-cutover', 'created')")
            pg.commit()
            post_cutover_rows = {table: len(_postgres_rows(pg, table)) for table in TABLES}

        _init_rollback_sqlite(rollback_db)
        steps = []
        for name in REQUIRED_STEPS:
            steps.append({
                "name": name,
                "argv": [
                    sys.executable, str(Path(__file__).resolve()),
                    "--step", name, "--dsn", dsn,
                    "--rollback-db", str(rollback_db), "--marker-dir", str(marker_dir),
                ],
                "timeout_seconds": 45,
            })
        plan_path = root / "rollback-plan.json"
        plan_path.write_text(json.dumps({
            "version": 1, "max_duration_seconds": 270, "steps": steps,
        }, indent=2), encoding="utf-8")
        plan = load_plan(plan_path)
        rollback_evidence = root / "rollback-evidence.json"
        rollback_status = run_plan(plan, rollback_evidence)
        rollback_report = json.loads(rollback_evidence.read_text(encoding="utf-8"))
        if rollback_status != 0 or rollback_report.get("status") != "succeeded":
            raise RuntimeError("rollback orchestrator did not complete all safety steps")

        with psycopg.connect(dsn) as pg, sqlite3.connect(rollback_db) as legacy:
            post_rollback: dict[str, Any] = {}
            for table in TABLES:
                pg_rows = _postgres_rows(pg, table)
                legacy_rows = _sqlite_rows(legacy, table)
                if _canonical(pg_rows) != _canonical(legacy_rows):
                    raise RuntimeError(f"PostgreSQL-to-legacy zero-diff check failed: {table}")
                post_rollback[table] = {
                    "rows_postgres": len(pg_rows),
                    "rows_legacy": len(legacy_rows),
                    "postgres_sha256": _sha(pg_rows),
                    "legacy_sha256": _sha(legacy_rows),
                    "zero_diff": True,
                }
        if (marker_dir / "writes-frozen").exists():
            raise RuntimeError("legacy writes remained frozen after successful rollback")
        if (marker_dir / "gateway-target").read_text(encoding="utf-8").strip() != "legacy":
            raise RuntimeError("gateway did not remain on legacy after rollback")

        report = {
            "schema": "agent-platform-issue-666-l4-t08-staging-drill-v1",
            "status": "passed",
            "environment": "disposable-ci-postgresql-and-synthetic-sqlite",
            "production_data_used": False,
            "production_cutover_performed": False,
            "source_manifest_tables": len(manifest["tables"]),
            "source_manifest_rows": sum(item["rows_exported"] for item in manifest["tables"]),
            "migration_dry_run": dry_run,
            "migration_apply": applied,
            "source_to_staging": pre_rollback,
            "simulated_post_cutover_rows": post_cutover_rows,
            "rollback": rollback_report,
            "postgres_to_legacy": post_rollback,
            "acceptance": {
                "all_source_rows_imported": True,
                "source_to_staging_zero_diff": True,
                "reverse_sync_completed": True,
                "postgres_to_legacy_zero_diff": True,
                "gateway_switched_to_legacy": True,
                "writes_unfrozen_after_health_check": True,
                "rollback_under_300_seconds": rollback_report["duration_seconds"] < 300,
            },
            "limitations": [
                "This is an executable staging drill against disposable CI infrastructure, not a production cutover.",
                "Synthetic fixtures do not establish completeness of the approved agency.db snapshot or business mapping.",
                "Production gateway integration and production rollback timing remain unverified.",
            ],
        }
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--step", choices=REQUIRED_STEPS)
    parser.add_argument("--rollback-db", type=Path)
    parser.add_argument("--marker-dir", type=Path)
    args = parser.parse_args()
    try:
        if args.step:
            if not args.dsn or not args.rollback_db or not args.marker_dir:
                raise ValueError("--step requires --dsn, --rollback-db and --marker-dir")
            return _step_main(args)
        if not args.dsn or not args.evidence:
            raise ValueError("--dsn and --evidence are required for a staging drill")
        report = run_drill(args.dsn, args.evidence)
        print(json.dumps({
            "status": report["status"],
            "environment": report["environment"],
            "source_manifest_rows": report["source_manifest_rows"],
            "rollback_seconds": report["rollback"]["duration_seconds"],
            "evidence": str(args.evidence),
        }, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
