from __future__ import annotations

import os
import unittest
from pathlib import Path
from uuid import uuid4

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class FileProcessingPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row

        from agent_platform.control_plane.file_processing import PostgresFileProcessingQueue
        self.tenant_id = "file-it-" + uuid4().hex
        self.asset_id = uuid4()
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            for path in sorted(Path("agent_platform/storage/postgres").glob("*.sql")):
                await conn.execute(path.read_text(), prepare=False)
        async with await AsyncConnection.connect(DSN) as conn:
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id,display_name) VALUES (%s,%s)",
                (self.tenant_id, "File Processing Integration"),
            )
            await conn.execute(
                """INSERT INTO platform_file_assets
                   (tenant_id,asset_id,owner_id,object_key,filename,media_type,size_bytes,sha256,status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending')""",
                (self.tenant_id, self.asset_id, "user-a",
                 f"tenant/{self.tenant_id}/assets/{uuid4()}/hash",
                 "notes.txt", "text/plain", 5, "a" * 64),
            )
            await conn.commit()
        async def connect():
            return await AsyncConnection.connect(DSN, row_factory=dict_row)
        self.queue = PostgresFileProcessingQueue(connection_factory=connect)

    async def test_enqueue_claim_and_complete_are_tenant_scoped(self):
        await self.queue.enqueue(tenant_id=self.tenant_id, asset_id=str(self.asset_id))
        await self.queue.enqueue(tenant_id=self.tenant_id, asset_id=str(self.asset_id))
        job = await self.queue.claim(worker_id="worker-a", lease_seconds=30)
        self.assertIsNotNone(job)
        self.assertEqual(job.tenant_id, self.tenant_id)
        self.assertEqual(job.asset_id, str(self.asset_id))
        self.assertEqual(job.attempts, 1)
        self.assertIsNone(await self.queue.claim(worker_id="worker-b", lease_seconds=30))
        await self.queue.complete(job)
        async with await self.queue._connect() as conn:
            cursor = await conn.execute(
                "SELECT status FROM platform_file_processing_jobs WHERE tenant_id=%s AND asset_id=%s",
                (self.tenant_id, self.asset_id),
            )
            self.assertEqual((await cursor.fetchone())["status"], "succeeded")

    async def test_failed_job_is_requeued_then_dead_lettered(self):
        await self.queue.enqueue(tenant_id=self.tenant_id, asset_id=str(self.asset_id))
        async with await self.queue._connect() as conn:
            await conn.execute(
                "UPDATE platform_file_processing_jobs SET max_attempts=2 WHERE tenant_id=%s AND asset_id=%s",
                (self.tenant_id, self.asset_id),
            )
        job = await self.queue.claim(worker_id="worker-a", lease_seconds=30)
        self.assertFalse(await self.queue.fail(job, error_code="parsererror"))
        async with await self.queue._connect() as conn:
            await conn.execute(
                """UPDATE platform_file_processing_jobs SET available_at=now()
                   WHERE tenant_id=%s AND asset_id=%s""",
                (self.tenant_id, self.asset_id),
            )
        job = await self.queue.claim(worker_id="worker-b", lease_seconds=30)
        self.assertEqual(job.attempts, 2)
        self.assertEqual(job.attempts, job.max_attempts)
        self.assertTrue(await self.queue.fail(job, error_code="parsererror"))
        async with await self.queue._connect() as conn:
            cursor = await conn.execute(
                "SELECT status FROM platform_file_processing_jobs WHERE tenant_id=%s AND asset_id=%s",
                (self.tenant_id, self.asset_id),
            )
            self.assertEqual((await cursor.fetchone())["status"], "dead")


if __name__ == "__main__":
    unittest.main()
