from __future__ import annotations

import unittest
from contextlib import asynccontextmanager

from agent_platform.data_governance.postgres_adapters import (
    PostgresSubjectTableAdapter,
    SubjectTable,
)


class FakeCursor:
    description = (("tenant_id",), ("user_id",), ("record_id",), ("payload",))

    def __init__(self, rows=()):
        self.rows = rows

    def __aiter__(self):
        self._iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration


class FakeConnection:
    def __init__(self, rows=()):
        self.rows = rows
        self.executions = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, query, params):
        self.executions.append((query, params))
        return FakeCursor(self.rows)


class PostgresSubjectTableAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_is_tenant_and_subject_scoped_and_streams_rows(self):
        connection = FakeConnection([
            ("tenant-a", "user-1", "r1", "one"),
            ("tenant-a", "user-1", "r2", "two"),
        ])
        async def connect():
            return connection

        adapter = PostgresSubjectTableAdapter(
            connection_factory=connect,
            table=SubjectTable(
                table="public.user_records", tenant_column="tenant_id",
                subject_column="user_id",
                columns=("tenant_id", "user_id", "record_id", "payload"),
                order_by=("record_id",),
            ),
        )
        rows = [row async for row in adapter.export_subject(
            tenant_id="tenant-a", subject_id="user-1",
        )]
        self.assertEqual(rows[0]["record_id"], "r1")
        query, params = connection.executions[0]
        self.assertIn('"tenant_id" = %s', query)
        self.assertIn('"user_id" = %s', query)
        self.assertEqual(params, ("tenant-a", "user-1"))

    async def test_delete_uses_parameterized_tenant_and_subject_predicates(self):
        connection = FakeConnection()
        async def connect():
            return connection

        adapter = PostgresSubjectTableAdapter(
            connection_factory=connect,
            table=SubjectTable(
                table="user_records", tenant_column="tenant_id",
                subject_column="user_id", columns=("record_id",),
                order_by=("record_id",),
            ),
        )
        await adapter.delete_subject(
            tenant_id="tenant-a", subject_id="user-1", operation_id="delete-1",
        )
        query, params = connection.executions[0]
        self.assertTrue(query.startswith('DELETE FROM "user_records"'))
        self.assertEqual(params, ("tenant-a", "user-1"))

    async def test_rejects_untrusted_identifiers_and_unstable_projection(self):
        with self.assertRaisesRegex(ValueError, "identifiers"):
            SubjectTable(
                table="user_records; DROP TABLE users", tenant_column="tenant_id",
                subject_column="user_id", columns=("id",), order_by=("id",),
            )
        with self.assertRaisesRegex(ValueError, "included"):
            SubjectTable(
                table="user_records", tenant_column="tenant_id",
                subject_column="user_id", columns=("id",), order_by=("created_at",),
            )


if __name__ == "__main__":
    unittest.main()
