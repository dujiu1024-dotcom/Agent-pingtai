"""Fault-injection integration tests for Redis/PostgreSQL recovery.

These tests use the real CI PostgreSQL and Redis services. Database outages are
injected at the connection boundary so the test suite never terminates a shared
PostgreSQL server process.
"""

import os
import unittest
from pathlib import Path
from uuid import uuid4

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")
REDIS_URL = os.environ.get("AGENT_PLATFORM_TEST_REDIS_URL")

if DSN and REDIS_URL:
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    from agent_platform.events.consumer import EventEnvelope, RedisStreamsConsumer
    from agent_platform.events.receipts import PostgresEventReceiptStore
    from agent_platform.events.redis_streams import RedisStreamsTransport


@unittest.skipUnless(
    DSN and REDIS_URL,
    "AGENT_PLATFORM_TEST_DSN and AGENT_PLATFORM_TEST_REDIS_URL are required",
)
class ReliabilityFaultInjectionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant_id = f"fault-it-{uuid4().hex}"
        self.topic = f"fault.event.{uuid4().hex}"
        self.group = f"fault-group-{uuid4().hex}"
        self.consumer_name = f"fault-consumer-{uuid4().hex}"
        self.stream_prefix = f"platform-fault-it-{uuid4().hex}"
        self.database_available = True

        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            for migration_path in sorted(
                Path("agent_platform/storage/postgres").glob("*.sql")
            ):
                await conn.execute(migration_path.read_text(), prepare=False)

        async with await AsyncConnection.connect(DSN) as conn:
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                (self.tenant_id, "Reliability Fault Injection"),
            )
            await conn.commit()

        async def connect():
            if not self.database_available:
                raise ConnectionError("injected PostgreSQL connectivity failure")
            return await AsyncConnection.connect(DSN, row_factory=dict_row)

        self.connect = connect
        self.transport = RedisStreamsTransport(
            REDIS_URL, stream_prefix=self.stream_prefix,
        )
        await self.transport.connect()
        await self.transport.ensure_group(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group,
        )
        self.receipts = PostgresEventReceiptStore(self.connect, lease_seconds=5)

    async def asyncTearDown(self):
        await self.transport.close()

    async def test_postgres_outage_keeps_redis_entry_pending_then_recovers(self):
        event_id = str(uuid4())
        message_id = await self.transport.publish(
            tenant_id=self.tenant_id,
            topic=self.topic,
            event_id=event_id,
            payload={"operation": "recover-after-db-outage"},
        )
        handled = []

        async def handler(event: EventEnvelope):
            handled.append(event.event_id)

        consumer = RedisStreamsConsumer(
            transport=self.transport,
            receipts=self.receipts,
            tenant_id=self.tenant_id,
            topic=self.topic,
            group=self.group,
            consumer_name=self.consumer_name,
            handler=handler,
        )

        self.database_available = False
        with self.assertRaisesRegex(ConnectionError, "injected PostgreSQL"):
            await consumer.consume_batch(count=10, block_ms=0, pending_idle_ms=0)

        pending_during_outage = await self.transport.pending_summary(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group,
        )
        self.assertEqual(pending_during_outage["pending"], 1)
        self.assertEqual(handled, [])

        self.database_available = True
        recovered = await consumer.consume_batch(
            count=10, block_ms=0, pending_idle_ms=0,
        )
        self.assertEqual(recovered["processed"], 1)
        self.assertEqual(handled, [event_id])
        self.assertEqual(
            await self.transport.pending_summary(
                tenant_id=self.tenant_id, topic=self.topic, group=self.group,
            ),
            {
                "pending": 0,
                "oldest_pending_id": None,
                "newest_pending_id": None,
                "consumer_count": 0,
                "dead_letter_count": 0,
            },
        )
        self.assertTrue(await self.receipts.is_completed(
            tenant_id=self.tenant_id,
            consumer_name=self.consumer_name,
            event_id=event_id,
        ))
        self.assertTrue(message_id)

    async def test_redis_transport_restart_reclaims_pending_and_deduplicates_event(self):
        event_id = str(uuid4())
        first_message_id = await self.transport.publish(
            tenant_id=self.tenant_id,
            topic=self.topic,
            event_id=event_id,
            payload={"operation": "restart"},
        )
        abandoned = await self.transport.read_group(
            tenant_id=self.tenant_id,
            topic=self.topic,
            group=self.group,
            consumer="worker-before-restart",
            count=10,
            block_ms=0,
        )
        self.assertEqual([row[0] for row in abandoned], [first_message_id])
        await self.transport.close()

        replacement_transport = RedisStreamsTransport(
            REDIS_URL, stream_prefix=self.stream_prefix,
        )
        self.transport = replacement_transport
        await replacement_transport.connect()
        await replacement_transport.ensure_group(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group,
        )
        reclaimed = await replacement_transport.claim_pending(
            tenant_id=self.tenant_id,
            topic=self.topic,
            group=self.group,
            consumer="worker-after-restart",
            min_idle_ms=0,
            count=10,
        )
        self.assertEqual([row[0] for row in reclaimed], [first_message_id])

        duplicate_message_id = await replacement_transport.publish(
            tenant_id=self.tenant_id,
            topic=self.topic,
            event_id=event_id,
            payload={"operation": "restart"},
        )
        handled = []

        async def handler(event: EventEnvelope):
            handled.append(event.event_id)

        consumer = RedisStreamsConsumer(
            transport=replacement_transport,
            receipts=self.receipts,
            tenant_id=self.tenant_id,
            topic=self.topic,
            group=self.group,
            consumer_name=self.consumer_name,
            handler=handler,
        )
        result = await consumer.consume_batch(
            count=10, block_ms=0, pending_idle_ms=0,
        )

        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(handled, [event_id])
        pending_after_restart = await replacement_transport.pending_summary(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group,
        )
        self.assertEqual(pending_after_restart["pending"], 0)
        self.assertNotEqual(first_message_id, duplicate_message_id)


if __name__ == "__main__":
    unittest.main()
