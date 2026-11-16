from __future__ import annotations

import unittest
from datetime import timedelta

from agent_platform.data_governance.lifecycle import (
    DataOwnership, DeletionCoordinator, RetentionPolicy, migration_fingerprint,
)


class FakeDeletionAdapter:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    async def delete_subject(self, *, tenant_id, subject_id, operation_id):
        self.calls.append((tenant_id, subject_id, operation_id))
        if self.fail:
            raise RuntimeError("backend unavailable")



class FakeDeletionLedger:
    def __init__(self):
        self.subjects = {}
        self.sources = {}
        self.status = {}

    async def begin_operation(self, *, tenant_id, operation_id, subject_id):
        key = (tenant_id, operation_id)
        existing = self.subjects.setdefault(key, subject_id)
        if existing != subject_id:
            raise ValueError("operation id is already bound to another subject")
        self.status[key] = "running"

    async def completed_systems(self, *, tenant_id, operation_id):
        return tuple(
            system for (tenant, operation, system), result in self.sources.items()
            if tenant == tenant_id and operation == operation_id
            and result["status"] == "completed"
        )

    async def mark_system_started(self, *, tenant_id, operation_id, system):
        self.sources[(tenant_id, operation_id, system)] = {"status": "running"}

    async def mark_system_completed(self, *, tenant_id, operation_id, system):
        self.sources[(tenant_id, operation_id, system)] = {"status": "completed"}

    async def mark_system_failed(self, *, tenant_id, operation_id, system, error_code):
        self.sources[(tenant_id, operation_id, system)] = {
            "status": "failed", "error_code": error_code,
        }

    async def finish_operation(self, *, tenant_id, operation_id, complete):
        self.status[(tenant_id, operation_id)] = "completed" if complete else "partial"


class DataLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_deletion_reports_partial_failure_and_can_resume(self):
        postgres = FakeDeletionAdapter()
        vectors = FakeDeletionAdapter(fail=True)
        coordinator = DeletionCoordinator({"postgres": postgres, "vectors": vectors})
        first = await coordinator.delete(
            tenant_id="tenant-a", subject_id="user-1", operation_id="delete-1",
        )
        self.assertFalse(first.complete)
        self.assertEqual(first.completed, ("postgres",))
        self.assertEqual(first.failed, {"vectors": "RuntimeError"})
        vectors.fail = False
        second = await coordinator.delete(
            tenant_id="tenant-a", subject_id="user-1", operation_id="delete-1",
            completed_systems=first.completed,
        )
        self.assertTrue(second.complete)
        self.assertEqual(len(postgres.calls), 1)
        self.assertEqual(len(vectors.calls), 2)


    async def test_durable_deletion_ledger_resumes_incomplete_systems(self):
        postgres = FakeDeletionAdapter()
        vectors = FakeDeletionAdapter(fail=True)
        coordinator = DeletionCoordinator({"postgres": postgres, "vectors": vectors})
        ledger = FakeDeletionLedger()
        first = await coordinator.delete(
            tenant_id="tenant-a", subject_id="user-1",
            operation_id="delete-durable", ledger=ledger,
        )
        self.assertFalse(first.complete)
        self.assertEqual(ledger.status[("tenant-a", "delete-durable")], "partial")
        self.assertEqual(
            ledger.sources[("tenant-a", "delete-durable", "postgres")]["status"],
            "completed",
        )

        vectors.fail = False
        second = await coordinator.delete(
            tenant_id="tenant-a", subject_id="user-1",
            operation_id="delete-durable", ledger=ledger,
        )
        self.assertTrue(second.complete)
        self.assertEqual(ledger.status[("tenant-a", "delete-durable")], "completed")
        self.assertEqual(len(postgres.calls), 1)
        self.assertEqual(len(vectors.calls), 2)

    async def test_deletion_ledger_prevents_operation_reuse_for_another_subject(self):
        ledger = FakeDeletionLedger()
        coordinator = DeletionCoordinator({"postgres": FakeDeletionAdapter()})
        await coordinator.delete(
            tenant_id="tenant-a", subject_id="user-1",
            operation_id="delete-bound", ledger=ledger,
        )
        with self.assertRaisesRegex(ValueError, "another subject"):
            await coordinator.delete(
                tenant_id="tenant-a", subject_id="user-2",
                operation_id="delete-bound", ledger=ledger,
            )

    async def test_ownership_and_retention_contracts_reject_invalid_values(self):
        with self.assertRaises(ValueError):
            DataOwnership(
                object_type="conversation", source_system="sqlite",
                target_system="postgres", authoritative_writer="both",
                migration_mode="batch", source_key="id", target_key="id",
            )
        with self.assertRaises(ValueError):
            RetentionPolicy("conversation", timedelta(0))

    async def test_migration_fingerprint_is_stable_and_payload_sensitive(self):
        one = migration_fingerprint(
            source_system="sqlite", target_system="postgres",
            source_key="conversation:1", payload={"body": "hello", "owner": "u1"},
        )
        same = migration_fingerprint(
            source_system="sqlite", target_system="postgres",
            source_key="conversation:1", payload={"owner": "u1", "body": "hello"},
        )
        changed = migration_fingerprint(
            source_system="sqlite", target_system="postgres",
            source_key="conversation:1", payload={"body": "changed", "owner": "u1"},
        )
        self.assertEqual(one, same)
        self.assertNotEqual(one, changed)


if __name__ == "__main__":
    unittest.main()
