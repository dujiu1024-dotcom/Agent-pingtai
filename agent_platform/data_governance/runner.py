"""Resumable, idempotent migration batch execution with post-write verification."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from agent_platform.data_governance.diff import MigrationDiff, compare_migration_rows
from agent_platform.data_governance.lifecycle import (
    MigrationBatch,
    MigrationRow,
    migration_fingerprint,
)


class MigrationLedger(Protocol):
    async def start_batch(self, tenant_id: str, batch_id: str) -> bool: ...
    async def get_row(self, tenant_id: str, batch_id: str, source_key: str) -> Mapping[str, Any] | None: ...
    async def record_row(self, row: MigrationRow) -> None: ...
    async def validate_batch(
        self, tenant_id: str, batch_id: str, *, source_count: int, target_count: int,
        source_checksum: str, target_checksum: str,
    ) -> bool: ...


class MigrationTarget(Protocol):
    """Target adapter must make upsert idempotent by source key."""

    async def upsert(self, *, source_key: str, payload: Mapping[str, Any]) -> str: ...
    async def snapshot(self, *, source_keys: Sequence[str]) -> Sequence[Mapping[str, Any]]: ...


@dataclass(frozen=True)
class MigrationExecutionReport:
    batch_id: str
    source_count: int
    imported_count: int
    failed_rows: Mapping[str, str]
    diff: MigrationDiff | None
    validated: bool


class MigrationRunner:
    def __init__(self, *, ledger: MigrationLedger) -> None:
        self.ledger = ledger

    async def execute(
        self,
        *,
        batch: MigrationBatch,
        source_rows: Sequence[Mapping[str, Any]],
        key_field: str,
        target: MigrationTarget,
        transform: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        normalize_target: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> MigrationExecutionReport:
        if not key_field.strip():
            raise ValueError("key_field is required")
        if not await self.ledger.start_batch(batch.tenant_id, batch.batch_id):
            raise ValueError("migration batch is not in a runnable state")

        source = [dict(row) for row in source_rows]
        source_keys: list[str] = []
        imported = 0
        failed: dict[str, str] = {}
        target_keys: dict[str, str | None] = {}

        # Validate key uniqueness before the first target write.
        seen: set[str] = set()
        for row in source:
            if key_field not in row or row[key_field] is None:
                raise ValueError(f"migration row is missing key field: {key_field}")
            key = str(row[key_field])
            if not key or key in seen:
                raise ValueError(f"missing or duplicate migration key: {key}")
            seen.add(key)
            source_keys.append(key)

        for source_row, source_key in zip(source, source_keys, strict=True):
            try:
                payload = dict(transform(source_row))
                digest = migration_fingerprint(
                    source_system=batch.source_system,
                    target_system=batch.target_system,
                    source_key=source_key,
                    payload=payload,
                )
                previous = await self.ledger.get_row(
                    batch.tenant_id, batch.batch_id, source_key,
                )
                if previous is not None and previous["payload_hash"] != digest:
                    raise ValueError("source row payload changed; create a new migration revision")
                if previous is not None and previous["status"] == "validated":
                    target_keys[source_key] = previous.get("target_key")
                    imported += 1
                    continue
                if previous is not None and previous["status"] == "skipped":
                    raise ValueError("previously skipped migration row requires a new batch")
                if previous is None or previous["status"] == "failed":
                    await self.ledger.record_row(MigrationRow(
                        batch.tenant_id, batch.batch_id, source_key,
                        previous.get("target_key") if previous else None,
                        digest, "pending",
                    ))
                target_key = await target.upsert(source_key=source_key, payload=payload)
                if not target_key:
                    raise ValueError("target adapter returned an empty target key")
                await self.ledger.record_row(MigrationRow(
                    batch.tenant_id, batch.batch_id, source_key, target_key, digest, "imported",
                ))
                target_keys[source_key] = target_key
                imported += 1
            except Exception as exc:
                failed[source_key] = type(exc).__name__
                try:
                    previous = await self.ledger.get_row(
                        batch.tenant_id, batch.batch_id, source_key,
                    )
                    if previous is not None:
                        await self.ledger.record_row(MigrationRow(
                            batch.tenant_id, batch.batch_id, source_key,
                            previous.get("target_key"), previous["payload_hash"], "failed",
                            type(exc).__name__,
                        ))
                except Exception:
                    # The batch remains running; a later retry reconciles its durable row ledger.
                    pass

        if failed:
            return MigrationExecutionReport(
                batch.batch_id, len(source), imported, failed, None, False,
            )

        target_rows = await target.snapshot(source_keys=source_keys)
        normalized_target = [dict(normalize_target(row)) for row in target_rows]
        diff = compare_migration_rows(
            source_rows=source, target_rows=normalized_target, key_field=key_field,
        )
        if not diff.matched:
            return MigrationExecutionReport(
                batch.batch_id, len(source), imported, {}, diff, False,
            )

        for source_key in source_keys:
            previous = await self.ledger.get_row(
                batch.tenant_id, batch.batch_id, source_key,
            )
            if previous is None:
                raise RuntimeError("migration ledger row disappeared during validation")
            await self.ledger.record_row(MigrationRow(
                batch.tenant_id, batch.batch_id, source_key,
                target_keys.get(source_key) or previous.get("target_key"),
                previous["payload_hash"], "validated",
            ))
        validated = await self.ledger.validate_batch(
            batch.tenant_id, batch.batch_id,
            source_count=diff.source_count,
            target_count=diff.target_count,
            source_checksum=diff.source_checksum,
            target_checksum=diff.target_checksum,
        )
        return MigrationExecutionReport(
            batch.batch_id, len(source), imported, {}, diff, validated,
        )
