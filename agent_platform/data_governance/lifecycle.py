"""Explicit source-of-truth and deletion lifecycle contracts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class DataOwnership:
    object_type: str
    source_system: str
    target_system: str
    authoritative_writer: str
    migration_mode: str
    source_key: str
    target_key: str

    def __post_init__(self) -> None:
        if not all((self.object_type, self.source_system, self.target_system,
                    self.authoritative_writer, self.source_key, self.target_key)):
            raise ValueError("data ownership mapping fields are required")
        if self.migration_mode not in {"adapter", "batch", "rebuild", "retire"}:
            raise ValueError("unsupported migration mode")
        if self.authoritative_writer not in {self.source_system, self.target_system}:
            raise ValueError("authoritative writer must be source or target system")


@dataclass(frozen=True)
class RetentionPolicy:
    object_type: str
    retain_for: timedelta
    legal_hold_supported: bool = True
    soft_delete_days: int = 30

    def __post_init__(self) -> None:
        if not self.object_type or self.retain_for <= timedelta(0) or self.soft_delete_days < 0:
            raise ValueError("invalid retention policy")


@dataclass(frozen=True)
class DeletionResult:
    operation_id: str
    subject_id: str
    completed: tuple[str, ...]
    failed: Mapping[str, str]
    pending: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.failed and not self.pending


class DeletionAdapter(Protocol):
    async def delete_subject(self, *, tenant_id: str, subject_id: str,
                             operation_id: str) -> None: ...


class DeletionCoordinator:
    """Run a deletion across all configured stores; failures remain explicit and retryable."""

    def __init__(self, adapters: Mapping[str, DeletionAdapter]) -> None:
        if not adapters or any(not name.strip() for name in adapters):
            raise ValueError("at least one named deletion adapter is required")
        self.adapters = dict(adapters)

    async def delete(self, *, tenant_id: str, subject_id: str,
                     operation_id: str, completed_systems: Sequence[str] = ()) -> DeletionResult:
        if not tenant_id or not subject_id or not operation_id:
            raise ValueError("tenant, subject and operation ids are required")
        unknown = set(completed_systems) - self.adapters.keys()
        if unknown:
            raise ValueError("completed systems include unknown adapters")
        completed = list(dict.fromkeys(completed_systems))
        failed: dict[str, str] = {}
        for name, adapter in self.adapters.items():
            if name in completed:
                continue
            try:
                await adapter.delete_subject(
                    tenant_id=tenant_id, subject_id=subject_id,
                    operation_id=operation_id,
                )
                completed.append(name)
            except Exception as exc:
                failed[name] = type(exc).__name__
        pending = tuple(name for name in self.adapters if name not in completed and name not in failed)
        return DeletionResult(
            operation_id=operation_id, subject_id=subject_id,
            completed=tuple(completed), failed=failed, pending=pending,
        )




@dataclass(frozen=True)
class MigrationBatch:
    tenant_id: str
    batch_id: str
    source_system: str
    target_system: str
    object_type: str
    status: str
    source_count: int = 0
    target_count: int = 0
    source_checksum: str | None = None
    target_checksum: str | None = None


@dataclass(frozen=True)
class MigrationRow:
    tenant_id: str
    batch_id: str
    source_key: str
    target_key: str | None
    payload_hash: str
    status: str
    error_code: str | None = None


class PostgresMigrationRepository:
    """Durable, tenant-scoped migration ledger with guarded state transitions."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def create_batch(self, batch: MigrationBatch) -> None:
        if not all((batch.tenant_id, batch.batch_id, batch.source_system,
                    batch.target_system, batch.object_type)):
            raise ValueError("migration batch identity fields are required")
        if batch.status != "planned":
            raise ValueError("new migration batches must start in planned state")
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_data_migration_batches
                   (tenant_id,batch_id,source_system,target_system,object_type,status)
                   VALUES (%s,%s,%s,%s,%s,'planned')""",
                (batch.tenant_id,batch.batch_id,batch.source_system,
                 batch.target_system,batch.object_type),
            )

    async def start_batch(self, tenant_id: str, batch_id: str) -> bool:
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """UPDATE platform_data_migration_batches
                       SET status='running',started_at=COALESCE(started_at,now())
                       WHERE tenant_id=%s AND batch_id=%s AND status IN ('planned','running')
                       RETURNING batch_id""",
                    (tenant_id,batch_id),
                )
                return await cursor.fetchone() is not None

    async def get_row(self, tenant_id: str, batch_id: str,
                      source_key: str) -> Mapping[str, Any] | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT payload_hash,target_key,status
                   FROM platform_data_migration_rows
                   WHERE tenant_id=%s AND batch_id=%s AND source_key=%s""",
                (tenant_id,batch_id,source_key),
            )
            row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def record_row(self, row: MigrationRow) -> None:
        if not all((row.tenant_id,row.batch_id,row.source_key,row.payload_hash)):
            raise ValueError("migration row identity and payload hash are required")
        if row.status not in {"pending","imported","validated","skipped","failed"}:
            raise ValueError("invalid migration row status")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """SELECT payload_hash,status FROM platform_data_migration_rows
                       WHERE tenant_id=%s AND batch_id=%s AND source_key=%s FOR UPDATE""",
                    (row.tenant_id,row.batch_id,row.source_key),
                )
                old = await cursor.fetchone()
                if old is not None:
                    if old["payload_hash"] != row.payload_hash:
                        raise ValueError("source row payload changed; create a new migration revision")
                    transitions = {
                        "pending": {"pending","imported","skipped","failed"},
                        "failed": {"failed","pending"},
                        "imported": {"imported","validated","failed"},
                        "validated": {"validated"},
                        "skipped": {"skipped"},
                    }
                    if row.status not in transitions.get(old["status"], set()):
                        raise ValueError("invalid migration row status transition")
                await connection.execute(
                    """INSERT INTO platform_data_migration_rows
                       (tenant_id,batch_id,source_key,target_key,payload_hash,status,error_code)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (tenant_id,batch_id,source_key) DO UPDATE SET
                         target_key=EXCLUDED.target_key,status=EXCLUDED.status,
                         error_code=EXCLUDED.error_code,updated_at=now()""",
                    (row.tenant_id,row.batch_id,row.source_key,row.target_key,
                     row.payload_hash,row.status,row.error_code),
                )

    async def validate_batch(self, tenant_id: str, batch_id: str, *,
                             source_count: int, target_count: int,
                             source_checksum: str, target_checksum: str) -> bool:
        if min(source_count,target_count) < 0 or not source_checksum or not target_checksum:
            raise ValueError("counts and checksums are required")
        valid = source_count == target_count and source_checksum == target_checksum
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """UPDATE platform_data_migration_batches
                       SET source_count=%s,target_count=%s,source_checksum=%s,target_checksum=%s,
                           status=%s,finished_at=CASE WHEN %s THEN now() ELSE NULL END
                       WHERE tenant_id=%s AND batch_id=%s AND status='running'
                       RETURNING batch_id""",
                    (source_count,target_count,source_checksum,target_checksum,
                     "validated" if valid else "failed",valid,tenant_id,batch_id),
                )
                return await cursor.fetchone() is not None and valid

    async def switch_batch(self, tenant_id: str, batch_id: str) -> bool:
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """UPDATE platform_data_migration_batches
                       SET status='switched',finished_at=now()
                       WHERE tenant_id=%s AND batch_id=%s AND status='validated'
                       RETURNING batch_id""",
                    (tenant_id,batch_id),
                )
                return await cursor.fetchone() is not None

    async def rollback_batch(self, tenant_id: str, batch_id: str, *, reason: str) -> bool:
        if not reason.strip():
            raise ValueError("rollback reason is required")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """UPDATE platform_data_migration_batches
                       SET status='rolled_back',rollback_reason=%s,finished_at=now()
                       WHERE tenant_id=%s AND batch_id=%s AND status='switched'
                       RETURNING batch_id""",
                    (reason,tenant_id,batch_id),
                )
                return await cursor.fetchone() is not None


def migration_fingerprint(*, source_system: str, target_system: str,
                           source_key: str, payload: Mapping[str, Any]) -> str:
    """Stable idempotency key for a source row and its normalized payload."""
    canonical = json.dumps(
        {"source_system": source_system, "target_system": target_system,
         "source_key": source_key, "payload": payload},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
