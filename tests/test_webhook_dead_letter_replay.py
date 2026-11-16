from __future__ import annotations

import unittest

from agent_platform.events.webhook_replay import WebhookDeadLetterReplayService


class FakeStore:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    async def replay_dead_letter(self, tenant_id, delivery_id):
        self.calls.append((tenant_id, delivery_id))
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
        self.assertEqual(store.calls, [("tenant-a", "delivery-a")])
        self.assertEqual(
            [event["action"] for event in audit.events],
            ["webhook.dead_letter.replay.requested", "webhook.dead_letter.replay"],
        )
        self.assertEqual(audit.events[0]["outcome"], "succeeded")
        self.assertEqual(audit.events[1]["outcome"], "succeeded")
        self.assertEqual(audit.events[1]["detail"]["reason"], "endpoint recovered")

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
        self.assertEqual(len(audit.events), 1)
        self.assertEqual(audit.events[0]["outcome"], "denied")


    async def test_replay_false_result_is_audited_as_denied(self):
        store, audit = FakeStore(result=False), FakeAudit()
        service = WebhookDeadLetterReplayService(
            store=store, audit=audit, authorize=lambda tenant, actor: True,
        )
        result = await service.replay(
            tenant_id="tenant-a", actor_id="operator-a",
            delivery_id="delivery-a", reason="subscription disabled",
        )
        self.assertFalse(result)
        self.assertEqual(audit.events[-1]["outcome"], "denied")
        self.assertFalse(audit.events[-1]["detail"]["replayed"])

    async def test_replay_failure_is_audited_without_raw_exception_message(self):
        class FailingStore(FakeStore):
            async def replay_dead_letter(self, tenant_id, delivery_id):
                raise RuntimeError("sensitive database details")
        store, audit = FailingStore(), FakeAudit()
        service = WebhookDeadLetterReplayService(
            store=store, audit=audit, authorize=lambda tenant, actor: True,
        )
        with self.assertRaisesRegex(RuntimeError, "sensitive database details"):
            await service.replay(
                tenant_id="tenant-a", actor_id="operator-a",
                delivery_id="delivery-a", reason="manual recovery",
            )
        self.assertEqual(audit.events[-1]["outcome"], "failed")
        self.assertEqual(audit.events[-1]["detail"]["error_type"], "RuntimeError")
        self.assertNotIn("sensitive database details", str(audit.events[-1]))
