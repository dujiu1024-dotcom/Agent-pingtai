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


    async def test_expiry_boundary_transitions_once_and_hides_memory(self):
        repository = InMemoryMemoryRepository()
        service = MemoryService(repository)
        expires_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
        candidate = MemoryCandidate(
            tenant_id="tenant-a", scope=MemoryScope.USER,
            scope_id="user-1", content="temporary preference",
            source_ref="session:turn-expiry-boundary", confidence=0.9,
            created_by="extractor", expires_at=expires_at,
        )
        await service.propose(candidate)
        active = await service.confirm(
            principal=MemoryPrincipal(tenant_id="tenant-a", user_id="user-1"),
            memory_id=candidate.memory_id,
        )

        self.assertEqual(
            await service.expire_due(tenant_id="tenant-a", now=expires_at),
            1,
        )
        expired = await repository.get("tenant-a", candidate.memory_id)
        self.assertIsNotNone(expired)
        self.assertEqual(expired.status.value, "expired")
        self.assertEqual(expired.version, active.version + 1)
        self.assertEqual(
            await service.retrieve(
                principal=MemoryPrincipal(
                    tenant_id="tenant-a", user_id="user-1",
                ),
            ),
            [],
        )
        self.assertEqual(
            await service.expire_due(tenant_id="tenant-a", now=expires_at),
            0,
        )
        unchanged = await repository.get("tenant-a", candidate.memory_id)
        self.assertEqual(unchanged.version, expired.version)

    async def test_expiry_cleanup_failure_is_retried_without_new_version(self):
        repository = InMemoryMemoryRepository()
        cleanup_calls: list[tuple[str, str]] = []

        async def cleanup(tenant_id: str, memory_id: str) -> None:
            cleanup_calls.append((tenant_id, memory_id))
            if len(cleanup_calls) == 1:
                raise RuntimeError("derived index temporarily unavailable")

        service = MemoryService(repository, on_delete=cleanup)
        expires_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
        candidate = MemoryCandidate(
            tenant_id="tenant-a", scope=MemoryScope.USER,
            scope_id="user-1", content="temporary preference",
            source_ref="session:turn-expiry-cleanup", confidence=0.9,
            created_by="extractor", expires_at=expires_at,
        )
        await service.propose(candidate)
        active = await service.confirm(
            principal=MemoryPrincipal(tenant_id="tenant-a", user_id="user-1"),
            memory_id=candidate.memory_id,
        )

        with self.assertRaisesRegex(
            RuntimeError, "derived index temporarily unavailable",
        ):
            await service.expire_due(tenant_id="tenant-a", now=expires_at)

        expired = await repository.get("tenant-a", candidate.memory_id)
        self.assertEqual(expired.status.value, "expired")
        self.assertEqual(expired.version, active.version + 1)

        # The retry only repeats downstream cleanup; it does not append a
        # second expiry transition or increment the memory version again.
        self.assertEqual(
            await service.expire_due(tenant_id="tenant-a", now=expires_at),
            0,
        )
        retried = await repository.get("tenant-a", candidate.memory_id)
        self.assertEqual(retried.status.value, "expired")
        self.assertEqual(retried.version, expired.version)
        self.assertEqual(
            cleanup_calls,
            [
                ("tenant-a", candidate.memory_id),
                ("tenant-a", candidate.memory_id),
            ],
        )
