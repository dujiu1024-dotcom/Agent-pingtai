from __future__ import annotations

import hashlib
import json
import unittest

from agent_platform.events.consumer import (
    EventEnvelope,
    IdempotentEventHandler,
    event_idempotency_key,
)


class DurableIdempotencyExecutor:
    """Model a downstream durable key+request ledger and an ambiguous response."""

    def __init__(self) -> None:
        self.records: dict[str, tuple[str, dict[str, object]]] = {}
        self.external_effect_count = 0
        self.lose_first_response = True

    async def execute(self, *, idempotency_key: str, event: EventEnvelope):
        request = json.dumps(
            {"topic": event.topic, "payload": event.payload},
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(request.encode()).hexdigest()
        previous = self.records.get(idempotency_key)
        if previous is not None:
            if previous[0] != digest:
                raise ValueError("idempotency key reused with a different request")
            return previous[1]

        # The downstream commits its idempotency record and effect atomically.
        result = {"accepted": True, "effect_id": "effect-1"}
        self.records[idempotency_key] = (digest, result)
        self.external_effect_count += 1
        if self.lose_first_response:
            self.lose_first_response = False
            raise ConnectionError("response lost after downstream commit")
        return result


class Issue13A1303AtLeastOnceEffectTests(unittest.IsolatedAsyncioTestCase):
    def event(
        self,
        *,
        tenant_id: str = "tenant-a",
        event_id: str = "00000000-0000-0000-0000-000000000013",
        message_id: str = "1710000000000-0",
        payload: dict[str, object] | None = None,
    ) -> EventEnvelope:
        return EventEnvelope(
            tenant_id=tenant_id,
            topic="blackboard.decision.recorded",
            event_id=event_id,
            message_id=message_id,
            payload=payload or {"decision_id": "decision-13", "version": 3},
        )

    async def test_lost_response_and_cross_worker_redelivery_create_one_effect(self):
        executor = DurableIdempotencyExecutor()
        first_worker = IdempotentEventHandler(executor)
        restarted_worker = IdempotentEventHandler(executor)
        original = self.event()

        with self.assertRaisesRegex(ConnectionError, "response lost"):
            await first_worker(original)

        # A different Redis Stream message ID and worker instance represent
        # at-least-once redelivery after the first worker lost its response.
        await restarted_worker(self.event(message_id="1710000009000-2"))

        self.assertEqual(executor.external_effect_count, 1)
        self.assertEqual(len(executor.records), 1)
        self.assertEqual(
            next(iter(executor.records)),
            "tenant:tenant-a:event:00000000-0000-0000-0000-000000000013",
        )

    async def test_same_key_with_changed_payload_is_rejected(self):
        executor = DurableIdempotencyExecutor()
        handler = IdempotentEventHandler(executor)
        original = self.event()
        with self.assertRaises(ConnectionError):
            await handler(original)

        with self.assertRaisesRegex(ValueError, "different request"):
            await handler(
                self.event(
                    message_id="1710000009000-3",
                    payload={"decision_id": "decision-tampered", "version": 4},
                )
            )
        self.assertEqual(executor.external_effect_count, 1)

    async def test_effect_keys_are_tenant_scoped_but_not_worker_or_message_scoped(self):
        event = self.event()
        replay = self.event(message_id="1710000010000-1")
        other_tenant = self.event(tenant_id="tenant-b")

        self.assertEqual(event_idempotency_key(event), event_idempotency_key(replay))
        self.assertNotEqual(event_idempotency_key(event), event_idempotency_key(other_tenant))

    async def test_invalid_event_identity_fails_before_downstream_effect(self):
        executor = DurableIdempotencyExecutor()
        handler = IdempotentEventHandler(executor)
        with self.assertRaisesRegex(ValueError, "event_id must be a UUID"):
            await handler(self.event(event_id="not-a-uuid"))
        self.assertEqual(executor.external_effect_count, 0)


if __name__ == "__main__":
    unittest.main()
