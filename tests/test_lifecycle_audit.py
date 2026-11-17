from __future__ import annotations

import unittest
from uuid import uuid4

from agent_platform.data_governance.lifecycle_audit import (
    LifecycleAuditEvent, LifecycleAuditService, evidence_digest,
)


class MemoryWriter:
    def __init__(self):
        self.events = []

    async def append(self, event):
        self.events.append(event)


class LifecycleAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_records_actor_scope_outcome_and_evidence_digest(self):
        writer = MemoryWriter()
        service = LifecycleAuditService(writer)
        event = await service.record(
            tenant_id="tenant-a", actor_id="operator-7", action="migration.batch.apply",
            outcome="succeeded", scope={"batch_id": "batch-12", "source": "legacy"},
            correlation_id=str(uuid4()), evidence={"rows": 20, "checksum": "abc"},
        )
        self.assertEqual(event.actor_id, "operator-7")
        self.assertEqual(event.scope["batch_id"], "batch-12")
        self.assertEqual(event.outcome, "succeeded")
        self.assertEqual(event.evidence_sha256, evidence_digest({"rows": 20, "checksum": "abc"}))
        self.assertEqual(len(writer.events), 1)

    async def test_failure_and_retry_are_linked_and_increment_attempt(self):
        writer = MemoryWriter()
        service = LifecycleAuditService(writer)
        failed, retry = await service.record_failure_and_retry(
            tenant_id="tenant-a", actor_id="worker:migration", action="migration.batch.apply",
            scope={"batch_id": "batch-12"}, correlation_id=str(uuid4()),
            attempt=2, failure_code="SOURCE_TIMEOUT", evidence="sanitized failure evidence",
        )
        self.assertEqual([e.outcome for e in writer.events], ["failed", "retry_scheduled"])
        self.assertEqual(failed.attempt, 2)
        self.assertEqual(retry.attempt, 3)
        self.assertEqual(failed.correlation_id, retry.correlation_id)
        self.assertEqual(retry.scope, failed.scope)
        self.assertIsNotNone(failed.evidence_sha256)

    async def test_rejects_invalid_or_sensitive_unbounded_scope(self):
        with self.assertRaises(ValueError):
            LifecycleAuditEvent(
                tenant_id="t", actor_id="a", action="bad action", outcome="started",
                scope={"batch_id": "b"}, correlation_id=str(uuid4()),
            )
        with self.assertRaises(ValueError):
            LifecycleAuditEvent(
                tenant_id="t", actor_id="a", action="migration.run", outcome="failed",
                scope={"batch_id": "b"}, correlation_id=str(uuid4()),
            )
        with self.assertRaises(ValueError):
            LifecycleAuditEvent(
                tenant_id="t", actor_id="a", action="migration.run", outcome="succeeded",
                scope={"prompt": "do not store"}, correlation_id=str(uuid4()),
            )

    async def test_rejects_non_uuid_correlation_before_storage(self):
        writer = MemoryWriter()
        service = LifecycleAuditService(writer)
        with self.assertRaisesRegex(ValueError, "UUIDs"):
            await service.record(
                tenant_id="tenant-a", actor_id="operator-7", action="migration.batch.apply",
                outcome="started", scope={"batch_id": "batch-12"}, correlation_id="not-a-uuid",
            )
        self.assertEqual(writer.events, [])

    async def test_digest_does_not_expose_original_evidence(self):
        secretish = "source-row-with-private-data"
        digest = evidence_digest(secretish)
        self.assertEqual(len(digest), 64)
        self.assertNotIn(secretish, digest)


if __name__ == "__main__":
    unittest.main()
