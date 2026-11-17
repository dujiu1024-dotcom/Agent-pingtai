"""Leased connector sync and credential-revocation cleanup workers."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol
from uuid import uuid4

from agent_platform.control_plane.data_connectors import (
    ConnectorError,
    DataConnectorService,
)


@dataclass(frozen=True)
class ConnectorSyncJob:
    tenant_id: str
    job_id: str
    connection_id: str
    job_type: str
    disposition: str
    attempts: int
    max_attempts: int
    lease_owner: str | None = None


class ConnectorSyncQueue(Protocol):
    async def enqueue(
        self, *, tenant_id: str, connection_id: str,
        job_type: str = "sync", disposition: str = "retain",
        max_attempts: int = 8,
    ) -> None: ...
    async def claim_next(
        self, *, worker_id: str, lease_seconds: int = 60,
    ) -> ConnectorSyncJob | None: ...
    async def complete(self, job: ConnectorSyncJob, *, worker_id: str) -> None: ...
    async def retry(
        self, job: ConnectorSyncJob, *, worker_id: str,
        error: str, delay_seconds: int,
    ) -> bool: ...


class InMemoryConnectorSyncQueue:
    def __init__(self) -> None:
        self._jobs: dict[str, ConnectorSyncJob] = {}
        self._available: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    async def enqueue(
        self, *, tenant_id: str, connection_id: str,
        job_type: str = "sync", disposition: str = "retain",
        max_attempts: int = 8,
    ) -> None:
        if job_type not in {"sync", "revoke_cleanup"} or disposition not in {"retain", "delete"}:
            raise ValueError("invalid connector job type or disposition")
        job_id = str(uuid4())
        async with self._lock:
            self._jobs[job_id] = ConnectorSyncJob(
                tenant_id, job_id, connection_id, job_type, disposition, 0, max_attempts,
            )
            self._available[job_id] = datetime.now(timezone.utc)

    async def claim_next(self, *, worker_id: str, lease_seconds: int = 60) -> ConnectorSyncJob | None:
        now = datetime.now(timezone.utc)
        async with self._lock:
            for job_id, job in tuple(self._jobs.items()):
                if self._available[job_id] > now:
                    continue
                claimed = ConnectorSyncJob(
                    job.tenant_id, job.job_id, job.connection_id, job.job_type,
                    job.disposition, job.attempts + 1, job.max_attempts, worker_id,
                )
                self._jobs[job_id] = claimed
                self._available[job_id] = now + timedelta(seconds=lease_seconds)
                return claimed
        return None

    async def complete(self, job: ConnectorSyncJob, *, worker_id: str) -> None:
        async with self._lock:
            current = self._jobs.get(job.job_id)
            if current is None or current.lease_owner != worker_id:
                raise ConnectorError("connector job lease was lost")
            self._jobs.pop(job.job_id, None)
            self._available.pop(job.job_id, None)

    async def retry(
        self, job: ConnectorSyncJob, *, worker_id: str,
        error: str, delay_seconds: int,
    ) -> bool:
        async with self._lock:
            current = self._jobs.get(job.job_id)
            if current is None or current.lease_owner != worker_id:
                raise ConnectorError("connector job lease was lost")
            if job.attempts >= job.max_attempts:
                self._jobs.pop(job.job_id, None)
                self._available.pop(job.job_id, None)
                return False
            self._jobs[job.job_id] = ConnectorSyncJob(
                job.tenant_id, job.job_id, job.connection_id, job.job_type,
                job.disposition, job.attempts, job.max_attempts,
            )
            self._available[job.job_id] = datetime.now(timezone.utc) + timedelta(
                seconds=max(0, delay_seconds),
            )
            return True


class PostgresConnectorSyncQueue:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def enqueue(
        self, *, tenant_id: str, connection_id: str,
        job_type: str = "sync", disposition: str = "retain",
        max_attempts: int = 8,
    ) -> None:
        if job_type not in {"sync", "revoke_cleanup"} or disposition not in {"retain", "delete"}:
            raise ValueError("invalid connector job type or disposition")
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_connector_sync_jobs
                   (tenant_id,job_id,connection_id,job_type,disposition,status,
                    attempts,max_attempts,available_at)
                   VALUES (%s,%s,%s,%s,%s,'queued',0,%s,now())""",
                (tenant_id, uuid4(), connection_id, job_type, disposition, max_attempts),
            )

    async def claim_next(self, *, worker_id: str, lease_seconds: int = 60) -> ConnectorSyncJob | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """WITH candidate AS (
                     SELECT tenant_id,job_id
                     FROM platform_connector_sync_jobs
                     WHERE (status='queued' AND available_at<=now())
                        OR (status='processing' AND lease_until<now())
                     ORDER BY available_at,created_at
                     FOR UPDATE SKIP LOCKED LIMIT 1
                   )
                   UPDATE platform_connector_sync_jobs j
                   SET status='processing',attempts=attempts+1,
                       lease_owner=%s,lease_until=now()+make_interval(secs => %s),
                       updated_at=now()
                   FROM candidate c
                   WHERE j.tenant_id=c.tenant_id AND j.job_id=c.job_id
                   RETURNING j.tenant_id,j.job_id,j.connection_id,j.job_type,
                             j.disposition,j.attempts,j.max_attempts,j.lease_owner""",
                (worker_id, lease_seconds),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return ConnectorSyncJob(
            tenant_id=row["tenant_id"], job_id=str(row["job_id"]),
            connection_id=row["connection_id"], job_type=row["job_type"],
            disposition=row["disposition"], attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]), lease_owner=row["lease_owner"],
        )

    async def complete(self, job: ConnectorSyncJob, *, worker_id: str) -> None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_connector_sync_jobs
                   SET status='completed',completed_at=now(),updated_at=now(),
                       lease_owner=NULL,lease_until=NULL
                   WHERE tenant_id=%s AND job_id=%s AND status='processing'
                     AND lease_owner=%s RETURNING job_id""",
                (job.tenant_id, job.job_id, worker_id),
            )
            if await cursor.fetchone() is None:
                raise ConnectorError("connector job lease was lost")

    async def retry(
        self, job: ConnectorSyncJob, *, worker_id: str,
        error: str, delay_seconds: int,
    ) -> bool:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_connector_sync_jobs
                   SET status=CASE WHEN attempts>=max_attempts THEN 'failed' ELSE 'queued' END,
                       available_at=now()+make_interval(secs => %s),
                       last_error=%s,updated_at=now(),lease_owner=NULL,lease_until=NULL
                   WHERE tenant_id=%s AND job_id=%s AND status='processing'
                     AND lease_owner=%s RETURNING status""",
                (max(0, delay_seconds), error[:500], job.tenant_id, job.job_id, worker_id),
            )
            row = await cursor.fetchone()
        if row is None:
            raise ConnectorError("connector job lease was lost")
        return row["status"] == "queued"


class DataConnectorSyncWorker:
    def __init__(
        self, *, service: DataConnectorService, queue: ConnectorSyncQueue,
        worker_id: str, lease_seconds: int = 60, max_retry_delay_seconds: int = 900,
    ) -> None:
        if not worker_id.strip() or lease_seconds < 5:
            raise ValueError("worker_id and a lease of at least 5 seconds are required")
        self.service = service
        self.queue = queue
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.max_retry_delay_seconds = max_retry_delay_seconds

    async def process_once(self) -> bool:
        job = await self.queue.claim_next(
            worker_id=self.worker_id, lease_seconds=self.lease_seconds,
        )
        if job is None:
            return False
        try:
            if job.job_type == "sync":
                connection = await self.service.repository.get(job.tenant_id, job.connection_id)
                if connection is None or connection.status != "active":
                    await self.queue.complete(job, worker_id=self.worker_id)
                    return True
                page = await self.service.sync_page(
                    tenant_id=job.tenant_id, connection_id=job.connection_id,
                )
                if page.has_more:
                    await self.queue.enqueue(
                        tenant_id=job.tenant_id, connection_id=job.connection_id,
                        job_type="sync", disposition=job.disposition,
                        max_attempts=job.max_attempts,
                    )
            elif job.job_type == "revoke_cleanup":
                await self.service.revoke(
                    tenant_id=job.tenant_id, connection_id=job.connection_id,
                    revocation_disposition=job.disposition, schedule_retry=False,
                )
                saved = await self.service.repository.get(job.tenant_id, job.connection_id)
                if saved is not None and saved.last_error:
                    raise ConnectorError("connector revocation cleanup is still pending")
            else:
                raise ConnectorError("unsupported connector job type")
            await self.queue.complete(job, worker_id=self.worker_id)
        except Exception as exc:
            delay = min(2 ** max(0, job.attempts - 1), self.max_retry_delay_seconds)
            await self.queue.retry(
                job, worker_id=self.worker_id, error=type(exc).__name__,
                delay_seconds=delay,
            )
        return True
