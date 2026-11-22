from __future__ import annotations

import hashlib
import unittest

from agent_platform.control_plane.file_processing import (
    FileProcessingJob,
    FileProcessingWorker,
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


class MemoryProcessingQueue:
    """Small deterministic queue mock that connects upload to the real worker."""

    def __init__(self) -> None:
        self.pending: list[tuple[str, str]] = []
        self.completed: list[str] = []
        self.results: list[dict] = []

    async def enqueue(self, *, tenant_id: str, asset_id: str) -> None:
        self.pending.append((tenant_id, asset_id))

    async def claim(
        self, *, worker_id: str, lease_seconds: int = 120,
    ) -> FileProcessingJob | None:
        del lease_seconds
        if not self.pending:
            return None
        tenant_id, asset_id = self.pending.pop(0)
        return FileProcessingJob(
            tenant_id=tenant_id,
            job_id=f"job-{asset_id}",
            asset_id=asset_id,
            attempts=1,
            max_attempts=3,
            lease_owner=worker_id,
        )

    async def complete(self, job: FileProcessingJob) -> None:
        self.completed.append(job.asset_id)

    async def fail(
        self, job: FileProcessingJob, *, error_code: str,
        error_message: str | None = None,
    ) -> bool:
        del job, error_code, error_message
        return False

    async def record_result(
        self, job: FileProcessingJob, *, result: dict,
        parser_name: str | None = None,
    ) -> None:
        self.results.append({
            "asset_id": job.asset_id,
            "result": result,
            "parser_name": parser_name,
        })


class RecordingScanner:
    def __init__(self, *, accepted: bool = True) -> None:
        self.accepted = accepted
        self.calls: list[tuple[str, str, bytes]] = []

    async def scan(self, filename: str, media_type: str, content: bytes) -> bool:
        self.calls.append((filename, media_type, content))
        return self.accepted


class FileWorkerUploadFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = InMemorySessionRepository()
        self.storage = InMemoryObjectStorage()
        self.queue = MemoryProcessingQueue()
        self.scanner = RecordingScanner()
        self.knowledge_repository = InMemoryKnowledgeRepository()
        self.knowledge = KnowledgeService(
            self.knowledge_repository,
            parsers=ParserRegistry(parsers=(PlainTextParser(),)),
        )
        self.service = SessionAssetService(
            self.repository,
            self.storage,
            processing_queue=self.queue,
            knowledge_for_tenant=lambda _tenant: self.knowledge,
        )
        self.worker = FileProcessingWorker(
            queue=self.queue,
            worker_id="file-worker-test",
            repository_for_tenant=lambda _tenant: self.repository,
            storage_for_tenant=lambda _tenant: self.storage,
            scanner=self.scanner,
            knowledge_for_tenant=lambda _tenant: self.knowledge,
        )

    async def test_upload_is_received_scanned_and_ingested_by_worker(self) -> None:
        payload = b"FileWorker end-to-end searchable upload content"
        asset = await self.service.upload_asset(
            tenant_id="tenant-e2e",
            owner_id="user-e2e",
            filename="worker-notes.txt",
            media_type="text/plain",
            content=payload,
        )

        # Receive: object bytes and metadata are durable in the in-memory adapters,
        # while the asset remains pending until the worker owns the queued job.
        self.assertEqual(asset.status, AssetStatus.PENDING)
        self.assertEqual(await self.storage.get(asset.object_key), payload)
        self.assertEqual(
            self.queue.pending,
            [("tenant-e2e", asset.asset_id)],
        )
        self.assertEqual(asset.size_bytes, len(payload))
        self.assertEqual(asset.sha256, hashlib.sha256(payload).hexdigest())

        # Scan + ingest: run the actual FileProcessingWorker against those adapters.
        self.assertTrue(await self.worker.run_once())
        saved = await self.repository.get_asset("tenant-e2e", asset.asset_id)
        self.assertIsNotNone(saved)
        self.assertEqual(saved.status, AssetStatus.AVAILABLE)
        self.assertEqual(
            self.scanner.calls,
            [("worker-notes.txt", "text/plain", payload)],
        )

        principal = KnowledgePrincipal(
            tenant_id="tenant-e2e",
            user_id="user-e2e",
        )
        hits = await self.knowledge.retrieve(
            principal=principal,
            query="searchable upload",
        )
        self.assertTrue(any(hit.document_id == asset.asset_id for hit in hits))
        self.assertEqual(self.queue.completed, [asset.asset_id])
        self.assertEqual(len(self.queue.results), 1)
        result = self.queue.results[0]
        self.assertEqual(result["asset_id"], asset.asset_id)
        self.assertEqual(result["result"]["document_id"], asset.asset_id)
        self.assertGreaterEqual(result["result"]["chunk_count"], 1)
        self.assertEqual(result["parser_name"], "PlainTextParser")

    async def test_scanner_rejection_never_creates_knowledge_document(self) -> None:
        self.scanner.accepted = False
        asset = await self.service.upload_asset(
            tenant_id="tenant-e2e",
            owner_id="user-e2e",
            filename="rejected.txt",
            media_type="text/plain",
            content=b"known-bad upload",
        )

        self.assertTrue(await self.worker.run_once())
        saved = await self.repository.get_asset("tenant-e2e", asset.asset_id)
        self.assertIsNotNone(saved)
        self.assertEqual(saved.status, AssetStatus.REJECTED)
        with self.assertRaises(Exception):
            await self.storage.get(asset.object_key)
        self.assertEqual(self.knowledge_repository.documents, {})
        self.assertEqual(self.queue.completed, [asset.asset_id])
        self.assertEqual(len(self.scanner.calls), 1)


if __name__ == "__main__":
    unittest.main()
