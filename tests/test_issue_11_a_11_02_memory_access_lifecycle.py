from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent_platform.memory.retrieval import MemoryRetrievalService
from agent_platform.memory.service import (
    InMemoryMemoryRepository,
    MemoryCandidate,
    MemoryPrincipal,
    MemoryScope,
    MemoryService,
)


class MemoryAccessLifecycleAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = InMemoryMemoryRepository()
        self.memory = MemoryService(self.repository)
        self.retrieval = MemoryRetrievalService(self.memory)

    async def _active(
        self,
        *,
        scope: MemoryScope,
        scope_id: str,
        owner: MemoryPrincipal,
        content: str,
        expires_at: datetime | None = None,
    ) -> str:
        candidate = MemoryCandidate(
            tenant_id=owner.tenant_id,
            scope=scope,
            scope_id=scope_id,
            content=content,
            source_ref=f"manual:{scope.value}:{scope_id}",
            confidence=0.9,
            created_by="acceptance-test",
            expires_at=expires_at,
        )
        await self.memory.propose(candidate)
        await self.memory.confirm(principal=owner, memory_id=candidate.memory_id)
        return candidate.memory_id

    async def test_retrieval_isolated_by_user_project_agent_and_tenant(self) -> None:
        tenant_admin = MemoryPrincipal(
            tenant_id="tenant-a",
            user_id="admin",
            permissions=frozenset({"memory.manage"}),
        )
        user_owner = MemoryPrincipal(tenant_id="tenant-a", user_id="alice")
        project_admin = MemoryPrincipal(
            tenant_id="tenant-a",
            user_id="project-admin",
            manage_project_ids=frozenset({"project-1"}),
        )
        agent_admin = MemoryPrincipal(
            tenant_id="tenant-a",
            user_id="agent-admin",
            manage_agent_ids=frozenset({"agent-1"}),
        )

        user_id = await self._active(
            scope=MemoryScope.USER,
            scope_id="alice",
            owner=user_owner,
            content="alice private preference",
        )
        project_id = await self._active(
            scope=MemoryScope.PROJECT,
            scope_id="project-1",
            owner=project_admin,
            content="project shared decision",
        )
        agent_id = await self._active(
            scope=MemoryScope.AGENT,
            scope_id="agent-1",
            owner=agent_admin,
            content="agent operating preference",
        )
        tenant_id = await self._active(
            scope=MemoryScope.TENANT,
            scope_id="tenant-a",
            owner=tenant_admin,
            content="tenant policy",
        )

        full_member = MemoryPrincipal(
            tenant_id="tenant-a",
            user_id="alice",
            project_ids=frozenset({"project-1"}),
            agent_ids=frozenset({"agent-1"}),
        )
        limited_member = MemoryPrincipal(tenant_id="tenant-a", user_id="bob")
        foreign_member = MemoryPrincipal(tenant_id="tenant-b", user_id="alice")

        full_hits = await self.retrieval.retrieve(principal=full_member)
        self.assertEqual(
            {hit.record.candidate.memory_id for hit in full_hits},
            {user_id, project_id, agent_id, tenant_id},
        )
        limited_hits = await self.retrieval.retrieve(principal=limited_member)
        self.assertEqual(
            {hit.record.candidate.memory_id for hit in limited_hits},
            {tenant_id},
        )
        foreign_hits = await self.retrieval.retrieve(principal=foreign_member)
        self.assertEqual(foreign_hits, [])

        service_hits = await self.memory.retrieve(principal=limited_member)
        self.assertEqual(
            {record.candidate.memory_id for record in service_hits},
            {tenant_id},
        )

    async def test_project_and_agent_membership_are_required(self) -> None:
        project_owner = MemoryPrincipal(
            tenant_id="tenant-a",
            user_id="owner",
            manage_project_ids=frozenset({"project-secret"}),
        )
        agent_owner = MemoryPrincipal(
            tenant_id="tenant-a",
            user_id="owner",
            manage_agent_ids=frozenset({"agent-secret"}),
        )
        project_id = await self._active(
            scope=MemoryScope.PROJECT,
            scope_id="project-secret",
            owner=project_owner,
            content="restricted project memory",
        )
        agent_id = await self._active(
            scope=MemoryScope.AGENT,
            scope_id="agent-secret",
            owner=agent_owner,
            content="restricted agent memory",
        )
        outsider = MemoryPrincipal(tenant_id="tenant-a", user_id="outsider")
        self.assertEqual(
            await self.retrieval.retrieve(principal=outsider),
            [],
        )
        project_member = MemoryPrincipal(
            tenant_id="tenant-a",
            user_id="outsider",
            project_ids=frozenset({"project-secret"}),
        )
        agent_member = MemoryPrincipal(
            tenant_id="tenant-a",
            user_id="outsider",
            agent_ids=frozenset({"agent-secret"}),
        )
        self.assertEqual(
            {hit.record.candidate.memory_id for hit in await self.retrieval.retrieve(
                principal=project_member
            )},
            {project_id},
        )
        self.assertEqual(
            {hit.record.candidate.memory_id for hit in await self.retrieval.retrieve(
                principal=agent_member
            )},
            {agent_id},
        )

    async def test_expired_archived_and_forgotten_memories_are_not_retrievable(self) -> None:
        owner = MemoryPrincipal(tenant_id="tenant-a", user_id="alice")
        expired_id = await self._active(
            scope=MemoryScope.USER,
            scope_id="alice",
            owner=owner,
            content="expired preference",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        archived_id = await self._active(
            scope=MemoryScope.USER,
            scope_id="alice",
            owner=owner,
            content="archived preference",
        )
        forgotten_id = await self._active(
            scope=MemoryScope.USER,
            scope_id="alice",
            owner=owner,
            content="forgotten preference",
        )

        self.assertTrue(await self.memory.archive(principal=owner, memory_id=archived_id))
        self.assertTrue(await self.memory.forget(principal=owner, memory_id=forgotten_id))
        visible = await self.retrieval.retrieve(principal=owner)
        visible_ids = {hit.record.candidate.memory_id for hit in visible}
        self.assertNotIn(expired_id, visible_ids)
        self.assertNotIn(archived_id, visible_ids)
        self.assertNotIn(forgotten_id, visible_ids)

    async def test_correction_is_authorized_and_updates_retrieved_version(self) -> None:
        owner = MemoryPrincipal(tenant_id="tenant-a", user_id="alice")
        stranger = MemoryPrincipal(tenant_id="tenant-a", user_id="bob")
        memory_id = await self._active(
            scope=MemoryScope.USER,
            scope_id="alice",
            owner=owner,
            content="old preference",
        )
        before = await self.repository.get("tenant-a", memory_id)
        self.assertIsNotNone(before)

        with self.assertRaises(PermissionError):
            await self.memory.correct(
                principal=stranger,
                memory_id=memory_id,
                content="unauthorized replacement",
                source_ref="manual:unauthorized",
            )

        corrected = await self.memory.correct(
            principal=owner,
            memory_id=memory_id,
            content="corrected preference",
            source_ref="manual:correction",
            confidence=0.95,
        )
        self.assertEqual(corrected.version, before.version + 1)
        hits = await self.retrieval.retrieve(principal=owner)
        matching = next(hit.record for hit in hits if hit.record.candidate.memory_id == memory_id)
        self.assertEqual(matching.candidate.content, "corrected preference")
        self.assertEqual(matching.candidate.source_ref, "manual:correction")
