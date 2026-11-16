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


    async def test_stream_retention_preserves_pending_and_undelivered_entries(self):
        event_ids = [str(uuid4()) for _ in range(3)]
        message_ids = []
        for index, event_id in enumerate(event_ids):
            message_ids.append(await self.transport.publish(
                tenant_id=self.tenant_id,
                topic=self.topic,
                event_id=event_id,
                payload={"index": index},
            ))

        first = await self.transport.read_group(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group,
            consumer="retention-consumer", count=1, block_ms=100,
        )
        self.assertEqual(first[0][0], message_ids[0])
        trimmed = await self.transport.trim_acknowledged(
            tenant_id=self.tenant_id, topic=self.topic,
        )
        self.assertEqual(trimmed, 0)
        rows = await self.transport._client.xrange(
            self.transport._stream(self.tenant_id, self.topic)
        )
        self.assertEqual([row[0] for row in rows], message_ids)

        await self.transport.acknowledge(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group,
            message_id=message_ids[0],
        )
        second = await self.transport.read_group(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group,
            consumer="retention-consumer", count=1, block_ms=100,
        )
        self.assertEqual(second[0][0], message_ids[1])
        trimmed = await self.transport.trim_acknowledged(
            tenant_id=self.tenant_id, topic=self.topic,
        )
        self.assertEqual(trimmed, 1)
        rows = await self.transport._client.xrange(
            self.transport._stream(self.tenant_id, self.topic)
        )
        self.assertEqual([row[0] for row in rows], message_ids[1:])

    async def test_stream_retention_uses_slowest_consumer_group_frontier(self):
        fast_group = "fast-retention"
        slow_group = "slow-retention"
        await self.transport.ensure_group(
            tenant_id=self.tenant_id, topic=self.topic, group=fast_group, start_id="0"
        )
        await self.transport.ensure_group(
            tenant_id=self.tenant_id, topic=self.topic, group=slow_group, start_id="0"
        )
        message_ids = [
            await self.transport.publish(
                tenant_id=self.tenant_id,
                topic=self.topic,
                event_id=str(uuid4()),
                payload={"index": index},
            )
            for index in range(3)
        ]

        fast_rows = await self.transport.read_group(
            tenant_id=self.tenant_id, topic=self.topic, group=fast_group,
            consumer="fast-consumer", count=3, block_ms=100,
        )
        for message_id, _fields in fast_rows:
            await self.transport.acknowledge(
                tenant_id=self.tenant_id, topic=self.topic,
                group=fast_group, message_id=message_id,
            )

        slow_rows = await self.transport.read_group(
            tenant_id=self.tenant_id, topic=self.topic, group=slow_group,
            consumer="slow-consumer", count=1, block_ms=100,
        )
        self.assertEqual(slow_rows[0][0], message_ids[0])
        self.assertEqual(
            await self.transport.trim_acknowledged(
                tenant_id=self.tenant_id, topic=self.topic,
            ),
            0,
        )

        await self.transport.acknowledge(
            tenant_id=self.tenant_id, topic=self.topic,
            group=slow_group, message_id=message_ids[0],
        )
        slow_next = await self.transport.read_group(
            tenant_id=self.tenant_id, topic=self.topic, group=slow_group,
            consumer="slow-consumer", count=1, block_ms=100,
        )
        self.assertEqual(slow_next[0][0], message_ids[1])
        group_states = await self.transport._client.xinfo_groups(
            self.transport._stream(self.tenant_id, self.topic)
        )
        slow_state = next(item for item in group_states if item["name"] == slow_group)
        fast_state = next(item for item in group_states if item["name"] == fast_group)
        slow_pending = await self.transport._client.xpending(
            self.transport._stream(self.tenant_id, self.topic), slow_group
        )
        self.assertEqual(slow_pending["min"], message_ids[1])
        self.assertEqual(fast_state["last-delivered-id"], message_ids[2])
        self.assertEqual(
            await self.transport.trim_acknowledged(
                tenant_id=self.tenant_id, topic=self.topic,
            ),
            1,
        )
        remaining = await self.transport._client.xrange(
            self.transport._stream(self.tenant_id, self.topic)
        )
        self.assertEqual([row[0] for row in remaining], message_ids[1:])

    async def test_stream_retention_handles_missing_dead_letter_stream(self):
        self.assertEqual(
            await self.transport.trim_acknowledged(
                tenant_id=self.tenant_id, topic="no-dead-letter-yet",
                dead_letter=True,
            ),
            0,
        )

    async def test_stream_retention_does_not_trim_without_consumer_groups(self):
        topic = "unconsumed.retention"
        await self.transport.publish(
            tenant_id=self.tenant_id, topic=topic,
            event_id=str(uuid4()), payload={"value": "unconsumed"},
        )
        self.assertEqual(
            await self.transport.trim_acknowledged(
                tenant_id=self.tenant_id, topic=topic,
            ),
            0,
        )
        self.assertEqual(
            await self.transport._client.xlen(
                self.transport._stream(self.tenant_id, topic)
            ),
            1,
        )

    async def test_publish_rejects_non_uuid_event_ids(self):
        with self.assertRaisesRegex(ValueError, "event_id must be a UUID"):
            await self.transport.publish(
                tenant_id=self.tenant_id,
                topic=self.topic,
                event_id="not-a-uuid",
                payload={"value": "invalid"},
            )

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
        health = await self.transport.pending_summary(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group
        )
        self.assertEqual(health["pending"], 1)
        self.assertEqual(health["consumer_count"], 1)

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

    async def test_dead_letter_copies_message_and_acknowledges_source(self):
        event_id = str(uuid4())
        await self.transport.publish(
            tenant_id=self.tenant_id, topic=self.topic, event_id=event_id,
            payload={"value": "poison"},
        )
        rows = await self.transport.read_group(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group,
            consumer=self.consumer, count=10, block_ms=100,
        )
        message_id, fields = rows[0]
        dlq_id = await self.transport.dead_letter(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group,
            message_id=message_id, fields=fields, event_id=event_id,
            reason="invalid payload", consumer_name=self.consumer,
        )
        self.assertTrue(dlq_id)
        self.assertEqual(await self.transport.claim_pending(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group,
            consumer="replacement", min_idle_ms=0, count=10,
        ), [])
        dlq_rows = await self.transport._client.xrange(
            self.transport._dead_letter_stream(self.tenant_id, self.topic)
        )
        self.assertEqual(len(dlq_rows), 1)
        self.assertEqual(dlq_rows[0][1]["failure_reason"], "invalid payload")
        self.assertEqual(dlq_rows[0][1]["source_message_id"], message_id)
        health = await self.transport.pending_summary(
            tenant_id=self.tenant_id, topic=self.topic, group=self.group
        )
        self.assertEqual(health["pending"], 0)
        self.assertEqual(health["dead_letter_count"], 1)

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
