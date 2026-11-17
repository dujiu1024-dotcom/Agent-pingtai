from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent_platform.data_governance.lifecycle_governance import (
    DataLifecycleService, ExportRequest, LifecyclePropagationWorker,
    RetentionPolicy, Tombstone,
)


class MemoryRepository:
    def __init__(self):
        self.policies = {}
        self.tombstones = {}
        self.jobs = []
        self.exports = []

    async def save_policy(self, policy):
        self.policies[(policy.tenant_id, policy.resource_type)] = policy

    async def get_policy(self, tenant_id, resource_type):
        return self.policies.get((tenant_id, resource_type))

    async def create_tombstone(self, item, sinks):
        key = (item.tenant_id, item.resource_type, item.resource_id)
        existing = self.tombstones.get(key)
        if existing:
            return existing
        self.tombstones[key] = item
        self.jobs.extend((item, sink) for sink in sinks)
        return item

    async def is_tombstoned(self, tenant_id, resource_type, resource_id):
        return (tenant_id, resource_type, resource_id) in self.tombstones

    async def create_export(self, item):
        self.exports.append(item)

    async def claim_propagation(self, worker_id, lease_seconds):
        if not self.jobs:
            return None
        item, sink = self.jobs.pop(0)
        from agent_platform.data_governance.lifecycle_governance import PropagationJob
        return PropagationJob(item.tenant_id, item.request_id, item.resource_type,
                              item.resource_id, sink, 1, worker_id)

    async def mark_sink_complete(self, job):
        return None

    async def complete_request_if_ready(self, job):
        return None


class MemorySource:
    def __init__(self):
        self.records = [{"id": "a", "value": 1}, {"id": "b", "value": 2}]
        self.archived = []
        self.expired = []

    async def export_records(self, *, tenant_id, resource_type):
        for record in self.records:
            yield record

    async def list_expired(self, *, tenant_id, resource_type, cutoff, limit):
        return self.expired[:limit]

    async def archive(self, *, tenant_id, resource_type, resource_id):
        self.archived.append((tenant_id, resource_type, resource_id))


class Sink:
    def __init__(self):
        self.deleted = []

    async def delete(self, **kwargs):
        self.deleted.append(kwargs)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = MemoryRepository()
        self.source = MemorySource()
        self.service = DataLifecycleService(
            self.repo, self.source,
            authorize=lambda tenant, actor, permission: permission not in {"data.delete", "data.export"} or actor == "admin",
        )

    async def test_retention_policy_validation(self):
        with self.assertRaises(ValueError):
            RetentionPolicy("t1", "asset", 30, archive_after_days=60)
        await self.service.set_policy(
            policy=RetentionPolicy("t1", "asset", 30, archive_after_days=10), actor_id="admin",
        )
        self.assertEqual((await self.repo.get_policy("t1", "asset")).retain_days, 30)

    async def test_legal_hold_blocks_archive_and_delete(self):
        await self.service.set_policy(
            policy=RetentionPolicy("t1", "asset", 1, archive_after_days=1, legal_hold=True),
            actor_id="admin",
        )
        self.source.expired = ["a"]
        self.assertEqual(await self.service.enforce_retention(
            tenant_id="t1", actor_id="admin", resource_type="asset",
            now=datetime.now(timezone.utc), limit=10,
        ), (0, 0))
        self.assertEqual(self.source.archived, [])
        self.assertEqual(self.repo.tombstones, {})

    async def test_retention_archives_then_creates_deletion_tombstone(self):
        await self.service.set_policy(
            policy=RetentionPolicy("t1", "asset", 30, archive_after_days=10), actor_id="admin",
        )
        self.source.expired = ["old-asset"]
        archived, deleted = await self.service.enforce_retention(
            tenant_id="t1", actor_id="admin", resource_type="asset",
            now=datetime.now(timezone.utc), limit=10,
        )
        self.assertEqual((archived, deleted), (1, 1))
        self.assertEqual(self.source.archived, [("t1", "asset", "old-asset")])
        self.assertTrue(await self.repo.is_tombstoned("t1", "asset", "old-asset"))

    async def test_delete_requires_authorization_and_creates_all_sink_jobs(self):
        with self.assertRaises(PermissionError):
            await self.service.delete_request(
                tenant_id="t1", actor_id="reader", resource_type="asset",
                resource_id="a", reason="user request",
            )
        tombstone = await self.service.delete_request(
            tenant_id="t1", actor_id="admin", resource_type="asset",
            resource_id="a", reason="user request",
        )
        self.assertEqual(len(self.repo.jobs), 3)
        self.assertEqual(tombstone.requested_by, "admin")

    async def test_delete_request_is_idempotent(self):
        first = await self.service.delete_request(
            tenant_id="t1", actor_id="admin", resource_type="asset",
            resource_id="a", reason="request",
        )
        second = await self.service.delete_request(
            tenant_id="t1", actor_id="admin", resource_type="asset",
            resource_id="a", reason="replay",
        )
        self.assertEqual(first.request_id, second.request_id)
        self.assertEqual(len(self.repo.jobs), 3)

    async def test_export_omits_tombstoned_records(self):
        await self.service.delete_request(
            tenant_id="t1", actor_id="admin", resource_type="asset",
            resource_id="a", reason="erasure",
        )
        request = await self.service.request_export(
            tenant_id="t1", actor_id="admin", resource_type="asset",
        )
        lines = [line async for line in self.service.export_jsonl(request=request)]
        self.assertEqual(len(lines), 1)
        self.assertIn(b'"id": "b"', lines[0])

    async def test_propagation_worker_deletes_from_each_sink(self):
        await self.service.delete_request(
            tenant_id="t1", actor_id="admin", resource_type="asset",
            resource_id="a", reason="erasure",
        )
        sinks = {name: Sink() for name in ("search_index", "cache", "object_storage")}
        worker = LifecyclePropagationWorker(
            repository=self.repo, sinks=sinks, worker_id="worker-1",
        )
        for _ in range(3):
            self.assertTrue(await worker.run_once())
        self.assertFalse(await worker.run_once())
        self.assertTrue(all(len(sink.deleted) == 1 for sink in sinks.values()))

    async def test_export_and_retention_are_tenant_scoped(self):
        await self.service.set_policy(
            policy=RetentionPolicy("tenant-a", "asset", 30), actor_id="admin",
        )
        self.assertIsNone(await self.repo.get_policy("tenant-b", "asset"))
        with self.assertRaises(PermissionError):
            await self.service.request_export(
                tenant_id="tenant-a", actor_id="no-export", resource_type="asset",
            )


if __name__ == "__main__":
    unittest.main()
