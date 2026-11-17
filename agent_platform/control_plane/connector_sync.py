"""Durable PostgreSQL queue and leased worker for external connector sync."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol
from uuid import UUID

from agent_platform.control_plane.data_connectors import DataConnectorService

logger = logging.getLogger(__name__)

ConnectionFactory = Callable[[], Any]
TenantServiceResolver = Callable[[str], Awaitable[DataConnectorService]]


@dataclass(frozen=True)
class ConnectorSyncAuditEvent:
    event_type: str
    tenant_id: str
    actor_id: str
    job_id: str
    connection_id: str
    attempt: int
    outcome: str
    occurred_at: datetime
    error_type: str | None = None


class ConnectorSyncAuditSink(Protocol):
    async def append(self, event: ConnectorSyncAuditEvent) -> None: ...


@dataclass(frozen=True)
class ConnectorSyncJob:
    tenant_id: str
    job_id: str
    connection_id: str
    attempts: int
    max_attempts: int
    lease_owner: str


class PostgresConnectorSyncQueue:
    def __init__(self, *, connection_factory: ConnectionFactory) -> None:
        self._connect = connection_factory

    async def enqueue(self, *, tenant_id: str, connection_id: str) -> None:
        db = await self._connect()
        async with db:
            await db.execute(
                """INSERT INTO platform_connector_sync_jobs
                   (tenant_id,connection_id)
                   VALUES (%s,%s)
                   ON CONFLICT (tenant_id,connection_id) DO UPDATE
                     SET status=CASE WHEN platform_connector_sync_jobs.status IN ('dead','succeeded')
                                     THEN 'queued' ELSE platform_connector_sync_jobs.status END,
                         attempts=CASE WHEN platform_connector_sync_jobs.status IN ('dead','succeeded')
                                       THEN 0 ELSE platform_connector_sync_jobs.attempts END,
                         available_at=CASE WHEN platform_connector_sync_jobs.status IN ('dead','succeeded')
                                           THEN now() ELSE platform_connector_sync_jobs.available_at END,
                         last_error_code=CASE WHEN platform_connector_sync_jobs.status IN ('dead','succeeded')
                                               THEN NULL ELSE platform_connector_sync_jobs.last_error_code END,
                         completed_at=NULL,updated_at=now()""",
                (tenant_id, connection_id),
            )

    async def claim(
        self, *, worker_id: str, lease_seconds: int = 120,
    ) -> ConnectorSyncJob | None:
        if not worker_id.strip() or lease_seconds < 5:
            raise ValueError("worker_id and lease_seconds >= 5 are required")
        db = await self._connect()
        async with db:
            async with db.transaction():
                cursor = await db.execute(
                    """SELECT tenant_id,job_id,connection_id,attempts,max_attempts
                       FROM platform_connector_sync_jobs
                       WHERE (status='queued' AND available_at<=now())
                          OR (status='running' AND lease_expires_at<=now())
                       ORDER BY available_at,created_at
                       LIMIT 1 FOR UPDATE SKIP LOCKED"""
                )
                row = await cursor.fetchone()
                if row is None:
                    return None
                updated = await db.execute(
                    """UPDATE platform_connector_sync_jobs
                       SET status='running',attempts=attempts+1,lease_owner=%s,
                           lease_expires_at=now()+(%s * interval '1 second'),updated_at=now()
                       WHERE tenant_id=%s AND job_id=%s RETURNING attempts""",
                    (worker_id, lease_seconds, row["tenant_id"], row["job_id"]),
                )
                attempt = await updated.fetchone()
        return ConnectorSyncJob(
            tenant_id=row["tenant_id"], job_id=str(row["job_id"]),
            connection_id=row["connection_id"], attempts=attempt["attempts"],
            max_attempts=row["max_attempts"], lease_owner=worker_id,
        )

    async def heartbeat(self, job: ConnectorSyncJob, *, lease_seconds: int = 120) -> bool:
        if lease_seconds < 5:
            raise ValueError("lease_seconds must be at least 5")
        db = await self._connect()
        async with db:
            cursor = await db.execute(
                """UPDATE platform_connector_sync_jobs
                   SET lease_expires_at=now()+(%s * interval '1 second')
                   WHERE tenant_id=%s AND job_id=%s AND status='running'
                     AND lease_owner=%s AND lease_expires_at>now()
                   RETURNING job_id""",
                (lease_seconds, job.tenant_id, UUID(job.job_id), job.lease_owner),
            )
            return await cursor.fetchone() is not None

    async def complete(self, job: ConnectorSyncJob) -> None:
        db = await self._connect()
        async with db:
            cursor = await db.execute(
                """UPDATE platform_connector_sync_jobs
                   SET status='succeeded',lease_owner=NULL,lease_expires_at=NULL,
                       completed_at=now(),updated_at=now()
                   WHERE tenant_id=%s AND job_id=%s AND status='running'
                     AND lease_owner=%s RETURNING job_id""",
                (job.tenant_id, UUID(job.job_id), job.lease_owner),
            )
            if await cursor.fetchone() is None:
                raise RuntimeError("connector sync lease was lost")

    async def fail(self, job: ConnectorSyncJob, *, error_code: str) -> bool:
        dead = job.attempts >= job.max_attempts
        delay = min(3600, 2 ** min(job.attempts, 10))
        db = await self._connect()
        async with db:
            cursor = await db.execute(
                """UPDATE platform_connector_sync_jobs
                   SET status=%s,available_at=now()+(%s * interval '1 second'),
                       lease_owner=NULL,lease_expires_at=NULL,last_error_code=%s,updated_at=now()
                   WHERE tenant_id=%s AND job_id=%s AND status='running'
                     AND lease_owner=%s RETURNING job_id""",
                ("dead" if dead else "queued", delay, error_code[:100],
                 job.tenant_id, UUID(job.job_id), job.lease_owner),
            )
            if await cursor.fetchone() is None:
                raise RuntimeError("connector sync lease was lost")
        return dead


class ConnectorSyncWorker:
    def __init__(
        self, *, queue: PostgresConnectorSyncQueue,
        worker_id: str, service: DataConnectorService | None = None,
        resolve_service: TenantServiceResolver | None = None,
        audit_sink: ConnectorSyncAuditSink,
        lease_seconds: int = 120,
    ) -> None:
        if not worker_id.strip() or lease_seconds < 5:
            raise ValueError("worker_id and lease_seconds >= 5 are required")
        if (service is None) == (resolve_service is None):
            raise ValueError("provide exactly one of service or tenant service resolver")
        self.queue = queue
        self.service = service
        self.resolve_service = resolve_service
        self.audit_sink = audit_sink
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="connector-sync-worker")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                if await self.run_once():
                    continue
            except Exception:
                # A transient database outage must not permanently stop the worker.
                logger.exception("connector sync worker iteration failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=1.0)
            except TimeoutError:
                pass

    async def run_once(self) -> bool:
        job = await self.queue.claim(
            worker_id=self.worker_id, lease_seconds=self.lease_seconds,
        )
        if job is None:
            return False

        audit_started = False

        async def append_audit(outcome: str, *, error_type: str | None = None) -> None:
            await self.audit_sink.append(ConnectorSyncAuditEvent(
                event_type="connector.sync",
                tenant_id=job.tenant_id,
                actor_id="system:connector-sync-worker",
                job_id=job.job_id,
                connection_id=job.connection_id,
                attempt=job.attempts,
                outcome=outcome,
                occurred_at=datetime.now(timezone.utc),
                error_type=error_type,
            ))

        async def synchronize() -> None:
            nonlocal audit_started
            service = self.service
            if self.resolve_service is not None:
                service = await self.resolve_service(job.tenant_id)
            if service is None:
                raise RuntimeError("tenant connector service is unavailable")
            connection = await service.repository.get(job.tenant_id, job.connection_id)
            # Revocation is terminal for pending sync work; never revive credentials.
            if connection is None or connection.status != "active":
                await append_audit("skipped", error_type="connection_inactive")
                return
            # Write-ahead audit is mandatory before provider/network side effects.
            await append_audit("started")
            audit_started = True
            await service.sync_once(
                tenant_id=job.tenant_id, connection_id=job.connection_id,
            )

        processing = asyncio.create_task(synchronize())

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(max(1, self.lease_seconds / 3))
                if not await self.queue.heartbeat(job, lease_seconds=self.lease_seconds):
                    processing.cancel()
                    raise RuntimeError("connector sync lease was lost")

        beat = asyncio.create_task(heartbeat())
        try:
            done, _ = await asyncio.wait(
                {processing, beat}, return_when=asyncio.FIRST_COMPLETED,
            )
            if beat in done:
                await beat
            await processing
            await self.queue.complete(job)
            if audit_started:
                try:
                    await append_audit("succeeded")
                except Exception:
                    # The provider side effect and durable queue completion already
                    # succeeded. Never retry it merely because result-audit storage
                    # is temporarily unavailable.
                    logger.exception("connector sync success audit append failed")
        except asyncio.CancelledError:
            processing.cancel()
            await asyncio.gather(processing, return_exceptions=True)
            raise
        except Exception as exc:
            if audit_started:
                try:
                    await append_audit("failed", error_type=type(exc).__name__)
                except Exception:
                    logger.exception("connector sync failure audit append failed")
            try:
                await self.queue.fail(job, error_code=type(exc).__name__.lower())
            except RuntimeError:
                # A replacement worker owns the lease and will retry safely.
                pass
        finally:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)
        return True
