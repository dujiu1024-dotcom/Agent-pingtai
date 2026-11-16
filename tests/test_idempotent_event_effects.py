from __future__ import annotations

import unittest

from agent_platform.events.consumer import (
    EventEnvelope,
    IdempotentEventHandler,
    event_idempotency_key,
)


class IdempotentEffectExecutor:
    def __init__(self):
        self.keys = set()
        self.side_effect_count = 0

    async def execute(self, *, idempotency_key, event):
        if idempotency_key not in self.keys:
            self.keys.add(idempotency_key)
            self.side_effect_count += 1
        return {"accepted": True}


class IdempotentEventEffectTests(unittest.IsolatedAsyncioTestCase):
    def event(self, *, tenant_id="tenant-a", event_id="00000000-0000-0000-0000-000000000001", message_id="1-0"):
        return EventEnvelope(
            tenant_id=tenant_id,
            topic="runs.completed",
            event_id=event_id,
            message_id=message_id,
            payload={"run_id": "run-a"},
        )

    async def test_replayed_event_uses_same_downstream_idempotency_key(self):
        executor = IdempotentEffectExecutor()
        handler = IdempotentEventHandler(executor)
        event = self.event()
        self.assertEqual(
            event_idempotency_key(event),
            "tenant:tenant-a:event:00000000-0000-0000-0000-000000000001",
        )
        await handler(event)
        await handler(self.event(message_id="2-0"))
        self.assertEqual(executor.side_effect_count, 1)

    async def test_same_event_id_in_different_tenants_has_distinct_keys(self):
        executor = IdempotentEffectExecutor()
        handler = IdempotentEventHandler(executor)
        await handler(self.event(tenant_id="tenant-a"))
        await handler(self.event(tenant_id="tenant-b"))
        self.assertEqual(executor.side_effect_count, 2)

    async def test_invalid_event_identity_is_rejected_before_effect(self):
        executor = IdempotentEffectExecutor()
        handler = IdempotentEventHandler(executor)
        with self.assertRaisesRegex(ValueError, "tenant_id and event_id"):
            await handler(self.event(tenant_id="", event_id=""))
        self.assertEqual(executor.side_effect_count, 0)


if __name__ == "__main__":
    unittest.main()
