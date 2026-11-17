"""Append-only, tenant-scoped audit evidence for data migration and lifecycle operations.

This SQLite-backed journal is intended for local migration tooling and controlled
offline runs. Production deployments should place the database on protected
persistent storage and export signed evidence to the organization's immutable
audit sink.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

_GENESIS = "0" * 64
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_ACTIONS = {
    "migration_started",
    "migration_chunk_applied",
    "migration_paused",
    "migration_resumed",
    "migration_completed",
    "migration_failed",
    "migration_retry",
    "migration_diff_reported",
    "lifecycle_export_requested",
    "lifecycle_delete_requested",
    "lifecycle_retention_enforced",
    "cutover_evaluated",
}
_RESULTS = {"accepted", "completed", "partial", "failed", "rejected", "retrying"}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _reference(value: str, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} must be a non-empty bounded identifier/reference")
    return value


def _string_list(values: Iterable[str], field: str, *, limit: int = 100) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field} must be a list of strings")
    items = tuple(values)
    if len(items) > limit:
        raise ValueError(f"{field} cannot contain more than {limit} entries")
    return tuple(_reference(item, field) for item in items)


@dataclass(frozen=True)
class MigrationAuditEvent:
    event_id: str
    tenant_id: str
    actor_id: str
    batch_id: str
    action: str
    result: str
    attempt: int
    failed_items: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    details: dict[str, Any]
    occurred_at: str
    previous_hash: str
    event_hash: str


class MigrationAuditError(RuntimeError):
    """Raised when audit evidence is inconsistent or cannot be safely appended."""


def _connect(path: str | Path) -> sqlite3.Connection:
    database = Path(path).expanduser()
    if database.is_symlink():
        raise ValueError("audit database must not be a symlink")
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(database), timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def _initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS migration_audit_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            tenant_id TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            action TEXT NOT NULL,
            result TEXT NOT NULL,
            attempt INTEGER NOT NULL CHECK(attempt >= 1),
            failed_items_json TEXT NOT NULL,
            evidence_refs_json TEXT NOT NULL,
            details_json TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL,
            UNIQUE(tenant_id, event_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_migration_audit_tenant_batch
            ON migration_audit_events(tenant_id, batch_id, sequence);
        CREATE TRIGGER IF NOT EXISTS migration_audit_no_update
        BEFORE UPDATE ON migration_audit_events
        BEGIN SELECT RAISE(ABORT, 'migration audit is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS migration_audit_no_delete
        BEFORE DELETE ON migration_audit_events
        BEGIN SELECT RAISE(ABORT, 'migration audit is append-only'); END;
        """
    )


def _event_payload(
    *,
    event_id: str,
    tenant_id: str,
    actor_id: str,
    batch_id: str,
    action: str,
    result: str,
    attempt: int,
    failed_items: tuple[str, ...],
    evidence_refs: tuple[str, ...],
    details: dict[str, Any],
    occurred_at: str,
    previous_hash: str,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "tenant_id": tenant_id,
        "actor_id": actor_id,
        "batch_id": batch_id,
        "action": action,
        "result": result,
        "attempt": attempt,
        "failed_items": list(failed_items),
        "evidence_refs": list(evidence_refs),
        "details": details,
        "occurred_at": occurred_at,
        "previous_hash": previous_hash,
    }


def append_migration_audit_event(
    database: str | Path,
    *,
    tenant_id: str,
    actor_id: str,
    batch_id: str,
    action: str,
    result: str,
    attempt: int = 1,
    failed_items: Iterable[str] = (),
    evidence_refs: Iterable[str] = (),
    details: dict[str, Any] | None = None,
) -> MigrationAuditEvent:
    """Atomically append a tenant-scoped audit event linked to the prior tenant event."""
    tenant = _reference(tenant_id, "tenant_id")
    actor = _reference(actor_id, "actor_id")
    batch = _reference(batch_id, "batch_id")
    if action not in _ACTIONS:
        raise ValueError("unsupported migration audit action")
    if result not in _RESULTS:
        raise ValueError("unsupported migration audit result")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("attempt must be a positive integer")
    failed = _string_list(failed_items, "failed_items")
    evidence = _string_list(evidence_refs, "evidence_refs")
    metadata = {} if details is None else details
    if not isinstance(metadata, dict):
        raise ValueError("details must be a JSON object")
    details_json = _canonical(metadata)
    if len(details_json.encode("utf-8")) > 16_384:
        raise ValueError("details exceeds 16 KiB")
    # Ensure metadata is JSON data and reject NaN/Infinity.
    json.loads(details_json, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))

    connection = _connect(database)
    try:
        _initialize(connection)
        connection.execute("BEGIN IMMEDIATE")
        prior = connection.execute(
            "SELECT event_hash FROM migration_audit_events WHERE tenant_id=? "
            "ORDER BY sequence DESC LIMIT 1",
            (tenant,),
        ).fetchone()
        previous_hash = str(prior["event_hash"]) if prior else _GENESIS
        event_id = str(uuid4())
        occurred_at = datetime.now(timezone.utc).isoformat()
        payload = _event_payload(
            event_id=event_id, tenant_id=tenant, actor_id=actor, batch_id=batch,
            action=action, result=result, attempt=attempt, failed_items=failed,
            evidence_refs=evidence, details=metadata, occurred_at=occurred_at,
            previous_hash=previous_hash,
        )
        event_hash = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
        connection.execute(
            "INSERT INTO migration_audit_events "
            "(event_id,tenant_id,actor_id,batch_id,action,result,attempt,failed_items_json,"
            "evidence_refs_json,details_json,occurred_at,previous_hash,event_hash) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id, tenant, actor, batch, action, result, attempt,
                _canonical(list(failed)), _canonical(list(evidence)), details_json,
                occurred_at, previous_hash, event_hash,
            ),
        )
        connection.commit()
        return MigrationAuditEvent(
            event_id, tenant, actor, batch, action, result, attempt, failed,
            evidence, metadata, occurred_at, previous_hash, event_hash,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_migration_audit_events(
    database: str | Path,
    *,
    tenant_id: str,
    batch_id: str | None = None,
    limit: int = 100,
) -> tuple[MigrationAuditEvent, ...]:
    """Read audit events within one tenant only, in append order."""
    tenant = _reference(tenant_id, "tenant_id")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    connection = _connect(database)
    try:
        _initialize(connection)
        if batch_id is None:
            rows = connection.execute(
                "SELECT * FROM migration_audit_events WHERE tenant_id=? "
                "ORDER BY sequence LIMIT ?",
                (tenant, limit),
            ).fetchall()
        else:
            batch = _reference(batch_id, "batch_id")
            rows = connection.execute(
                "SELECT * FROM migration_audit_events WHERE tenant_id=? AND batch_id=? "
                "ORDER BY sequence LIMIT ?",
                (tenant, batch, limit),
            ).fetchall()
        return tuple(
            MigrationAuditEvent(
                row["event_id"], row["tenant_id"], row["actor_id"], row["batch_id"],
                row["action"], row["result"], row["attempt"],
                tuple(json.loads(row["failed_items_json"])),
                tuple(json.loads(row["evidence_refs_json"])),
                json.loads(row["details_json"]), row["occurred_at"],
                row["previous_hash"], row["event_hash"],
            )
            for row in rows
        )
    finally:
        connection.close()


def verify_migration_audit_chain(
    database: str | Path, *, tenant_id: str
) -> dict[str, Any]:
    """Verify every event hash and previous-hash link for a single tenant."""
    tenant = _reference(tenant_id, "tenant_id")
    connection = _connect(database)
    try:
        _initialize(connection)
        rows = connection.execute(
            "SELECT * FROM migration_audit_events WHERE tenant_id=? ORDER BY sequence",
            (tenant,),
        ).fetchall()
        expected_previous = _GENESIS
        for index, row in enumerate(rows, start=1):
            if row["previous_hash"] != expected_previous:
                return {"valid": False, "events_checked": index - 1, "failed_sequence": row["sequence"],
                        "reason": "previous_hash_mismatch"}
            payload = _event_payload(
                event_id=row["event_id"], tenant_id=row["tenant_id"],
                actor_id=row["actor_id"], batch_id=row["batch_id"],
                action=row["action"], result=row["result"], attempt=row["attempt"],
                failed_items=tuple(json.loads(row["failed_items_json"])),
                evidence_refs=tuple(json.loads(row["evidence_refs_json"])),
                details=json.loads(row["details_json"]), occurred_at=row["occurred_at"],
                previous_hash=row["previous_hash"],
            )
            actual = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
            if actual != row["event_hash"]:
                return {"valid": False, "events_checked": index - 1, "failed_sequence": row["sequence"],
                        "reason": "event_hash_mismatch"}
            expected_previous = actual
        return {"valid": True, "events_checked": len(rows), "failed_sequence": None, "reason": None}
    finally:
        connection.close()
