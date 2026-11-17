from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent_platform.memory.maintenance import MemoryExpiryWorker
from agent_platform.memory.service import (
    InMemoryMemoryRepository,
    MemoryCandidate,
    MemoryPrincipal,
    MemoryScope,
    MemoryService,
)


class MemoryExpiryWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_once_sweeps_each_explicit_tenant(self):
        repository = InMemoryMemoryRepository()
        service = MemoryService(repository)
        expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        for tenant_id in ("tenant-a", "tenant-b"):
            candidate = MemoryCandidate(
                tenant_id=tenant_id, scope=MemoryScope.USER,
                scope_id="user-1", content="temporary fact",
                source_ref="session:turn-1", confidence=0.9,
                created_by="extractor", expires_at=expired_at,
            )
            await service.propose(candidate)
            await service.confirm(
                principal=MemoryPrincipal(tenant_id=tenant_id, user_id="user-1"),
                memory_id=candidate.memory_id,
            )

        async def list_tenants():
            return ("tenant-a", "tenant-b")

        worker = MemoryExpiryWorker(
            memory=service, list_tenant_ids=list_tenants,
        )
        self.assertEqual(
            await worker.run_once(), {"tenant-a": 1, "tenant-b": 1},
        )
        self.assertEqual(
            await worker.run_once(), {"tenant-a": 0, "tenant-b": 0},
        )

    async def test_rejects_invalid_interval_and_tenant_ids(self):
        async def empty():
            return ()

        with self.assertRaises(ValueError):
            MemoryExpiryWorker(
                memory=MemoryService(InMemoryMemoryRepository()),
                list_tenant_ids=empty, interval_seconds=1,
            )

        async def invalid_tenants():
            return ("tenant-a", "")

        worker = MemoryExpiryWorker(
            memory=MemoryService(InMemoryMemoryRepository()),
            list_tenant_ids=invalid_tenants,
        )
        with self.assertRaises(ValueError):
            await worker.run_once()
