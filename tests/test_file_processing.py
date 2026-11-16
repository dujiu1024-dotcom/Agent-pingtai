from __future__ import annotations

import unittest

from agent_platform.control_plane.file_processing import FileProcessingJob, FileProcessingWorker
from agent_platform.control_plane.sessions_assets import (
    AssetStatus,
    InMemoryObjectStorage,
    InMemorySessionRepository,
    SessionAssetService,
)
from agent_platform.knowledge.contracts import KnowledgePrincipal
from agent_platform.knowledge.ingestion import ParserRegistry, PlainTextParser
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService


class Queue:
    def __init__(self):
        self.jobs = []
        self.completed = []
        self.failures = []
    async def enqueue(self, *, tenant_id, asset_id):
        self.jobs.append((tenant_id, asset_id))
    async def claim(self, *, worker_id, lease_seconds=120):
        if not self.jobs:
            return None
        tenant_id, asset_id = self.jobs.pop(0)
        return FileProcessingJob(tenant_id, "job-1", asset_id, 1, 3, worker_id)
    async def complete(self, job):
        self.completed.append(job.asset_id)
    async def fail(self, job, *, error_code):
        self.failures.append(error_code)
        return False


class Scanner:
    def __init__(self, accepted=True):
        self.accepted = accepted
    async def scan(self, filename, media_type, content):
        return self.accepted


class FileProcessingWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = InMemorySessionRepository()
        self.storage = InMemoryObjectStorage()
        self.queue = Queue()
        self.knowledge_repository = InMemoryKnowledgeRepository()
        self.knowledge = KnowledgeService(
            self.knowledge_repository,
            parsers=ParserRegistry(parsers=(PlainTextParser(),)),
        )
        self.service = SessionAssetService(
            self.repository, self.storage, processing_queue=self.queue,
            knowledge_for_tenant=lambda tenant: self.knowledge,
        )

    async def test_upload_is_pending_until_worker_scans_and_indexes(self):
        session = await self.service.create_session(
            tenant_id="tenant-a", owner_id="user-a", project_id="project-a",
        )
        asset = await self.service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="notes.txt",
            media_type="text/plain", content=b"hello searchable world",
            project_id="project-a", session_id=session.session_id,
        )
        self.assertEqual(asset.status, AssetStatus.PENDING)
        self.assertEqual(self.queue.jobs, [("tenant-a", asset.asset_id)])
        worker = FileProcessingWorker(
            queue=self.queue, worker_id="worker-a",
            repository_for_tenant=lambda tenant: self.repository,
            storage_for_tenant=lambda tenant: self.storage,
            scanner=Scanner(),
            knowledge_for_tenant=lambda tenant: self.knowledge,
        )
        self.assertTrue(await worker.run_once())
        saved = await self.repository.get_asset("tenant-a", asset.asset_id)
        self.assertEqual(saved.status, AssetStatus.AVAILABLE)
        principal = KnowledgePrincipal(
            tenant_id="tenant-a", user_id="user-a", project_ids=frozenset({"project-a"}),
        )
        hits = await self.knowledge.retrieve(principal=principal, query="searchable")
        self.assertEqual(hits[0].document_id, asset.asset_id)
        self.assertEqual(self.queue.completed, [asset.asset_id])

    async def test_rejected_file_is_deleted_and_never_indexed(self):
        asset = await self.service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="bad.txt",
            media_type="text/plain", content=b"malware",
        )
        worker = FileProcessingWorker(
            queue=self.queue, worker_id="worker-a",
            repository_for_tenant=lambda tenant: self.repository,
            storage_for_tenant=lambda tenant: self.storage,
            scanner=Scanner(accepted=False),
            knowledge_for_tenant=lambda tenant: self.knowledge,
        )
        await worker.run_once()
        saved = await self.repository.get_asset("tenant-a", asset.asset_id)
        self.assertEqual(saved.status, AssetStatus.REJECTED)
        with self.assertRaises(Exception):
            await self.storage.get(asset.object_key)
        self.assertEqual(self.knowledge_repository.documents, {})

    async def test_delete_asset_removes_index_and_object(self):
        asset = await self.service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="indexed.txt",
            media_type="text/plain", content=b"remove searchable content",
        )
        worker = FileProcessingWorker(
            queue=self.queue, worker_id="worker-a",
            repository_for_tenant=lambda tenant: self.repository,
            storage_for_tenant=lambda tenant: self.storage,
            scanner=Scanner(),
            knowledge_for_tenant=lambda tenant: self.knowledge,
        )
        await worker.run_once()
        self.assertIn(asset.asset_id, self.knowledge_repository.documents)

        await self.service.delete_asset(tenant_id="tenant-a", asset_id=asset.asset_id)

        saved = await self.repository.get_asset("tenant-a", asset.asset_id)
        self.assertEqual(saved.status, AssetStatus.DELETED)
        self.assertNotIn(asset.asset_id, self.knowledge_repository.documents)
        with self.assertRaises(Exception):
            await self.storage.get(asset.object_key)

    async def test_worker_does_not_reindex_asset_deleted_during_scan(self):
        asset = await self.service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="race.txt",
            media_type="text/plain", content=b"deleted while scanning",
        )

        class DeletingScanner:
            async def scan(inner_self, filename, media_type, content):
                await self.service.delete_asset(
                    tenant_id="tenant-a", asset_id=asset.asset_id,
                )
                return True

        worker = FileProcessingWorker(
            queue=self.queue, worker_id="worker-a",
            repository_for_tenant=lambda tenant: self.repository,
            storage_for_tenant=lambda tenant: self.storage,
            scanner=DeletingScanner(),
            knowledge_for_tenant=lambda tenant: self.knowledge,
        )
        await worker.run_once()

        saved = await self.repository.get_asset("tenant-a", asset.asset_id)
        self.assertEqual(saved.status, AssetStatus.DELETED)
        self.assertNotIn(asset.asset_id, self.knowledge_repository.documents)
        self.assertEqual(self.queue.completed, [asset.asset_id])

    async def test_worker_removes_index_if_delete_races_with_ingestion(self):
        asset = await self.service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="ingest-race.txt",
            media_type="text/plain", content=b"delete while indexing",
        )

        class DeleteAfterIngest:
            repository = self.knowledge_repository

            async def ingest(inner_self, **kwargs):
                result = await self.knowledge.ingest(**kwargs)
                await self.service.delete_asset(
                    tenant_id="tenant-a", asset_id=asset.asset_id,
                )
                return result

        worker = FileProcessingWorker(
            queue=self.queue, worker_id="worker-a",
            repository_for_tenant=lambda tenant: self.repository,
            storage_for_tenant=lambda tenant: self.storage,
            scanner=Scanner(),
            knowledge_for_tenant=lambda tenant: DeleteAfterIngest(),
        )
        await worker.run_once()

        saved = await self.repository.get_asset("tenant-a", asset.asset_id)
        self.assertEqual(saved.status, AssetStatus.DELETED)
        self.assertNotIn(asset.asset_id, self.knowledge_repository.documents)

    async def test_transient_processing_failure_returns_asset_to_pending(self):
        asset = await self.service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="bad.bin",
            media_type="application/x-unknown", content=b"payload",
        )
        worker = FileProcessingWorker(
            queue=self.queue, worker_id="worker-a",
            repository_for_tenant=lambda tenant: self.repository,
            storage_for_tenant=lambda tenant: self.storage,
            scanner=Scanner(),
            knowledge_for_tenant=lambda tenant: self.knowledge,
        )
        await worker.run_once()
        saved = await self.repository.get_asset("tenant-a", asset.asset_id)
        self.assertEqual(saved.status, AssetStatus.PENDING)
        self.assertEqual(self.queue.failures, ["valueerror"])


if __name__ == "__main__":
    unittest.main()
