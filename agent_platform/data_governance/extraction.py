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
