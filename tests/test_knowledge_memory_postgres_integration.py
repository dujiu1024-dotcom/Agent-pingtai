"""PostgreSQL integration tests for tenant-scoped Knowledge and Memory services."""
from __future__ import annotations
import os
import unittest
from pathlib import Path
from uuid import uuid4
DSN=os.environ.get("AGENT_PLATFORM_TEST_DSN")
if DSN:
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from agent_platform.knowledge.contracts import Document,DocumentChunk,KnowledgePrincipal,KnowledgeScope
    from agent_platform.knowledge.postgres_repository import PostgresKnowledgeRepository
    from agent_platform.memory.postgres_repository import PostgresMemoryRepository
    from agent_platform.memory.service import MemoryCandidate,MemoryPrincipal,MemoryScope,MemoryService
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
            await conn.commit()
        denied=await self.knowledge.search(principal=KnowledgePrincipal(tenant_id=self.tenant_id,user_id="denied"),query="beta",query_vector=None,limit=5)
        allowed=await self.knowledge.search(principal=KnowledgePrincipal(tenant_id=self.tenant_id,user_id="allowed"),query="beta",query_vector=None,limit=5)
        self.assertEqual(denied,[])
        self.assertEqual(len(allowed),1)
    async def test_document_deletion_removes_chunks_and_acl(self):
        doc=Document(tenant_id=self.tenant_id,title="delete source",source_uri="file://delete",content_hash="h")
        chunk=DocumentChunk(tenant_id=self.tenant_id,document_id=doc.document_id,document_version=1,ordinal=0,text="gamma document",content_hash="c",start_offset=0,end_offset=15)
        await self.knowledge.save_document(doc,[chunk])
        self.assertTrue(await self.knowledge.delete_document(self.tenant_id,doc.document_id))
        self.assertEqual(await self.knowledge.search(principal=KnowledgePrincipal(tenant_id=self.tenant_id,user_id="u"),query="gamma",query_vector=None,limit=5),[])
        async with await self.connect() as conn:
            cur=await conn.execute("SELECT count(*) n FROM knowledge_chunks WHERE tenant_id=%s AND document_id=%s",(self.tenant_id,doc.document_id))
            self.assertEqual((await cur.fetchone())["n"],0)
    async def test_memory_candidate_confirmation_correction_export_expiry_and_delete(self):
        candidate=MemoryCandidate(tenant_id=self.tenant_id,scope=MemoryScope.USER,scope_id="user-1",content="old preference",source_ref="conversation:1",confidence=.8,created_by="extractor")
        await self.memory.propose(candidate)
        principal=MemoryPrincipal(tenant_id=self.tenant_id,user_id="user-1")
        self.assertEqual(await self.memory.retrieve(principal=principal),[])
        async with await self.connect() as conn:
            cur=await conn.execute("SELECT count(*) n FROM platform_memory_versions WHERE tenant_id=%s AND memory_id=%s",(self.tenant_id,candidate.memory_id))
            self.assertEqual((await cur.fetchone())["n"],4)
        await self.memory.confirm(tenant_id=self.tenant_id,memory_id=candidate.memory_id,confirmer_id="user-1")
        updated=await self.memory.correct(principal=principal,memory_id=candidate.memory_id,content="corrected preference",source_ref="conversation:2")
        self.assertEqual(updated.version,3)
        self.assertEqual((await self.memory.export(principal=principal))[0]["content"],"corrected preference")
        self.assertTrue(await self.memory.forget(principal=principal,memory_id=candidate.memory_id))
        self.assertEqual(await self.memory.retrieve(principal=principal),[])
