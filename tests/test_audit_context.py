from __future__ import annotations

import unittest

from agent_platform.security.audit_context import AuditActor, AuditContext, current_audit_actor


class FakeSink:
    def __init__(self):
        self.events = []

    async def record(self, **event):
        self.events.append(event)


class AuditContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_context_binds_and_restores_actor(self):
        actor = AuditActor("tenant-a", "user-a", "req-a")
        self.assertIsNone(current_audit_actor())
        with AuditContext(actor):
            self.assertEqual(current_audit_actor(), actor)
        self.assertIsNone(current_audit_actor())

    async def test_action_uses_authenticated_actor_not_payload(self):
        sink = FakeSink()
        actor = AuditActor("tenant-a", "user-a")
        with AuditContext(actor) as context:
            await context.record_action(
                sink, action="file.download", subject_id="file-a",
                detail={"resource_type": "file"},
            )
        self.assertEqual(sink.events, [{
            "tenant_id": "tenant-a", "actor_id": "user-a",
            "action": "file.download", "subject_id": "file-a",
            "outcome": "succeeded", "detail": {"resource_type": "file"},
        }])

    async def test_invalid_actor_and_action_are_rejected(self):
        with self.assertRaises(ValueError):
            AuditActor("", "user-a")
        context = AuditContext(AuditActor("tenant-a", "user-a"))
        with self.assertRaises(ValueError):
            await context.record_action(FakeSink(), action="", subject_id="x")
