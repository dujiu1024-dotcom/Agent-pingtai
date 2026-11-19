from __future__ import annotations

import unittest

from agent_platform.memory.review import (
    InMemoryMemoryReviewAuditRepository,
    MemoryCandidateWorkflow,
    MemoryReviewDecision,
)
from agent_platform.memory.service import (
    InMemoryMemoryRepository,
    MemoryCandidate,
    MemoryPrincipal,
    MemoryScope,
    MemoryService,
)


class MemoryCandidateWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = InMemoryMemoryRepository()
        self.service = MemoryService(self.repository)
        self.audit = InMemoryMemoryReviewAuditRepository()
        self.workflow = MemoryCandidateWorkflow(memory=self.service, audit=self.audit)

    async def _candidate(self, *, scope=MemoryScope.USER, scope_id="user-a", conflict_key=None):
        candidate = MemoryCandidate(
            tenant_id="tenant-a",
            scope=scope,
            scope_id=scope_id,
            content="prefers concise answers",
            source_ref="conversation:turn-42",
            confidence=0.93,
            created_by="extractor",
            conflict_key=conflict_key,
        )
        await self.service.propose(candidate)
        return candidate

    async def test_only_scope_manager_can_confirm_and_review_is_audited(self):
        candidate = await self._candidate()
        stranger = MemoryPrincipal(tenant_id="tenant-a", user_id="user-b")
        with self.assertRaises(PermissionError):
            await self.workflow.review(
                principal=stranger, memory_id=candidate.memory_id,
                decision=MemoryReviewDecision.CONFIRM,
            )

        owner = MemoryPrincipal(tenant_id="tenant-a", user_id="user-a")
        confirmed = await self.workflow.review(
            principal=owner,
            memory_id=candidate.memory_id,
            decision=MemoryReviewDecision.CONFIRM,
            reviewer_note="User explicitly confirmed this preference.",
        )
        self.assertEqual(confirmed.status.value, "active")
        events = await self.workflow.history(principal=owner, memory_id=candidate.memory_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].decision, MemoryReviewDecision.CONFIRM)
        self.assertEqual(events[0].reviewer_id, "user-a")
        self.assertEqual(events[0].source_ref, "conversation:turn-42")
        self.assertEqual(events[0].candidate_version, 1)
        self.assertEqual(events[0].reviewer_note, "User explicitly confirmed this preference.")

    async def test_rejection_records_decision_and_remains_unretrievable(self):
        candidate = await self._candidate()
        owner = MemoryPrincipal(tenant_id="tenant-a", user_id="user-a")
        result = await self.workflow.review(
            principal=owner, memory_id=candidate.memory_id,
            decision=MemoryReviewDecision.REJECT, reviewer_note="Not durable.",
        )
        self.assertTrue(result)
        self.assertEqual(
            (await self.repository.get("tenant-a", candidate.memory_id)).status.value,
            "deleted",
        )
        self.assertEqual(len(await self.workflow.history(
            principal=owner, memory_id=candidate.memory_id
        )), 1)

    async def test_conflict_requires_explicit_selection_not_simple_confirmation(self):
        original = await self._candidate(conflict_key="preference")
        await self.service.confirm(
            memory_id=original.memory_id,
            principal=MemoryPrincipal(tenant_id="tenant-a", user_id="user-a"),
        )
        conflicting = MemoryCandidate(
            tenant_id="tenant-a", scope=MemoryScope.USER, scope_id="user-a",
            content="prefers detailed answers", source_ref="conversation:turn-43",
            confidence=0.9, created_by="extractor", conflict_key="preference",
        )
        await self.service.propose(conflicting)
        owner = MemoryPrincipal(tenant_id="tenant-a", user_id="user-a")
        with self.assertRaisesRegex(ValueError, "explicit resolve_conflict"):
            await self.workflow.review(
                principal=owner, memory_id=conflicting.memory_id,
                decision=MemoryReviewDecision.CONFIRM,
            )
        self.assertEqual(await self.workflow.history(
            principal=owner, memory_id=conflicting.memory_id
        ), ())

    async def test_project_review_requires_project_manager_grant(self):
        candidate = await self._candidate(scope=MemoryScope.PROJECT, scope_id="project-a")
        reader = MemoryPrincipal(
            tenant_id="tenant-a", user_id="reader",
            project_ids=frozenset({"project-a"}),
        )
        with self.assertRaises(PermissionError):
            await self.workflow.review(
                principal=reader, memory_id=candidate.memory_id,
                decision=MemoryReviewDecision.CONFIRM,
            )
        manager = MemoryPrincipal(
            tenant_id="tenant-a", user_id="manager",
            manage_project_ids=frozenset({"project-a"}),
        )
        result = await self.workflow.review(
            principal=manager, memory_id=candidate.memory_id,
            decision=MemoryReviewDecision.CONFIRM,
        )
        self.assertEqual(result.status.value, "active")

    async def test_reviewer_note_is_bounded(self):
        candidate = await self._candidate()
        owner = MemoryPrincipal(tenant_id="tenant-a", user_id="user-a")
        with self.assertRaises(ValueError):
            await self.workflow.review(
                principal=owner, memory_id=candidate.memory_id,
                decision=MemoryReviewDecision.CONFIRM, reviewer_note="x" * 2001,
            )


if __name__ == "__main__":
    unittest.main()
