from __future__ import annotations

import unittest

from agent_platform.events.webhook_replay import WebhookDeadLetterReplayService


class FakeStore:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    async def replay_dead_letter(self, tenant_id, delivery_id, *, reason):
        self.calls.append((tenant_id, delivery_id, reason))
        return self.result


class FakeAudit:
    def __init__(self):
        self.events = []

    async def record(self, **event):
        self.events.append(event)


class WebhookDeadLetterReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_requires_authorization_and_records_audit(self):
        store, audit = FakeStore(), FakeAudit()
        service = WebhookDeadLetterReplayService(
            store=store, audit=audit, authorize=lambda tenant, actor: True,
        )
        result = await service.replay(
            tenant_id="tenant-a", actor_id="operator-a",
            delivery_id="delivery-a", reason="endpoint recovered",
        )
        self.assertTrue(result)
        self.assertEqual(store.calls, [("tenant-a", "delivery-a", "endpoint recovered")])
        self.assertEqual(audit.events[0]["action"], "webhook.dead_letter.replay")
        self.assertEqual(audit.events[0]["outcome"], "requested")

    async def test_unauthorized_replay_does_not_touch_store(self):
        store, audit = FakeStore(), FakeAudit()
        service = WebhookDeadLetterReplayService(
            store=store, audit=audit, authorize=lambda tenant, actor: False,
        )
        with self.assertRaises(PermissionError):
            await service.replay(
                tenant_id="tenant-a", actor_id="operator-a",
                delivery_id="delivery-a", reason="manual retry",
            )
        self.assertEqual(store.calls, [])
        self.assertEqual(audit.events, [])
