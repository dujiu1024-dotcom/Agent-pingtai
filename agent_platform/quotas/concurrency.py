"""Shared queue/worker-pool concurrency lease helper."""
from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from typing import AsyncIterator

from agent_platform.quotas.service import QuotaReservation, QuotaService


class QuotaConcurrencyLimiter:
    """Acquire PostgreSQL-backed queue/worker slots and release them on exit."""

    def __init__(
        self, quota_service: QuotaService, *,
        lease_seconds: int = 300, heartbeat_interval_seconds: float = 60.0,
    ) -> None:
        if not 15 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 15 and 3600")
        if not 0 < heartbeat_interval_seconds < lease_seconds:
            raise ValueError("heartbeat interval must be positive and shorter than the lease")
        self.quota_service = quota_service
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = float(heartbeat_interval_seconds)

    async def _maintain_lease(self, reservation: QuotaReservation) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval_seconds)
            renewed = await self.quota_service.heartbeat_reservation(
                reservation, lease_seconds=self.lease_seconds,
            )
            if not renewed:
                raise RuntimeError("quota concurrency reservation lease could not be renewed")

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
            heartbeat_supported = callable(
                getattr(self.quota_service.store, "heartbeat_reservation", None)
            )
            if heartbeat_supported:
                # TaskGroup propagates heartbeat failures into the execution task,
                # preventing work from continuing after its shared slot lease is lost.
                async with asyncio.TaskGroup() as group:
                    heartbeat = group.create_task(
                        self._maintain_lease(reservation),
                        name=f"quota-reservation-heartbeat-{reservation.reservation_id}",
                    )
                    try:
                        yield reservation
                    finally:
                        heartbeat.cancel()
            else:
                yield reservation
        finally:
            await self.release(reservation)


class QuotaReservationMaintenanceWorker:
    """Lifecycle-managed bounded cleanup for reservations orphaned by crashed workers."""

    def __init__(
        self, quota_service: QuotaService, *,
        poll_interval_seconds: float = 30.0, batch_size: int = 500,
    ) -> None:
        if not 0.1 <= poll_interval_seconds <= 3600:
            raise ValueError("poll interval must be between 0.1 and 3600 seconds")
        if not 1 <= batch_size <= 10000:
            raise ValueError("batch size must be between 1 and 10000")
        if not callable(getattr(quota_service.store, "expire_stale_reservations", None)):
            raise ValueError("quota store must support stale reservation recovery")
        self.quota_service = quota_service
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.batch_size = batch_size
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.cycles = 0
        self.last_expired = 0
        self.last_error_type: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def run_once(self) -> int:
        expired = await self.quota_service.expire_stale_reservations(limit=self.batch_size)
        if expired < 0:
            raise ValueError("expired reservation count cannot be negative")
        self.last_expired = expired
        self.last_error_type = None
        self.cycles += 1
        return expired

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error_type = type(exc).__name__
                self.cycles += 1
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.poll_interval_seconds,
                )
            except TimeoutError:
                continue

    async def start(self) -> None:
        if self.running:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(
            self.run_forever(), name="quota-reservation-maintenance",
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
