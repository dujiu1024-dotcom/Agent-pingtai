from __future__ import annotations
import unittest
from agent_platform.memory.service import InMemoryMemoryRepository,MemoryCandidate,MemoryPrincipal,MemoryScope,MemoryService
class MemoryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_candidate_is_not_retrievable_until_confirmed(self):
        service=MemoryService(InMemoryMemoryRepository()); c=MemoryCandidate(tenant_id="t",scope=MemoryScope.USER,scope_id="u1",content="prefers concise reports",source_ref="conversation:123",confidence=.9,created_by="extractor")
        await service.propose(c); p=MemoryPrincipal(tenant_id="t",user_id="u1"); self.assertEqual(await service.retrieve(principal=p),[])
        await service.confirm(tenant_id="t",memory_id=c.memory_id,confirmer_id="u1"); self.assertEqual(len(await service.retrieve(principal=p)),1)
    async def test_user_scope_isolation_and_forget(self):
        service=MemoryService(InMemoryMemoryRepository()); c=MemoryCandidate(tenant_id="t",scope=MemoryScope.USER,scope_id="u1",content="x",source_ref="chat:1",confidence=.8,created_by="u1")
        await service.propose(c); await service.confirm(tenant_id="t",memory_id=c.memory_id,confirmer_id="u1")
        self.assertEqual(await service.retrieve(principal=MemoryPrincipal(tenant_id="t",user_id="u2")),[])
        self.assertTrue(await service.forget(principal=MemoryPrincipal(tenant_id="t",user_id="u1"),memory_id=c.memory_id))
        self.assertEqual(await service.retrieve(principal=MemoryPrincipal(tenant_id="t",user_id="u1")),[])
    async def test_tenant_boundary(self):
        service=MemoryService(InMemoryMemoryRepository()); c=MemoryCandidate(tenant_id="t1",scope=MemoryScope.TENANT,scope_id="t1",content="policy",source_ref="doc:1",confidence=1,created_by="admin")
        await service.propose(c); await service.confirm(tenant_id="t1",memory_id=c.memory_id,confirmer_id="admin")
        self.assertEqual(await service.retrieve(principal=MemoryPrincipal(tenant_id="t2",user_id="u")),[])
