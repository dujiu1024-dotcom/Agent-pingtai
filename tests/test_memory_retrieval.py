from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent_platform.memory.retrieval import MemoryRecallPolicy, MemoryRetrievalService
from agent_platform.memory.service import (
    InMemoryMemoryRepository,
    MemoryCandidate,
    MemoryPrincipal,
    MemoryScope,
    MemoryService,
)


class MemoryRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = InMemoryMemoryRepository()
        self.memory = MemoryService(self.repository)
        self.retrieval = MemoryRetrievalService(memory=self.memory)
        self.owner = MemoryPrincipal(
            tenant_id="tenant-a",
            user_id="user-a",
            project_ids=frozenset({"project-a"}),
            agent_ids=frozenset({"agent-a"}),
            manage_project_ids=frozenset({"project-a"}),
            manage_agent_ids=frozenset({"agent-a"}),
            permissions=frozenset({"memory.manage"}),
        )

    async def _active(
        self,
        *,
        scope: MemoryScope = MemoryScope.USER,
        scope_id: str = "user-a",
        content: str = "prefers concise project status",
        source_ref: str = "session:turn-1",
        confidence: float = 0.9,
        expires_at: datetime | None = None,
        metadata: dict[str, str] | None = None,
    ):
        candidate = MemoryCandidate(
            tenant_id="tenant-a",
            scope=scope,
            scope_id=scope_id,
            content=content,
            source_ref=source_ref,
            confidence=confidence,
            created_by="user-a",
            expires_at=expires_at,
            metadata=metadata or {},
        )
        await self.memory.propose(candidate)
        await self.memory.confirm(
            tenant_id="tenant-a",
            memory_id=candidate.memory_id,
            confirmer_id="user-a",
        )
        return candidate

    async def test_recall_is_tenant_and_scope_authorized_before_ranking(self):
        own = await self._active()
        await self._active(scope=MemoryScope.USER, scope_id="user-b")
        await self._active(scope=MemoryScope.PROJECT, scope_id="project-b")
        stranger = MemoryPrincipal(tenant_id="tenant-b", user_id="user-a")
        self.assertEqual(await self.retrieval.retrieve(principal=stranger), [])
        results = await self.retrieval.retrieve(principal=self.owner, query="concise")
        self.assertEqual([item.record.candidate.memory_id for item in results], [own.memory_id])

    async def test_temporal_expiry_validity_and_max_age_filters(self):
        now = datetime.now(timezone.utc)
        future = await self._active(
            content="future planning preference",
            metadata={"valid_from": (now + timedelta(days=1)).isoformat()},
        )
        expired = await self._active(
            content="expired planning preference",
            expires_at=now - timedelta(seconds=1),
        )
        old = await self._active(
            content="old planning preference",
        )
        record = await self.repository.get("tenant-a", old.memory_id)
        record = type(record)(
            candidate=record.candidate,
            status=record.status,
            version=record.version,
            confirmed_by=record.confirmed_by,
            created_at=now - timedelta(days=60),
            updated_at=now - timedelta(days=60),
            deleted_at=record.deleted_at,
        )
        self.repository.records[("tenant-a", old.memory_id)] = record
        results = await self.retrieval.retrieve(
            principal=self.owner,
            query="planning preference",
            policy=MemoryRecallPolicy(max_age=timedelta(days=30)),
        )
        ids = {item.record.candidate.memory_id for item in results}
        self.assertNotIn(future.memory_id, ids)
        self.assertNotIn(expired.memory_id, ids)
        self.assertNotIn(old.memory_id, ids)

    async def test_confidence_scope_source_and_query_filters_are_deterministic(self):
        high = await self._active(
            scope=MemoryScope.PROJECT,
            scope_id="project-a",
            content="project delivery uses concise weekly reports",
            source_ref="session:weekly-1",
            confidence=0.95,
        )
        await self._active(
            scope=MemoryScope.PROJECT,
            scope_id="project-a",
            content="project delivery uses detailed daily reports",
            source_ref="session:daily-1",
            confidence=0.4,
        )
        results = await self.retrieval.retrieve(
            principal=self.owner,
            query="concise reports",
            policy=MemoryRecallPolicy(
                scopes=frozenset({MemoryScope.PROJECT}),
                min_confidence=0.8,
                source_prefixes=("session:weekly",),
            ),
        )
        self.assertEqual([item.record.candidate.memory_id for item in results], [high.memory_id])
        self.assertEqual(results[0].citation.source_ref, "session:weekly-1")
        self.assertEqual(results[0].citation.version, results[0].record.version)

    async def test_version_history_is_authorized_and_preserves_source_and_version(self):
        candidate = await self._active()
        corrected = await self.memory.correct(
            principal=self.owner,
            memory_id=candidate.memory_id,
            content="prefers concise weekly status",
            source_ref="user-correction:42",
            confidence=0.98,
        )
        history = await self.retrieval.version_history(
            principal=self.owner, memory_id=candidate.memory_id
        )
        self.assertEqual([item.version for item in history], [1, 2, 3])
        self.assertEqual(history[-1].source_ref, "user-correction:42")
        self.assertEqual(history[-1].version, corrected.version)
        stranger = MemoryPrincipal(tenant_id="tenant-a", user_id="user-b")
        with self.assertRaises(PermissionError):
            await self.retrieval.version_history(
                principal=stranger, memory_id=candidate.memory_id
            )

    async def test_invalid_policy_and_naive_time_are_rejected(self):
        with self.assertRaises(ValueError):
            MemoryRecallPolicy(min_confidence=1.1)
        with self.assertRaises(ValueError):
            MemoryRecallPolicy(limit=0)
        with self.assertRaises(ValueError):
            MemoryRecallPolicy(as_of=datetime(2026, 1, 1))
