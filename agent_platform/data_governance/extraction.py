"""Paged, resumable source extraction for migration batches.

A staging adapter must persist each page and its next cursor atomically. This
makes a retry safe if the process stops between source reads and later import.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class ExtractionPage:
    rows: Sequence[Mapping[str, Any]]
    next_cursor: str | None


@dataclass(frozen=True)
class ExtractionReport:
    batch_id: str
    extracted_count: int
    pages_read: int
    resumed_from_cursor: str | None
    completed: bool


class MigrationSource(Protocol):
    async def extract_page(
        self, *, cursor: str | None, limit: int,
    ) -> ExtractionPage: ...


class MigrationStaging(Protocol):
    """Durable staging contract; append_page must atomically save rows + cursor."""

    async def get_checkpoint(self, *, tenant_id: str, batch_id: str) -> str | None: ...

    async def append_page(
        self, *, tenant_id: str, batch_id: str, rows: Sequence[Mapping[str, Any]],
        next_cursor: str | None,
    ) -> int:
        """Idempotently stage rows and persist next_cursor in one transaction."""
        ...

    async def iter_rows(
        self, *, tenant_id: str, batch_id: str,
    ) -> Sequence[Mapping[str, Any]]: ...


class MigrationExtractor:
    """Extract source pages into durable staging with cursor-based recovery."""

    def __init__(self, *, staging: MigrationStaging, page_size: int = 500) -> None:
        if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1:
            raise ValueError("page_size must be a positive integer")
        self.staging = staging
        self.page_size = page_size

    async def extract(
        self, *, tenant_id: str, batch_id: str, source: MigrationSource,
    ) -> ExtractionReport:
        if not tenant_id.strip() or not batch_id.strip():
            raise ValueError("tenant_id and batch_id are required")

        cursor = await self.staging.get_checkpoint(
            tenant_id=tenant_id, batch_id=batch_id,
        )
        resumed_from = cursor
        seen_cursors: set[str] = set()
        if cursor is not None:
            seen_cursors.add(cursor)
        extracted = 0
        pages = 0

        while True:
            page = await source.extract_page(cursor=cursor, limit=self.page_size)
            if not isinstance(page, ExtractionPage):
                raise TypeError("source must return an ExtractionPage")
            if page.next_cursor is not None:
                if not isinstance(page.next_cursor, str) or not page.next_cursor:
                    raise ValueError("next_cursor must be a non-empty string or None")
                if page.next_cursor == cursor or page.next_cursor in seen_cursors:
                    raise ValueError("source extraction cursor did not advance")
            if not page.rows and page.next_cursor is not None:
                raise ValueError("empty extraction page cannot advance the cursor")

            # The adapter must commit page contents and cursor together. A retry
            # after a crash therefore repeats at most an idempotent page write.
            extracted += await self.staging.append_page(
                tenant_id=tenant_id, batch_id=batch_id,
                rows=page.rows, next_cursor=page.next_cursor,
            )
            pages += 1
            cursor = page.next_cursor
            if cursor is None:
                return ExtractionReport(
                    batch_id=batch_id, extracted_count=extracted, pages_read=pages,
                    resumed_from_cursor=resumed_from, completed=True,
                )
            seen_cursors.add(cursor)

class PostgresMigrationStaging:
    """PostgreSQL-backed page staging and cursor checkpoint store."""

    def __init__(self, *, connection_factory: Any, key_field: str = "id") -> None:
        if not key_field.strip():
            raise ValueError("key_field is required")
        self._connect = connection_factory
        self.key_field = key_field

    async def get_checkpoint(self, *, tenant_id: str, batch_id: str) -> str | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT next_cursor FROM platform_data_migration_checkpoints
                   WHERE tenant_id=%s AND batch_id=%s""",
                (tenant_id, batch_id),
            )
            row = await cursor.fetchone()
        return row["next_cursor"] if row is not None else None

    async def append_page(
        self, *, tenant_id: str, batch_id: str,
        rows: Sequence[Mapping[str, Any]], next_cursor: str | None,
    ) -> int:
        import json

        keys: set[str] = set()
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                for row in rows:
                    if self.key_field not in row or row[self.key_field] is None:
                        raise ValueError(f"staged row is missing key field: {self.key_field}")
                    source_key = str(row[self.key_field])
                    if not source_key or source_key in keys:
                        raise ValueError("staged page contains an empty or duplicate source key")
                    keys.add(source_key)
                    payload = json.dumps(
                        dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                    )
                    cursor = await connection.execute(
                        """INSERT INTO platform_data_migration_staging
                           (tenant_id,batch_id,source_key,source_payload)
                           VALUES (%s,%s,%s,%s::jsonb)
                           ON CONFLICT (tenant_id,batch_id,source_key) DO UPDATE
                             SET source_payload=EXCLUDED.source_payload
                             WHERE platform_data_migration_staging.source_payload=EXCLUDED.source_payload
                           RETURNING source_key""",
                        (tenant_id, batch_id, source_key, payload),
                    )
                    if await cursor.fetchone() is None:
                        raise ValueError("staged source row changed during extraction")
                await connection.execute(
                    """INSERT INTO platform_data_migration_checkpoints
                       (tenant_id,batch_id,next_cursor)
                       VALUES (%s,%s,%s)
                       ON CONFLICT (tenant_id,batch_id) DO UPDATE SET
                         next_cursor=EXCLUDED.next_cursor,updated_at=now()""",
                    (tenant_id, batch_id, next_cursor),
                )
        return len(rows)

    async def iter_rows(
        self, *, tenant_id: str, batch_id: str,
    ) -> Sequence[Mapping[str, Any]]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT source_payload FROM platform_data_migration_staging
                   WHERE tenant_id=%s AND batch_id=%s ORDER BY source_key""",
                (tenant_id, batch_id),
            )
            rows = await cursor.fetchall()
        return tuple(dict(row["source_payload"]) for row in rows)
