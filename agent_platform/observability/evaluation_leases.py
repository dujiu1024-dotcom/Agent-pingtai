"""PostgreSQL-backed leases for multi-replica evaluation scheduling."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Callable
from agent_platform.observability.evaluation_worker import EvaluationSchedule


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _fingerprint(schedule: EvaluationSchedule) -> str:
    payload = {
        "tenant_id": schedule.tenant_id, "schedule_id": schedule.schedule_id,
        "dataset_id": schedule.dataset_id, "dataset_version": schedule.dataset_version,
        "model_key": schedule.model_key, "interval_seconds": schedule.interval.total_seconds(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PostgresEvaluationScheduleLeaseStore:
    """Atomically claim schedules and persist their next eligible execution time."""
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def claim(self, schedule: EvaluationSchedule, *, owner_id: str, now: datetime, lease_seconds: float) -> bool:
        _aware(now, "now")
        if not owner_id.strip():
            raise ValueError("owner_id is required")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, (int, float)) or not 3 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 3 and 3600")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """INSERT INTO platform_evaluation_schedule_leases
                       (tenant_id,schedule_id,schedule_fingerprint,next_run_at,lease_owner,lease_expires_at,updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (tenant_id,schedule_id) DO UPDATE
                         SET lease_owner=EXCLUDED.lease_owner,
                             lease_expires_at=EXCLUDED.lease_expires_at,updated_at=EXCLUDED.updated_at
                       WHERE platform_evaluation_schedule_leases.schedule_fingerprint=EXCLUDED.schedule_fingerprint
                         AND platform_evaluation_schedule_leases.next_run_at <= %s
                         AND (platform_evaluation_schedule_leases.lease_expires_at IS NULL
                              OR platform_evaluation_schedule_leases.lease_expires_at <= %s)
                       RETURNING schedule_id""",
                    (schedule.tenant_id,schedule.schedule_id,_fingerprint(schedule),now,owner_id,
                     now+timedelta(seconds=lease_seconds),now,now,now),
                )
                return await cursor.fetchone() is not None

    async def renew(self, schedule: EvaluationSchedule, *, owner_id: str, now: datetime, lease_seconds: float) -> bool:
        _aware(now, "now")
        if not owner_id.strip():
            raise ValueError("owner_id is required")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, (int, float)) or not 3 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 3 and 3600")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_evaluation_schedule_leases SET lease_expires_at=%s,updated_at=%s
                   WHERE tenant_id=%s AND schedule_id=%s AND lease_owner=%s
                     AND schedule_fingerprint=%s AND lease_expires_at > %s RETURNING schedule_id""",
                (now+timedelta(seconds=lease_seconds),now,schedule.tenant_id,schedule.schedule_id,
                 owner_id,_fingerprint(schedule),now),
            )
            return await cursor.fetchone() is not None

    async def complete(self, schedule: EvaluationSchedule, *, owner_id: str, now: datetime, succeeded: bool) -> bool:
        _aware(now, "now")
        if not owner_id.strip():
            raise ValueError("owner_id is required")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_evaluation_schedule_leases
                   SET next_run_at=%s,lease_owner=NULL,lease_expires_at=NULL,updated_at=%s
                   WHERE tenant_id=%s AND schedule_id=%s AND lease_owner=%s
                     AND schedule_fingerprint=%s RETURNING schedule_id""",
                (now+schedule.interval if succeeded else now,now,schedule.tenant_id,
                 schedule.schedule_id,owner_id,_fingerprint(schedule)),
            )
            return await cursor.fetchone() is not None
