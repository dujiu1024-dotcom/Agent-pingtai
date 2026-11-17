from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from agent_platform.graph.governance import (
    GraphEntityStatus,
    GraphGovernanceConflict,
    GraphGovernanceService,
    GovernedGraphEntity,
    GovernedGraphRelation,
    InMemoryGraphGovernanceRepository,
)
from agent_platform.graph.schema import default_graph_schema
from agent_platform.knowledge.contracts import KnowledgePrincipal


def _entity(entity_id, *, project_id=None, title=None, expires_at=None):
    attrs = {} if title is None else {"title": title}
    return GovernedGraphEntity(
        tenant_id="tenant-a", entity_id=entity_id, entity_type="document",
        attributes=attrs, project_id=project_id,
        source_refs=(f"knowledge://tenant-a/{entity_id}@1",),
        expires_at=expires_at,
    )


def _principal(*, project_manage=(), permissions=("knowledge.manage",), tenant="tenant-a"):
    return KnowledgePrincipal(
        tenant_id=tenant, user_id="operator",
        manage_project_ids=frozenset(project_manage),
        permissions=frozenset(permissions),
    )


class GraphGovernanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = InMemoryGraphGovernanceRepository()
        self.service = GraphGovernanceService(
            self.repository,
            schema_resolver=lambda tenant: default_graph_schema(tenant),
        )
        self.principal = _principal()

    async def test_delete_propagates_to_relations_indexes_caches_and_audit(self):
        await self.repository.add_entity(_entity("doc-a", title="A"))
        await self.repository.add_entity(_entity("doc-b", title="B"))
        await self.repository.add_relation(GovernedGraphRelation(
            "tenant-a", "edge-1", "references", "doc-a", "doc-b",
        ))
        deleted = await self.service.delete_entity(
            principal=self.principal, entity_id="doc-a", expected_version=1,
        )
        self.assertIs(deleted.status, GraphEntityStatus.DELETED)
        self.assertEqual(deleted.version, 2)
        relations = await self.repository.get_relations("tenant-a", include_inactive=True)
        self.assertIs(relations[0].status, GraphEntityStatus.DELETED)
        jobs = await self.repository.invalidation_jobs("tenant-a")
        self.assertEqual(
            {(job.object_kind, job.object_id, job.operation) for job in jobs},
            {
                ("entity", "doc-a", "delete"),
                ("cache", "doc-a", "invalidate"),
                ("relation", "edge-1", "delete"),
            },
        )
        audit = await self.repository.audit_events("tenant-a", "doc-a")
        self.assertEqual(audit[-1].action, "deleted")
        self.assertEqual(audit[-1].details["affected_relation_ids"], ["edge-1"])

    async def test_delete_is_idempotent_and_does_not_duplicate_audit_or_jobs(self):
        await self.repository.add_entity(_entity("doc-a"))
        first = await self.service.delete_entity(
            principal=self.principal, entity_id="doc-a", expected_version=1,
        )
        second = await self.service.delete_entity(
            principal=self.principal, entity_id="doc-a", expected_version=1,
        )
        self.assertEqual(first.version, second.version)
        self.assertEqual(len(await self.repository.audit_events("tenant-a")), 1)
        self.assertEqual(len(await self.repository.invalidation_jobs("tenant-a")), 2)

    async def test_archived_entity_can_later_be_deleted(self):
        await self.repository.add_entity(_entity("doc-a"))
        archived = await self.service.archive_entity(
            principal=self.principal, entity_id="doc-a", expected_version=1,
        )
        self.assertIs(archived.status, GraphEntityStatus.ARCHIVED)
        deleted = await self.service.delete_entity(
            principal=self.principal, entity_id="doc-a", expected_version=2,
        )
        self.assertIs(deleted.status, GraphEntityStatus.DELETED)
        self.assertEqual(deleted.version, 3)

    async def test_correction_validates_schema_and_preserves_version_lineage(self):
        await self.repository.add_entity(_entity("doc-a", title="Old"))
        updated = await self.service.correct_entity(
            principal=self.principal, entity_id="doc-a",
            expected_version=1, attributes={"title": "Corrected"},
        )
        self.assertEqual(updated.version, 2)
        history = await self.repository.entity_history("tenant-a", "doc-a")
        self.assertEqual([item.version for item in history], [1, 2])
        self.assertEqual(history[0].attributes["title"], "Old")
        self.assertEqual(history[1].attributes["title"], "Corrected")
        with self.assertRaisesRegex(ValueError, "unknown properties"):
            await self.service.correct_entity(
                principal=self.principal, entity_id="doc-a",
                expected_version=2, attributes={"title": "Bad", "secret": "x"},
            )
        current = await self.repository.get_entity("tenant-a", "doc-a")
        self.assertEqual(current.version, 2)

    async def test_concurrent_corrections_use_compare_and_swap(self):
        await self.repository.add_entity(_entity("doc-a"))
        async def correct(title):
            return await self.service.correct_entity(
                principal=self.principal, entity_id="doc-a",
                expected_version=1, attributes={"title": title},
            )
        results = await asyncio.gather(
            correct("first"), correct("second"), return_exceptions=True,
        )
        self.assertEqual(sum(not isinstance(item, Exception) for item in results), 1)
        self.assertEqual(sum(isinstance(item, GraphGovernanceConflict) for item in results), 1)
        current = await self.repository.get_entity("tenant-a", "doc-a")
        self.assertEqual(current.version, 2)

    async def test_project_governance_requires_manage_scope(self):
        await self.repository.add_entity(_entity("project-doc", project_id="project-a"))
        with self.assertRaises(PermissionError):
            await self.service.archive_entity(
                principal=_principal(), entity_id="project-doc", expected_version=1,
            )
        archived = await self.service.archive_entity(
            principal=_principal(project_manage=("project-a",)),
            entity_id="project-doc", expected_version=1,
        )
        self.assertIs(archived.status, GraphEntityStatus.ARCHIVED)

    async def test_cross_tenant_entity_is_not_discoverable_or_mutable(self):
        await self.repository.add_entity(_entity("doc-a"))
        with self.assertRaises(KeyError):
            await self.service.delete_entity(
                principal=_principal(tenant="tenant-b"),
                entity_id="doc-a", expected_version=1,
            )
        current = await self.repository.get_entity("tenant-a", "doc-a")
        self.assertIs(current.status, GraphEntityStatus.ACTIVE)

    async def test_merge_preserves_target_and_deduplicates_rewired_relations(self):
        await self.repository.add_entity(_entity("source", title="source title"))
        await self.repository.add_entity(_entity("target", title="target title"))
        await self.repository.add_entity(_entity("neighbor", title="neighbor"))
        await self.repository.add_relation(GovernedGraphRelation(
            "tenant-a", "target-edge", "references", "target", "neighbor",
        ))
        await self.repository.add_relation(GovernedGraphRelation(
            "tenant-a", "source-edge", "references", "source", "neighbor",
        ))
        merged = await self.service.merge_entities(
            principal=self.principal, source_id="source", target_id="target",
            source_version=1, target_version=1, conflict_policy="prefer_target",
        )
        self.assertEqual(merged.attributes["title"], "target title")
        source = await self.repository.get_entity("tenant-a", "source")
        self.assertIs(source.status, GraphEntityStatus.SUPERSEDED)
        self.assertEqual(source.merged_into, "target")
        relations = await self.repository.get_relations("tenant-a", include_inactive=True)
        self.assertEqual(len([r for r in relations if r.status is GraphEntityStatus.ACTIVE]), 1)
        active = next(r for r in relations if r.status is GraphEntityStatus.ACTIVE)
        self.assertEqual(active.relation_id, "target-edge")
        self.assertEqual(active.source_entity_id, "target")
        audit = await self.repository.audit_events("tenant-a")
        self.assertEqual({event.action for event in audit}, {"merge_target", "merge_source"})

    async def test_merge_rejects_cross_project_and_invalid_policy(self):
        await self.repository.add_entity(_entity("source", project_id="project-a"))
        await self.repository.add_entity(_entity("target", project_id="project-b"))
        principal = _principal(project_manage=("project-a", "project-b"))
        with self.assertRaisesRegex(ValueError, "share type and project"):
            await self.service.merge_entities(
                principal=principal, source_id="source", target_id="target",
                source_version=1, target_version=1,
            )
        await self.repository.add_entity(_entity("target-2", project_id="project-a"))
        with self.assertRaisesRegex(ValueError, "conflict_policy"):
            await self.service.merge_entities(
                principal=principal, source_id="source", target_id="target-2",
                source_version=1, target_version=1, conflict_policy="last_write_wins",
            )

    async def test_expiry_worker_archives_due_entities_and_records_audit(self):
        due = datetime.now(timezone.utc) - timedelta(minutes=1)
        await self.repository.add_entity(_entity("expired", expires_at=due))
        await self.repository.add_entity(_entity(
            "not-expired", expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        count = await self.service.expire_due(now=datetime.now(timezone.utc))
        self.assertEqual(count, 1)
        expired = await self.repository.get_entity("tenant-a", "expired")
        active = await self.repository.get_entity("tenant-a", "not-expired")
        self.assertIs(expired.status, GraphEntityStatus.EXPIRED)
        self.assertIs(active.status, GraphEntityStatus.ACTIVE)

    async def test_invalidation_jobs_require_current_lease_owner(self):
        await self.repository.add_entity(_entity("doc-a"))
        await self.service.delete_entity(
            principal=self.principal, entity_id="doc-a", expected_version=1,
        )
        jobs = await self.repository.claim_invalidation_jobs(worker_id="worker-a", limit=1)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertFalse(await self.repository.complete_invalidation_job(
            job_id=job.job_id, worker_id="worker-b",
        ))
        self.assertTrue(await self.repository.complete_invalidation_job(
            job_id=job.job_id, worker_id="worker-a",
        ))
        self.assertFalse(await self.repository.complete_invalidation_job(
            job_id=job.job_id, worker_id="worker-a",
        ))


if __name__ == "__main__":
    unittest.main()
