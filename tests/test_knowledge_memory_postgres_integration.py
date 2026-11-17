"""PostgreSQL integration tests for tenant-scoped Knowledge and Memory services."""
from __future__ import annotations

import os
import unittest
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

DSN=os.environ.get("AGENT_PLATFORM_TEST_DSN")
if DSN:
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    from agent_platform.knowledge.contracts import (
        Document,
        DocumentChunk,
        KnowledgePrincipal,
        KnowledgeScope,
    )
    from agent_platform.knowledge.postgres_repository import PostgresKnowledgeRepository
    from agent_platform.memory.postgres_repository import PostgresMemoryRepository
    from agent_platform.memory.service import (
        MemoryCandidate,
        MemoryPrincipal,
        MemoryScope,
        MemoryService,
        MemoryStatus,
    )
@unittest.skipUnless(DSN,"AGENT_PLATFORM_TEST_DSN is not configured")
class KnowledgeMemoryPostgresIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant_id="km-"+uuid4().hex
        async with await AsyncConnection.connect(DSN,autocommit=True) as conn:
            for path in sorted(Path("agent_platform/storage/postgres").glob("*.sql")):
                await conn.execute(path.read_text(),prepare=False)
        async with await AsyncConnection.connect(DSN) as conn:
            await conn.execute("INSERT INTO platform_tenants(tenant_id,display_name) VALUES(%s,%s)",(self.tenant_id,"Knowledge Memory IT"))
            await conn.commit()
        async def connect():
            return await AsyncConnection.connect(DSN,row_factory=dict_row)
        self.connect=connect
        self.knowledge=PostgresKnowledgeRepository(connect)
        self.memory=MemoryService(PostgresMemoryRepository(connect))
    async def test_knowledge_retrieval_filters_private_and_explicit_acl_in_sql(self):
        private=Document(tenant_id=self.tenant_id,title="private source",source_uri="file://private",content_hash="h",scope=KnowledgeScope.PRIVATE,owner_user_id="owner")
        chunk=DocumentChunk(tenant_id=self.tenant_id,document_id=private.document_id,document_version=1,ordinal=0,text="restricted alpha evidence",content_hash="c",start_offset=0,end_offset=26)
        await self.knowledge.save_document(private,[chunk])
        owner=KnowledgePrincipal(tenant_id=self.tenant_id,user_id="owner")
        stranger=KnowledgePrincipal(tenant_id=self.tenant_id,user_id="stranger")
        self.assertEqual(len(await self.knowledge.search(principal=owner,query="alpha",query_vector=None,limit=5)),1)
        self.assertEqual(await self.knowledge.search(principal=stranger,query="alpha",query_vector=None,limit=5),[])
    async def test_explicit_document_acl_restricts_default_tenant_visibility(self):
        doc=Document(tenant_id=self.tenant_id,title="acl source",source_uri="file://acl",content_hash="h")
        chunk=DocumentChunk(tenant_id=self.tenant_id,document_id=doc.document_id,document_version=1,ordinal=0,text="classified beta record",content_hash="c",start_offset=0,end_offset=22)
        await self.knowledge.save_document(doc,[chunk])
        async with await self.connect() as conn:
            await conn.execute("INSERT INTO knowledge_document_acl(tenant_id,document_id,principal_type,principal_id,permission,granted_by) VALUES(%s,%s,'user','allowed','read','admin')",(self.tenant_id,doc.document_id))
            await conn.execute("INSERT INTO knowledge_document_acl(tenant_id,document_id,principal_type,principal_id,permission,granted_by) VALUES(%s,%s,'role','reader','read','admin')",(self.tenant_id,doc.document_id))
            await conn.commit()
        denied=await self.knowledge.search(principal=KnowledgePrincipal(tenant_id=self.tenant_id,user_id="denied"),query="beta",query_vector=None,limit=5)
        allowed=await self.knowledge.search(principal=KnowledgePrincipal(tenant_id=self.tenant_id,user_id="allowed"),query="beta",query_vector=None,limit=5)
        role_allowed=await self.knowledge.search(principal=KnowledgePrincipal(tenant_id=self.tenant_id,user_id="role-user",roles=frozenset({"reader"})),query="beta",query_vector=None,limit=5)
        self.assertEqual(denied,[])
        self.assertEqual(len(allowed),1)
        self.assertEqual(len(role_allowed),1)

    async def test_acl_grant_and_revoke_is_enforced_in_postgres_search(self):
        document = Document(
            tenant_id=self.tenant_id, title="private ACL source",
            source_uri="file://private-acl", content_hash="pending",
            scope=KnowledgeScope.PRIVATE, owner_user_id="owner",
        )
        chunk = DocumentChunk(
            tenant_id=self.tenant_id, document_id=document.document_id,
            document_version=1, ordinal=0, text="private retention alpha evidence",
            content_hash="chunk-acl", start_offset=0, end_offset=32,
        )
        await self.knowledge.save_document(document, [chunk])
        owner = KnowledgePrincipal(tenant_id=self.tenant_id, user_id="owner")
        reader = KnowledgePrincipal(tenant_id=self.tenant_id, user_id="reader")
        outsider = KnowledgePrincipal(tenant_id=self.tenant_id, user_id="outsider")

        self.assertEqual(
            len(await self.knowledge.search(
                principal=owner, query="retention", query_vector=None, limit=5,
            )), 1,
        )
        await self.knowledge.grant_document_access(
            self.tenant_id, document.document_id, "user", "reader", "read", "owner",
        )
        self.assertEqual(
            len(await self.knowledge.search(
                principal=reader, query="retention", query_vector=None, limit=5,
            )), 1,
        )
        self.assertEqual(
            await self.knowledge.search(
                principal=outsider, query="retention", query_vector=None, limit=5,
            ), [],
        )
        self.assertTrue(await self.knowledge.revoke_document_access(
            self.tenant_id, document.document_id, "user", "reader", "read",
        ))
        self.assertEqual(
            await self.knowledge.search(
                principal=reader, query="retention", query_vector=None, limit=5,
            ), [],
        )
        self.assertEqual(
            len(await self.knowledge.search(
                principal=owner, query="retention", query_vector=None, limit=5,
            )), 1,
        )

    async def test_document_deletion_removes_chunks_and_acl(self):
        doc=Document(tenant_id=self.tenant_id,title="delete source",source_uri="file://delete",content_hash="h")
        chunk=DocumentChunk(tenant_id=self.tenant_id,document_id=doc.document_id,document_version=1,ordinal=0,text="gamma document",content_hash="c",start_offset=0,end_offset=15)
        await self.knowledge.save_document(doc,[chunk])
        await self.knowledge.save_embeddings(
            tenant_id=self.tenant_id, chunks=[chunk],
            vectors=[[1.0] + [0.0] * 1535],
        )
        # A document can have multiple immutable versions. Deletion must
        # tombstone every version and remove all derived chunks/vectors.
        from dataclasses import replace
        newer = replace(doc, version=2, title="delete source v2")
        newer_chunk = DocumentChunk(
            tenant_id=self.tenant_id, document_id=doc.document_id,
            document_version=2, ordinal=0, text="gamma document revised",
            content_hash="c2", start_offset=0, end_offset=23,
        )
        await self.knowledge.save_document(newer, [newer_chunk])
        await self.knowledge.save_embeddings(
            tenant_id=self.tenant_id, chunks=[newer_chunk],
            vectors=[[0.0, 1.0] + [0.0] * 1534],
        )
        async with await self.connect() as conn:
            await conn.execute(
                "INSERT INTO knowledge_document_acl(tenant_id,document_id,principal_type,principal_id,permission,granted_by) VALUES(%s,%s,'user','reader','read','admin')",
                (self.tenant_id, doc.document_id),
            )
            await conn.commit()
        await self.knowledge.grant_chunk_access(
            self.tenant_id, doc.document_id, newer_chunk.chunk_id,
            "user", "reader", "read", "owner",
        )
        self.assertTrue(await self.knowledge.delete_document(self.tenant_id,doc.document_id))
        self.assertEqual(await self.knowledge.search(principal=KnowledgePrincipal(tenant_id=self.tenant_id,user_id="u"),query="gamma",query_vector=None,limit=5),[])
        async with await self.connect() as conn:
            cur=await conn.execute("SELECT count(*) n FROM knowledge_chunks WHERE tenant_id=%s AND document_id=%s",(self.tenant_id,doc.document_id))
            self.assertEqual((await cur.fetchone())["n"],0)
            cur=await conn.execute("SELECT count(*) n FROM knowledge_embeddings WHERE tenant_id=%s AND chunk_id IN (%s,%s)",(self.tenant_id,chunk.chunk_id,newer_chunk.chunk_id))
            self.assertEqual((await cur.fetchone())["n"],0)
            cur=await conn.execute(
                "SELECT count(*) n FROM knowledge_document_acl WHERE tenant_id=%s AND document_id=%s",
                (self.tenant_id, doc.document_id),
            )
            self.assertEqual((await cur.fetchone())["n"],0)
            cur=await conn.execute(
                "SELECT count(*) n FROM knowledge_chunk_acl WHERE tenant_id=%s AND chunk_id=%s",
                (self.tenant_id, newer_chunk.chunk_id),
            )
            self.assertEqual((await cur.fetchone())["n"],0)
    async def test_chunk_acl_is_filtered_before_retrieval_and_emits_cache_invalidation(self):
        document = Document(
            tenant_id=self.tenant_id, title="mixed access source",
            source_uri="https://example.test/source", content_hash="source-hash",
        )
        allowed_chunk = DocumentChunk(
            tenant_id=self.tenant_id, document_id=document.document_id,
            document_version=1, ordinal=0, text="public context marker",
            content_hash="public", start_offset=0, end_offset=21,
        )
        restricted_chunk = DocumentChunk(
            tenant_id=self.tenant_id, document_id=document.document_id,
            document_version=1, ordinal=1, text="secret needle marker",
            content_hash="secret", start_offset=22, end_offset=42,
        )
        await self.knowledge.save_document(document, [allowed_chunk, restricted_chunk])
        await self.knowledge.grant_chunk_access(
            self.tenant_id, document.document_id, restricted_chunk.chunk_id,
            "user", "permitted", "read", "owner",
        )
        denied = await self.knowledge.search(
            principal=KnowledgePrincipal(tenant_id=self.tenant_id, user_id="denied"),
            query="needle", query_vector=None, limit=5,
        )
        permitted = await self.knowledge.search(
            principal=KnowledgePrincipal(tenant_id=self.tenant_id, user_id="permitted"),
            query="needle", query_vector=None, limit=5,
        )
        self.assertEqual(denied, [])
        self.assertEqual(len(permitted), 1)
        self.assertEqual(
            await self.knowledge.list_visible_chunks(
                KnowledgePrincipal(tenant_id=self.tenant_id, user_id="denied"),
                document.document_id, 1,
            ), [],
        )
        visible_chunks = await self.knowledge.list_visible_chunks(
            KnowledgePrincipal(tenant_id=self.tenant_id, user_id="permitted"),
            document.document_id, 1,
        )
        self.assertEqual([item.chunk_id for item in visible_chunks], [restricted_chunk.chunk_id])
        citation = permitted[0].retrieval_trace["citation"]
        self.assertEqual(citation["document_id"], document.document_id)
        self.assertEqual(citation["chunk_id"], restricted_chunk.chunk_id)
        self.assertEqual(citation["source_uri"], document.source_uri)
        self.assertEqual(citation["start_offset"], 22)
        async with await self.connect() as conn:
            cur = await conn.execute(
                "SELECT reason FROM knowledge_cache_invalidations WHERE tenant_id=%s AND resource_type='chunk' AND resource_id=%s ORDER BY event_id DESC LIMIT 1",
                (self.tenant_id, restricted_chunk.chunk_id),
            )
            self.assertEqual((await cur.fetchone())["reason"], "acl_changed")
        self.assertTrue(await self.knowledge.revoke_chunk_access(
            self.tenant_id, restricted_chunk.chunk_id, "user", "permitted", "read",
        ))
        self.assertEqual(await self.knowledge.search(
            principal=KnowledgePrincipal(tenant_id=self.tenant_id, user_id="permitted"),
            query="needle", query_vector=None, limit=5,
        ), [])

    async def test_memory_candidate_confirmation_correction_export_expiry_and_delete(self):
        candidate=MemoryCandidate(tenant_id=self.tenant_id,scope=MemoryScope.USER,scope_id="user-1",content="old preference",source_ref="conversation:1",confidence=.8,created_by="extractor")
        await self.memory.propose(candidate)
        principal=MemoryPrincipal(tenant_id=self.tenant_id,user_id="user-1")
        self.assertEqual(await self.memory.retrieve(principal=principal),[])
        async with await self.connect() as conn:
            cur=await conn.execute("SELECT count(*) n FROM platform_memory_versions WHERE tenant_id=%s AND memory_id=%s",(self.tenant_id,candidate.memory_id))
            self.assertEqual((await cur.fetchone())["n"],1)
        await self.memory.confirm(principal=principal,memory_id=candidate.memory_id)
        async with await self.connect() as conn:
            cur=await conn.execute("SELECT count(*) n FROM platform_memory_versions WHERE tenant_id=%s AND memory_id=%s",(self.tenant_id,candidate.memory_id))
            self.assertEqual((await cur.fetchone())["n"],2)
        updated=await self.memory.correct(principal=principal,memory_id=candidate.memory_id,content="corrected preference",source_ref="conversation:2")
        async with await self.connect() as conn:
            cur=await conn.execute("SELECT count(*) n FROM platform_memory_versions WHERE tenant_id=%s AND memory_id=%s",(self.tenant_id,candidate.memory_id))
            self.assertEqual((await cur.fetchone())["n"],3)
        self.assertEqual(updated.version,3)
        self.assertEqual((await self.memory.export(principal=principal))[0]["content"],"corrected preference")
        self.assertTrue(await self.memory.forget(principal=principal,memory_id=candidate.memory_id))
        async with await self.connect() as conn:
            cur=await conn.execute("SELECT count(*) n FROM platform_memory_versions WHERE tenant_id=%s AND memory_id=%s",(self.tenant_id,candidate.memory_id))
            self.assertEqual((await cur.fetchone())["n"],4)
        self.assertEqual(await self.memory.retrieve(principal=principal),[])

    async def test_cross_tenant_document_and_memory_ids_are_not_visible_or_mutable(self):
        other_tenant = self.tenant_id + "-other"
        async with await self.connect() as conn:
            await conn.execute(
                "INSERT INTO platform_tenants(tenant_id,display_name) VALUES(%s,%s)",
                (other_tenant, "Other Tenant"),
            )
            await conn.commit()

        document = Document(
            tenant_id=self.tenant_id,
            title="tenant-private evidence",
            source_uri="file://tenant-a",
            content_hash="tenant-a-hash",
            scope=KnowledgeScope.TENANT,
        )
        chunk = DocumentChunk(
            tenant_id=self.tenant_id,
            document_id=document.document_id,
            document_version=1,
            ordinal=0,
            text="tenant-a-only confidential marker",
            content_hash="chunk-a",
            start_offset=0,
            end_offset=34,
        )
        await self.knowledge.save_document(document, [chunk])
        query_vector = [1.0] + [0.0] * 1535
        await self.knowledge.save_embeddings(
            tenant_id=self.tenant_id, chunks=[chunk], vectors=[query_vector],
        )

        other_principal = KnowledgePrincipal(
            tenant_id=other_tenant, user_id="other-user",
        )
        self.assertIsNone(await self.knowledge.get_document(
            other_tenant, document.document_id,
        ))
        self.assertEqual(await self.knowledge.search(
            principal=other_principal, query="confidential",
            query_vector=None, limit=5,
        ), [])
        self.assertEqual(await self.knowledge.search(
            principal=other_principal, query="no lexical match",
            query_vector=query_vector, limit=5,
        ), [])
        self.assertEqual(len(await self.knowledge.search(
            principal=KnowledgePrincipal(tenant_id=self.tenant_id, user_id="owner"),
            query="no lexical match", query_vector=query_vector, limit=5,
        )), 1)
        self.assertFalse(await self.knowledge.delete_document(
            other_tenant, document.document_id,
        ))
        self.assertIsNotNone(await self.knowledge.get_document(
            self.tenant_id, document.document_id,
        ))

        candidate = MemoryCandidate(
            tenant_id=self.tenant_id,
            scope=MemoryScope.USER,
            scope_id="owner-a",
            content="tenant-a memory",
            source_ref="conversation:tenant-a",
            confidence=0.9,
            created_by="owner-a",
        )
        await self.memory.propose(candidate)
        self.assertIsNone(await self.memory.repository.get(
            other_tenant, candidate.memory_id,
        ))
        self.assertEqual(await self.memory.repository.list(other_tenant), [])
        self.assertFalse(await self.memory.forget(
            principal=MemoryPrincipal(tenant_id=other_tenant, user_id="owner-a"),
            memory_id=candidate.memory_id,
        ))
        self.assertIsNotNone(await self.memory.repository.get(
            self.tenant_id, candidate.memory_id,
        ))

    async def test_postgres_memory_expiry_cleanup_is_retryable(self):
        from datetime import datetime, timedelta, timezone

        attempts = []
        async def cleanup(tenant_id, memory_id):
            attempts.append((tenant_id, memory_id))
            if len(attempts) == 1:
                raise RuntimeError("derived index unavailable")

        service = MemoryService(
            PostgresMemoryRepository(self.connect), on_delete=cleanup,
        )
        candidate = MemoryCandidate(
            tenant_id=self.tenant_id, scope=MemoryScope.USER, scope_id="expiry-user",
            content="temporary memory", source_ref="conversation:expiry",
            confidence=0.9, created_by="extractor",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        await service.propose(candidate)
        await service.confirm(
            principal=MemoryPrincipal(tenant_id=self.tenant_id, user_id="expiry-user"),
            memory_id=candidate.memory_id,
        )
        with self.assertRaisesRegex(RuntimeError, "derived index unavailable"):
            await service.expire_due(tenant_id=self.tenant_id)
        expired = await service.repository.get(self.tenant_id, candidate.memory_id)
        self.assertEqual(expired.status, MemoryStatus.EXPIRED)
        self.assertEqual(await service.expire_due(tenant_id=self.tenant_id), 0)
        self.assertEqual(attempts, [
            (self.tenant_id, candidate.memory_id),
            (self.tenant_id, candidate.memory_id),
        ])
        async with await self.connect() as conn:
            cur = await conn.execute(
                "SELECT count(*) n FROM platform_memory_versions WHERE tenant_id=%s AND memory_id=%s",
                (self.tenant_id, candidate.memory_id),
            )
            self.assertEqual((await cur.fetchone())["n"], 3)

    async def test_connector_cursor_is_durable_and_tenant_scoped(self):
        await self.knowledge.save_connector_cursor(self.tenant_id,"fixture","cursor-1")
        self.assertEqual(await self.knowledge.get_connector_cursor(self.tenant_id,"fixture"),"cursor-1")
        self.assertIsNone(await self.knowledge.get_connector_cursor(self.tenant_id+"-other","fixture"))
        await self.knowledge.save_connector_cursor(self.tenant_id,"fixture","cursor-2")
        self.assertEqual(await self.knowledge.get_connector_cursor(self.tenant_id,"fixture"),"cursor-2")


    async def test_memory_retrieval_returns_historical_version_with_source_citation(self):
        from agent_platform.memory.retrieval import (
            MemoryRetrievalQuery,
            MemoryRetrievalService,
        )

        candidate = MemoryCandidate(
            tenant_id=self.tenant_id, scope=MemoryScope.USER, scope_id="history-user",
            content="original memory fact", source_ref="conversation:original",
            confidence=0.8, created_by="extractor",
        )
        await self.memory.propose(candidate)
        principal = MemoryPrincipal(tenant_id=self.tenant_id, user_id="history-user")
        await self.memory.confirm(
            tenant_id=self.tenant_id, memory_id=candidate.memory_id,
            confirmer_id="history-user",
        )
        original = await self.memory.repository.get(self.tenant_id, candidate.memory_id)
        await self.memory.correct(
            principal=principal, memory_id=candidate.memory_id,
            content="corrected memory fact", source_ref="manual:correction",
        )
        retrieval = MemoryRetrievalService(self.memory)
        historical = await retrieval.search(
            principal=principal,
            query=MemoryRetrievalQuery(as_of=original.updated_at + timedelta(microseconds=1)),
        )
        current = await retrieval.search(principal=principal)
        self.assertEqual(historical[0].citation.source_ref, "conversation:original")
        self.assertEqual(historical[0].citation.version, original.version)
        self.assertEqual(current[0].citation.source_ref, "manual:correction")
