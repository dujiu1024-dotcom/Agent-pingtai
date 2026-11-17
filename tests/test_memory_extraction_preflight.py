from __future__ import annotations

import unittest

from agent_platform.memory.extraction import (
    MAX_CANDIDATE_CONTENT_CHARS,
    MemoryDraft,
    MemoryExtractionService,
    MemoryTurn,
)
from agent_platform.memory.service import (
    InMemoryMemoryRepository,
    MemoryPrincipal,
    MemoryScope,
    MemoryService,
)


class StaticExtractor:
    def __init__(self, drafts):
        self.drafts = drafts

    async def extract(self, *, turn):
        return self.drafts


class MemoryExtractionPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = InMemoryMemoryRepository()
        self.memory = MemoryService(self.repository)
        self.principal = MemoryPrincipal(tenant_id="tenant-a", user_id="user-a")
        self.turn = MemoryTurn(
            tenant_id="tenant-a",
            user_id="user-a",
            source_ref="session://tenant-a/session-a/turn-1",
            user_message="Remember my preference",
        )

    async def test_invalid_later_draft_does_not_persist_earlier_candidate(self):
        extractor = StaticExtractor([
            MemoryDraft(
                scope=MemoryScope.USER,
                scope_id="user-a",
                content="valid first candidate",
                confidence=0.9,
            ),
            MemoryDraft(
                scope=MemoryScope.USER,
                scope_id="user-a",
                content="x" * (MAX_CANDIDATE_CONTENT_CHARS + 1),
                confidence=0.9,
            ),
        ])
        workflow = MemoryExtractionService(memory=self.memory, extractor=extractor)

        with self.assertRaisesRegex(ValueError, "size limit"):
            await workflow.propose_from_turn(principal=self.principal, turn=self.turn)

        self.assertEqual(await self.repository.list("tenant-a"), [])

    async def test_unauthorized_later_draft_does_not_persist_earlier_candidate(self):
        extractor = StaticExtractor([
            MemoryDraft(
                scope=MemoryScope.USER,
                scope_id="user-a",
                content="valid first candidate",
                confidence=0.9,
            ),
            MemoryDraft(
                scope=MemoryScope.TENANT,
                scope_id="tenant-a",
                content="tenant-wide policy",
                confidence=0.9,
            ),
        ])
        workflow = MemoryExtractionService(memory=self.memory, extractor=extractor)

        with self.assertRaises(PermissionError):
            await workflow.propose_from_turn(principal=self.principal, turn=self.turn)

        self.assertEqual(await self.repository.list("tenant-a"), [])

    async def test_valid_batch_is_persisted_only_as_candidates(self):
        extractor = StaticExtractor([
            MemoryDraft(
                scope=MemoryScope.USER,
                scope_id="user-a",
                content="prefers concise answers",
                confidence=0.9,
            ),
            MemoryDraft(
                scope=MemoryScope.USER,
                scope_id="user-a",
                content="prefers examples",
                confidence=0.8,
            ),
        ])
        workflow = MemoryExtractionService(memory=self.memory, extractor=extractor)

        records = await workflow.propose_from_turn(
            principal=self.principal, turn=self.turn,
        )

        self.assertEqual(len(records), 2)
        self.assertTrue(all(record.status.value == "candidate" for record in records))
        self.assertEqual(len(await self.repository.list("tenant-a")), 2)


if __name__ == "__main__":
    unittest.main()
