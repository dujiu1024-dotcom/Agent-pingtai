"""Durable file scan/parse queue and worker for tenant-scoped assets."""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from agent_platform.control_plane.sessions_assets import (
    AssetStatus,
    FileAsset,
    ObjectStorage,
    SessionRepository,
)


class AssetProcessingError(RuntimeError):
    pass


@dataclass(frozen=True)
class AssetProcessingJob:
    tenant_id: str
    job_id: str
    asset_id: str
    attempts: int
    max_attempts: int
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None


@dataclass(frozen=True)
class ParsedAssetContent:
    text: str
    metadata: Mapping[str, Any]


class AssetProcessingQueue(Protocol):
    async def enqueue(self, *, tenant_id: str, asset_id: str, max_attempts: int = 5) -> None: ...
    async def claim_next(self, *, worker_id: str, lease_seconds: int = 60) -> AssetProcessingJob | None: ...
    async def complete(self, job: AssetProcessingJob, *, worker_id: str) -> None: ...
    async def retry(
        self, job: AssetProcessingJob, *, worker_id: str, error: str,
        delay_seconds: int,
    ) -> bool: ...


class AssetParser(Protocol):
    async def parse(self, asset: FileAsset, content: bytes) -> ParsedAssetContent: ...


class AssetContentSink(Protocol):
    async def upsert(self, *, asset: FileAsset, parsed: ParsedAssetContent) -> None: ...


class InMemoryAssetProcessingQueue:
    def __init__(self) -> None:
        self._jobs: dict[tuple[str, str], AssetProcessingJob] = {}
        self._available: dict[tuple[str, str], datetime] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, *, tenant_id: str, asset_id: str, max_attempts: int = 5) -> None:
        key = (tenant_id, asset_id)
        async with self._lock:
            existing = self._jobs.get(key)
            if existing is not None and existing.attempts < existing.max_attempts:
                return
            self._jobs[key] = AssetProcessingJob(
                tenant_id, str(uuid4()), asset_id, 0, max_attempts,
            )
            self._available[key] = datetime.now(timezone.utc)

    async def claim_next(self, *, worker_id: str, lease_seconds: int = 60) -> AssetProcessingJob | None:
        now = datetime.now(timezone.utc)
        async with self._lock:
            for key, job in tuple(self._jobs.items()):
                if self._available.get(key, now) > now:
                    continue
                claimed = AssetProcessingJob(
                    job.tenant_id, job.job_id, job.asset_id, job.attempts + 1,
                    job.max_attempts, worker_id,
                    now + timedelta(seconds=lease_seconds),
                )
                self._jobs[key] = claimed
                self._available[key] = claimed.lease_expires_at or now
                return claimed
        return None

    async def complete(self, job: AssetProcessingJob, *, worker_id: str) -> None:
        async with self._lock:
            key = (job.tenant_id, job.asset_id)
            current = self._jobs.get(key)
            if current is None or current.job_id != job.job_id or current.lease_owner != worker_id:
                raise AssetProcessingError("asset processing lease was lost")
            self._jobs.pop(key, None)
            self._available.pop(key, None)

    async def retry(
        self, job: AssetProcessingJob, *, worker_id: str, error: str,
        delay_seconds: int,
    ) -> bool:
        async with self._lock:
            key = (job.tenant_id, job.asset_id)
            current = self._jobs.get(key)
            if current is None or current.job_id != job.job_id or current.lease_owner != worker_id:
                raise AssetProcessingError("asset processing lease was lost")
            if job.attempts >= job.max_attempts:
                self._jobs.pop(key, None)
                self._available.pop(key, None)
                return False
            self._jobs[key] = AssetProcessingJob(
                job.tenant_id, job.job_id, job.asset_id, job.attempts,
                job.max_attempts,
            )
            self._available[key] = datetime.now(timezone.utc) + timedelta(
                seconds=max(0, delay_seconds),
            )
            return True


class PostgresAssetProcessingQueue:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def enqueue(self, *, tenant_id: str, asset_id: str, max_attempts: int = 5) -> None:
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_asset_processing_jobs
                   (tenant_id,job_id,asset_id,status,attempts,max_attempts,available_at)
                   VALUES (%s,%s,%s,'queued',0,%s,now())
                   ON CONFLICT (tenant_id,asset_id) DO NOTHING""",
                (tenant_id, uuid4(), asset_id, max_attempts),
            )

    async def claim_next(self, *, worker_id: str, lease_seconds: int = 60) -> AssetProcessingJob | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """WITH candidate AS (
                     SELECT tenant_id,job_id
                     FROM platform_asset_processing_jobs
                     WHERE (status='queued' AND available_at<=now())
                        OR (status='processing' AND lease_until<now())
                     ORDER BY available_at,created_at
                     FOR UPDATE SKIP LOCKED LIMIT 1
                   )
                   UPDATE platform_asset_processing_jobs j
                   SET status='processing',attempts=attempts+1,
                       lease_owner=%s,lease_until=now()+make_interval(secs => %s),
                       updated_at=now()
                   FROM candidate c
                   WHERE j.tenant_id=c.tenant_id AND j.job_id=c.job_id
                   RETURNING j.tenant_id,j.job_id,j.asset_id,j.attempts,j.max_attempts,
                             j.lease_owner,j.lease_until""",
                (worker_id, lease_seconds),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return AssetProcessingJob(
            tenant_id=row["tenant_id"], job_id=str(row["job_id"]),
            asset_id=str(row["asset_id"]), attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]), lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_until"],
        )

    async def complete(self, job: AssetProcessingJob, *, worker_id: str) -> None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_asset_processing_jobs
                   SET status='completed',completed_at=now(),updated_at=now(),
                       lease_owner=NULL,lease_until=NULL
                   WHERE tenant_id=%s AND job_id=%s AND status='processing'
                     AND lease_owner=%s RETURNING job_id""",
                (job.tenant_id, job.job_id, worker_id),
            )
            if await cursor.fetchone() is None:
                raise AssetProcessingError("asset processing lease was lost")

    async def retry(
        self, job: AssetProcessingJob, *, worker_id: str, error: str,
        delay_seconds: int,
    ) -> bool:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_asset_processing_jobs
                   SET status=CASE WHEN attempts>=max_attempts THEN 'failed' ELSE 'queued' END,
                       available_at=now()+make_interval(secs => %s),
                       last_error=%s,updated_at=now(),lease_owner=NULL,lease_until=NULL
                   WHERE tenant_id=%s AND job_id=%s AND status='processing'
                     AND lease_owner=%s
                   RETURNING status""",
                (max(0, delay_seconds), error[:500], job.tenant_id, job.job_id, worker_id),
            )
            row = await cursor.fetchone()
        if row is None:
            raise AssetProcessingError("asset processing lease was lost")
        return row["status"] == "queued"


class InMemoryAssetContentSink:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], ParsedAssetContent] = {}

    async def upsert(self, *, asset: FileAsset, parsed: ParsedAssetContent) -> None:
        self.records[(asset.tenant_id, asset.asset_id)] = parsed


class PostgresAssetContentSink:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def upsert(self, *, asset: FileAsset, parsed: ParsedAssetContent) -> None:
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_file_asset_contents
                   (tenant_id,asset_id,extracted_text,metadata,parsed_at)
                   VALUES (%s,%s,%s,%s,now())
                   ON CONFLICT (tenant_id,asset_id) DO UPDATE SET
                     extracted_text=EXCLUDED.extracted_text,
                     metadata=EXCLUDED.metadata,parsed_at=now()""",
                (asset.tenant_id, asset.asset_id, parsed.text, Jsonb(dict(parsed.metadata))),
            )


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


class TextAssetParser:
    """Safe UTF-8 text parser; binary formats are delegated to registered parsers."""

    def __init__(self, *, max_text_chars: int = 2_000_000) -> None:
        self.max_text_chars = max_text_chars

    async def parse(self, asset: FileAsset, content: bytes) -> ParsedAssetContent:
        supported = {
            "text/plain", "text/markdown", "text/csv", "text/html",
            "application/json", "application/xml",
        }
        if asset.media_type not in supported:
            raise AssetProcessingError("no parser is registered for this media type")
        try:
            text = content.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as exc:
            raise AssetProcessingError("text asset is not valid UTF-8") from exc
        if asset.media_type == "text/html":
            parser = _HTMLTextExtractor()
            parser.feed(text)
            text = html.unescape("\n".join(parser.parts))
        if len(text) > self.max_text_chars:
            raise AssetProcessingError("extracted text exceeds parser size limit")
        if asset.media_type == "application/json":
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                raise AssetProcessingError("JSON asset is invalid") from exc
        return ParsedAssetContent(
            text=text,
            metadata={"parser": "utf8-text-v1", "media_type": asset.media_type},
        )


class AssetProcessingWorker:
    def __init__(
        self, *, queue: AssetProcessingQueue, repository: SessionRepository,
        storage: ObjectStorage, scanner: Any, parser: AssetParser,
        content_sink: AssetContentSink, worker_id: str,
        lease_seconds: int = 60, max_retry_delay_seconds: int = 300,
    ) -> None:
        if not worker_id.strip() or lease_seconds < 5:
            raise ValueError("worker_id and a lease of at least 5 seconds are required")
        self.queue = queue
        self.repository = repository
        self.storage = storage
        self.scanner = scanner
        self.parser = parser
        self.content_sink = content_sink
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.max_retry_delay_seconds = max_retry_delay_seconds

    async def process_once(self) -> bool:
        job = await self.queue.claim_next(
            worker_id=self.worker_id, lease_seconds=self.lease_seconds,
        )
        if job is None:
            return False
        asset = await self.repository.get_asset(job.tenant_id, job.asset_id)
        if asset is None or asset.status in {AssetStatus.REJECTED, AssetStatus.DELETED}:
            await self.queue.complete(job, worker_id=self.worker_id)
            return True
        try:
            await self.repository.set_asset_status(job.tenant_id, job.asset_id, AssetStatus.SCANNING)
            content = await self.storage.get(asset.object_key)
            if len(content) != asset.size_bytes or hashlib.sha256(content).hexdigest() != asset.sha256:
                raise AssetProcessingError("stored object integrity check failed")
            accepted = await self.scanner.scan(asset.filename, asset.media_type, content)
            if not accepted:
                await self.repository.set_asset_status(job.tenant_id, job.asset_id, AssetStatus.REJECTED)
                await self.storage.delete(asset.object_key)
                await self.queue.complete(job, worker_id=self.worker_id)
                return True
            parsed = await self.parser.parse(asset, content)
            await self.content_sink.upsert(asset=asset, parsed=parsed)
            await self.repository.set_asset_status(job.tenant_id, job.asset_id, AssetStatus.AVAILABLE)
            await self.queue.complete(job, worker_id=self.worker_id)
            return True
        except Exception as exc:
            delay = min(2 ** max(0, job.attempts - 1), self.max_retry_delay_seconds)
            retrying = await self.queue.retry(
                job, worker_id=self.worker_id,
                error=type(exc).__name__, delay_seconds=delay,
            )
            if retrying:
                await self.repository.set_asset_status(job.tenant_id, job.asset_id, AssetStatus.PENDING)
            else:
                await self.repository.set_asset_status(job.tenant_id, job.asset_id, AssetStatus.REJECTED)
                await self.storage.delete(asset.object_key)
            return True
