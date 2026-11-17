from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent_platform.memory.retrieval import (
    MemoryRecallStrategy,
    MemoryRetrievalQuery,
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

    async def add_active(self, *, content, source_ref, scope=MemoryScope.USER,
                         scope_id="user-a", confidence=0.8, expires_at=None):
        candidate = MemoryCandidate(
            tenant_id="tenant-a", scope=scope, scope_id=scope_id,
            content=content, source_ref=source_ref, confidence=confidence,
            created_by="extractor", expires_at=expires_at,
        )
        await self.memory.propose(candidate)
        await self.memory.confirm(
            tenant_id="tenant-a", memory_id=candidate.memory_id,
            confirmer_id="reviewer",
        )
        return candidate

    async def test_search_returns_version_and_verifiable_source_citation(self):
        candidate = await self.add_active(
            content="prefers concise technical answers", source_ref="conversation:turn-1"
        )
        await self.memory.correct(
            principal=self.owner, memory_id=candidate.memory_id,
            content="prefers concise Python answers", source_ref="conversation:turn-2",
        )
        hits = await self.retrieval.search(
            principal=self.owner,
            query=MemoryRetrievalQuery(text="Python answers"),
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].citation.memory_id, candidate.memory_id)
        self.assertEqual(hits[0].citation.version, 3)
        self.assertEqual(hits[0].citation.source_ref, "conversation:turn-2")
        self.assertEqual(hits[0].record.candidate.content, "prefers concise Python answers")

    async def test_filters_scope_source_confidence_and_recorded_time(self):
        await self.add_active(content="project deployment policy", source_ref="project:1",
                              scope=MemoryScope.PROJECT, scope_id="project-a", confidence=0.95)
        await self.add_active(content="personal writing preference", source_ref="chat:2",
                              confidence=0.6)
        hits = await self.retrieval.search(
            principal=self.owner,
            query=MemoryRetrievalQuery(
                scopes=frozenset({MemoryScope.PROJECT}),
                source_refs=frozenset({"project:1"}),
                min_confidence=0.9,
                created_after=datetime(2000, 1, 1, tzinfo=timezone.utc),
            ),
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].citation.source_ref, "project:1")

    async def test_as_of_returns_prior_version_but_not_future_version(self):
        candidate = await self.add_active(
            content="old preference", source_ref="chat:old"
        )
        history = await self.repository.list_versions("tenant-a", candidate.memory_id)
        first_active = history[-1].updated_at
        corrected = await self.memory.correct(
            principal=self.owner, memory_id=candidate.memory_id,
            content="new preference", source_ref="chat:new",
        )
        before_correction = await self.retrieval.search(
            principal=self.owner,
            query=MemoryRetrievalQuery(as_of=first_active + timedelta(microseconds=1)),
        )
        self.assertEqual(before_correction[0].citation.version, 2)
        self.assertEqual(before_correction[0].citation.source_ref, "chat:old")
        self.assertEqual(corrected.version, 3)

    async def test_expired_and_cross_tenant_memories_are_not_visible(self):
        await self.add_active(
            content="temporary fact", source_ref="chat:expired",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        hits = await self.retrieval.search(principal=self.owner)
        self.assertEqual(hits, [])
        foreign = MemoryPrincipal(tenant_id="tenant-b", user_id="user-a")
        self.assertEqual(await self.retrieval.search(principal=foreign), [])

    async def test_shared_scope_requires_membership_and_tenant_scope_requires_admin(self):
        await self.add_active(
            content="project secret", source_ref="project:secret",
            scope=MemoryScope.PROJECT, scope_id="project-a",
        )
        reader = MemoryPrincipal(tenant_id="tenant-a", user_id="reader")
        self.assertEqual(await self.retrieval.search(principal=reader), [])
        admin = MemoryPrincipal(
            tenant_id="tenant-a", user_id="admin",
            permissions=frozenset({"memory.manage"}),
        )
        await self.add_active(
            content="tenant policy", source_ref="policy:1",
            scope=MemoryScope.TENANT, scope_id="tenant-a",
        )
        self.assertEqual(len(await self.retrieval.search(principal=admin)), 2)
        self.assertEqual(await self.retrieval.search(principal=reader), [])

    async def test_recall_strategies_rank_deterministically(self):
        await self.add_active(content="common preference", source_ref="chat:low", confidence=0.5)
        await self.add_active(content="common preference", source_ref="chat:high", confidence=0.99)
        confidence = await self.retrieval.search(
            principal=self.owner,
            query=MemoryRetrievalQuery(strategy=MemoryRecallStrategy.CONFIDENCE),
        )
        self.assertGreaterEqual(
            confidence[0].record.candidate.confidence,
            confidence[1].record.candidate.confidence,
        )
        recency = await self.retrieval.search(
            principal=self.owner,
            query=MemoryRetrievalQuery(strategy=MemoryRecallStrategy.RECENCY, limit=1),
        )
        self.assertEqual(len(recency), 1)

    async def test_invalid_query_is_rejected(self):
        invalid = (
            {"min_confidence": float("nan")},
            {"min_confidence": True},
            {"limit": True},
            {"strategy": "recency"},
            {"scopes": {"user"}},
            {"source_refs": {" "}},
            {"text": " "},
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                MemoryRetrievalQuery(**values)


if __name__ == "__main__":
    unittest.main()
