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
        admin=MemoryPrincipal(tenant_id="t",user_id="admin",permissions=frozenset({"memory.manage"}))
        selected=await service.resolve_conflict(principal=admin,conflict_key="policy.version",selected_memory_id=second.memory_id)
        self.assertEqual(selected.status,MemoryStatus.ACTIVE)
        self.assertEqual(await service.retrieve(principal=admin),[selected])
        self.assertTrue(await service.archive(principal=admin,memory_id=second.memory_id))
        self.assertEqual(await service.retrieve(principal=admin),[])

    async def test_project_memory_management_requires_project_scoped_grant(self):
        service = MemoryService(InMemoryMemoryRepository())
        candidate = MemoryCandidate(
            tenant_id="t", scope=MemoryScope.PROJECT, scope_id="project-a",
            content="project decision", source_ref="project:1",
            confidence=.9, created_by="extractor",
        )
        await service.propose(candidate)
        await service.confirm(
            tenant_id="t", memory_id=candidate.memory_id, confirmer_id="reviewer",
        )
        reader = MemoryPrincipal(
            tenant_id="t", user_id="reader",
            project_ids=frozenset({"project-a"}),
        )
        with self.assertRaises(PermissionError):
            await service.correct(
                principal=reader, memory_id=candidate.memory_id,
                content="unauthorized", source_ref="project:2",
            )
        with self.assertRaises(PermissionError):
            await service.archive(principal=reader, memory_id=candidate.memory_id)
        with self.assertRaises(PermissionError):
            await service.forget(principal=reader, memory_id=candidate.memory_id)

        manager = MemoryPrincipal(
            tenant_id="t", user_id="manager",
            project_ids=frozenset({"project-a"}),
            manage_project_ids=frozenset({"project-a"}),
        )
        corrected = await service.correct(
            principal=manager, memory_id=candidate.memory_id,
            content="approved decision", source_ref="project:3",
        )
        self.assertEqual(corrected.candidate.content, "approved decision")
        self.assertTrue(await service.archive(
            principal=manager, memory_id=candidate.memory_id,
        ))

    async def test_agent_memory_management_requires_agent_scoped_grant(self):
        service = MemoryService(InMemoryMemoryRepository())
        candidate = MemoryCandidate(
            tenant_id="t", scope=MemoryScope.AGENT, scope_id="agent-a",
            content="agent operating rule", source_ref="agent:1",
            confidence=.9, created_by="extractor",
        )
        await service.propose(candidate)
        await service.confirm(
            tenant_id="t", memory_id=candidate.memory_id, confirmer_id="reviewer",
        )
        runner = MemoryPrincipal(
            tenant_id="t", user_id="runner", agent_ids=frozenset({"agent-a"}),
        )
        with self.assertRaises(PermissionError):
            await service.correct(
                principal=runner, memory_id=candidate.memory_id,
                content="unauthorized", source_ref="agent:2",
            )
        manager = MemoryPrincipal(
            tenant_id="t", user_id="manager", agent_ids=frozenset({"agent-a"}),
            manage_agent_ids=frozenset({"agent-a"}),
        )
        corrected = await service.correct(
            principal=manager, memory_id=candidate.memory_id,
            content="approved rule", source_ref="agent:3",
        )
        self.assertEqual(corrected.candidate.content, "approved rule")

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

    async def test_extraction_requires_scoped_manage_grant_for_shared_memory(self):
        from agent_platform.memory.extraction import MemoryDraft, MemoryExtractionService, MemoryTurn

        class ProjectExtractor:
            async def extract(self, *, turn):
                return [MemoryDraft(
                    scope=MemoryScope.PROJECT,
                    scope_id="project-a",
                    content="shared project decision",
                    confidence=.9,
                )]

        pipeline = MemoryExtractionService(
            memory=MemoryService(InMemoryMemoryRepository()),
            extractor=ProjectExtractor(),
        )
        reader = MemoryPrincipal(
            tenant_id="t", user_id="reader",
            project_ids=frozenset({"project-a"}),
        )
        turn = MemoryTurn(
            tenant_id="t", user_id="reader", source_ref="conversation:1",
            user_message="Project decision",
            project_id="project-a",
        )
        with self.assertRaises(PermissionError):
            await pipeline.propose_from_turn(principal=reader, turn=turn)

        manager = MemoryPrincipal(
            tenant_id="t", user_id="manager",
            project_ids=frozenset({"project-a"}),
            manage_project_ids=frozenset({"project-a"}),
        )
        manager_turn = MemoryTurn(
            tenant_id="t", user_id="manager", source_ref="conversation:2",
            user_message="Project decision",
            project_id="project-a",
        )
        records = await pipeline.propose_from_turn(principal=manager, turn=manager_turn)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].candidate.scope, MemoryScope.PROJECT)

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

    async def test_conflict_resolution_does_not_cross_memory_scope_ids(self):
        service=MemoryService(InMemoryMemoryRepository())
        own=MemoryCandidate(tenant_id="t",scope=MemoryScope.USER,scope_id="u1",content="old",source_ref="chat:1",confidence=.8,created_by="u1",conflict_key="preference")
        other=MemoryCandidate(tenant_id="t",scope=MemoryScope.USER,scope_id="u2",content="other user's preference",source_ref="chat:2",confidence=.8,created_by="u2",conflict_key="preference")
        await service.propose(own);await service.confirm(tenant_id="t",memory_id=own.memory_id,confirmer_id="u1")
        await service.propose(other);await service.confirm(tenant_id="t",memory_id=other.memory_id,confirmer_id="u2")
        revised=MemoryCandidate(tenant_id="t",scope=MemoryScope.USER,scope_id="u1",content="new",source_ref="chat:3",confidence=.9,created_by="u1",conflict_key="preference")
        conflict=await service.propose(revised)
        admin=MemoryPrincipal(tenant_id="t",user_id="admin",permissions=frozenset({"memory.manage"}))
        await service.resolve_conflict(principal=admin,conflict_key="preference",selected_memory_id=revised.memory_id)
        self.assertEqual((await service.repository.get("t",other.memory_id)).status.value,"active")
        self.assertEqual((await service.repository.get("t",own.memory_id)).status.value,"superseded")
        self.assertEqual(conflict.status.value,"conflict")

    async def test_forget_retries_cleanup_after_soft_delete(self):
        attempts=[]
        async def cleanup(tenant_id,memory_id):
            attempts.append((tenant_id,memory_id))
            if len(attempts)==1:
                raise RuntimeError("cache unavailable")
        service=MemoryService(InMemoryMemoryRepository(),on_delete=cleanup)
        candidate=MemoryCandidate(tenant_id="t",scope=MemoryScope.USER,scope_id="u",content="private fact",source_ref="chat:1",confidence=.9,created_by="u")
        await service.propose(candidate)
        await service.confirm(tenant_id="t",memory_id=candidate.memory_id,confirmer_id="u")
        principal=MemoryPrincipal(tenant_id="t",user_id="u")
        with self.assertRaisesRegex(RuntimeError,"cache unavailable"):
            await service.forget(principal=principal,memory_id=candidate.memory_id)
        self.assertEqual((await service.repository.get("t",candidate.memory_id)).status.value,"deleted")
        self.assertFalse(await service.forget(principal=principal,memory_id=candidate.memory_id))
        self.assertEqual(attempts,[("t",candidate.memory_id),("t",candidate.memory_id)])

    async def test_expiry_retries_derived_data_cleanup(self):
        from datetime import datetime, timedelta, timezone

        attempts = []
        async def cleanup(tenant_id, memory_id):
            attempts.append((tenant_id, memory_id))
            if len(attempts) == 1:
                raise RuntimeError("index unavailable")

        service = MemoryService(InMemoryMemoryRepository(), on_delete=cleanup)
        candidate = MemoryCandidate(
            tenant_id="t", scope=MemoryScope.USER, scope_id="u",
            content="temporary preference", source_ref="chat:expiry",
            confidence=0.9, created_by="extractor",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        await service.propose(candidate)
        await service.confirm(tenant_id="t", memory_id=candidate.memory_id, confirmer_id="u")
        with self.assertRaisesRegex(RuntimeError, "index unavailable"):
            await service.expire_due(tenant_id="t")
        self.assertEqual((await service.repository.get("t", candidate.memory_id)).status.value, "expired")
        self.assertEqual(await service.expire_due(tenant_id="t"), 0)
        self.assertEqual(attempts, [("t", candidate.memory_id), ("t", candidate.memory_id)])

