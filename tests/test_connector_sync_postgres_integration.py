from __future__ import annotations

import os
import unittest
from pathlib import Path
from uuid import uuid4

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class ConnectorSyncPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row
        self.tenant_id = "connector-sync-" + uuid4().hex
        self.connection_id = "source-" + uuid4().hex
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            for path in sorted(Path("agent_platform/storage/postgres").glob("*.sql")):
                await conn.execute(path.read_text(), prepare=False)
        async with await AsyncConnection.connect(DSN) as conn:
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id,display_name) VALUES (%s,%s)",
                (self.tenant_id, "Connector Sync Integration"),
            )
            await conn.execute(
                """INSERT INTO platform_data_connections
                   (tenant_id,connection_id,provider,display_name,secret_ref,created_by)
                   VALUES (%s,%s,'drive','Drive','secret://tenant/source','integration')""",
                (self.tenant_id, self.connection_id),
            )
            await conn.commit()

        async def connect():
            return await AsyncConnection.connect(DSN, row_factory=dict_row)
        self.connect = connect

    async def test_sync_queue_is_idempotent_and_retries_after_failure(self):
        from agent_platform.control_plane.connector_sync import PostgresConnectorSyncQueue
        queue = PostgresConnectorSyncQueue(connection_factory=self.connect)
        await queue.enqueue(tenant_id=self.tenant_id, connection_id=self.connection_id)
        await queue.enqueue(tenant_id=self.tenant_id, connection_id=self.connection_id)
        job = await queue.claim(worker_id="sync-a", lease_seconds=30)
        self.assertEqual(job.attempts, 1)
        self.assertFalse(await queue.fail(job, error_code="providerunavailable"))
        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_connector_sync_jobs SET available_at=now()
                   WHERE tenant_id=%s AND connection_id=%s""",
                (self.tenant_id, self.connection_id),
            )
        retry = await queue.claim(worker_id="sync-b", lease_seconds=30)
        self.assertEqual(retry.attempts, 2)
        self.assertEqual(retry.lease_owner, "sync-b")
        await queue.complete(retry)
        async with await self.connect() as conn:
            cursor = await conn.execute(
                """SELECT status,attempts,last_error_code,completed_at IS NOT NULL AS completed
                   FROM platform_connector_sync_jobs
                   WHERE tenant_id=%s AND connection_id=%s""",
                (self.tenant_id, self.connection_id),
            )
            row = await cursor.fetchone()
        self.assertEqual(row["status"], "succeeded")
        self.assertEqual(row["attempts"], 2)
        self.assertEqual(row["last_error_code"], "providerunavailable")
        self.assertTrue(row["completed"])

    async def test_exhausted_sync_attempts_move_to_dead_letter(self):
        from agent_platform.control_plane.connector_sync import PostgresConnectorSyncQueue
        queue = PostgresConnectorSyncQueue(connection_factory=self.connect)
        await queue.enqueue(tenant_id=self.tenant_id, connection_id=self.connection_id)
        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_connector_sync_jobs SET max_attempts=1
                   WHERE tenant_id=%s AND connection_id=%s""",
                (self.tenant_id, self.connection_id),
            )
        job = await queue.claim(worker_id="sync-a", lease_seconds=30)
        self.assertTrue(await queue.fail(job, error_code="permanentfailure"))
        async with await self.connect() as conn:
            cursor = await conn.execute(
                """SELECT status,last_error_code FROM platform_connector_sync_jobs
                   WHERE tenant_id=%s AND connection_id=%s""",
                (self.tenant_id, self.connection_id),
            )
            row = await cursor.fetchone()
        self.assertEqual(row, {"status": "dead", "last_error_code": "permanentfailure"})


if __name__ == "__main__":
    unittest.main()
