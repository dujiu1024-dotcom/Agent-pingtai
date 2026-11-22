from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

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

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, query, params=()):
        self.query = query
        self.params = params
        return self.cursor


class RunLeaseTokenEnforcementTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_token_cannot_heartbeat_or_release_lease(self):
        for method_name in ("heartbeat", "release_lease"):
            connection = _Connection(row=None)

            async def connect():
                return connection

            store = PostgresRunStateStore(connect)
            method = getattr(store, method_name)
            result = await method(
                "tenant-a", "run-a", "old-worker", lease_token=4,
            )
            self.assertFalse(result)
            self.assertIn("AND lease_token=%s", connection.query)
            self.assertNotIn("IS NULL OR", connection.query)
            self.assertEqual(connection.params[-1:], (4,))

    async def test_stale_token_cannot_record_worker_failure(self):
        connection = _Connection(row=None)

        async def connect():
            return connection

        store = PostgresRunStateStore(connect)
        result = await store.record_workbench_failure(
            "tenant-a",
            "00000000-0000-0000-0000-000000000001",
            "old-worker",
            error="stale worker failure",
            lease_token=4,
        )
        self.assertIsNone(result)
        self.assertIn("AND lease_token=%s", connection.query)
        self.assertNotIn("IS NULL OR", connection.query)
        self.assertEqual(connection.params[-1:], (4,))

    async def test_stale_token_does_not_own_lease(self):
        connection = _Connection(row=None)

        async def connect():
            return connection

        store = PostgresRunStateStore(connect)
        self.assertFalse(await store.owns_lease(
            "tenant-a", "run-a", "old-worker", lease_token=4,
        ))
        self.assertIn("AND lease_token=%s", connection.query)
        self.assertEqual(connection.params[-1:], (4,))

    async def test_lease_writes_require_token_argument(self):
        connect = AsyncMock()
        store = PostgresRunStateStore(connect)
        with self.assertRaises(TypeError):
            await store.heartbeat("tenant-a", "run-a", "worker-a")
        with self.assertRaises(TypeError):
            await store.release_lease("tenant-a", "run-a", "worker-a")
        with self.assertRaises(TypeError):
            await store.record_workbench_failure(
                "tenant-a",
                "00000000-0000-0000-0000-000000000001",
                "worker-a",
                error="failure",
            )
        connect.assert_not_awaited()

    async def test_non_positive_and_boolean_tokens_are_rejected(self):
        connect = AsyncMock()
        store = PostgresRunStateStore(connect)
        for token in (0, -1, True, 1.5):
            with self.subTest(token=token):
                with self.assertRaisesRegex(
                    ValueError, "lease_token must be a positive integer",
                ):
                    await store.heartbeat(
                        "tenant-a", "run-a", "worker-a", lease_token=token,
                    )
        connect.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
