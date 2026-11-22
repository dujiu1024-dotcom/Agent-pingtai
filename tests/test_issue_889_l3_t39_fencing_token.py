from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from agent_platform.runtime.lifecycle import RunStatus
from agent_platform.runtime.postgres_lifecycle import PostgresRunStateStore


class _Cursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, cursor):
        self.cursor = cursor
        self.query = None
        self.params = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, query, params=()):
        self.query = query
        self.params = params
        return self.cursor


class PostgresRunLeaseFencingTokenTests(unittest.IsolatedAsyncioTestCase):
    async def test_claim_returns_persisted_monotonic_token(self):
        row = {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "status": "running",
            "version": 2,
            "checkpoint_ref": None,
            "last_error": None,
            "updated_at": datetime.now(timezone.utc),
            "lease_token": 7,
        }
        connection = _Connection(_Cursor(rows=[row]))

        async def connect():
            return connection

        store = PostgresRunStateStore(connect)
        claimed = await store.claim_recoverable("worker-a")
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0].lease_token, 7)
        self.assertEqual(claimed[0].status, RunStatus.RUNNING)
        self.assertIn("lease_token=r.lease_token+1", connection.query)
        self.assertIn("r.lease_token", connection.query)

    async def test_heartbeat_checks_supplied_token_in_update_predicate(self):
        connection = _Connection(_Cursor(row={"run_id": "run-a"}))

        async def connect():
            return connection

        store = PostgresRunStateStore(connect)
        self.assertTrue(await store.heartbeat(
            "tenant-a", "run-a", "worker-a", lease_token=12,
        ))
        self.assertIn("lease_token=%s", connection.query)
        self.assertEqual(connection.params[-2:], (12, 12))

    async def test_release_checks_supplied_token_in_update_predicate(self):
        connection = _Connection(_Cursor(row={"run_id": "run-a"}))

        async def connect():
            return connection

        store = PostgresRunStateStore(connect)
        self.assertTrue(await store.release_lease(
            "tenant-a", "run-a", "worker-a", lease_token=12,
        ))
        self.assertIn("lease_token=%s", connection.query)
        self.assertEqual(connection.params[-2:], (12, 12))

    async def test_owns_lease_checks_supplied_token(self):
        connection = _Connection(_Cursor(row={"exists": 1}))

        async def connect():
            return connection

        store = PostgresRunStateStore(connect)
        self.assertTrue(await store.owns_lease(
            "tenant-a", "run-a", "worker-a", lease_token=3,
        ))
        self.assertIn("lease_token=%s", connection.query)
        self.assertEqual(connection.params[-2:], (3, 3))


    async def test_workbench_failure_write_checks_supplied_token(self):
        connection = _Connection(_Cursor(row={"status": "interrupted"}))

        async def connect():
            return connection

        store = PostgresRunStateStore(connect)
        self.assertEqual(await store.record_workbench_failure(
            "tenant-a", "00000000-0000-0000-0000-000000000001",
            "worker-a", error="failed", lease_token=9,
        ), "interrupted")
        self.assertIn("lease_token=%s", connection.query)
        self.assertEqual(connection.params[-2:], (9, 9))

    async def test_lease_write_rejects_non_positive_token_before_database_access(self):
        connect = AsyncMock()
        store = PostgresRunStateStore(connect)
        for method in (
            lambda: store.heartbeat("tenant-a", "run-a", "worker-a", lease_token=0),
            lambda: store.release_lease("tenant-a", "run-a", "worker-a", lease_token=-1),
            lambda: store.owns_lease("tenant-a", "run-a", "worker-a", lease_token=True),
            lambda: store.record_workbench_failure("tenant-a", "00000000-0000-0000-0000-000000000001", "worker-a", error="x", lease_token=0),
        ):
            with self.subTest(method=method), self.assertRaisesRegex(
                ValueError, "lease_token must be a positive integer",
            ):
                await method()
        connect.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
