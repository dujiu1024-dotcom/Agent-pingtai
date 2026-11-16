"""Real PostgreSQL receipt + Redis Streams worker-restart integration tests."""

from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from uuid import uuid4

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")
REDIS_URL = os.environ.get("AGENT_PLATFORM_TEST_REDIS_URL")

if DSN and REDIS_URL:
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    from agent_platform.events.consumer import RedisStreamsConsumer
    from agent_platform.events.receipts import PostgresEventReceiptStore
    from agent_platform.events.redis_streams import RedisStreamsTransport


@unittest.skipUnless(
    DSN and REDIS_URL,
    "PostgreSQL and Redis integration services are required",
)
class PostgresRedisWorkerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant_id = f"worker-recovery-{uuid4().hex}"
        self.topic = "integration.worker-recovery"
        self.group = "worker-recovery-group"
        self.consumer_name = "durable-event-worker"

        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            migration_dir = Path("agent_platform/storage/postgres")
            for migration_path in sorted(migration_dir.glob("*.sql")):
                await conn.execute(migration_path.read_text(), prepare=False)

        async def connect():
            return await AsyncConnection.connect(DSN, row_factory=dict_row)

        self.connect = connect
        async with await self.connect() as conn:
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                (self.tenant_id, "Worker Recovery Integration"),
            )
            await conn.commit()

        self.receipts = PostgresEventReceiptStore(self.connect, lease_seconds=5)
        self.transport = RedisStreamsTransport(
            REDIS_URL, stream_prefix=f"platform-worker-recovery-{uuid4().hex}"
        )
        await self.transport.connect()
        await self.transport.ensure_group(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group
        )

    async def asyncTearDown(self):
        await self.transport.close()

    async def test_expired_receipt_and_pending_message_resume_after_worker_restart(self):
        event_id = str(uuid4())
        await self.transport.publish(
            tenant_id=self.tenant_id,
            topic=self.topic,
            event_id=event_id,
            payload={"run_id": "run-after-restart"},
        )

        # Simulate a worker that fetched the Redis entry and claimed its durable
        # receipt, then died before executing the handler or acknowledging Redis.
        crashed_delivery = await self.transport.read_group(
            tenant_id=self.tenant_id,
            topic=self.topic,
            group=self.group,
            consumer="crashed-process",
            count=1,
            block_ms=100,
        )
        self.assertEqual(len(crashed_delivery), 1)
        claim = await self.receipts.claim(
            tenant_id=self.tenant_id,
            consumer_name=self.consumer_name,
            event_id=event_id,
        )
        self.assertIsNotNone(claim)

        # Expire the lease deterministically instead of sleeping for five seconds.
        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_event_receipts
                   SET lease_until = now() - interval '1 second'
                   WHERE tenant_id=%s AND consumer_name=%s AND event_id=%s""",
                (self.tenant_id, self.consumer_name, event_id),
            )
            await conn.commit()

        handled = []

        async def handler(event):
            handled.append((event.event_id, event.payload["run_id"]))

        restarted_worker = RedisStreamsConsumer(
            transport=self.transport,
            receipts=self.receipts,
            tenant_id=self.tenant_id,
            topic=self.topic,
            group=self.group,
            consumer_name=self.consumer_name,
            handler=handler,
        )
        result = await restarted_worker.consume_batch(
            count=5, block_ms=0, pending_idle_ms=0
        )

        self.assertEqual(result["processed"], 1)
        self.assertEqual(handled, [(event_id, "run-after-restart")])
        self.assertTrue(await self.receipts.is_completed(
            tenant_id=self.tenant_id,
            consumer_name=self.consumer_name,
            event_id=event_id,
        ))
        pending = await self.transport.pending_summary(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group
        )
        self.assertEqual(pending["pending"], 0)

    async def test_concurrent_postgres_receipt_claim_has_single_winner(self):
        event_id = str(uuid4())
        claims = await asyncio.gather(*[
            self.receipts.claim(
                tenant_id=self.tenant_id,
                consumer_name=self.consumer_name,
                event_id=event_id,
            )
            for _ in range(8)
        ])
        winners = [claim for claim in claims if claim is not None]
        self.assertEqual(len(winners), 1)
        self.assertTrue(await self.receipts.complete(winners[0]))
        self.assertTrue(await self.receipts.is_completed(
            tenant_id=self.tenant_id,
            consumer_name=self.consumer_name,
            event_id=event_id,
        ))


if __name__ == "__main__":
    unittest.main()
