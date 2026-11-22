from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from agent_platform.runtime.lifecycle import RunSnapshot, RunStatus
from agent_platform.runtime.postgres_lifecycle import PostgresRunStateStore


class _Cursor:
    def __init__(self, row=None):
        self.row = row

    async def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row=None):
        self.cursor = _Cursor(row)
        self.query = ""
        self.params = ()
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, query, params=()):
        self.query = query
        self.params = params
        self.calls += 1
        return self.cursor


class RunLeaseFencingEnforcementTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_worker_heartbeat_is_rejected_by_database_predicate(self):
        connection = _Connection(row=None)

        async def connect():
            return connection

        store = PostgresRunStateStore(connect)
        accepted = await store.heartbeat(
            "tenant-a", "run-a", "worker-a", lease_token=11,
        )

        self.assertFalse(accepted)
        self.assertIn("lease_token=%s", connection.query)
        self.assertNotIn("IS NULL OR", connection.query)
        self.assertEqual(connection.params[-1], 11)

    async def test_stale_worker_release_cannot_clear_new_workers_lease(self):
        connection = _Connection(row=None)

        async def connect():
            return connection

        store = PostgresRunStateStore(connect)
        released = await store.release_lease(
            "tenant-a", "run-a", "worker-a", lease_token=11,
        )

        self.assertFalse(released)
        self.assertIn("worker_id=%s", connection.query)
        self.assertIn("lease_token=%s", connection.query)
        self.assertNotIn("IS NULL OR", connection.query)
        self.assertEqual(connection.params[-1], 11)

    async def test_stale_worker_failure_write_is_rejected(self):
        connection = _Connection(row=None)

        async def connect():
            return connection

        store = PostgresRunStateStore(connect)
        result = await store.record_workbench_failure(
            "tenant-a", "00000000-0000-0000-0000-000000000001",
            "worker-a", error="late failure", lease_token=11,
        )

        self.assertIsNone(result)
        self.assertIn("lease_token=%s", connection.query)
        self.assertNotIn("IS NULL OR", connection.query)
        self.assertEqual(connection.params[-1], 11)

    async def test_lease_ownership_requires_exact_current_token(self):
        connection = _Connection(row=None)

        async def connect():
            return connection

        store = PostgresRunStateStore(connect)
        owned = await store.owns_lease(
            "tenant-a", "run-a", "worker-a", lease_token=11,
        )

        self.assertFalse(owned)
        self.assertIn("lease_token=%s", connection.query)
        self.assertNotIn("IS NULL OR", connection.query)
        self.assertEqual(connection.params[-1], 11)


    async def test_stale_worker_run_transition_is_rejected_before_update(self):
        connection = _Connection(row={
            "status": "running",
            "version": 5,
            "session_id": None,
            "created_by": "actor-a",
            "project_id": None,
            "agent_id": "agent-a",
            "lease_token": 12,
        })

        async def connect():
            return connection

        store = PostgresRunStateStore(connect)
        stale_snapshot = RunSnapshot(
            "tenant-a", "run-a", RunStatus.SUCCEEDED, 5,
            lease_token=11,
        )
        with self.assertRaisesRegex(RuntimeError, "fencing token mismatch"):
            await store.transition(stale_snapshot, expected_version=5)

        self.assertEqual(connection.calls, 1)
        self.assertIn("FOR UPDATE", connection.query)

    async def test_missing_or_invalid_token_fails_before_database_access(self):
        connect = AsyncMock()
        store = PostgresRunStateStore(connect)
        with self.assertRaises(TypeError):
            await store.heartbeat("tenant-a", "run-a", "worker-a")
        with self.assertRaises(TypeError):
            await store.release_lease("tenant-a", "run-a", "worker-a")
        for token in (0, -1, True, None):
            with self.subTest(token=token):
                with self.assertRaises(ValueError):
                    await store.heartbeat(
                        "tenant-a", "run-a", "worker-a", lease_token=token,
                    )
        connect.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
