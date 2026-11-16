"""PostgreSQL-backed cleanup queue for revoked data connectors."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable
from uuid import UUID

from agent_platform.control_plane.data_connectors import (
    ConnectorCleanupJob,
    DataConnectorService,
)

logger = logging.getLogger(__name__)

ConnectionFactory = Callable[[], Any]
TenantServiceResolver = Callable[[str], Awaitable[DataConnectorService]]


class PostgresConnectorCleanupQueue:
    def __init__(self, *, connection_factory: ConnectionFactory) -> None:
        self._connect = connection_factory

    async def enqueue(
        self, *, tenant_id: str, connection_id: str, disposition: str,
    ) -> None:
        if disposition not in {"retain", "delete"}:
            raise ValueError("disposition must be retain or delete")
        db = await self._connect()
        async with db:
            await db.execute(
                """INSERT INTO platform_connector_cleanup_jobs
                   (tenant_id,connection_id,disposition)
                   VALUES (%s,%s,%s)
                   ON CONFLICT (tenant_id,connection_id,disposition) DO UPDATE
                     SET status=CASE WHEN platform_connector_cleanup_jobs.status='dead'
                                     THEN 'queued' ELSE platform_connector_cleanup_jobs.status END,
                         attempts=CASE WHEN platform_connector_cleanup_jobs.status='dead'
                                       THEN 0 ELSE platform_connector_cleanup_jobs.attempts END,
                         available_at=now(),last_error_code=NULL,updated_at=now()""",
                (tenant_id, connection_id, disposition),
            )

    async def claim(
        self, *, worker_id: str, lease_seconds: int = 120,
    ) -> ConnectorCleanupJob | None:
        if not worker_id.strip() or lease_seconds < 5:
            raise ValueError("worker_id and lease_seconds >= 5 are required")
        db = await self._connect()
        async with db:
            async with db.transaction():
                cursor = await db.execute(
                    """SELECT tenant_id,job_id,connection_id,disposition,
                              remote_revoked,sink_cleaned,attempts,max_attempts
                       FROM platform_connector_cleanup_jobs
                       WHERE (status='queued' AND available_at<=now())
                          OR (status='running' AND lease_expires_at<=now())
                       ORDER BY available_at,created_at LIMIT 1 FOR UPDATE SKIP LOCKED"""
                )
                row = await cursor.fetchone()
                if row is None:
                    return None
                updated = await db.execute(
                    """UPDATE platform_connector_cleanup_jobs
                       SET status='running',attempts=attempts+1,lease_owner=%s,
                           lease_expires_at=now()+(%s * interval '1 second'),updated_at=now()
                       WHERE tenant_id=%s AND job_id=%s RETURNING attempts""",
                    (worker_id, lease_seconds, row["tenant_id"], row["job_id"]),
                )
                attempts = await updated.fetchone()
        return ConnectorCleanupJob(
            tenant_id=row["tenant_id"], job_id=str(row["job_id"]),
            connection_id=row["connection_id"], disposition=row["disposition"],
            remote_revoked=row["remote_revoked"], sink_cleaned=row["sink_cleaned"],
            attempts=attempts["attempts"], max_attempts=row["max_attempts"],
            lease_owner=worker_id,
        )

    async def mark_remote_revoked(self, job: ConnectorCleanupJob) -> None:
        await self._mark_step(job, "remote_revoked")

    async def mark_sink_cleaned(self, job: ConnectorCleanupJob) -> None:
        await self._mark_step(job, "sink_cleaned")

    async def _mark_step(self, job: ConnectorCleanupJob, column: str) -> None:
        if column not in {"remote_revoked", "sink_cleaned"}:
            raise ValueError("invalid cleanup step")
        db = await self._connect()
        async with db:
            cursor = await db.execute(
                f"""UPDATE platform_connector_cleanup_jobs SET {column}=true,updated_at=now()
                    WHERE tenant_id=%s AND job_id=%s AND status='running' AND lease_owner=%s
                    RETURNING job_id""",
                (job.tenant_id, UUID(job.job_id), job.lease_owner),
            )
            if await cursor.fetchone() is None:
                raise RuntimeError("connector cleanup lease was lost")

    async def complete(self, job: ConnectorCleanupJob) -> None:
        db = await self._connect()
        async with db:
            cursor = await db.execute(
                """UPDATE platform_connector_cleanup_jobs
                   SET status='succeeded',lease_owner=NULL,lease_expires_at=NULL,updated_at=now()
                   WHERE tenant_id=%s AND job_id=%s AND status='running'
                     AND lease_owner=%s AND remote_revoked=true AND sink_cleaned=true
                   RETURNING job_id""",
                (job.tenant_id, UUID(job.job_id), job.lease_owner),
            )
            if await cursor.fetchone() is None:
                raise RuntimeError("connector cleanup is incomplete or lease was lost")

    async def heartbeat(self, job: ConnectorCleanupJob, *, lease_seconds: int = 120) -> bool:
        if lease_seconds < 5:
            raise ValueError("lease_seconds must be at least 5")
        db = await self._connect()
        async with db:
            cursor = await db.execute(
                """UPDATE platform_connector_cleanup_jobs
                   SET lease_expires_at=now()+(%s * interval '1 second')
                   WHERE tenant_id=%s AND job_id=%s AND status='running'
                     AND lease_owner=%s AND lease_expires_at>now()
                   RETURNING job_id""",
                (lease_seconds, job.tenant_id, UUID(job.job_id), job.lease_owner),
            )
            return await cursor.fetchone() is not None

    async def fail(self, job: ConnectorCleanupJob, *, error_code: str) -> bool:
        dead = job.attempts >= job.max_attempts
        delay = min(3600, 2 ** min(job.attempts, 10))
        db = await self._connect()
        async with db:
            cursor = await db.execute(
                """UPDATE platform_connector_cleanup_jobs
                   SET status=%s,available_at=now()+(%s * interval '1 second'),
                       lease_owner=NULL,lease_expires_at=NULL,last_error_code=%s,updated_at=now()
                   WHERE tenant_id=%s AND job_id=%s AND status='running' AND lease_owner=%s
                   RETURNING job_id""",
                ("dead" if dead else "queued", delay, error_code[:100],
                 job.tenant_id, UUID(job.job_id), job.lease_owner),
            )
            if await cursor.fetchone() is None:
                raise RuntimeError("connector cleanup lease was lost")
        return dead


class ConnectorCleanupWorker:
    def __init__(
        self, *, queue: PostgresConnectorCleanupQueue,
        worker_id: str, service: DataConnectorService | None = None,
        resolve_service: TenantServiceResolver | None = None,
        lease_seconds: int = 120,
    ) -> None:
        if not worker_id.strip() or lease_seconds < 5:
            raise ValueError("worker_id and lease_seconds >= 5 are required")
        if (service is None) == (resolve_service is None):
            raise ValueError("provide exactly one of service or tenant service resolver")
        self.queue = queue
        self.service = service
        self.resolve_service = resolve_service
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="connector-cleanup-worker")

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
                logger.exception("connector cleanup worker iteration failed")
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
        async def cleanup() -> None:
            service = self.service
            if self.resolve_service is not None:
                service = await self.resolve_service(job.tenant_id)
            if service is None:
                raise RuntimeError("tenant connector service is unavailable")
            await service.process_cleanup(job)

        processing = asyncio.create_task(cleanup())
        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(max(1, self.lease_seconds / 3))
                if not await self.queue.heartbeat(job, lease_seconds=self.lease_seconds):
                    processing.cancel()
                    raise RuntimeError("connector cleanup lease was lost")
        beat = asyncio.create_task(heartbeat())
        try:
            done, _ = await asyncio.wait(
                {processing, beat}, return_when=asyncio.FIRST_COMPLETED,
            )
            if beat in done:
                await beat
            await processing
        except asyncio.CancelledError:
            processing.cancel()
            await asyncio.gather(processing, return_exceptions=True)
            raise
        except Exception as exc:
            try:
                await self.queue.fail(job, error_code=type(exc).__name__.lower())
            except RuntimeError:
                # A replacement worker owns the lease; it will finish the job.
                pass
        finally:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)
        return True
