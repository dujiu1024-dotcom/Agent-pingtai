"""PostgreSQL Outbox recovery after complete loss of a Redis stream.

This integration test deletes an isolated test stream (not a shared Redis DB),
then proves the durable business fact and published outbox row survive and can
repopulate the stream with the original event identity and payload.
"""

from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")
REDIS_URL = os.environ.get("AGENT_PLATFORM_TEST_REDIS_URL")

if DSN and REDIS_URL:
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    from agent_platform.events.outbox import OutboxRelay, PostgresOutboxStore
    from agent_platform.events.redis_streams import RedisStreamsTransport


@unittest.skipUnless(
    DSN and REDIS_URL,
    "PostgreSQL and Redis integration services are required",
)
class RedisOutboxLossRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant_id = f"outbox-loss-{uuid4().hex}"
        self.topic = "integration.redis-loss-replay"
        self.stream_prefix = f"platform-outbox-loss-{uuid4().hex}"
        self.event_id = str(uuid4())
        self.entry_id = str(uuid4())
        self.correlation_id = str(uuid4())

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
                (self.tenant_id, "Redis Outbox Loss Recovery"),
            )
            await conn.commit()

        self.transport = RedisStreamsTransport(
            REDIS_URL, stream_prefix=self.stream_prefix
        )
        await self.transport.connect()
        self.store = PostgresOutboxStore(connection_factory=self.connect)

    async def asyncTearDown(self):
        await self.transport.close()

    async def _insert_durable_business_fact_and_outbox(self):
        fact = {
            "decision": "approved",
            "business_reference": "approval-redis-loss",
            "amount": 125,
        }
        payload = {
            "event_id": self.event_id,
            "event_type": self.topic,
            "entry_id": self.entry_id,
            "entry_version": 1,
            "payload": {"kind": "fact", "status": "accepted"},
        }
        async with await self.connect() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO blackboard_entries (
                        tenant_id, entry_id, scope, scope_id, kind, version,
                        status, content, source_refs, author_id, correlation_id
                    ) VALUES (%s, %s, 'task', 'redis-loss-task', 'fact', 1,
                              'accepted', %s, '[]'::jsonb, 'integration-test', %s)
                    """,
                    (
                        self.tenant_id,
                        self.entry_id,
                        Jsonb(fact),
                        self.correlation_id,
                    ),
                )
                await conn.execute(
                    """
                    INSERT INTO platform_outbox (tenant_id, event_id, topic, payload)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (self.tenant_id, self.event_id, self.topic, Jsonb(payload)),
                )
        return fact, payload

    async def test_deleted_redis_stream_is_rebuilt_from_postgres_outbox(self):
        fact, payload = await self._insert_durable_business_fact_and_outbox()
        replay_window_start = datetime.now(timezone.utc) - timedelta(minutes=1)
        published = []

        class AuditSink:
            async def record(self, **event):
                published.append(event)

        relay = OutboxRelay(
            store=self.store,
            transport=self.transport,
            audit_sink=AuditSink(),
        )

        # First delivery: the outbox row is acknowledged as published.
        initial = await relay.publish_pending(limit=10)
        self.assertEqual(initial, {"claimed": 1, "published": 1, "failed": 0})
        async with await self.connect() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT published_at, payload FROM platform_outbox
                    WHERE tenant_id=%s AND event_id=%s
                    """,
                    (self.tenant_id, self.event_id),
                )
            ).fetchone()
            durable_fact = await (
                await conn.execute(
                    """
                    SELECT content FROM blackboard_entries
                    WHERE tenant_id=%s AND entry_id=%s AND version=1
                    """,
                    (self.tenant_id, self.entry_id),
                )
            ).fetchone()
        self.assertIsNotNone(row["published_at"])
        self.assertEqual(row["payload"], payload)
        self.assertEqual(durable_fact["content"], fact)

        stream_key = self.transport._stream(self.tenant_id, self.topic)
        self.assertEqual(await self.transport._client.xlen(stream_key), 1)

        # Simulate total loss of this tenant/topic stream. The isolated key is
        # removed; no shared Redis database or other tenant stream is touched.
        self.assertEqual(await self.transport._client.delete(stream_key), 1)
        self.assertEqual(await self.transport._client.exists(stream_key), 0)

        # Business facts and the PostgreSQL event payload remain authoritative.
        async with await self.connect() as conn:
            durable_fact_after_loss = await (
                await conn.execute(
                    """
                    SELECT content FROM blackboard_entries
                    WHERE tenant_id=%s AND entry_id=%s AND version=1
                    """,
                    (self.tenant_id, self.entry_id),
                )
            ).fetchone()
            outbox_after_loss = await (
                await conn.execute(
                    """
                    SELECT published_at, payload FROM platform_outbox
                    WHERE tenant_id=%s AND event_id=%s
                    """,
                    (self.tenant_id, self.event_id),
                )
            ).fetchone()
        self.assertEqual(durable_fact_after_loss["content"], fact)
        self.assertEqual(outbox_after_loss["payload"], payload)
        self.assertIsNotNone(outbox_after_loss["published_at"])

        # Operator recovery explicitly requeues only a bounded published window.
        replayed_count = await self.store.requeue_published(
            since=replay_window_start,
            until=datetime.now(timezone.utc) + timedelta(minutes=1),
            tenant_id=self.tenant_id,
            limit=10,
            confirmation="REPLAY_PUBLISHED_OUTBOX",
        )
        self.assertEqual(replayed_count, 1)

        recovered = await relay.publish_pending(limit=10)
        self.assertEqual(recovered, {"claimed": 1, "published": 1, "failed": 0})
        self.assertEqual(await self.transport._client.xlen(stream_key), 1)

        # Recreated stream contains the same stable event_id and payload;
        # replay does not manufacture a new business event identity.
        await self.transport.ensure_group(
            tenant_id=self.tenant_id,
            topic=self.topic,
            group="recovery-verifier",
            start_id="0",
        )
        messages = await self.transport.read_group(
            tenant_id=self.tenant_id,
            topic=self.topic,
            group="recovery-verifier",
            consumer="recovery-test",
            count=10,
            block_ms=100,
        )
        self.assertEqual(len(messages), 1)
        recovered_fields = messages[0][1]
        self.assertEqual(recovered_fields["event_id"], self.event_id)
        recovered_payload = json.loads(recovered_fields["payload"])
        self.assertEqual(recovered_payload, payload)
        self.assertEqual(
            [event["action"] for event in published],
            [
                "outbox.event.publish.attempt",
                "outbox.event.publish",
                "outbox.event.publish.attempt",
                "outbox.event.publish",
            ],
        )


if __name__ == "__main__":
    unittest.main()
