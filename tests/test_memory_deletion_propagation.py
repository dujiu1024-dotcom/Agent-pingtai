from __future__ import annotations

import unittest

from agent_platform.memory.service import (
    InMemoryMemoryRepository, MemoryCandidate, MemoryPrincipal,
    MemoryScope, MemoryService,
)


class MemoryDeletionPropagationTests(unittest.IsolatedAsyncioTestCase):
    async def test_forget_retries_cleanup_after_transient_failure(self):
        calls = []

        async def cleanup(tenant_id, memory_id):
            calls.append((tenant_id, memory_id))
            if len(calls) == 1:
                raise RuntimeError("cache unavailable")

        repository = InMemoryMemoryRepository()
        service = MemoryService(repository, on_delete=cleanup)
        candidate = MemoryCandidate(
            tenant_id="tenant-a", scope=MemoryScope.USER, scope_id="user-a",
            content="retain no longer", source_ref="turn:1", confidence=0.9,
            created_by="user-a",
        )
        await service.propose(candidate)
        await service.confirm(
            principal=MemoryPrincipal(tenant_id="tenant-a", user_id="user-a"),
            memory_id=candidate.memory_id,
        )
        principal = MemoryPrincipal(tenant_id="tenant-a", user_id="user-a")

        with self.assertRaisesRegex(RuntimeError, "cache unavailable"):
            await service.forget(principal=principal, memory_id=candidate.memory_id)
        self.assertEqual(
            (await repository.get("tenant-a", candidate.memory_id)).status.value,
            "deleted",
        )
        self.assertTrue(
            await service.forget(principal=principal, memory_id=candidate.memory_id)
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(await service.retrieve(principal=principal), [])

    async def test_forget_is_idempotent_after_successful_cleanup(self):
        calls = []

        async def cleanup(tenant_id, memory_id):
            calls.append(memory_id)

        repository = InMemoryMemoryRepository()
        service = MemoryService(repository, on_delete=cleanup)
        candidate = MemoryCandidate(
            tenant_id="tenant-a", scope=MemoryScope.USER, scope_id="user-a",
            content="forget me", source_ref="turn:2", confidence=0.8,
            created_by="user-a",
        )
        await service.propose(candidate)
        await service.confirm(
            principal=MemoryPrincipal(tenant_id="tenant-a", user_id="user-a"),
            memory_id=candidate.memory_id,
        )
        principal = MemoryPrincipal(tenant_id="tenant-a", user_id="user-a")
        self.assertTrue(await service.forget(principal=principal, memory_id=candidate.memory_id))
        self.assertTrue(await service.forget(principal=principal, memory_id=candidate.memory_id))
        self.assertEqual(calls, [candidate.memory_id, candidate.memory_id])


    async def test_concurrent_delete_tombstone_is_reported_as_success(self):
        class ConcurrentDeleteRepository(InMemoryMemoryRepository):
            async def delete(self, tenant_id, memory_id, at):
                await super().delete(tenant_id, memory_id, at)
                return False

        calls = []

        async def cleanup(tenant_id, memory_id):
            calls.append(memory_id)

        repository = ConcurrentDeleteRepository()
        service = MemoryService(repository, on_delete=cleanup)
        candidate = MemoryCandidate(
            tenant_id="tenant-a", scope=MemoryScope.USER, scope_id="user-a",
            content="concurrent forget", source_ref="turn:3", confidence=0.8,
            created_by="user-a",
        )
        await service.propose(candidate)
        await service.confirm(
            principal=MemoryPrincipal(tenant_id="tenant-a", user_id="user-a"),
            memory_id=candidate.memory_id,
        )
        principal = MemoryPrincipal(tenant_id="tenant-a", user_id="user-a")
        self.assertTrue(
            await service.forget(principal=principal, memory_id=candidate.memory_id)
        )
        self.assertEqual(calls, [candidate.memory_id])


if __name__ == "__main__":
    unittest.main()
