"""Data lifecycle orchestration with tenant-bound retention, export and tombstone fan-out."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Callable, Mapping, Protocol, Sequence
from uuid import UUID, uuid4


@dataclass(frozen=True)
class RetentionPolicy:
    tenant_id: str
    resource_type: str
    retain_days: int
    archive_after_days: int | None = None
    legal_hold: bool = False

    def __post_init__(self):
        if not self.tenant_id.strip() or not self.resource_type.strip():
            raise ValueError("tenant and resource type are required")
        if isinstance(self.retain_days, bool) or not isinstance(self.retain_days, int) or self.retain_days < 1:
            raise ValueError("retain_days must be a positive integer")
        if self.archive_after_days is not None and (
            isinstance(self.archive_after_days, bool) or not isinstance(self.archive_after_days, int)
            or self.archive_after_days < 1 or self.archive_after_days > self.retain_days
        ):
            raise ValueError("archive_after_days must be within retention period")


@dataclass(frozen=True)
class Tombstone:
    tenant_id: str
    resource_type: str
    resource_id: str
    request_id: str
    requested_by: str
    reason: str
    created_at: datetime


@dataclass(frozen=True)
class ExportRequest:
    tenant_id: str
    export_id: str
    resource_type: str
    requested_by: str
    created_at: datetime


@dataclass(frozen=True)
class PropagationJob:
    tenant_id: str
    request_id: str
    resource_type: str
    resource_id: str
    sink: str
    attempts: int
    lease_owner: str


class LifecycleRepository(Protocol):
    async def save_policy(self, policy: RetentionPolicy) -> None: ...
    async def get_policy(self, tenant_id: str, resource_type: str) -> RetentionPolicy | None: ...
    async def create_tombstone(self, item: Tombstone, sinks: Sequence[str]) -> Tombstone: ...
    async def is_tombstoned(self, tenant_id: str, resource_type: str, resource_id: str) -> bool: ...
    async def create_export(self, item: ExportRequest) -> None: ...
    async def claim_propagation(self, worker_id: str, lease_seconds: int) -> PropagationJob | None: ...
    async def mark_sink_complete(self, job: PropagationJob) -> None: ...
    async def complete_request_if_ready(self, job: PropagationJob) -> None: ...


class LifecycleSource(Protocol):
    def export_records(self, *, tenant_id: str, resource_type: str) -> AsyncIterator[Mapping[str, Any]]: ...
    async def list_expired(self, *, tenant_id: str, resource_type: str, cutoff: datetime, limit: int) -> Sequence[str]: ...
    async def archive(self, *, tenant_id: str, resource_type: str, resource_id: str) -> None: ...


class LifecycleSink(Protocol):
    async def delete(self, *, tenant_id: str, resource_type: str, resource_id: str, request_id: str) -> None: ...


class DataLifecycleService:
    def __init__(self, repository: LifecycleRepository, source: LifecycleSource,
                 *, authorize: Callable[[str, str, str], bool]) -> None:
        self.repository, self.source, self.authorize = repository, source, authorize

    def _authorize(self, tenant_id: str, actor_id: str, permission: str) -> None:
        if not tenant_id.strip() or not actor_id.strip():
            raise ValueError("tenant_id and actor_id are required")
        if not self.authorize(tenant_id, actor_id, permission):
            raise PermissionError(f"{permission} is not authorized")

    async def set_policy(self, *, policy: RetentionPolicy, actor_id: str) -> None:
        self._authorize(policy.tenant_id, actor_id, "lifecycle.manage")
        await self.repository.save_policy(policy)

    async def delete_request(self, *, tenant_id: str, actor_id: str,
                             resource_type: str, resource_id: str, reason: str,
                             sinks: Sequence[str] = ("search_index", "cache", "object_storage")) -> Tombstone:
        self._authorize(tenant_id, actor_id, "data.delete")
        if not resource_type.strip() or not resource_id.strip() or not reason.strip():
            raise ValueError("resource_type, resource_id and reason are required")
        policy = await self.repository.get_policy(tenant_id, resource_type)
        if policy is not None and policy.legal_hold:
            raise PermissionError("legal hold prevents deletion")
        destinations = tuple(dict.fromkeys(sinks))
        if not destinations or set(destinations) - {"search_index", "cache", "object_storage"}:
            raise ValueError("unsupported lifecycle sink")
        item = Tombstone(tenant_id, resource_type, resource_id, str(uuid4()),
                         actor_id, reason[:500], datetime.now(timezone.utc))
        return await self.repository.create_tombstone(item, destinations)

    async def request_export(self, *, tenant_id: str, actor_id: str,
                             resource_type: str) -> ExportRequest:
        self._authorize(tenant_id, actor_id, "data.export")
        if not resource_type.strip():
            raise ValueError("resource_type is required")
        item = ExportRequest(tenant_id, str(uuid4()), resource_type, actor_id, datetime.now(timezone.utc))
        await self.repository.create_export(item)
        return item

    async def export_jsonl(self, *, request: ExportRequest) -> AsyncIterator[bytes]:
        async for record in self.source.export_records(tenant_id=request.tenant_id, resource_type=request.resource_type):
            resource_id = str(record.get("id", ""))
            if resource_id and await self.repository.is_tombstoned(request.tenant_id, request.resource_type, resource_id):
                continue
            yield (json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) + "\n").encode()

    async def enforce_retention(self, *, tenant_id: str, actor_id: str, resource_type: str,
                               now: datetime | None = None, limit: int = 100) -> tuple[int, int]:
        self._authorize(tenant_id, actor_id, "lifecycle.manage")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        policy = await self.repository.get_policy(tenant_id, resource_type)
        if policy is None:
            raise ValueError("retention policy is not configured")
        if policy.legal_hold:
            return 0, 0
        current = now or datetime.now(timezone.utc)
        archived = 0
        if policy.archive_after_days is not None:
            old = await self.source.list_expired(tenant_id=tenant_id, resource_type=resource_type,
                cutoff=current - timedelta(days=policy.archive_after_days), limit=limit)
            for rid in old:
                if not await self.repository.is_tombstoned(tenant_id, resource_type, rid):
                    await self.source.archive(tenant_id=tenant_id, resource_type=resource_type, resource_id=rid)
                    archived += 1
        expired = await self.source.list_expired(tenant_id=tenant_id, resource_type=resource_type,
            cutoff=current - timedelta(days=policy.retain_days), limit=limit)
        deleted = 0
        for rid in expired:
            if not await self.repository.is_tombstoned(tenant_id, resource_type, rid):
                await self.delete_request(tenant_id=tenant_id, actor_id=actor_id,
                    resource_type=resource_type, resource_id=rid, reason="retention_expired")
                deleted += 1
        return archived, deleted


class LifecyclePropagationWorker:
    def __init__(self, *, repository: LifecycleRepository, sinks: Mapping[str, LifecycleSink],
                 worker_id: str, lease_seconds: int = 120) -> None:
        if not worker_id.strip() or lease_seconds < 5:
            raise ValueError("worker_id and lease_seconds >= 5 are required")
        self.repository, self.sinks = repository, dict(sinks)
        self.worker_id, self.lease_seconds = worker_id, lease_seconds

    async def run_once(self) -> bool:
        job = await self.repository.claim_propagation(self.worker_id, self.lease_seconds)
        if job is None:
            return False
        adapter = self.sinks.get(job.sink)
        if adapter is None:
            raise RuntimeError(f"missing lifecycle sink adapter: {job.sink}")
        await adapter.delete(tenant_id=job.tenant_id, resource_type=job.resource_type,
            resource_id=job.resource_id, request_id=job.request_id)
        await self.repository.mark_sink_complete(job)
        await self.repository.complete_request_if_ready(job)
        return True


class PostgresLifecycleRepository:
    def __init__(self, *, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def save_policy(self, policy: RetentionPolicy) -> None:
        db = await self._connect()
        async with db:
            await db.execute("""INSERT INTO platform_data_retention_policies
                (tenant_id,resource_type,retain_days,archive_after_days,legal_hold)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_id,resource_type) DO UPDATE SET
                retain_days=EXCLUDED.retain_days,archive_after_days=EXCLUDED.archive_after_days,
                legal_hold=EXCLUDED.legal_hold,updated_at=now()""",
                (policy.tenant_id,policy.resource_type,policy.retain_days,policy.archive_after_days,policy.legal_hold))

    async def get_policy(self, tenant_id: str, resource_type: str) -> RetentionPolicy | None:
        db = await self._connect()
        async with db:
            cur = await db.execute("""SELECT tenant_id,resource_type,retain_days,archive_after_days,legal_hold
                FROM platform_data_retention_policies WHERE tenant_id=%s AND resource_type=%s""",
                (tenant_id,resource_type))
            row = await cur.fetchone()
        return RetentionPolicy(**dict(row)) if row else None

    async def create_tombstone(self, item: Tombstone, sinks: Sequence[str]) -> Tombstone:
        db = await self._connect()
        async with db:
            async with db.transaction():
                cur = await db.execute("""INSERT INTO platform_data_tombstones
                    (tenant_id,resource_type,resource_id,request_id,requested_by,reason,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (tenant_id,resource_type,resource_id) DO UPDATE
                    SET resource_id=EXCLUDED.resource_id RETURNING request_id,requested_by,reason,created_at""",
                    (item.tenant_id,item.resource_type,item.resource_id,UUID(item.request_id),
                     item.requested_by,item.reason,item.created_at))
                row = await cur.fetchone()
                request_id = row["request_id"]
                await db.execute("""INSERT INTO platform_data_deletion_requests
                    (tenant_id,request_id,resource_type,resource_id,requested_by,reason)
                    VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (tenant_id,request_id) DO NOTHING""",
                    (item.tenant_id,request_id,item.resource_type,item.resource_id,row["requested_by"],row["reason"]))
                for sink in sinks:
                    await db.execute("""INSERT INTO platform_data_lifecycle_propagation
                        (tenant_id,request_id,sink) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                        (item.tenant_id,request_id,sink))
        return Tombstone(item.tenant_id,item.resource_type,item.resource_id,str(request_id),
                         row["requested_by"],row["reason"],row["created_at"])

    async def is_tombstoned(self, tenant_id: str, resource_type: str, resource_id: str) -> bool:
        db = await self._connect()
        async with db:
            cur = await db.execute("""SELECT 1 FROM platform_data_tombstones
                WHERE tenant_id=%s AND resource_type=%s AND resource_id=%s""",
                (tenant_id,resource_type,resource_id))
            return await cur.fetchone() is not None

    async def create_export(self, item: ExportRequest) -> None:
        db = await self._connect()
        async with db:
            await db.execute("""INSERT INTO platform_data_exports
                (tenant_id,export_id,resource_type,created_by,created_at,status)
                VALUES (%s,%s,%s,%s,%s,'ready')""",
                (item.tenant_id,UUID(item.export_id),item.resource_type,item.requested_by,item.created_at))

    async def claim_propagation(self, worker_id: str, lease_seconds: int) -> PropagationJob | None:
        db = await self._connect()
        async with db:
            async with db.transaction():
                cur = await db.execute("""SELECT p.tenant_id,p.request_id,p.sink,p.attempts,
                    t.resource_type,t.resource_id FROM platform_data_lifecycle_propagation p
                    JOIN platform_data_tombstones t ON t.tenant_id=p.tenant_id AND t.request_id=p.request_id
                    WHERE p.status='queued' OR (p.status='running' AND p.lease_expires_at<=now())
                    ORDER BY p.created_at LIMIT 1 FOR UPDATE OF p SKIP LOCKED""")
                row = await cur.fetchone()
                if row is None:
                    return None
                await db.execute("""UPDATE platform_data_lifecycle_propagation
                    SET status='running',attempts=attempts+1,lease_owner=%s,
                    lease_expires_at=now()+(%s * interval '1 second'),updated_at=now()
                    WHERE tenant_id=%s AND request_id=%s AND sink=%s""",
                    (worker_id,lease_seconds,row["tenant_id"],row["request_id"],row["sink"]))
        return PropagationJob(row["tenant_id"],str(row["request_id"]),row["resource_type"],
            row["resource_id"],row["sink"],row["attempts"]+1,worker_id)

    async def mark_sink_complete(self, job: PropagationJob) -> None:
        db = await self._connect()
        async with db:
            cur = await db.execute("""UPDATE platform_data_lifecycle_propagation
                SET status='succeeded',lease_owner=NULL,lease_expires_at=NULL,updated_at=now()
                WHERE tenant_id=%s AND request_id=%s AND sink=%s
                AND status='running' AND lease_owner=%s RETURNING sink""",
                (job.tenant_id,UUID(job.request_id),job.sink,job.lease_owner))
            if await cur.fetchone() is None:
                raise RuntimeError("lifecycle propagation lease was lost")

    async def complete_request_if_ready(self, job: PropagationJob) -> None:
        db = await self._connect()
        async with db:
            await db.execute("""UPDATE platform_data_deletion_requests r SET
                status='propagated',propagated_at=now()
                WHERE r.tenant_id=%s AND r.request_id=%s AND NOT EXISTS (
                  SELECT 1 FROM platform_data_lifecycle_propagation p
                  WHERE p.tenant_id=r.tenant_id AND p.request_id=r.request_id AND p.status<>'succeeded')""",
                (job.tenant_id,UUID(job.request_id)))
