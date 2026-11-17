from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent_platform.memory.retrieval import (
    MemoryRecallQuery,
    MemoryRecallStrategy,
    MemoryRetrievalService,
)
from agent_platform.memory.service import (
    InMemoryMemoryRepository,
    MemoryCandidate,
    MemoryPrincipal,
    MemoryScope,
    MemoryService,
)


class MemoryRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = InMemoryMemoryRepository()
        self.memory = MemoryService(self.repository)
        self.retrieval = MemoryRetrievalService(self.memory)
        self.owner = MemoryPrincipal(
            tenant_id="tenant-a", user_id="user-a",
            project_ids=frozenset({"project-a"}),
            agent_ids=frozenset({"agent-a"}),
        )

    async def add_active(self, *, content, source_ref, confidence=.8, scope=MemoryScope.USER,
                         scope_id="user-a", applies_from=None, applies_until=None,
                         expires_at=None):
        candidate = MemoryCandidate(
            tenant_id="tenant-a", scope=scope, scope_id=scope_id, content=content,
            source_ref=source_ref, confidence=confidence, created_by="extractor",
            applies_from=applies_from, applies_until=applies_until, expires_at=expires_at,
        )
        await self.memory.propose(candidate)
        await self.memory.confirm(
            tenant_id="tenant-a", memory_id=candidate.memory_id, confirmer_id="user-a",
        )
        return candidate.memory_id

    async def test_scope_permissions_and_tenant_isolation_are_enforced(self):
        own = await self.add_active(content="my preference", source_ref="chat:1")
        project = await self.add_active(
            content="project decision", source_ref="project:1",
            scope=MemoryScope.PROJECT, scope_id="project-a",
        )
        hits = await self.retrieval.retrieve(principal=self.owner)
        self.assertEqual({h.record.candidate.memory_id for h in hits}, {own, project})
        outsider = MemoryPrincipal(tenant_id="tenant-b", user_id="user-a")
        self.assertEqual(await self.retrieval.retrieve(principal=outsider), [])
        reader = MemoryPrincipal(tenant_id="tenant-a", user_id="user-b")
        self.assertEqual(await self.retrieval.retrieve(principal=reader), [])

    async def test_applicability_window_and_expiry_are_applied(self):
        now = datetime.now(timezone.utc)
        await self.add_active(
            content="future fact", source_ref="doc:future",
            applies_from=now + timedelta(days=1),
        )
        await self.add_active(
            content="ended fact", source_ref="doc:ended",
            applies_until=now - timedelta(seconds=1),
        )
        await self.add_active(
            content="expired fact", source_ref="doc:expired",
            expires_at=now - timedelta(seconds=1),
        )
        visible = await self.retrieval.retrieve(
            principal=self.owner, query=MemoryRecallQuery(as_of=now),
        )
        self.assertEqual(visible, [])

    async def test_time_created_scope_source_and_confidence_filters(self):
        now = datetime.now(timezone.utc)
        target = await self.add_active(
            content="architecture decision", source_ref="decision:42", confidence=.95,
        )
        await self.add_active(
            content="unrelated preference", source_ref="chat:9", confidence=.4,
        )
        hits = await self.retrieval.retrieve(
            principal=self.owner,
            query=MemoryRecallQuery(
                text="architecture", scopes=frozenset({MemoryScope.USER}),
                created_after=now - timedelta(minutes=1),
                created_before=now + timedelta(minutes=1),
                min_confidence=.8, source_ref_prefix="decision:",
            ),
        )
        self.assertEqual([hit.record.candidate.memory_id for hit in hits], [target])
        self.assertEqual(hits[0].record.candidate.source_ref, "decision:42")
        self.assertEqual(hits[0].matched_terms, ("architecture",))

    async def test_recall_strategies_rank_confidence_and_recency(self):
        old = await self.add_active(
            content="old high confidence", source_ref="chat:old", confidence=.99,
        )
        new = await self.add_active(
            content="new lower confidence", source_ref="chat:new", confidence=.6,
        )
        confidence = await self.retrieval.retrieve(
            principal=self.owner,
            query=MemoryRecallQuery(strategy=MemoryRecallStrategy.CONFIDENCE),
        )
        recency = await self.retrieval.retrieve(
            principal=self.owner,
            query=MemoryRecallQuery(strategy=MemoryRecallStrategy.RECENCY),
        )
        self.assertEqual(confidence[0].record.candidate.memory_id, old)
        self.assertEqual(recency[0].record.candidate.memory_id, new)

    async def test_historical_version_requires_explicit_authorized_read(self):
        memory_id = await self.add_active(
            content="original preference", source_ref="chat:1",
        )
        await self.memory.correct(
            principal=self.owner, memory_id=memory_id,
            content="corrected preference", source_ref="chat:2",
        )
        historical = await self.retrieval.retrieve_version(
            principal=self.owner, memory_id=memory_id, version=2,
        )
        self.assertEqual(historical.record.candidate.content, "original preference")
        self.assertEqual(historical.record.candidate.source_ref, "chat:1")
        outsider = MemoryPrincipal(tenant_id="tenant-a", user_id="user-b")
        with self.assertRaises(PermissionError):
            await self.retrieval.retrieve_version(
                principal=outsider, memory_id=memory_id, version=2,
            )

    async def test_non_active_historical_versions_are_not_recalled(self):
        memory_id = await self.add_active(content="candidate", source_ref="chat:1")
        with self.assertRaisesRegex(ValueError, "not applicable"):
            await self.retrieval.retrieve_version(
                principal=self.owner, memory_id=memory_id, version=1,
            )

    async def test_invalid_time_range_and_limits_are_rejected(self):
        now = datetime.now(timezone.utc)
        with self.assertRaises(ValueError):
            await self.retrieval.retrieve(
                principal=self.owner,
                query=MemoryRecallQuery(
                    created_after=now, created_before=now - timedelta(seconds=1),
                ),
            )
        with self.assertRaises(ValueError):
            await self.retrieval.retrieve(
                principal=self.owner, query=MemoryRecallQuery(limit=101),
            )

    async def test_archive_and_forget_versions_are_not_recalled(self):
        memory_id = await self.add_active(content="sensitive", source_ref="chat:1")
        await self.memory.archive(principal=self.owner, memory_id=memory_id)
        self.assertEqual(await self.retrieval.retrieve(principal=self.owner), [])
        with self.assertRaises(ValueError):
            await self.retrieval.retrieve_version(
                principal=self.owner, memory_id=memory_id, version=2,
            )


if __name__ == "__main__":
    unittest.main()
