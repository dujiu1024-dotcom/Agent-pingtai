"""Durable file processing worker: scan, extract, index, and retry safely."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from uuid import UUID

from agent_platform.control_plane.sessions_assets import (
    AssetStatus,
    ObjectStorage,
    SessionRepository,
)
from agent_platform.knowledge.contracts import Document, KnowledgeScope
from agent_platform.knowledge.service import KnowledgeService


@dataclass(frozen=True)
class FileProcessingJob:
    tenant_id: str
    job_id: str
    asset_id: str
    attempts: int
    max_attempts: int
    lease_owner: str


class FileProcessingQueue(Protocol):
    async def enqueue(self, *, tenant_id: str, asset_id: str) -> None: ...
    async def claim(self, *, worker_id: str, lease_seconds: int = 120) -> FileProcessingJob | None: ...
    async def complete(self, job: FileProcessingJob) -> None: ...
    async def fail(self, job: FileProcessingJob, *, error_code: str) -> bool: ...


class FileProcessingScanner(Protocol):
    async def scan(self, filename: str, media_type: str, content: bytes) -> bool: ...


ConnectionFactory = Callable[[], Any]


class PostgresFileProcessingQueue:
    """Global queue adapter; each job remains tenant-bound at every mutation."""

    def __init__(self, *, connection_factory: ConnectionFactory) -> None:
        self._connect = connection_factory

    async def enqueue(self, *, tenant_id: str, asset_id: str) -> None:
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_file_processing_jobs (tenant_id,asset_id)
                   VALUES (%s,%s) ON CONFLICT (tenant_id,asset_id) DO NOTHING""",
                (tenant_id, UUID(asset_id)),
            )

    async def claim(self, *, worker_id: str, lease_seconds: int = 120) -> FileProcessingJob | None:
        if not worker_id.strip() or lease_seconds < 5:
            raise ValueError("worker_id and lease_seconds >= 5 are required")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """SELECT tenant_id,job_id,asset_id,attempts,max_attempts
                       FROM platform_file_processing_jobs
                       WHERE (status='queued' AND available_at<=now())
                          OR (status='running' AND lease_expires_at<=now())
                       ORDER BY available_at,created_at
                       LIMIT 1 FOR UPDATE SKIP LOCKED"""
                )
                row = await cursor.fetchone()
                if row is None:
                    return None
                updated = await connection.execute(
                    """UPDATE platform_file_processing_jobs
                       SET status='running', attempts=attempts+1, lease_owner=%s,
                           lease_expires_at=now()+(%s * interval '1 second'), updated_at=now()
                       WHERE tenant_id=%s AND job_id=%s
                       RETURNING attempts""",
                    (worker_id, lease_seconds, row["tenant_id"], row["job_id"]),
                )
                current = await updated.fetchone()
                return FileProcessingJob(
                    tenant_id=row["tenant_id"], job_id=str(row["job_id"]),
                    asset_id=str(row["asset_id"]), attempts=current["attempts"],
                    max_attempts=row["max_attempts"], lease_owner=worker_id,
                )

    async def complete(self, job: FileProcessingJob) -> None:
        connection = await self._connect()
        async with connection:
            result = await connection.execute(
                """UPDATE platform_file_processing_jobs
                   SET status='succeeded',lease_owner=NULL,lease_expires_at=NULL,updated_at=now()
                   WHERE tenant_id=%s AND job_id=%s AND status='running' AND lease_owner=%s""",
                (job.tenant_id, UUID(job.job_id), job.lease_owner),
            )
            if result.rowcount != 1:
                raise RuntimeError("file processing job lease was lost")

    async def fail(self, job: FileProcessingJob, *, error_code: str) -> bool:
        dead = job.attempts >= job.max_attempts
        delay = min(3600, 2 ** min(job.attempts, 10))
        connection = await self._connect()
        async with connection:
            result = await connection.execute(
                """UPDATE platform_file_processing_jobs
                   SET status=%s,available_at=now()+(%s * interval '1 second'),
                       lease_owner=NULL,lease_expires_at=NULL,last_error_code=%s,updated_at=now()
                   WHERE tenant_id=%s AND job_id=%s AND status='running' AND lease_owner=%s""",
                ("dead" if dead else "queued", delay, error_code[:100],
                 job.tenant_id, UUID(job.job_id), job.lease_owner),
            )
            if result.rowcount != 1:
                raise RuntimeError("file processing job lease was lost")
        return dead


class FileProcessingWorker:
    """One-shot worker. Host scheduler repeatedly calls run_once()."""

    def __init__(
        self, *, queue: FileProcessingQueue, worker_id: str,
        repository_for_tenant: Callable[[str], SessionRepository],
        storage_for_tenant: Callable[[str], ObjectStorage],
        scanner: FileProcessingScanner,
        knowledge_for_tenant: Callable[[str], KnowledgeService],
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        self.queue = queue
        self.worker_id = worker_id
        self.repository_for_tenant = repository_for_tenant
        self.storage_for_tenant = storage_for_tenant
        self.scanner = scanner
        self.knowledge_for_tenant = knowledge_for_tenant

    async def run_once(self) -> bool:
        job = await self.queue.claim(worker_id=self.worker_id)
        if job is None:
            return False
        repository = self.repository_for_tenant(job.tenant_id)
        storage = self.storage_for_tenant(job.tenant_id)
        asset = await repository.get_asset(job.tenant_id, job.asset_id)
        if asset is None or asset.status is AssetStatus.DELETED:
            await self.queue.complete(job)
            return True
        try:
            await repository.set_asset_status(job.tenant_id, job.asset_id, AssetStatus.SCANNING)
            content = await storage.get(asset.object_key)
            import hashlib
            if hashlib.sha256(content).hexdigest() != asset.sha256:
                raise ValueError("asset_integrity_mismatch")
            accepted = await self.scanner.scan(asset.filename, asset.media_type, content)
            if not accepted:
                await repository.set_asset_status(job.tenant_id, job.asset_id, AssetStatus.REJECTED)
                await storage.delete(asset.object_key)
                await self.queue.complete(job)
                return True
            scope = KnowledgeScope.PROJECT if asset.project_id else KnowledgeScope.PRIVATE
            document = Document(
                tenant_id=asset.tenant_id, title=asset.filename,
                source_uri=f"asset://{asset.tenant_id}/{asset.asset_id}",
                content_hash=asset.sha256, scope=scope,
                project_id=asset.project_id,
                owner_user_id=asset.owner_id if scope is KnowledgeScope.PRIVATE else None,
                document_id=asset.asset_id, media_type=asset.media_type,
                metadata={"asset_id": asset.asset_id, "session_id": asset.session_id},
            )
            await self.knowledge_for_tenant(job.tenant_id).ingest(
                document=document, content=content,
            )
            await repository.set_asset_status(job.tenant_id, job.asset_id, AssetStatus.AVAILABLE)
            await self.queue.complete(job)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_code = type(exc).__name__.lower()
            dead = await self.queue.fail(job, error_code=error_code)
            if dead:
                await repository.set_asset_status(job.tenant_id, job.asset_id, AssetStatus.REJECTED)
                await storage.delete(asset.object_key)
            else:
                await repository.set_asset_status(job.tenant_id, job.asset_id, AssetStatus.PENDING)
            return True
