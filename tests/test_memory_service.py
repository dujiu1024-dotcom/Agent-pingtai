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

    async def test_correct_export_and_expiry(self):
        from datetime import datetime, timedelta, timezone
        service=MemoryService(InMemoryMemoryRepository())
        expiry=datetime.now(timezone.utc)+timedelta(days=1)
        candidate=MemoryCandidate(tenant_id="t",scope=MemoryScope.USER,scope_id="u",content="old",source_ref="chat:1",confidence=.7,created_by="extractor",expires_at=expiry)
        await service.propose(candidate)
        await service.confirm(tenant_id="t",memory_id=candidate.memory_id,confirmer_id="u")
        principal=MemoryPrincipal(tenant_id="t",user_id="u")
        corrected=await service.correct(principal=principal,memory_id=candidate.memory_id,content="new",source_ref="chat:2")
        self.assertEqual(corrected.candidate.content,"new")
        exported=await service.export(principal=principal)
        self.assertEqual(exported[0]["source_ref"],"chat:2")
        expired=await service.expire_due(tenant_id="t",now=expiry+timedelta(seconds=1))
        self.assertEqual(expired,1)
        self.assertEqual(await service.retrieve(principal=principal),[])

    async def test_conflict_resolution_and_archive(self):
        from agent_platform.memory.service import MemoryStatus
        service=MemoryService(InMemoryMemoryRepository())
        first=MemoryCandidate(tenant_id="t",scope=MemoryScope.TENANT,scope_id="t",content="old policy",source_ref="policy:1",confidence=.9,created_by="admin",conflict_key="policy.version")
        await service.propose(first)
        await service.confirm(tenant_id="t",memory_id=first.memory_id,confirmer_id="admin")
        second=MemoryCandidate(tenant_id="t",scope=MemoryScope.TENANT,scope_id="t",content="new policy",source_ref="policy:2",confidence=.95,created_by="admin",conflict_key="policy.version")
        proposed=await service.propose(second)
        self.assertEqual(proposed.status,MemoryStatus.CONFLICT)
        admin=MemoryPrincipal(tenant_id="t",user_id="admin",roles=frozenset({"memory.manage"}))
        selected=await service.resolve_conflict(principal=admin,conflict_key="policy.version",selected_memory_id=second.memory_id)
        self.assertEqual(selected.status,MemoryStatus.ACTIVE)
        self.assertEqual(await service.retrieve(principal=admin),[selected])
        self.assertTrue(await service.archive(principal=admin,memory_id=second.memory_id))
        self.assertEqual(await service.retrieve(principal=admin),[])

    async def test_extraction_is_single_turn_provenance_bound_and_candidate_only(self):
        from agent_platform.memory.extraction import MemoryDraft,MemoryExtractionService,MemoryTurn
        class Extractor:
            async def extract(self,*,turn):
                self.received=turn
                return [MemoryDraft(scope=MemoryScope.USER,scope_id=turn.user_id,content="prefers concise answers",confidence=.91)]
        repo=InMemoryMemoryRepository()
        service=MemoryService(repo)
        extractor=Extractor()
        pipeline=MemoryExtractionService(memory=service,extractor=extractor)
        principal=MemoryPrincipal(tenant_id="t",user_id="u")
        turn=MemoryTurn(tenant_id="t",user_id="u",source_ref="conversation:turn-9",user_message="Please keep it concise.")
        records=await pipeline.propose_from_turn(principal=principal,turn=turn)
        self.assertEqual(len(records),1)
        self.assertEqual(records[0].status.value,"candidate")
        self.assertEqual(records[0].candidate.source_ref,"conversation:turn-9")
        self.assertEqual(extractor.received,turn)
        self.assertEqual(await service.retrieve(principal=principal),[])
        with self.assertRaises(PermissionError):
            await pipeline.propose_from_turn(
                principal=principal,
                turn=MemoryTurn(tenant_id="other",user_id="u",source_ref="conversation:other",user_message="x"),
            )

    async def test_extraction_cannot_write_unapproved_tenant_scope(self):
        from agent_platform.memory.extraction import MemoryDraft,MemoryExtractionService,MemoryTurn
        class TenantExtractor:
            async def extract(self,*,turn):
                return [MemoryDraft(scope=MemoryScope.TENANT,scope_id=turn.tenant_id,content="shared policy",confidence=.9)]
        pipeline=MemoryExtractionService(memory=MemoryService(InMemoryMemoryRepository()),extractor=TenantExtractor())
        with self.assertRaises(PermissionError):
            await pipeline.propose_from_turn(
                principal=MemoryPrincipal(tenant_id="t",user_id="u"),
                turn=MemoryTurn(tenant_id="t",user_id="u",source_ref="conversation:1",user_message="policy"),
            )
