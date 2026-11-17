from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.graph.governance import GraphEntityStatus, GovernedGraphEntity
from agent_platform.graph.postgres_governance import _entity, _relation


class PostgresGovernanceSerializationTests(unittest.TestCase):
    def test_entity_row_round_trip_preserves_version_status_and_lineage(self):
        now = datetime.now(timezone.utc)
        entity = _entity({
            "tenant_id": "tenant-a", "entity_id": "doc-a", "entity_type": "document",
            "attributes": {"title": "A"}, "project_id": "project-a",
            "source_refs": ["knowledge://tenant-a/doc-a@3"], "version": 4,
            "status": "archived", "expires_at": None, "merged_into": None,
            "updated_at": now, "updated_by": "operator",
        })
        self.assertEqual(entity.version, 4)
        self.assertIs(entity.status, GraphEntityStatus.ARCHIVED)
        self.assertEqual(entity.source_refs, ("knowledge://tenant-a/doc-a@3",))
        self.assertEqual(entity.attributes["title"], "A")

    def test_entity_json_text_rows_are_decoded(self):
        entity = _entity({
            "tenant_id": "tenant-a", "entity_id": "doc-a", "entity_type": "document",
            "attributes": '{"title":"A"}', "project_id": None,
            "source_refs": '["knowledge://tenant-a/doc-a@1"]', "version": 1,
            "status": "active", "expires_at": None, "merged_into": None,
            "updated_at": datetime.now(timezone.utc), "updated_by": "system",
        })
        self.assertEqual(entity.attributes["title"], "A")

    def test_relation_row_round_trip_preserves_tombstone(self):
        relation = _relation({
            "tenant_id": "tenant-a", "relation_id": "edge-a",
            "relation_type": "references", "source_entity_id": "doc-a",
            "target_entity_id": "doc-b", "project_id": None,
            "attributes": {}, "source_refs": [], "version": 2,
            "status": "deleted", "updated_at": datetime.now(timezone.utc),
        })
        self.assertIs(relation.status, GraphEntityStatus.DELETED)
        self.assertEqual(relation.version, 2)

    def test_tenant_mismatch_is_rejected_at_entity_contract(self):
        with self.assertRaises(ValueError):
            GovernedGraphEntity(
                tenant_id="tenant-a", entity_id="bad", entity_type="document",
                attributes={}, source_refs=("knowledge://tenant-b/doc@1",),
            )


class _FakeDurableInvalidationRepository:
    def __init__(self, jobs):
        self.jobs = tuple(jobs)
        self.completed = []
        self.retried = []

    async def claim_invalidation_jobs(self, *, worker_id, limit=20, lease_seconds=60):
        self.claim_args = (worker_id, limit, lease_seconds)
        return self.jobs[:limit]

    async def complete_invalidation_job(self, *, job_id, worker_id):
        self.completed.append((job_id, worker_id))
        return job_id != "lease-lost"

    async def retry_invalidation_job(self, *, job_id, worker_id, delay_seconds=30):
        self.retried.append((job_id, worker_id, delay_seconds))
        return True


class PostgresInvalidationWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_completes_successful_job_and_retries_failed_job(self):
        from types import SimpleNamespace
        from agent_platform.graph.postgres_governance import PostgresGraphInvalidationWorker

        jobs = [
            SimpleNamespace(job_id="ok", object_kind="entity", operation="delete"),
            SimpleNamespace(job_id="retry", object_kind="cache", operation="invalidate"),
        ]
        repository = _FakeDurableInvalidationRepository(jobs)

        async def delete_handler(job):
            self.assertEqual(job.job_id, "ok")

        def failed_handler(job):
            raise RuntimeError("transient index outage")

        worker = PostgresGraphInvalidationWorker(
            repository,
            handlers={
                ("entity", "delete"): delete_handler,
                ("cache", "invalidate"): failed_handler,
            },
        )
        self.assertEqual(
            await worker.run_once(
                worker_id="worker-a", limit=7, lease_seconds=45,
                retry_delay_seconds=12,
            ),
            (1, 1),
        )
        self.assertEqual(repository.claim_args, ("worker-a", 7, 45))
        self.assertEqual(repository.completed, [("ok", "worker-a")])
        self.assertEqual(repository.retried, [("retry", "worker-a", 12)])

    async def test_worker_retries_missing_handler_and_does_not_count_lost_lease(self):
        from types import SimpleNamespace
        from agent_platform.graph.postgres_governance import PostgresGraphInvalidationWorker

        jobs = [
            SimpleNamespace(job_id="unhandled", object_kind="relation", operation="delete"),
            SimpleNamespace(job_id="lease-lost", object_kind="entity", operation="upsert"),
        ]
        repository = _FakeDurableInvalidationRepository(jobs)
        worker = PostgresGraphInvalidationWorker(
            repository,
            handlers={("entity", "upsert"): lambda job: None},
        )
        self.assertEqual(await worker.run_once(worker_id="worker-b"), (0, 1))
        self.assertEqual(repository.retried, [("unhandled", "worker-b", 30)])
        self.assertEqual(repository.completed, [("lease-lost", "worker-b")])


if __name__ == "__main__":
    unittest.main()
