from __future__ import annotations

import unittest

from agent_platform.memory.conflicts import (
    InMemoryMemoryConflictAuditRepository,
    MemoryConflictResolution,
    MemoryConflictResolver,
)
from agent_platform.memory.service import (
    InMemoryMemoryRepository,
    MemoryCandidate,
    MemoryPrincipal,
    MemoryScope,
    MemoryService,
    MemoryStatus,
)


class MemoryConflictResolverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = InMemoryMemoryRepository()
        self.memory = MemoryService(self.repository)
        self.audit = InMemoryMemoryConflictAuditRepository()
        self.resolver = MemoryConflictResolver(memory=self.memory, audit=self.audit)
        self.owner = MemoryPrincipal(
            tenant_id="tenant-a",
            user_id="user-a",
            permissions=frozenset({"memory.manage"}),
        )

    async def _conflict(self):
        first = MemoryCandidate(
            tenant_id="tenant-a", scope=MemoryScope.USER, scope_id="user-a",
            content="prefers concise reports", source_ref="session:one",
            confidence=0.8, created_by="user-a", conflict_key="writing-style",
        )
        active = await self.memory.propose(first)
        active = await self.memory.confirm(
            tenant_id="tenant-a", memory_id=first.memory_id, confirmer_id="user-a"
        )
        second = MemoryCandidate(
            tenant_id="tenant-a", scope=MemoryScope.USER, scope_id="user-a",
            content="prefers detailed reports", source_ref="session:two",
            confidence=0.9, created_by="user-a", conflict_key="writing-style",
        )
        conflict = await self.memory.propose(second)
        self.assertIs(conflict.status, MemoryStatus.CONFLICT)
        return active, conflict

    async def test_select_resolution_records_exact_versions_and_supersedes_loser(self):
        active, conflict = await self._conflict()
        result = await self.resolver.resolve(
            principal=self.owner, conflict_key="writing-style",
            selected_memory_id=conflict.candidate.memory_id,
        )
        self.assertIs(result.status, MemoryStatus.ACTIVE)
        self.assertEqual(result.version, conflict.version + 1)
        old = await self.repository.get("tenant-a", active.candidate.memory_id)
        self.assertIs(old.status, MemoryStatus.SUPERSEDED)
        history = await self.resolver.history(
            principal=self.owner, memory_id=result.candidate.memory_id
        )
        self.assertEqual(len(history), 1)
        self.assertEqual(
            {(item.memory_id, item.version, item.source_ref) for item in history[0].sources},
            {
                (active.candidate.memory_id, active.version, "session:one"),
                (conflict.candidate.memory_id, conflict.version, "session:two"),
            },
        )

    async def test_merge_creates_new_versioned_memory_with_source_lineage(self):
        active, conflict = await self._conflict()
        result = await self.resolver.resolve(
            principal=self.owner, conflict_key="writing-style",
            selected_memory_id=active.candidate.memory_id,
            resolution=MemoryConflictResolution.MERGE,
            merged_content="prefers concise but sufficiently detailed reports",
        )
        self.assertIs(result.status, MemoryStatus.ACTIVE)
        self.assertEqual(result.version, 2)
        self.assertTrue(result.candidate.source_ref.startswith("memory-conflict-merge:"))
        self.assertIn(active.candidate.memory_id, result.candidate.metadata["merged_from"])
        self.assertIn(conflict.candidate.memory_id, result.candidate.metadata["merged_from"])
        self.assertEqual(result.candidate.metadata["conflict_decision_id"],
                         (await self.resolver.history(
                             principal=self.owner, memory_id=result.candidate.memory_id
                         ))[0].decision_id)

    async def test_scope_authorization_and_invalid_resolution_are_rejected(self):
        _, conflict = await self._conflict()
        stranger = MemoryPrincipal(tenant_id="tenant-a", user_id="user-b")
        with self.assertRaises(PermissionError):
            await self.resolver.resolve(
                principal=stranger, conflict_key="writing-style",
                selected_memory_id=conflict.candidate.memory_id,
            )
        with self.assertRaises(ValueError):
            await self.resolver.resolve(
                principal=self.owner, conflict_key="writing-style",
                selected_memory_id=conflict.candidate.memory_id,
                resolution=MemoryConflictResolution.MERGE,
            )
        self.assertEqual(await self.audit.list_for_conflict("tenant-a", "writing-style"), ())

    async def test_conflict_audit_history_is_scope_protected(self):
        _, conflict = await self._conflict()
        await self.resolver.resolve(
            principal=self.owner, conflict_key="writing-style",
            selected_memory_id=conflict.candidate.memory_id,
        )
        stranger = MemoryPrincipal(tenant_id="tenant-a", user_id="user-b")
        with self.assertRaises(PermissionError):
            await self.resolver.history(
                principal=stranger, memory_id=conflict.candidate.memory_id
            )
