"""Tenant-fair queueing and reject/wait admission for concurrency quotas.

The reservation store remains the cross-worker authority for hard concurrency
limits. This scheduler provides FIFO-within-tenant, round-robin-across-tenant
fairness among callers sharing one controller instance.
"""
from __future__ import annotations

import asyncio
import math
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator
from uuid import uuid4

from agent_platform.quotas.concurrency import QuotaConcurrencyLimiter
from agent_platform.quotas.service import BudgetExceeded, QuotaReservation


@dataclass(frozen=True)
class AdmissionKey:
    scope_type: str
    scope_id: str


@dataclass(frozen=True)
class AdmissionRequest:
    tenant_id: str
    run_id: str
    scope_type: str
    scope_id: str
    strategy: str = "reject"
    wait_timeout_seconds: float | None = None


class TenantFairQuotaAdmissionController:
    """Round-robin tenant scheduler backed by atomic quota reservations.

    Each tenant gets one turn per round; requests from a tenant retain FIFO
    ordering. 'reject' fails immediately when capacity is unavailable, while
    'queue' waits for a release/lease-expiry notification until its timeout.
    """

    def __init__(self, limiter: QuotaConcurrencyLimiter) -> None:
        self.limiter = limiter
        self._conditions: dict[AdmissionKey, asyncio.Condition] = {}
        self._queues: dict[AdmissionKey, OrderedDict[str, deque[str]]] = {}
        self._requests: dict[str, AdmissionRequest] = {}
        self._attempted_tenants: dict[AdmissionKey, set[str]] = {}

    @staticmethod
    def _validate(request: AdmissionRequest) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (
            request.tenant_id, request.run_id, request.scope_type, request.scope_id,
        )):
            raise ValueError("tenant, Run and quota scope identifiers are required")
        if not isinstance(request.strategy, str) or request.strategy not in {"reject", "queue"}:
            raise ValueError("strategy must be 'reject' or 'queue'")
        if request.wait_timeout_seconds is not None and (
            isinstance(request.wait_timeout_seconds, bool)
            or not isinstance(request.wait_timeout_seconds, (int, float))
            or not math.isfinite(request.wait_timeout_seconds)
            or request.wait_timeout_seconds <= 0
        ):
            raise ValueError("wait timeout must be a positive number")

    async def acquire(self, request: AdmissionRequest) -> QuotaReservation:
        self._validate(request)
        key = AdmissionKey(request.scope_type, request.scope_id)
        condition = self._conditions.setdefault(key, asyncio.Condition())
        queues = self._queues.setdefault(key, OrderedDict())
        ticket = str(uuid4())
        self._requests[ticket] = request
        async with condition:
            tenant_queue = queues.setdefault(request.tenant_id, deque())
            tenant_queue.append(ticket)
            condition.notify_all()
            try:
                async def wait_for_slot() -> QuotaReservation:
                    while True:
                        # Only the head request of the next tenant may attempt
                        # reservation. Store-level locking remains authoritative.
                        first_tenant = next(iter(queues), None)
                        is_turn = (
                            first_tenant == request.tenant_id
                            and bool(queues[request.tenant_id])
                            and queues[request.tenant_id][0] == ticket
                        )
                        if is_turn:
                            try:
                                reservation = await self.limiter.acquire(
                                    tenant_id=request.tenant_id, run_id=request.run_id,
                                    scope_type=request.scope_type, scope_id=request.scope_id,
                                )
                            except BudgetExceeded:
                                if request.strategy == "reject":
                                    raise
                                # A saturated tenant must not block other tenants
                                # sharing this queue. Rotate it and let the next
                                # tenant use its independently scoped capacity.
                                if request.tenant_id in queues:
                                    queues.move_to_end(request.tenant_id)
                                attempted = self._attempted_tenants.setdefault(key, set())
                                attempted.add(request.tenant_id)
                                if set(queues).issubset(attempted):
                                    # Every queued tenant has had one fair attempt;
                                    # sleep until capacity is actually returned.
                                    await condition.wait()
                                else:
                                    condition.notify_all()
                                    await condition.wait()
                                continue
                            self._pop(queues, request.tenant_id, ticket)
                            # Rotate this tenant behind every tenant already
                            # waiting; an empty tenant is removed from the ring.
                            if request.tenant_id in queues:
                                queues.move_to_end(request.tenant_id)
                            condition.notify_all()
                            return reservation
                        if request.strategy == "reject":
                            raise BudgetExceeded("concurrency slot unavailable; request queued ahead")
                        await condition.wait()

                if request.strategy == "queue" and request.wait_timeout_seconds is not None:
                    async with asyncio.timeout(request.wait_timeout_seconds):
                        return await wait_for_slot()
                return await wait_for_slot()
            except TimeoutError as exc:
                raise BudgetExceeded("quota admission queue wait timed out") from exc
            finally:
                if ticket in self._requests:
                    self._pop(queues, request.tenant_id, ticket)
                    self._requests.pop(ticket, None)
                    condition.notify_all()
                if not queues:
                    self._queues.pop(key, None)
                    self._attempted_tenants.pop(key, None)

    @staticmethod
    def _pop(queues: OrderedDict[str, deque[str]], tenant_id: str, ticket: str) -> None:
        queue = queues.get(tenant_id)
        if queue is None:
            return
        try:
            queue.remove(ticket)
        except ValueError:
            pass
        if not queue:
            queues.pop(tenant_id, None)

    async def notify_capacity_changed(self, scope_type: str, scope_id: str) -> None:
        """Wake queued callers after lease expiry or an external capacity change."""
        key = AdmissionKey(scope_type, scope_id)
        condition = self._conditions.get(key)
        if condition is not None:
            async with condition:
                self._attempted_tenants.pop(key, None)
                condition.notify_all()

    async def release(self, reservation: QuotaReservation) -> bool:
        released = await self.limiter.release(reservation)
        # Wake waiters after capacity has actually been returned.
        key = AdmissionKey(reservation.scope_type, reservation.scope_id)
        condition = self._conditions.get(key)
        if condition is not None:
            async with condition:
                self._attempted_tenants.pop(key, None)
                condition.notify_all()
        return released

    @asynccontextmanager
    async def slot(self, request: AdmissionRequest) -> AsyncIterator[QuotaReservation]:
        reservation = await self.acquire(request)
        try:
            async with self.limiter.quota_service.reservation_lease(
                reservation, lease_seconds=self.limiter.lease_seconds,
                heartbeat_interval_seconds=self.limiter.heartbeat_interval_seconds,
            ):
                yield reservation
        finally:
            await self.release(reservation)
