"""Configurable, tenant-scoped PostgreSQL adapters for subject export and deletion.

Only direct ownership columns are supported. Domains with indirect ownership,
external replicas, or special retention rules must provide dedicated adapters.
"""
from __future__ import annotations

import re

from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Mapping


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _qualified_identifier(value: str) -> str:
    parts = value.split(".")
    if not parts or any(not _IDENTIFIER.fullmatch(part) for part in parts):
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
