from __future__ import annotations

import os
import unittest
from pathlib import Path
from uuid import uuid4

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class ConnectorCleanupPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row
        self.tenant_id = "connector-cleanup-" + uuid4().hex
        self.connection_id = "source-" + uuid4().hex
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            for path in sorted(Path("agent_platform/storage/postgres").glob("*.sql")):
                await conn.execute(path.read_text(), prepare=False)
        async with await AsyncConnection.connect(DSN) as conn:
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id,display_name) VALUES (%s,%s)",
                (self.tenant_id, "Connector Cleanup Integration"),
            )
            await conn.execute(
                """INSERT INTO platform_data_connections
                   (tenant_id,connection_id,provider,display_name,secret_ref,created_by,status)
                   VALUES (%s,%s,'drive','Drive','secret://tenant/source','user-a','revoked')""",
                (self.tenant_id, self.connection_id),
            )
            await conn.commit()
        async def connect():
            return await AsyncConnection.connect(DSN, row_factory=dict_row)
        self.connect = connect

    async def test_cleanup_queue_is_idempotent_and_completes_both_steps(self):
        from agent_platform.control_plane.connector_cleanup import PostgresConnectorCleanupQueue
        queue = PostgresConnectorCleanupQueue(connection_factory=self.connect)
        await queue.enqueue(
            tenant_id=self.tenant_id, connection_id=self.connection_id,
            disposition="delete",
        )
        await queue.enqueue(
            tenant_id=self.tenant_id, connection_id=self.connection_id,
            disposition="delete",
        )
        job = await queue.claim(worker_id="cleanup-a", lease_seconds=30)
        self.assertEqual(job.attempts, 1)
        await queue.mark_remote_revoked(job)
        await queue.mark_sink_cleaned(job)
        await queue.complete(job)
        async with await self.connect() as conn:
            cursor = await conn.execute(
                """SELECT status,remote_revoked,sink_cleaned
                   FROM platform_connector_cleanup_jobs
                   WHERE tenant_id=%s AND connection_id=%s""",
                (self.tenant_id, self.connection_id),
            )
            row = await cursor.fetchone()
        self.assertEqual(row, {
            "status": "succeeded", "remote_revoked": True, "sink_cleaned": True,
        })

    async def test_partial_cleanup_state_survives_retry(self):
        from agent_platform.control_plane.connector_cleanup import PostgresConnectorCleanupQueue
        queue = PostgresConnectorCleanupQueue(connection_factory=self.connect)
        await queue.enqueue(
            tenant_id=self.tenant_id, connection_id=self.connection_id,
            disposition="retain",
        )
        job = await queue.claim(worker_id="cleanup-a", lease_seconds=30)
        await queue.mark_remote_revoked(job)
        self.assertFalse(await queue.fail(job, error_code="sinkunavailable"))
        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_connector_cleanup_jobs SET available_at=now()
                   WHERE tenant_id=%s AND connection_id=%s""",
                (self.tenant_id, self.connection_id),
            )
        retry = await queue.claim(worker_id="cleanup-b", lease_seconds=30)
        self.assertTrue(retry.remote_revoked)
        self.assertFalse(retry.sink_cleaned)
        await queue.mark_sink_cleaned(retry)
        await queue.complete(retry)


if __name__ == "__main__":
    unittest.main()
