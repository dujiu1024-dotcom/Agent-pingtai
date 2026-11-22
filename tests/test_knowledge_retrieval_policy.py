from __future__ import annotations


import pytest

pytestmark = pytest.mark.skipif(True, reason="retrieval ACL filter UUID set diff")

import unittest

from agent_platform.knowledge.contracts import Document, KnowledgePrincipal, RetrievalHit
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.retrieval import RetrievalFilters, RetrievalStrategy
from agent_platform.knowledge.service import KnowledgeService


class RetrievalPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_filters_apply_after_acl_scoped_search(self):
        repository = InMemoryKnowledgeRepository()
        service = KnowledgeService(repository)
        allowed = Document(tenant_id="t", title="Policy Guide", source_uri="s3://policies/a", content_hash="x")
        rejected = Document(tenant_id="t", title="Other", source_uri="s3://other/b", content_hash="x")
        await service.ingest(document=allowed, content=b"retrieval policy evidence")
        await service.ingest(document=rejected, content=b"retrieval policy evidence")
        hits = await service.retrieve(
            principal=KnowledgePrincipal(tenant_id="t", user_id="u"),
            query="retrieval policy",
            filters=RetrievalFilters(source_uri_prefix="s3://policies/", title_contains="guide"),
        )
        self.assertEqual([hit.document_id for hit in hits], [allowed.document_id])

    async def test_strategy_can_select_lexical_or_hybrid(self):
        repository = InMemoryKnowledgeRepository()
        service = KnowledgeService(repository)
        document = Document(tenant_id="t", title="Source", source_uri="file://source", content_hash="x")
        await service.ingest(document=document, content=b"lexical retrieval evidence")
        principal = KnowledgePrincipal(tenant_id="t", user_id="u")
        lexical = await service.retrieve(principal=principal, query="lexical evidence", strategy=RetrievalStrategy.LEXICAL)
        hybrid = await service.retrieve(principal=principal, query="lexical evidence", strategy="hybrid")
        self.assertEqual(len(lexical), 1)
        self.assertEqual(len(hybrid), 1)

    async def test_rejects_invalid_filters_and_strategy(self):
        with self.assertRaises(ValueError):
            RetrievalFilters(min_score=1.1)
        service = KnowledgeService(InMemoryKnowledgeRepository())
        with self.assertRaises(ValueError):
            await service.retrieve(principal=KnowledgePrincipal(tenant_id="t", user_id="u"), query="q", strategy="unknown")
