"""Durable file processing worker: scan, extract, index, and retry safely."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from uuid import UUID, uuid4

from agent_platform.control_plane.sessions_assets import (
    AssetStatus,
    ObjectStorage,
    SessionRepository,
)
from agent_platform.knowledge.contracts import Document, KnowledgeScope
from agent_platform.knowledge.service import KnowledgeService


def _jsonb(value: Any) -> Any:
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
    return Jsonb(value)


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
    async def fail(
        self, job: FileProcessingJob, *, error_code: str,
        error_message: str | None = None,
    ) -> bool: ...


class InMemoryFileProcessingQueue:
    """Deterministic queue adapter for unit tests and local development only."""

    def __init__(self, *, max_attempts: int = 3) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.max_attempts = max_attempts
        self._pending: list[tuple[str, str]] = []
        self._attempts: dict[tuple[str, str], int] = {}
        self._job_ids: dict[tuple[str, str], str] = {}
        self._inflight: dict[tuple[str, str], FileProcessingJob] = {}
        self._completed: set[tuple[str, str]] = set()

    async def enqueue(self, *, tenant_id: str, asset_id: str) -> None:
        key = (tenant_id, asset_id)
        if key in self._completed or key in self._inflight or key in self._pending:
            return
        self._job_ids.setdefault(key, str(uuid4()))
        self._pending.append(key)

    async def claim(
        self, *, worker_id: str, lease_seconds: int = 120,
    ) -> FileProcessingJob | None:
        if not worker_id.strip() or lease_seconds < 5:
            raise ValueError("worker_id and lease_seconds >= 5 are required")
        if not self._pending:
            return None
        key = self._pending.pop(0)
        attempts = self._attempts.get(key, 0) + 1
        self._attempts[key] = attempts
        job = FileProcessingJob(
            tenant_id=key[0],
            job_id=self._job_ids[key],
            asset_id=key[1],
            attempts=attempts,
            max_attempts=self.max_attempts,
            lease_owner=worker_id,
        )
        self._inflight[key] = job
        return job

    async def complete(self, job: FileProcessingJob) -> None:
        key = (job.tenant_id, job.asset_id)
        if self._inflight.get(key) != job:
            raise RuntimeError("file processing job is not owned by this worker")
        self._inflight.pop(key)
        self._completed.add(key)

    async def fail(
        self, job: FileProcessingJob, *, error_code: str,
        error_message: str | None = None,
    ) -> bool:
        del error_code, error_message
        key = (job.tenant_id, job.asset_id)
        if self._inflight.get(key) != job:
            raise RuntimeError("file processing job is not owned by this worker")
        self._inflight.pop(key)
        if job.attempts >= job.max_attempts:
            return True
        self._pending.append(key)
        return False


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
                if row["attempts"]:
                    await connection.execute(
                        """UPDATE platform_file_processing_attempts
                           SET status='abandoned',finished_at=now(),
                               error_code='lease_expired',error_message='worker lease expired'
                           WHERE tenant_id=%s AND job_id=%s AND attempt=%s AND status='running'""",
                        (row["tenant_id"], row["job_id"], row["attempts"]),
                    )
                updated = await connection.execute(
                    """UPDATE platform_file_processing_jobs
                       SET status='running', attempts=attempts+1, lease_owner=%s,
                           lease_expires_at=now()+(%s * interval '1 second'), updated_at=now()
                       WHERE tenant_id=%s AND job_id=%s
                       RETURNING attempts""",
                    (worker_id, lease_seconds, row["tenant_id"], row["job_id"]),
                )
                current = await updated.fetchone()
                await connection.execute(
                    """INSERT INTO platform_file_processing_attempts
                       (tenant_id,job_id,attempt,worker_id,status)
                       VALUES (%s,%s,%s,%s,'running')
                       ON CONFLICT (tenant_id,job_id,attempt) DO UPDATE
                       SET worker_id=EXCLUDED.worker_id,status='running',
                           started_at=now(),finished_at=NULL,error_code=NULL,error_message=NULL""",
                    (row["tenant_id"], row["job_id"], current["attempts"], worker_id),
                )
                return FileProcessingJob(
                    tenant_id=row["tenant_id"], job_id=str(row["job_id"]),
                    asset_id=str(row["asset_id"]), attempts=current["attempts"],
                    max_attempts=row["max_attempts"], lease_owner=worker_id,
                )

    async def record_result(self, job: FileProcessingJob, *, result: dict[str, Any],
                            parser_name: str | None = None) -> None:
        connection = await self._connect()
        async with connection:
            updated = await connection.execute(
                """UPDATE platform_file_processing_jobs
                   SET result=%s,parser_name=%s,updated_at=now()
                   WHERE tenant_id=%s AND job_id=%s AND status='running' AND lease_owner=%s""",
                (_jsonb(result), parser_name, job.tenant_id, UUID(job.job_id), job.lease_owner),
            )
            if updated.rowcount != 1:
                raise RuntimeError("file processing job lease was lost")
            await connection.execute(
                """UPDATE platform_file_processing_attempts
                   SET result=%s,parser_name=%s
                   WHERE tenant_id=%s AND job_id=%s AND attempt=%s AND worker_id=%s""",
                (_jsonb(result), parser_name, job.tenant_id, UUID(job.job_id),
                 job.attempts, job.lease_owner),
            )

    async def complete(self, job: FileProcessingJob) -> None:
        connection = await self._connect()
        async with connection:
            result = await connection.execute(
                """UPDATE platform_file_processing_jobs
                   SET status='succeeded',lease_owner=NULL,lease_expires_at=NULL,
                       completed_at=now(),updated_at=now()
                   WHERE tenant_id=%s AND job_id=%s AND status='running' AND lease_owner=%s""",
                (job.tenant_id, UUID(job.job_id), job.lease_owner),
            )
            if result.rowcount != 1:
                raise RuntimeError("file processing job lease was lost")
            await connection.execute(
                """UPDATE platform_file_processing_attempts
                   SET status='succeeded',finished_at=now()
                   WHERE tenant_id=%s AND job_id=%s AND attempt=%s AND worker_id=%s""",
                (job.tenant_id, UUID(job.job_id), job.attempts, job.lease_owner),
            )

    async def fail(self, job: FileProcessingJob, *, error_code: str,
                   error_message: str | None = None) -> bool:
        dead = job.attempts >= job.max_attempts
        delay = min(3600, 2 ** min(job.attempts, 10))
        connection = await self._connect()
        async with connection:
            result = await connection.execute(
                """UPDATE platform_file_processing_jobs
                   SET status=%s,available_at=now()+(%s * interval '1 second'),
                       lease_owner=NULL,lease_expires_at=NULL,last_error_code=%s,
                       last_error_message=%s,updated_at=now()
                   WHERE tenant_id=%s AND job_id=%s AND status='running' AND lease_owner=%s""",
                ("dead" if dead else "queued", delay, error_code[:100],
                 (error_message or error_code)[:500], job.tenant_id,
                 UUID(job.job_id), job.lease_owner),
            )
            if result.rowcount != 1:
                raise RuntimeError("file processing job lease was lost")
            await connection.execute(
                """UPDATE platform_file_processing_attempts
                   SET status=%s,error_code=%s,error_message=%s,finished_at=now()
                   WHERE tenant_id=%s AND job_id=%s AND attempt=%s AND worker_id=%s""",
                ("dead" if dead else "retrying", error_code[:100],
                 (error_message or error_code)[:500], job.tenant_id,
                 UUID(job.job_id), job.attempts, job.lease_owner),
            )
        return dead


class FileProcessingWorker:
    """File processor with both one-shot and managed lifecycle interfaces."""

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
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def health(self) -> dict[str, Any]:
        """Return a side-effect-free lifecycle snapshot."""
        return {
            "status": "running" if self.running else "stopped",
            "running": self.running,
            "worker_id": self.worker_id,
            "last_error": self._last_error,
        }

    async def start(self, *, poll_interval_seconds: float = 1.0) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("file worker poll interval must be positive")
        if self.running:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run_loop(poll_interval_seconds),
            name=f"file-processing-worker-{self.worker_id}",
        )

    async def _run_loop(self, poll_interval_seconds: float) -> None:
        while not self._stop_event.is_set():
            try:
                processed = await self.run_once()
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = type(exc).__name__
                processed = False
            if not processed:
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=poll_interval_seconds,
                    )
                except TimeoutError:
                    pass

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        if task is not None:
            await task
            if self._task is task:
                self._task = None

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
            if not await repository.set_asset_status_if_not_deleted(
                job.tenant_id, job.asset_id, AssetStatus.SCANNING,
            ):
                await self.queue.complete(job)
                return True
            content = await storage.get(asset.object_key)
            import hashlib
            if hashlib.sha256(content).hexdigest() != asset.sha256:
                raise ValueError("asset_integrity_mismatch")
            accepted = await self.scanner.scan(asset.filename, asset.media_type, content)
            latest = await repository.get_asset(job.tenant_id, job.asset_id)
            if latest is None or latest.status is AssetStatus.DELETED:
                await self.queue.complete(job)
                return True
            if not accepted:
                if not await repository.set_asset_status_if_not_deleted(
                    job.tenant_id, job.asset_id, AssetStatus.REJECTED,
                ):
                    await self.queue.complete(job)
                    return True
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
            knowledge = self.knowledge_for_tenant(job.tenant_id)
            ingest_result = await knowledge.ingest(document=document, content=content)
            parser_names = [
                type(parser).__name__ for parser in getattr(getattr(knowledge, "parsers", None), "parsers", ())
                if asset.media_type in getattr(parser, "supported_media_types", ())
            ]
            record_result = getattr(self.queue, "record_result", None)
            if callable(record_result):
                await record_result(
                    job,
                    result={
                        "document_id": ingest_result.document.document_id,
                        "document_version": ingest_result.document.version,
                        "content_hash": ingest_result.document.content_hash,
                        "chunk_count": len(ingest_result.chunks),
                        "skipped_unchanged": ingest_result.skipped_unchanged,
                        "parser_candidates": parser_names,
                    },
                    parser_name=parser_names[0] if parser_names else None,
                )
            # Deletion may race with parsing/indexing. Never resurrect a tombstoned
            # asset; remove any index entry created while the worker was running.
            latest = await repository.get_asset(job.tenant_id, job.asset_id)
            if latest is None or latest.status is AssetStatus.DELETED:
                await knowledge.repository.delete_document(job.tenant_id, job.asset_id)
                await self.queue.complete(job)
                return True
            became_available = await repository.set_asset_status_if_not_deleted(
                job.tenant_id, job.asset_id, AssetStatus.AVAILABLE,
            )
            if not became_available:
                await knowledge.repository.delete_document(job.tenant_id, job.asset_id)
            await self.queue.complete(job)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            latest = await repository.get_asset(job.tenant_id, job.asset_id)
            if latest is None or latest.status is AssetStatus.DELETED:
                await self.knowledge_for_tenant(job.tenant_id).repository.delete_document(
                    job.tenant_id, job.asset_id,
                )
                await self.queue.complete(job)
                return True
            error_code = type(exc).__name__.lower()
            fail = self.queue.fail
            try:
                dead = await fail(
                    job, error_code=error_code,
                    error_message=f"file processing failed ({error_code})",
                )
            except TypeError:
                # Backwards-compatible queue adapters may implement the old port.
                dead = await fail(job, error_code=error_code)
            if dead:
                marked_rejected = await repository.set_asset_status_if_not_deleted(
                    job.tenant_id, job.asset_id, AssetStatus.REJECTED,
                )
                if marked_rejected:
                    await storage.delete(asset.object_key)
                else:
                    await self.knowledge_for_tenant(job.tenant_id).repository.delete_document(
                        job.tenant_id, job.asset_id,
                    )
            else:
                await repository.set_asset_status_if_not_deleted(
                    job.tenant_id, job.asset_id, AssetStatus.PENDING,
                )
            return True
