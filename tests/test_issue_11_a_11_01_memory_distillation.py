from __future__ import annotations

import unittest
from dataclasses import fields

from agent_platform.memory.conflicts import (
    InMemoryMemoryConflictAuditRepository,
    MemoryConflictResolution,
    MemoryConflictResolver,
)
from agent_platform.memory.extraction import (
    MemoryDraft,
    MemoryExtractionService,
    MemoryTurn,
)
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
    MemoryStatus,
)


class RecordingExtractor:
    def __init__(self, drafts: list[MemoryDraft]) -> None:
        self.drafts = drafts
        self.received_turn: MemoryTurn | None = None

    async def extract(self, *, turn: MemoryTurn) -> list[MemoryDraft]:
        self.received_turn = turn
        return self.drafts


class MemoryDistillationAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = InMemoryMemoryRepository()
        self.memory = MemoryService(self.repository)
        self.principal = MemoryPrincipal(
            tenant_id="tenant-a",
            user_id="user-a",
        )
        self.review_audit = InMemoryMemoryReviewAuditRepository()
        self.review = MemoryCandidateWorkflow(
            memory=self.memory,
            audit=self.review_audit,
        )

    async def test_completed_turn_produces_only_reviewable_distilled_candidate(self) -> None:
        turn = MemoryTurn(
            tenant_id="tenant-a",
            user_id="user-a",
            source_ref="session://tenant-a/session-a@17",
            user_message=(
                "Here is the full conversation about my work, travel plans, "
                "family details, and several unrelated questions."
            ),
            assistant_message=(
                "I summarized the discussion and answered the unrelated questions."
            ),
            completed=True,
        )
        distilled_fact = "prefers concise technical explanations"
        extractor = RecordingExtractor([
            MemoryDraft(
                scope=MemoryScope.USER,
                scope_id="user-a",
                content=distilled_fact,
                confidence=0.94,
            ),
        ])
        extraction = MemoryExtractionService(
            memory=self.memory,
            extractor=extractor,
        )

        records = await extraction.propose_from_turn(
            principal=self.principal,
            turn=turn,
        )

        self.assertIs(extractor.received_turn, turn)
        turn_fields = {item.name for item in fields(MemoryTurn)}
        self.assertTrue({"user_message", "assistant_message", "source_ref"} <= turn_fields)
        self.assertFalse({"transcript", "messages", "conversation_history"} & turn_fields)
        self.assertEqual(len(records), 1)
        candidate = records[0]
        self.assertIs(candidate.status, MemoryStatus.CANDIDATE)
        self.assertEqual(candidate.candidate.content, distilled_fact)
        self.assertNotEqual(candidate.candidate.content, turn.user_message)
        self.assertNotEqual(candidate.candidate.content, turn.assistant_message)
        self.assertEqual(candidate.candidate.source_ref, turn.source_ref)
        self.assertEqual(candidate.candidate.metadata["extraction"], "single_turn")

        # Candidate data is persisted for review, but never enters retrieval as
        # long-term memory until the authorized user confirms it.
        self.assertEqual(await self.memory.retrieve(principal=self.principal), [])
        reviewed = await self.review.review(
            principal=self.principal,
            memory_id=candidate.candidate.memory_id,
            decision=MemoryReviewDecision.CONFIRM,
            reviewer_note="Confirmed as a durable preference.",
        )
        self.assertIs(reviewed.status, MemoryStatus.ACTIVE)
        recalls = await self.memory.retrieve(principal=self.principal)
        self.assertEqual([item.candidate.content for item in recalls], [distilled_fact])

        history = await self.review.history(
            principal=self.principal,
            memory_id=candidate.candidate.memory_id,
        )
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].decision, MemoryReviewDecision.CONFIRM)
        self.assertEqual(history[0].source_ref, turn.source_ref)
        self.assertEqual(history[0].reviewer_id, self.principal.user_id)

    async def test_verbatim_turn_content_is_rejected_before_any_candidate_is_persisted(self) -> None:
        turn = MemoryTurn(
            tenant_id="tenant-a",
            user_id="user-a",
            source_ref="session://tenant-a/session-verbatim@3",
            user_message="My full message includes work, travel, and family details.",
            assistant_message="I will answer each topic in detail.",
            completed=True,
        )
        extractor = RecordingExtractor([
            MemoryDraft(
                scope=MemoryScope.USER,
                scope_id="user-a",
                content="My full message includes work, travel, and family details.",
                confidence=0.99,
            ),
        ])
        extraction = MemoryExtractionService(memory=self.memory, extractor=extractor)

        with self.assertRaisesRegex(ValueError, "must be distilled"):
            await extraction.propose_from_turn(principal=self.principal, turn=turn)
        self.assertEqual(await self.repository.list("tenant-a"), [])

    async def test_combined_chat_turn_is_rejected_even_with_whitespace_variation(self) -> None:
        turn = MemoryTurn(
            tenant_id="tenant-a",
            user_id="user-a",
            source_ref="session://tenant-a/session-combined@3",
            user_message="I need concise reports.",
            assistant_message="I will prepare concise reports.",
            completed=True,
        )
        extractor = RecordingExtractor([
            MemoryDraft(
                scope=MemoryScope.USER,
                scope_id="user-a",
                content="I need concise reports.  I will prepare concise reports.",
                confidence=0.99,
            ),
        ])
        extraction = MemoryExtractionService(memory=self.memory, extractor=extractor)

        with self.assertRaisesRegex(ValueError, "must be distilled"):
            await extraction.propose_from_turn(principal=self.principal, turn=turn)
        self.assertEqual(await self.repository.list("tenant-a"), [])

    async def test_conflicting_extracted_fact_requires_explicit_audited_resolution(self) -> None:
        original = MemoryCandidate(
            tenant_id="tenant-a",
            scope=MemoryScope.USER,
            scope_id="user-a",
            content="prefers concise reports",
            source_ref="session://tenant-a/session-before@4",
            confidence=0.88,
            created_by="user-a",
            conflict_key="writing-style",
        )
        original_record = await self.memory.propose(original)
        await self.memory.confirm(
            principal=self.principal,
            memory_id=original.memory_id,
        )

        turn = MemoryTurn(
            tenant_id="tenant-a",
            user_id="user-a",
            source_ref="session://tenant-a/session-after@9",
            user_message="I now prefer detailed reports with examples.",
            assistant_message="I will use that preference when preparing reports.",
            completed=True,
        )
        extractor = RecordingExtractor([
            MemoryDraft(
                scope=MemoryScope.USER,
                scope_id="user-a",
                content="prefers detailed reports with examples",
                confidence=0.96,
                conflict_key="writing-style",
            ),
        ])
        extraction = MemoryExtractionService(
            memory=self.memory,
            extractor=extractor,
        )
        proposed = await extraction.propose_from_turn(
            principal=self.principal,
            turn=turn,
        )
        conflict = proposed[0]
        self.assertIs(conflict.status, MemoryStatus.CONFLICT)

        with self.assertRaisesRegex(ValueError, "explicit resolve_conflict"):
            await self.review.review(
                principal=self.principal,
                memory_id=conflict.candidate.memory_id,
                decision=MemoryReviewDecision.CONFIRM,
            )

        conflict_audit = InMemoryMemoryConflictAuditRepository()
        resolver = MemoryConflictResolver(
            memory=self.memory,
            audit=conflict_audit,
        )
        resolved = await resolver.resolve(
            principal=self.principal,
            conflict_key="writing-style",
            selected_memory_id=conflict.candidate.memory_id,
            resolution=MemoryConflictResolution.SELECT,
        )

        self.assertIs(resolved.status, MemoryStatus.ACTIVE)
        self.assertEqual(resolved.candidate.content, conflict.candidate.content)
        old_record = await self.repository.get(
            "tenant-a",
            original_record.candidate.memory_id,
        )
        self.assertIs(old_record.status, MemoryStatus.SUPERSEDED)
        decisions = await resolver.history(
            principal=self.principal,
            memory_id=resolved.candidate.memory_id,
        )
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].actor_id, self.principal.user_id)
        self.assertEqual(
            {item.source_ref for item in decisions[0].sources},
            {original.source_ref, turn.source_ref},
        )


if __name__ == "__main__":
    unittest.main()
