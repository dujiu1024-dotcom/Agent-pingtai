"""Real Redis Streams integration tests; enabled only with AGENT_PLATFORM_TEST_REDIS_URL."""

import os
import unittest
from uuid import uuid4

REDIS_URL = os.environ.get("AGENT_PLATFORM_TEST_REDIS_URL")
if REDIS_URL:
    from agent_platform.events.redis_streams import RedisStreamsTransport


@unittest.skipUnless(REDIS_URL, "AGENT_PLATFORM_TEST_REDIS_URL is not configured")
class RedisStreamsIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant_id = f"it-{uuid4().hex}"
        self.topic = "integration.event"
        self.group = "integration-group"
        self.consumer = "integration-consumer"
        self.transport = RedisStreamsTransport(REDIS_URL, stream_prefix="platform-it")
        await self.transport.connect()
        await self.transport.ensure_group(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group
        )

    async def asyncTearDown(self):
        await self.transport.close()

    async def test_pending_entry_can_be_reclaimed_by_replacement_consumer(self):
        event_id = str(uuid4())
        await self.transport.publish(
            tenant_id=self.tenant_id, topic=self.topic, event_id=event_id,
            payload={"value": "pending"},
        )
        first = await self.transport.read_group(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group,
            consumer="crashed-consumer", count=10, block_ms=100,
        )
        self.assertEqual(len(first), 1)

        reclaimed = await self.transport.claim_pending(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group,
            consumer="replacement-consumer", min_idle_ms=0, count=10,
        )
        self.assertEqual(len(reclaimed), 1)
        self.assertEqual(reclaimed[0][0], first[0][0])
        self.assertEqual(reclaimed[0][1]["event_id"], event_id)

        self.assertEqual(await self.transport.acknowledge(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group,
            message_id=reclaimed[0][0],
        ), 1)

    async def test_publish_read_and_ack(self):
        event_id = str(uuid4())
        await self.transport.publish(
            tenant_id=self.tenant_id, topic=self.topic, event_id=event_id,
            payload={"value": "persisted"},
        )
        rows = await self.transport.read_group(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group,
            consumer=self.consumer, count=10, block_ms=100,
        )
        self.assertEqual(len(rows), 1)
        message_id, fields = rows[0]
        self.assertEqual(fields["event_id"], event_id)
        self.assertEqual(await self.transport.acknowledge(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group,
            message_id=message_id,
        ), 1)


if __name__ == "__main__":
    unittest.main()
