"""Unit tests for Redis Streams recovery semantics without a Redis server."""
from __future__ import annotations

import unittest

from agent_platform.events.redis_streams import RedisStreamsTransport


class FakeRedis:
    def __init__(self):
        self.claim_result = ("0-0", [], [])
        self.eval_calls = []
        self.eval_result = "1710000000000-0"

    async def xautoclaim(self, *args, **kwargs):
        return self.claim_result

    async def eval(self, *args):
        self.eval_calls.append(args)
        return self.eval_result


class RedisStreamsRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def transport(self, client):
        transport = RedisStreamsTransport("redis://unit.test")
        transport._client = client
        return transport

    async def test_pending_claim_reports_entries_deleted_from_stream(self):
        client = FakeRedis()
        client.claim_result = (
            "0-0",
            [("1-0", {"event_id": "evt-1", "payload": "{}"})],
            ["2-0", "3-0"],
        )
        messages, deleted = await self.transport(client).claim_pending_recovery(
            tenant_id="tenant-a", topic="events", group="workers",
            consumer="worker-2", min_idle_ms=100,
        )
        self.assertEqual(messages, [("1-0", {"event_id": "evt-1", "payload": "{}"})])
        self.assertEqual(deleted, ["2-0", "3-0"])

    async def test_legacy_pending_claim_keeps_message_only_contract(self):
        client = FakeRedis()
        client.claim_result = ("0-0", [("1-0", {"event_id": "evt-1"})], ["2-0"])
        messages = await self.transport(client).claim_pending(
            tenant_id="tenant-a", topic="events", group="workers", consumer="worker-2",
        )
        self.assertEqual(messages, [("1-0", {"event_id": "evt-1"})])

    async def test_dlq_replay_requires_exact_tenant_topic_confirmation(self):
        transport = self.transport(FakeRedis())
        with self.assertRaises(PermissionError):
            await transport.replay_dead_letter(
                tenant_id="tenant-a", topic="events", dlq_group="ops",
                message_id="1-0", confirmation="REPLAY-DLQ:tenant-a:other",
            )
        self.assertEqual(transport._client.eval_calls, [])

    async def test_dlq_replay_uses_atomic_copy_and_ack_script(self):
        client = FakeRedis()
        transport = self.transport(client)
        result = await transport.replay_dead_letter(
            tenant_id="tenant-a", topic="events", dlq_group="ops",
            message_id="7-2", confirmation="REPLAY-DLQ:tenant-a:events",
        )
        self.assertEqual(result, "1710000000000-0")
        script, key_count, dlq, source, entry_id, group = client.eval_calls[0]
        self.assertEqual(key_count, 2)
        self.assertEqual(dlq, "agent-platform:tenant-a:events:dead-letter")
        self.assertEqual(source, "agent-platform:tenant-a:events")
        self.assertEqual((entry_id, group), ("7-2", "ops"))
        self.assertIn("XRANGE", script)
        self.assertIn("XACK", script)
        self.assertIn("XDEL", script)

    async def test_dlq_replay_rejects_invalid_stream_id_and_missing_group(self):
        transport = self.transport(FakeRedis())
        with self.assertRaises(ValueError):
            await transport.replay_dead_letter(
                tenant_id="tenant-a", topic="events", dlq_group=" ",
                message_id="7-2", confirmation="REPLAY-DLQ:tenant-a:events",
            )
        with self.assertRaises(ValueError):
            await transport.replay_dead_letter(
                tenant_id="tenant-a", topic="events", dlq_group="ops",
                message_id="bad", confirmation="REPLAY-DLQ:tenant-a:events",
            )

    async def test_dlq_replay_missing_pending_entry_fails_without_acknowledgement(self):
        client = FakeRedis()
        client.eval_result = None
        with self.assertRaises(LookupError):
            await self.transport(client).replay_dead_letter(
                tenant_id="tenant-a", topic="events", dlq_group="ops",
                message_id="7-2", confirmation="REPLAY-DLQ:tenant-a:events",
            )


if __name__ == "__main__":
    unittest.main()
