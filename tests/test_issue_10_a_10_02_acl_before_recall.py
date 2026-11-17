from __future__ import annotations

import unittest

from agent_platform.knowledge.contracts import Document, KnowledgePrincipal
from agent_platform.knowledge.postgres_repository import PostgresKnowledgeRepository
from agent_platform.knowledge.rag import RAGService
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService


class FakeCursor:
    async def fetchall(self):
        return []


class FakeConnection:
    def __init__(self):
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, query, params=()):
        self.calls.append((query, tuple(params)))
        return FakeCursor()


class Issue10A1002AclBeforeRecallTests(unittest.IsolatedAsyncioTestCase):
    async def test_postgres_lexical_and_vector_queries_apply_tenant_document_and_chunk_acl_in_where(self):
        connection = FakeConnection()

        async def connect():
            return connection

        repository = PostgresKnowledgeRepository(connect)
        principal = KnowledgePrincipal(
            tenant_id="tenant-allowed",
            user_id="user-allowed",
            roles=frozenset({"role-allowed"}),
            project_ids=frozenset({"project-allowed"}),
        )

        hits = await repository.search(
            principal=principal,
            query="restricted evidence",
            query_vector=[0.0] * 1536,
            limit=8,
        )

        self.assertEqual(hits, [])
        self.assertEqual(len(connection.calls), 2)
        for query, params in connection.calls:
            normalized = " ".join(query.split())
            where = normalized.split(" WHERE ", 1)[1].split(" ORDER BY ", 1)[0]
            self.assertIn("d.tenant_id=%s", where)
            self.assertIn("d.deleted_at IS NULL", where)
            self.assertIn("knowledge_document_acl", where)
            self.assertIn("knowledge_chunk_acl", where)
            self.assertIn("c.acl_enforced", where)
            self.assertIn("ca.tenant_id=c.tenant_id", where)
            self.assertIn("d.version=(SELECT max(dv.version)", where)
            self.assertEqual(params.count("tenant-allowed"), 1)
            self.assertIn("user-allowed", params)
            self.assertIn("role-allowed", params)
            self.assertIn("project-allowed", params)
            self.assertLess(normalized.index(" WHERE "), normalized.index(" ORDER BY "))

    async def test_denied_document_never_reaches_rag_generator(self):
        repository = InMemoryKnowledgeRepository()
        service = KnowledgeService(repository)
        document = Document(
            tenant_id="tenant-a",
            title="restricted",
            source_uri="private://restricted",
            content_hash="pending",
        )
        await service.ingest(document=document, content=b"secret evidence must not escape")
        manager = KnowledgePrincipal(
            tenant_id="tenant-a",
            user_id="owner",
            permissions=frozenset({"knowledge.manage"}),
        )
        reader = KnowledgePrincipal(tenant_id="tenant-a", user_id="reader")
        outsider = KnowledgePrincipal(tenant_id="tenant-a", user_id="outsider")
        await service.grant_document_access(
            principal=manager,
            document_id=document.document_id,
            principal_type="user",
            principal_id=reader.user_id,
        )

        class Generator:
            def __init__(self):
                self.contexts = []

            async def generate(self, *, query, context):
                self.contexts.append(context)
                return {"answer": "should not be called", "citation_ids": []}

        generator = Generator()
        rag = RAGService(knowledge=service, generator=generator)
        denied = await rag.answer(principal=outsider, query="secret evidence")
        allowed = await rag.answer(principal=reader, query="secret evidence")

        self.assertTrue(denied.abstained)
        self.assertEqual(denied.reason, "no_authorized_evidence")
        self.assertEqual(len(denied.retrieved), 0)
        self.assertEqual(len(generator.contexts), 1)
        self.assertEqual(len(generator.contexts[0]), 1)
        self.assertIn("secret evidence", generator.contexts[0][0]["text"])
        self.assertEqual([hit.document_id for hit in allowed.retrieved], [document.document_id])

    async def test_other_tenant_never_receives_matching_document_text(self):
        repository = InMemoryKnowledgeRepository()
        service = KnowledgeService(repository)
        await service.ingest(
            document=Document(
                tenant_id="tenant-secret",
                title="tenant secret",
                source_uri="private://tenant-secret",
                content_hash="pending",
            ),
            content=b"cross tenant secret evidence",
        )

        hits = await service.retrieve(
            principal=KnowledgePrincipal(tenant_id="tenant-other", user_id="user-other"),
            query="cross tenant secret",
        )
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
