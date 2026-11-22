from __future__ import annotations

import asyncio
import unittest

from agent_platform.control_plane.file_processing import (
    FileProcessingJob,
    FileProcessingWorker,
    InMemoryFileProcessingQueue,
)
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
        self.results = []
    async def record_result(self, job, *, result, parser_name=None):
        self.results.append((job.asset_id, result, parser_name))
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
        self.assertEqual(self.queue.results[0][0], asset.asset_id)
        self.assertEqual(self.queue.results[0][1]["document_id"], asset.asset_id)
        self.assertEqual(self.queue.results[0][1]["chunk_count"], 1)
        self.assertEqual(self.queue.results[0][2], "PlainTextParser")

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
        self.assertTrue(any(key[1] == asset.asset_id for key in self.knowledge_repository.documents))

        await self.service.delete_asset(tenant_id="tenant-a", asset_id=asset.asset_id)

        saved = await self.repository.get_asset("tenant-a", asset.asset_id)
        self.assertEqual(saved.status, AssetStatus.DELETED)
        self.assertTrue(all(
            document.deleted_at is not None
            for key, document in self.knowledge_repository.documents.items()
            if key[1] == asset.asset_id
        ))
        self.assertFalse(any(
            key[1] == asset.asset_id for key in self.knowledge_repository.chunks
        ))
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
        self.assertTrue(all(
            document.deleted_at is not None
            for key, document in self.knowledge_repository.documents.items()
            if key[1] == asset.asset_id
        ))
        self.assertFalse(any(
            key[1] == asset.asset_id for key in self.knowledge_repository.chunks
        ))
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
        self.assertTrue(all(
            document.deleted_at is not None
            for key, document in self.knowledge_repository.documents.items()
            if key[1] == asset.asset_id
        ))
        self.assertFalse(any(
            key[1] == asset.asset_id for key in self.knowledge_repository.chunks
        ))

    async def test_delete_retries_index_cleanup_after_transient_failure(self):
        asset = await self.service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="retry-delete.txt",
            media_type="text/plain", content=b"retry index deletion",
        )
        original_delete = self.knowledge_repository.delete_document
        attempts = 0

        async def flaky_delete(tenant_id, document_id):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary index outage")
            return await original_delete(tenant_id, document_id)

        self.knowledge_repository.delete_document = flaky_delete
        with self.assertRaisesRegex(Exception, "index cleanup failed"):
            await self.service.delete_asset(
                tenant_id="tenant-a", asset_id=asset.asset_id,
            )

        saved = await self.repository.get_asset("tenant-a", asset.asset_id)
        self.assertEqual(saved.status, AssetStatus.DELETED)
        self.assertEqual(await self.storage.get(asset.object_key), b"retry index deletion")

        await self.service.delete_asset(tenant_id="tenant-a", asset_id=asset.asset_id)
        with self.assertRaises(Exception):
            await self.storage.get(asset.object_key)
        self.assertEqual(attempts, 2)

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


    async def test_in_memory_queue_deduplicates_retries_and_dead_letters(self):
        queue = InMemoryFileProcessingQueue(max_attempts=2)
        await queue.enqueue(tenant_id="tenant-a", asset_id="asset-a")
        await queue.enqueue(tenant_id="tenant-a", asset_id="asset-a")

        first = await queue.claim(worker_id="worker-a")
        self.assertIsNotNone(first)
        self.assertEqual(first.attempts, 1)
        self.assertFalse(await queue.fail(first, error_code="temporary"))

        second = await queue.claim(worker_id="worker-b")
        self.assertIsNotNone(second)
        self.assertEqual(second.attempts, 2)
        self.assertTrue(await queue.fail(second, error_code="permanent"))
        self.assertIsNone(await queue.claim(worker_id="worker-a"))

    async def test_worker_start_stop_health_processes_memory_queue(self):
        asset = await self.service.upload_asset(
            tenant_id="tenant-a", owner_id="user-a", filename="managed.txt",
            media_type="text/plain", content=b"managed worker searchable content",
        )
        queue = InMemoryFileProcessingQueue()
        await queue.enqueue(tenant_id="tenant-a", asset_id=asset.asset_id)
        worker = FileProcessingWorker(
            queue=queue, worker_id="memory-worker",
            repository_for_tenant=lambda tenant: self.repository,
            storage_for_tenant=lambda tenant: self.storage,
            scanner=Scanner(),
            knowledge_for_tenant=lambda tenant: self.knowledge,
        )

        self.assertEqual(worker.health()["status"], "stopped")
        await worker.start(poll_interval_seconds=0.01)
        await worker.start(poll_interval_seconds=0.01)
        self.assertEqual(worker.health()["status"], "running")

        for _ in range(100):
            saved = await self.repository.get_asset("tenant-a", asset.asset_id)
            if saved.status is AssetStatus.AVAILABLE:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(saved.status, AssetStatus.AVAILABLE)

        await worker.stop()
        await worker.stop()
        self.assertEqual(worker.health()["status"], "stopped")
        self.assertFalse(worker.health()["running"])
        self.assertIsNone(await queue.claim(worker_id="verification-worker"))

    async def test_worker_rejects_non_positive_poll_interval(self):
        worker = FileProcessingWorker(
            queue=InMemoryFileProcessingQueue(), worker_id="memory-worker",
            repository_for_tenant=lambda tenant: self.repository,
            storage_for_tenant=lambda tenant: self.storage,
            scanner=Scanner(),
            knowledge_for_tenant=lambda tenant: self.knowledge,
        )
        with self.assertRaisesRegex(ValueError, "poll interval"):
            await worker.start(poll_interval_seconds=0)


if __name__ == "__main__":
    unittest.main()
