"""Configurable, tenant-scoped PostgreSQL adapters for subject export and deletion.

Only direct ownership columns are supported. Domains with indirect ownership,
external replicas, or special retention rules must provide dedicated adapters.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Mapping


def _valid_identifier(value: str) -> bool:
    return bool(value) and (value[0].isascii() and (value[0].isalpha() or value[0] == "_")) and all(
        char.isascii() and (char.isalnum() or char == "_") for char in value
    )


def _qualified_identifier(value: str) -> str:
    parts = value.split(".")
    if not parts or any(not _valid_identifier(part) for part in parts):
        raise ValueError("SQL identifiers must be simple, pre-approved identifiers")
    return ".".join(f'"{part}"' for part in parts)


@dataclass(frozen=True)
class SubjectTable:
    table: str
    tenant_column: str
    subject_column: str
    columns: tuple[str, ...]
    order_by: tuple[str, ...]

    def __post_init__(self) -> None:
        _qualified_identifier(self.table)
        for column in (self.tenant_column, self.subject_column, *self.columns, *self.order_by):
            _qualified_identifier(column)
        if not self.columns or not self.order_by:
            raise ValueError("subject table requires explicit export columns and stable ordering")
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("subject export columns must be unique")
        if not set(self.order_by).issubset(self.columns):
            raise ValueError("order_by columns must be included in the export projection")
        if self.tenant_column == self.subject_column:
            raise ValueError("tenant and subject ownership columns must be distinct")


class PostgresSubjectTableAdapter:
    """Direct-ownership adapter implementing both export and deletion contracts.

    The supplied connection factory must return an async psycopg-compatible
    connection. Use dict_row or tuple rows; both are normalized to mappings.
    """

    def __init__(self, *, connection_factory: Callable[[], Any], table: SubjectTable) -> None:
        self._connect = connection_factory
        self.table = table

    async def export_subject(
        self, *, tenant_id: str, subject_id: str,
    ) -> AsyncIterator[Mapping[str, object]]:
        if not tenant_id or not subject_id:
            raise ValueError("tenant and subject ids are required")
        projection = ", ".join(_qualified_identifier(name) for name in self.table.columns)
        ordering = ", ".join(_qualified_identifier(name) for name in self.table.order_by)
        query = (
            f"SELECT {projection} FROM {_qualified_identifier(self.table.table)} "
            f"WHERE {_qualified_identifier(self.table.tenant_column)} = %s "
            f"AND {_qualified_identifier(self.table.subject_column)} = %s "
            f"ORDER BY {ordering}"
        )
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(query, (tenant_id, subject_id))
            names = tuple(
                getattr(column, "name", column[0]) for column in (cursor.description or ())
            )
            async for row in cursor:
                if isinstance(row, Mapping):
                    yield dict(row)
                else:
                    if len(row) != len(names):
                        raise ValueError("PostgreSQL row does not match cursor projection")
                    yield dict(zip(names, row, strict=True))

    async def delete_subject(
        self, *, tenant_id: str, subject_id: str, operation_id: str,
    ) -> None:
        if not tenant_id or not subject_id or not operation_id:
            raise ValueError("tenant, subject and operation ids are required")
        query = (
            f"DELETE FROM {_qualified_identifier(self.table.table)} "
            f"WHERE {_qualified_identifier(self.table.tenant_column)} = %s "
            f"AND {_qualified_identifier(self.table.subject_column)} = %s"
        )
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await connection.execute(query, (tenant_id, subject_id))


class PostgresSubjectExportLedger:
    """Durable source checkpoints for resumable subject exports."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    @staticmethod
    def _row_value(row: Any, name: str, index: int) -> Any:
        return row[name] if isinstance(row, Mapping) else row[index]

    async def begin_operation(
        self, *, tenant_id: str, operation_id: str, subject_id: str,
    ) -> None:
        if not tenant_id or not operation_id or not subject_id:
            raise ValueError("tenant, operation and subject ids are required")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    """INSERT INTO platform_subject_export_operations
                       (tenant_id,operation_id,subject_id,status)
                       VALUES (%s,%s,%s,'planned')
                       ON CONFLICT (tenant_id,operation_id) DO NOTHING""",
                    (tenant_id, operation_id, subject_id),
                )
                cursor = await connection.execute(
                    """SELECT subject_id FROM platform_subject_export_operations
                       WHERE tenant_id=%s AND operation_id=%s FOR UPDATE""",
                    (tenant_id, operation_id),
                )
                row = await cursor.fetchone()
                if row is None or self._row_value(row, "subject_id", 0) != subject_id:
                    raise ValueError("export operation id is already bound to another subject")
                await connection.execute(
                    """UPDATE platform_subject_export_operations
                       SET status=CASE WHEN status='completed' THEN status ELSE 'running' END,
                           updated_at=now()
                       WHERE tenant_id=%s AND operation_id=%s""",
                    (tenant_id, operation_id),
                )

    async def completed_sources(self, *, tenant_id: str, operation_id: str) -> tuple[str, ...]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT source_name FROM platform_subject_export_sources
                   WHERE tenant_id=%s AND operation_id=%s AND status='completed'
                   ORDER BY source_name""",
                (tenant_id, operation_id),
            )
            rows = await cursor.fetchall()
        return tuple(self._row_value(row, "source_name", 0) for row in rows)

    async def mark_source_started(
        self, *, tenant_id: str, operation_id: str, source: str,
    ) -> None:
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_subject_export_sources
                   (tenant_id,operation_id,source_name,status)
                   VALUES (%s,%s,%s,'running')
                   ON CONFLICT (tenant_id,operation_id,source_name) DO UPDATE
                   SET status=CASE
                         WHEN platform_subject_export_sources.status='completed' THEN 'completed'
                         ELSE 'running' END,
                       error_code=NULL,updated_at=now()""",
                (tenant_id, operation_id, source),
            )

    async def mark_source_completed(
        self, *, tenant_id: str, operation_id: str, source: str,
        record_count: int, sha256: str,
    ) -> None:
        if record_count < 0 or not sha256:
            raise ValueError("completed export source requires count and checksum")
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_subject_export_sources
                   (tenant_id,operation_id,source_name,status,record_count,sha256)
                   VALUES (%s,%s,%s,'completed',%s,%s)
                   ON CONFLICT (tenant_id,operation_id,source_name) DO UPDATE
                   SET status='completed',record_count=EXCLUDED.record_count,
                       sha256=EXCLUDED.sha256,error_code=NULL,updated_at=now()""",
                (tenant_id, operation_id, source, record_count, sha256),
            )

    async def mark_source_failed(
        self, *, tenant_id: str, operation_id: str, source: str, error_code: str,
    ) -> None:
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_subject_export_sources
                   (tenant_id,operation_id,source_name,status,error_code)
                   VALUES (%s,%s,%s,'failed',%s)
                   ON CONFLICT (tenant_id,operation_id,source_name) DO UPDATE
                   SET status='failed',error_code=EXCLUDED.error_code,updated_at=now()""",
                (tenant_id, operation_id, source, error_code),
            )

    async def finish_operation(
        self, *, tenant_id: str, operation_id: str, complete: bool,
    ) -> None:
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """UPDATE platform_subject_export_operations AS operation
                   SET status=CASE
                         WHEN EXISTS (
                           SELECT 1 FROM platform_subject_export_sources AS source
                           WHERE source.tenant_id=operation.tenant_id
                             AND source.operation_id=operation.operation_id
                             AND source.status='failed'
                         ) THEN 'partial'
                         WHEN %s THEN 'completed'
                         ELSE 'partial'
                       END,
                       updated_at=now()
                   WHERE tenant_id=%s AND operation_id=%s""",
                (complete, tenant_id, operation_id),
            )
