"""Shared queue/worker-pool concurrency lease helper."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from agent_platform.quotas.service import QuotaReservation, QuotaService


class QuotaConcurrencyLimiter:
    """Acquire PostgreSQL-backed queue/worker slots and release them on exit."""

    def __init__(self, quota_service: QuotaService) -> None:
        self.quota_service = quota_service

    async def acquire(
        self, *, tenant_id: str, run_id: str,
        scope_type: str, scope_id: str,
    ) -> QuotaReservation:
        return await self.quota_service.reserve_concurrency(
            tenant_id=tenant_id, run_id=run_id,
            scope_type=scope_type, scope_id=scope_id,
        )

    async def release(self, reservation: QuotaReservation) -> bool:
        return await self.quota_service.release_run(reservation)

    @asynccontextmanager
    async def slot(
        self, *, tenant_id: str, run_id: str,
        scope_type: str, scope_id: str,
    ) -> AsyncIterator[QuotaReservation]:
        reservation = await self.acquire(
            tenant_id=tenant_id, run_id=run_id,
            scope_type=scope_type, scope_id=scope_id,
        )
        try:
            yield reservation
        finally:
            await self.release(reservation)
