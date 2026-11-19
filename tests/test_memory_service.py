from __future__ import annotations

import unittest

from agent_platform.memory.service import (
    InMemoryMemoryRepository,
    MemoryCandidate,
    MemoryPrincipal,
    MemoryScope,
    MemoryService,
)


class MemoryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_candidate_rejects_invalid_scope_confidence_and_metadata(self):
        base = dict(
            tenant_id="tenant-a", scope=MemoryScope.USER, scope_id="user-a",
            content="prefers concise answers", source_ref="session://t/turn-1@1",
            confidence=0.9, created_by="extractor",
        )
        invalid = (
            {"confidence": True}, {"confidence": float("nan")},
            {"confidence": float("inf")}, {"scope": "user"},
            {"tenant_id": 7}, {"source_ref": " "},
            {"metadata": {"token": 7}}, {"metadata": {1: "bad"}},
            {"conflict_key": " "},
        )
        for overrides in invalid:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                MemoryCandidate(**{**base, **overrides})

    async def test_candidate_is_not_retrievable_until_confirmed(self):
        service=MemoryService(InMemoryMemoryRepository()); c=MemoryCandidate(tenant_id="t",scope=MemoryScope.USER,scope_id="u1",content="prefers concise reports",source_ref="session://t/turn-123@1",confidence=.9,created_by="extractor")
        await service.propose(c); p=MemoryPrincipal(tenant_id="t",user_id="u1"); self.assertEqual(await service.retrieve(principal=p),[])
        await service.confirm(principal=MemoryPrincipal(tenant_id="t", user_id="u1", permissions=frozenset({"memory.manage"})), memory_id=c.memory_id); self.assertEqual(len(await service.retrieve(principal=p)),1)
    async def test_reject_retries_derived_data_cleanup_after_tombstone(self):
        attempts = []

        async def cleanup(tenant_id, memory_id):
            attempts.append((tenant_id, memory_id))
            if len(attempts) == 1:
                raise RuntimeError("index unavailable")

        service = MemoryService(InMemoryMemoryRepository(), on_delete=cleanup)
        candidate = MemoryCandidate(
            tenant_id="t", scope=MemoryScope.USER, scope_id="u1",
            content="candidate fact", source_ref="chat:reject",
            confidence=0.8, created_by="extractor",
        )
        await service.propose(candidate)
        with self.assertRaisesRegex(RuntimeError, "index unavailable"):
            await service.reject(principal=MemoryPrincipal(tenant_id="t", user_id="reviewer", permissions=frozenset({"memory.manage"})), memory_id=candidate.memory_id)
        self.assertEqual(
            (await service.repository.get("t", candidate.memory_id)).status.value,
            "deleted",
        )
        self.assertTrue(await service.reject(principal=MemoryPrincipal(tenant_id="t", user_id="reviewer", permissions=frozenset({"memory.manage"})), memory_id=candidate.memory_id))
        self.assertEqual(attempts, [("t", candidate.memory_id)] * 2)

    async def test_user_scope_isolation_and_forget(self):
        service=MemoryService(InMemoryMemoryRepository()); c=MemoryCandidate(tenant_id="t",scope=MemoryScope.USER,scope_id="u1",content="x",source_ref="chat:1",confidence=.8,created_by="u1")
        await service.propose(c); await service.confirm(principal=MemoryPrincipal(tenant_id="t", user_id="u1", permissions=frozenset({"memory.manage"})), memory_id=c.memory_id)
        self.assertEqual(await service.retrieve(principal=MemoryPrincipal(tenant_id="t",user_id="u2")),[])
        self.assertTrue(await service.forget(principal=MemoryPrincipal(tenant_id="t",user_id="u1"),memory_id=c.memory_id))
        self.assertEqual(await service.retrieve(principal=MemoryPrincipal(tenant_id="t",user_id="u1")),[])
    async def test_tenant_boundary(self):
        service=MemoryService(InMemoryMemoryRepository()); c=MemoryCandidate(tenant_id="t1",scope=MemoryScope.TENANT,scope_id="t1",content="policy",source_ref="doc:1",confidence=1,created_by="admin")
        await service.propose(c); await service.confirm(principal=MemoryPrincipal(tenant_id="t1", user_id="admin", permissions=frozenset({"memory.manage"})), memory_id=c.memory_id)
        self.assertEqual(await service.retrieve(principal=MemoryPrincipal(tenant_id="t2",user_id="u")),[])

    async def test_correct_export_and_expiry(self):
        from datetime import datetime, timedelta, timezone
        service=MemoryService(InMemoryMemoryRepository())
        expiry=datetime.now(timezone.utc)+timedelta(days=1)
        candidate=MemoryCandidate(tenant_id="t",scope=MemoryScope.USER,scope_id="u",content="old",source_ref="chat:1",confidence=.7,created_by="extractor",expires_at=expiry)
        await service.propose(candidate)
        await service.confirm(principal=MemoryPrincipal(tenant_id="t", user_id="u", permissions=frozenset({"memory.manage"})), memory_id=candidate.memory_id)
        principal=MemoryPrincipal(tenant_id="t",user_id="u")
        corrected=await service.correct(principal=principal,memory_id=candidate.memory_id,content="new",source_ref="chat:2")
        self.assertEqual(corrected.candidate.content,"new")
        exported=await service.export(principal=principal)
        self.assertEqual(exported[0]["source_ref"],"chat:2")
        expired=await service.expire_due(tenant_id="t",now=expiry+timedelta(seconds=1))
        self.assertEqual(expired,1)
        self.assertEqual(await service.retrieve(principal=principal),[])

    async def test_export_includes_non_active_owned_records_but_not_other_users_or_deleted(self):
        service = MemoryService(InMemoryMemoryRepository())
        principal = MemoryPrincipal(tenant_id="t", user_id="u1")
        manager = MemoryPrincipal(
            tenant_id="t", user_id="reviewer",
            permissions=frozenset({"memory.manage"}),
        )

        active = MemoryCandidate(
            tenant_id="t", scope=MemoryScope.USER, scope_id="u1",
            content="active fact", source_ref="chat:active",
            confidence=0.9, created_by="extractor",
        )
        archived = MemoryCandidate(
            tenant_id="t", scope=MemoryScope.USER, scope_id="u1",
            content="archived fact", source_ref="chat:archived",
            confidence=0.8, created_by="extractor",
        )
        pending = MemoryCandidate(
            tenant_id="t", scope=MemoryScope.USER, scope_id="u1",
            content="pending candidate", source_ref="chat:pending",
            confidence=0.7, created_by="extractor",
        )
        foreign = MemoryCandidate(
            tenant_id="t", scope=MemoryScope.USER, scope_id="u2",
            content="private other-user fact", source_ref="chat:foreign",
            confidence=1.0, created_by="u2",
        )
        deleted = MemoryCandidate(
            tenant_id="t", scope=MemoryScope.USER, scope_id="u1",
            content="deleted fact", source_ref="chat:deleted",
            confidence=0.6, created_by="extractor",
        )
        for candidate in (active, archived, pending, foreign, deleted):
            await service.propose(candidate)
        for candidate in (active, archived, foreign, deleted):
            await service.confirm(principal=manager, memory_id=candidate.memory_id)
        await service.archive(principal=manager, memory_id=archived.memory_id)
        await service.forget(principal=manager, memory_id=deleted.memory_id)

        exported = await service.export(principal=principal)
        by_id = {item["memory_id"]: item for item in exported}
        self.assertEqual(set(by_id), {
            active.memory_id, archived.memory_id, pending.memory_id,
        })
        self.assertEqual(by_id[active.memory_id]["status"], "active")
        self.assertEqual(by_id[archived.memory_id]["status"], "archived")
        self.assertEqual(by_id[pending.memory_id]["status"], "candidate")
        self.assertNotIn(foreign.memory_id, by_id)
        self.assertNotIn(deleted.memory_id, by_id)

    async def test_conflict_resolution_and_archive(self):
        from agent_platform.memory.service import MemoryStatus
        service=MemoryService(InMemoryMemoryRepository())
        first=MemoryCandidate(tenant_id="t",scope=MemoryScope.TENANT,scope_id="t",content="old policy",source_ref="policy:1",confidence=.9,created_by="admin",conflict_key="policy.version")
        await service.propose(first)
        await service.confirm(principal=MemoryPrincipal(tenant_id="t", user_id="admin", permissions=frozenset({"memory.manage"})), memory_id=first.memory_id)
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
        await service.confirm(principal=MemoryPrincipal(tenant_id="t", user_id="reviewer", permissions=frozenset({"memory.manage"})), memory_id=candidate.memory_id)
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
        await service.confirm(principal=MemoryPrincipal(tenant_id="t", user_id="reviewer", permissions=frozenset({"memory.manage"})), memory_id=candidate.memory_id)
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
        from agent_platform.memory.extraction import (
            MemoryDraft,
            MemoryExtractionService,
            MemoryTurn,
        )
        class Extractor:
            async def extract(self,*,turn):
                self.received=turn
                return [MemoryDraft(scope=MemoryScope.USER,scope_id=turn.user_id,content="prefers concise answers",confidence=.91)]
        repo=InMemoryMemoryRepository()
        service=MemoryService(repo)
        extractor=Extractor()
        pipeline=MemoryExtractionService(memory=service,extractor=extractor)
        principal=MemoryPrincipal(tenant_id="t",user_id="u")
        turn=MemoryTurn(tenant_id="t",user_id="u",source_ref="session://t/turn-9@1",user_message="Please keep it concise.",assistant_message="Okay.",completed=True)
        records=await pipeline.propose_from_turn(principal=principal,turn=turn)
        self.assertEqual(len(records),1)
        self.assertEqual(records[0].status.value,"candidate")
        self.assertEqual(records[0].candidate.source_ref,"session://t/turn-9@1")
        self.assertEqual(extractor.received,turn)
        self.assertEqual(await service.retrieve(principal=principal),[])
        with self.assertRaises(PermissionError):
            await pipeline.propose_from_turn(
                principal=principal,
                turn=MemoryTurn(tenant_id="other",user_id="u",source_ref="session://other/turn-other@1",user_message="x",assistant_message="y",completed=True),
            )

    async def test_repeated_turn_extraction_reuses_same_candidate_ids(self):
        from agent_platform.memory.extraction import (
            MemoryDraft,
            MemoryExtractionService,
            MemoryTurn,
        )
        class Extractor:
            async def extract(self, *, turn):
                return [MemoryDraft(scope=MemoryScope.USER, scope_id=turn.user_id, content='keep concise', confidence=0.9)]
        repository = InMemoryMemoryRepository()
        memory = MemoryService(repository)
        pipeline = MemoryExtractionService(memory=memory, extractor=Extractor())
        principal = MemoryPrincipal(tenant_id='t', user_id='u')
        turn = MemoryTurn(tenant_id='t', user_id='u', source_ref='session://t/turn-m2@1', user_message='Remember this', assistant_message='Okay', completed=True)
        first = await pipeline.propose_from_turn(principal=principal, turn=turn)
        second = await pipeline.propose_from_turn(principal=principal, turn=turn)
        self.assertEqual(first[0].candidate.memory_id, second[0].candidate.memory_id)
        self.assertEqual(len(await repository.list('t')), 1)
        self.assertEqual(second[0].status.value, 'candidate')

    async def test_repeated_turn_extraction_preserves_reviewed_memory(self):
        from agent_platform.memory.extraction import (
            MemoryDraft,
            MemoryExtractionService,
            MemoryTurn,
        )
        class Extractor:
            async def extract(self, *, turn):
                return [MemoryDraft(scope=MemoryScope.USER, scope_id=turn.user_id, content='original', confidence=0.9)]
        memory = MemoryService(InMemoryMemoryRepository())
        pipeline = MemoryExtractionService(memory=memory, extractor=Extractor())
        principal = MemoryPrincipal(tenant_id='t', user_id='u')
        turn = MemoryTurn(tenant_id='t', user_id='u', source_ref='session://t/turn-m4@1', user_message='Fact', assistant_message='Acknowledged', completed=True)
        first = (await pipeline.propose_from_turn(principal=principal, turn=turn))[0]
        await memory.confirm(principal=MemoryPrincipal(tenant_id='t', user_id='u', permissions=frozenset({"memory.manage"})), memory_id=first.candidate.memory_id)
        corrected = await memory.correct(principal=principal, memory_id=first.candidate.memory_id, content='user corrected', source_ref='manual:1')
        repeated = (await pipeline.propose_from_turn(principal=principal, turn=turn))[0]
        self.assertEqual(repeated.candidate.content, 'user corrected')
        self.assertEqual(repeated.version, corrected.version)
        self.assertEqual(repeated.status.value, 'active')

    async def test_extraction_requires_scoped_manage_grant_for_shared_memory(self):
        from agent_platform.memory.extraction import (
            MemoryDraft,
            MemoryExtractionService,
            MemoryTurn,
        )

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
            tenant_id="t", user_id="reader", source_ref="session://t/turn-1@1",
            user_message="Project decision",
            assistant_message="Decision noted.",
            project_id="project-a",
            completed=True,
        )
        with self.assertRaises(PermissionError):
            await pipeline.propose_from_turn(principal=reader, turn=turn)

        manager = MemoryPrincipal(
            tenant_id="t", user_id="manager",
            project_ids=frozenset({"project-a"}),
            manage_project_ids=frozenset({"project-a"}),
        )
        manager_turn = MemoryTurn(
            tenant_id="t", user_id="manager", source_ref="session://t/turn-2@1",
            user_message="Project decision",
            assistant_message="Decision noted.",
            project_id="project-a",
            completed=True,
        )
        records = await pipeline.propose_from_turn(principal=manager, turn=manager_turn)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].candidate.scope, MemoryScope.PROJECT)

    async def test_extraction_cannot_write_unapproved_tenant_scope(self):
        from agent_platform.memory.extraction import (
            MemoryDraft,
            MemoryExtractionService,
            MemoryTurn,
        )
        class TenantExtractor:
            async def extract(self,*,turn):
                return [MemoryDraft(scope=MemoryScope.TENANT,scope_id=turn.tenant_id,content="shared policy",confidence=.9)]
        pipeline=MemoryExtractionService(memory=MemoryService(InMemoryMemoryRepository()),extractor=TenantExtractor())
        with self.assertRaises(PermissionError):
            await pipeline.propose_from_turn(
                principal=MemoryPrincipal(tenant_id="t",user_id="u"),
                turn=MemoryTurn(tenant_id="t",user_id="u",source_ref="session://t/turn-1@1",user_message="policy",assistant_message="understood",completed=True),
            )

    async def test_conflict_resolution_does_not_cross_memory_scope_ids(self):
        service=MemoryService(InMemoryMemoryRepository())
        own=MemoryCandidate(tenant_id="t",scope=MemoryScope.USER,scope_id="u1",content="old",source_ref="chat:1",confidence=.8,created_by="u1",conflict_key="preference")
        other=MemoryCandidate(tenant_id="t",scope=MemoryScope.USER,scope_id="u2",content="other user's preference",source_ref="chat:2",confidence=.8,created_by="u2",conflict_key="preference")
        await service.propose(own);await service.confirm(principal=MemoryPrincipal(tenant_id="t", user_id="u1", permissions=frozenset({"memory.manage"})), memory_id=own.memory_id)
        await service.propose(other);await service.confirm(principal=MemoryPrincipal(tenant_id="t", user_id="u2", permissions=frozenset({"memory.manage"})), memory_id=other.memory_id)
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
        await service.confirm(principal=MemoryPrincipal(tenant_id="t", user_id="u", permissions=frozenset({"memory.manage"})), memory_id=candidate.memory_id)
        principal=MemoryPrincipal(tenant_id="t",user_id="u")
        with self.assertRaisesRegex(RuntimeError,"cache unavailable"):
            await service.forget(principal=principal,memory_id=candidate.memory_id)
        self.assertEqual((await service.repository.get("t",candidate.memory_id)).status.value,"deleted")
        # A repeated logical deletion is successful once the tombstone exists;
        # the cleanup hook above is retried before the idempotent response.
        self.assertTrue(await service.forget(principal=principal,memory_id=candidate.memory_id))
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
        await service.confirm(principal=MemoryPrincipal(tenant_id="t", user_id="u", permissions=frozenset({"memory.manage"})), memory_id=candidate.memory_id)
        with self.assertRaisesRegex(RuntimeError, "index unavailable"):
            await service.expire_due(tenant_id="t")
        self.assertEqual((await service.repository.get("t", candidate.memory_id)).status.value, "expired")
        self.assertEqual(await service.expire_due(tenant_id="t"), 0)
        self.assertEqual(attempts, [("t", candidate.memory_id), ("t", candidate.memory_id)])


    async def test_memory_history_is_versioned_and_scope_authorized(self):
        service = MemoryService(InMemoryMemoryRepository())
        candidate = MemoryCandidate(
            tenant_id="t", scope=MemoryScope.USER, scope_id="u1",
            content="prefers concise answers", source_ref="chat:1",
            confidence=0.8, created_by="extractor",
        )
        await service.propose(candidate)
        await service.confirm(principal=MemoryPrincipal(tenant_id="t", user_id="u1", permissions=frozenset({"memory.manage"})), memory_id=candidate.memory_id)
        owner = MemoryPrincipal(tenant_id="t", user_id="u1")
        await service.correct(
            principal=owner, memory_id=candidate.memory_id,
            content="prefers concise technical answers", source_ref="chat:2",
        )
        history = await service.history(principal=owner, memory_id=candidate.memory_id)
        self.assertEqual([record.version for record in history], [1, 2, 3])
        self.assertEqual(history[0].candidate.content, "prefers concise answers")
        self.assertEqual(history[-1].candidate.source_ref, "chat:2")
        with self.assertRaises(PermissionError):
            await service.history(
                principal=MemoryPrincipal(tenant_id="t", user_id="u2"),
                memory_id=candidate.memory_id,
            )
        with self.assertRaises(ValueError):
            await service.history(principal=owner, memory_id="missing")

    async def test_memory_history_retains_forget_tombstone(self):
        service = MemoryService(InMemoryMemoryRepository())
        candidate = MemoryCandidate(
            tenant_id="t", scope=MemoryScope.USER, scope_id="u",
            content="temporary fact", source_ref="chat:1",
            confidence=0.8, created_by="extractor",
        )
        await service.propose(candidate)
        await service.confirm(principal=MemoryPrincipal(tenant_id="t", user_id="u", permissions=frozenset({"memory.manage"})), memory_id=candidate.memory_id)
        principal = MemoryPrincipal(tenant_id="t", user_id="u")
        self.assertTrue(await service.forget(
            principal=principal, memory_id=candidate.memory_id,
        ))
        history = await service.history(principal=principal, memory_id=candidate.memory_id)
        self.assertEqual(history[-1].status.value, "deleted")
        self.assertEqual(history[-1].version, 3)


    async def test_archive_propagates_and_retries_index_cleanup(self):
        from agent_platform.memory.service import MemoryStatus

        calls = []

        async def cleanup(tenant_id, memory_id):
            calls.append((tenant_id, memory_id))
            if len(calls) == 1:
                raise RuntimeError("vector index unavailable")

        repository = InMemoryMemoryRepository()
        service = MemoryService(repository, on_delete=cleanup)
        candidate = MemoryCandidate(
            tenant_id="tenant-a", scope=MemoryScope.USER, scope_id="user-a",
            content="archive this", source_ref="turn:archive", confidence=0.9,
            created_by="user-a",
        )
        await service.propose(candidate)
        await service.confirm(principal=MemoryPrincipal(tenant_id="tenant-a", user_id="user-a", permissions=frozenset({"memory.manage"})), memory_id=candidate.memory_id)
        principal = MemoryPrincipal(tenant_id="tenant-a", user_id="user-a")

        with self.assertRaisesRegex(RuntimeError, "vector index unavailable"):
            await service.archive(principal=principal, memory_id=candidate.memory_id)
        self.assertEqual(
            (await repository.get("tenant-a", candidate.memory_id)).status,
            MemoryStatus.ARCHIVED,
        )
        self.assertTrue(await service.archive(principal=principal, memory_id=candidate.memory_id))
        self.assertEqual(len(calls), 2)
        self.assertEqual(await service.retrieve(principal=principal), [])

    async def test_conflict_superseding_propagates_hidden_memory_cleanup(self):
        calls = []

        async def cleanup(tenant_id, memory_id):
            calls.append(memory_id)

        repository = InMemoryMemoryRepository()
        service = MemoryService(repository, on_delete=cleanup)
        principal = MemoryPrincipal(
            tenant_id="tenant-a", user_id="admin",
            permissions=frozenset({"memory.manage"}),
        )
        first = MemoryCandidate(
            tenant_id="tenant-a", scope=MemoryScope.TENANT, scope_id="tenant-a",
            content="old policy", source_ref="policy:old", confidence=0.8,
            created_by="admin", conflict_key="policy",
        )
        await service.propose(first)
        await service.confirm(principal=MemoryPrincipal(tenant_id="tenant-a", user_id="admin", permissions=frozenset({"memory.manage"})), memory_id=first.memory_id)
        second = MemoryCandidate(
            tenant_id="tenant-a", scope=MemoryScope.TENANT, scope_id="tenant-a",
            content="new policy", source_ref="policy:new", confidence=0.9,
            created_by="admin", conflict_key="policy",
        )
        await service.propose(second)
        await service.resolve_conflict(
            principal=principal, conflict_key="policy", selected_memory_id=second.memory_id,
        )
        self.assertIn(first.memory_id, calls)
        self.assertEqual(await service.retrieve(principal=principal), [
            await repository.get("tenant-a", second.memory_id)
        ])

    async def test_candidate_review_requires_scope_authorization(self):
        service = MemoryService(InMemoryMemoryRepository())
        candidate = MemoryCandidate(
            tenant_id="t", scope=MemoryScope.USER, scope_id="owner",
            content="private preference", source_ref="turn:review",
            confidence=0.9, created_by="extractor",
        )
        await service.propose(candidate)
        intruder = MemoryPrincipal(tenant_id="t", user_id="intruder")
        with self.assertRaises(PermissionError):
            await service.confirm(principal=intruder, memory_id=candidate.memory_id)
        with self.assertRaises(PermissionError):
            await service.reject(principal=intruder, memory_id=candidate.memory_id)
        owner = MemoryPrincipal(tenant_id="t", user_id="owner")
        confirmed = await service.confirm(principal=owner, memory_id=candidate.memory_id)
        self.assertEqual(confirmed.confirmed_by, "owner")

    async def test_conflict_resolution_retries_superseded_cleanup(self):
        calls = []
        async def cleanup(tenant_id, memory_id):
            calls.append(memory_id)
            if len(calls) == 1:
                raise RuntimeError("index temporarily unavailable")

        repository = InMemoryMemoryRepository()
        service = MemoryService(repository, on_delete=cleanup)
        admin = MemoryPrincipal(
            tenant_id="t", user_id="admin",
            permissions=frozenset({"memory.manage"}),
        )
        previous = MemoryCandidate(
            tenant_id="t", scope=MemoryScope.TENANT, scope_id="t",
            content="old policy", source_ref="policy:old", confidence=0.8,
            created_by="admin", conflict_key="policy",
        )
        await service.propose(previous)
        await service.confirm(principal=admin, memory_id=previous.memory_id)
        proposed = MemoryCandidate(
            tenant_id="t", scope=MemoryScope.TENANT, scope_id="t",
            content="new policy", source_ref="policy:new", confidence=0.9,
            created_by="admin", conflict_key="policy",
        )
        await service.propose(proposed)
        with self.assertRaisesRegex(RuntimeError, "index temporarily unavailable"):
            await service.resolve_conflict(
                principal=admin, conflict_key="policy",
                selected_memory_id=proposed.memory_id,
            )
        self.assertEqual(
            (await repository.get("t", previous.memory_id)).status.value,
            "superseded",
        )
        selected = await service.resolve_conflict(
            principal=admin, conflict_key="policy",
            selected_memory_id=proposed.memory_id,
        )
        self.assertEqual(selected.status.value, "active")
        self.assertEqual(calls, [previous.memory_id, previous.memory_id])

    async def test_export_includes_immutable_version_history(self):
        service = MemoryService(InMemoryMemoryRepository())
        principal = MemoryPrincipal(tenant_id="t", user_id="owner")
        candidate = MemoryCandidate(
            tenant_id="t", scope=MemoryScope.USER, scope_id="owner",
            content="original preference", source_ref="chat:original",
            confidence=0.7, created_by="extractor",
            metadata={"origin": "conversation"},
        )
        await service.propose(candidate)
        await service.confirm(principal=principal, memory_id=candidate.memory_id)
        await service.correct(
            principal=principal, memory_id=candidate.memory_id,
            content="corrected preference", source_ref="chat:correction",
            confidence=0.95,
        )

        exported = await service.export(principal=principal)
        self.assertEqual(len(exported), 1)
        item = exported[0]
        self.assertEqual(item["version"], 3)
        self.assertEqual(item["content"], "corrected preference")
        self.assertEqual(
            [entry["version"] for entry in item["history"]], [1, 2, 3],
        )
        self.assertEqual(
            [entry["content"] for entry in item["history"]],
            ["original preference", "original preference", "corrected preference"],
        )
        self.assertEqual(
            [entry["source_ref"] for entry in item["history"]],
            ["chat:original", "chat:original", "chat:correction"],
        )
        self.assertEqual(item["history"][0]["status"], "active")
        self.assertEqual(item["history"][1]["confirmed_by"], "owner")
        self.assertEqual(item["history"][1]["metadata"], {"origin": "conversation"})

