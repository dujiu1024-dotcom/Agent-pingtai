"""Atomic tenant quota reservations and usage settlement.

The in-memory store is a deterministic test adapter. Production deployments
must replace it with a shared transactional PostgreSQL/Redis implementation;
Agent-supplied arguments never define or override quota policies.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol
from uuid import uuid4


class QuotaDimension(str, Enum):
    CONCURRENT_RUNS = "concurrent_runs"
    TOKENS = "tokens"
    TOOL_CALLS = "tool_calls"
    STORAGE_BYTES = "storage_bytes"
    RUNTIME_SECONDS = "runtime_seconds"


class QuotaPolicyMissing(RuntimeError):
    pass


class QuotaExceeded(RuntimeError):
    def __init__(self, tenant_id: str, dimension: QuotaDimension, limit: int, projected: int) -> None:
        self.tenant_id = tenant_id
        self.dimension = dimension
        self.limit = limit
        self.projected = projected
        super().__init__(f"tenant quota exceeded: {dimension.value} {projected}/{limit}")


@dataclass(frozen=True)
class QuotaPolicy:
    tenant_id: str
    dimension: QuotaDimension
    limit: int
    window_seconds: int | None
    hard_limit: bool = True
    warning_threshold: float = 0.8

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or self.limit < 1:
            raise ValueError("tenant_id and positive quota limit are required")
        if self.dimension == QuotaDimension.CONCURRENT_RUNS:
            if self.window_seconds is not None:
                raise ValueError("concurrent run quota must not have a time window")
        elif self.window_seconds is None or self.window_seconds < 1:
            raise ValueError("metered quota requires a positive window_seconds")
        if not 0 < self.warning_threshold <= 1:
            raise ValueError("warning_threshold must be in (0, 1]")


@dataclass(frozen=True)
class QuotaReservation:
    reservation_id: str
    tenant_id: str
    run_id: str
    dimension: QuotaDimension
    window_start: int
    amount: int
    idempotency_key: str
    created_at: datetime


@dataclass(frozen=True)
class QuotaDecision:
    reservation: QuotaReservation
    used: int
    reserved: int
    limit: int
    warning: bool


@dataclass(frozen=True)
class QuotaSettlement:
    tenant_id: str
    dimension: QuotaDimension
    used: int
    limit: int
    over_limit: bool


class QuotaStore(Protocol):
    async def reserve(self, policy: QuotaPolicy, *, run_id: str, amount: int, idempotency_key: str) -> QuotaDecision: ...
    async def commit(self, reservation_id: str, *, actual_amount: int | None = None) -> QuotaSettlement: ...
    async def release(self, reservation_id: str) -> bool: ...
    async def current_usage(self, policy: QuotaPolicy) -> tuple[int, int]: ...


class InMemoryQuotaStore:
    def __init__(self, *, clock=None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._used: dict[tuple[str, QuotaDimension, int], int] = {}
        self._reservations: dict[str, QuotaReservation] = {}
        self._idempotency: dict[tuple[str, str, QuotaDimension, str], str] = {}
        self._lock = asyncio.Lock()

    def _window_start(self, policy: QuotaPolicy) -> int:
        if policy.window_seconds is None:
            return 0
        epoch = int(self._clock().timestamp())
        return epoch - epoch % policy.window_seconds

    async def reserve(self, policy: QuotaPolicy, *, run_id: str, amount: int, idempotency_key: str) -> QuotaDecision:
        if not run_id.strip() or not idempotency_key.strip() or amount < 1:
            raise ValueError("run_id, idempotency_key and positive amount are required")
        async with self._lock:
            window = self._window_start(policy)
            idem = (policy.tenant_id, run_id, policy.dimension, idempotency_key)
            existing_id = self._idempotency.get(idem)
            if existing_id:
                existing = self._reservations.get(existing_id)
                if existing is not None:
                    used = self._used.get((policy.tenant_id, policy.dimension, window), 0)
                    reserved = sum(
                        r.amount for r in self._reservations.values()
                        if r.tenant_id == policy.tenant_id and r.dimension == policy.dimension and r.window_start == window
                    )
                    return QuotaDecision(existing, used, reserved, policy.limit, (used + reserved) / policy.limit >= policy.warning_threshold)
                raise ValueError("idempotency key has already been settled")
            used_key = (policy.tenant_id, policy.dimension, window)
            used = self._used.get(used_key, 0)
            reserved = sum(
                r.amount for r in self._reservations.values()
                if r.tenant_id == policy.tenant_id and r.dimension == policy.dimension and r.window_start == window
            )
            projected = used + reserved + amount
            if policy.hard_limit and projected > policy.limit:
                raise QuotaExceeded(policy.tenant_id, policy.dimension, policy.limit, projected)
            reservation = QuotaReservation(
                str(uuid4()), policy.tenant_id, run_id, policy.dimension, window,
                amount, idempotency_key, self._clock(),
            )
            self._reservations[reservation.reservation_id] = reservation
            self._idempotency[idem] = reservation.reservation_id
            return QuotaDecision(
                reservation, used, reserved + amount, policy.limit,
                projected / policy.limit >= policy.warning_threshold,
            )

    async def commit(self, reservation_id: str, *, actual_amount: int | None = None) -> QuotaSettlement:
        async with self._lock:
            reservation = self._reservations.pop(reservation_id, None)
            if reservation is None:
                raise KeyError("active quota reservation not found")
            if reservation.dimension == QuotaDimension.CONCURRENT_RUNS:
                raise ValueError("concurrency reservations must be released, not committed")
            amount = reservation.amount if actual_amount is None else actual_amount
            if amount < 0:
                self._reservations[reservation_id] = reservation
                raise ValueError("actual_amount must be non-negative")
            key = (reservation.tenant_id, reservation.dimension, reservation.window_start)
            used = self._used.get(key, 0) + amount
            self._used[key] = used
            return QuotaSettlement(reservation.tenant_id, reservation.dimension, used, 0, False)

    async def release(self, reservation_id: str) -> bool:
        async with self._lock:
            reservation = self._reservations.pop(reservation_id, None)
            return reservation is not None

    async def current_usage(self, policy: QuotaPolicy) -> tuple[int, int]:
        async with self._lock:
            window = self._window_start(policy)
            used = self._used.get((policy.tenant_id, policy.dimension, window), 0)
            reserved = sum(
                r.amount for r in self._reservations.values()
                if r.tenant_id == policy.tenant_id and r.dimension == policy.dimension and r.window_start == window
            )
            return used, reserved


class QuotaService:
    def __init__(self, store: QuotaStore, policies: tuple[QuotaPolicy, ...]) -> None:
        self.store = store
        self._policies = {(p.tenant_id, p.dimension): p for p in policies}

    def policy_for(self, tenant_id: str, dimension: QuotaDimension) -> QuotaPolicy:
        policy = self._policies.get((tenant_id, dimension))
        if policy is None:
            raise QuotaPolicyMissing(f"quota policy missing for {tenant_id}/{dimension.value}")
        return policy

    async def reserve(self, *, tenant_id: str, run_id: str, dimension: QuotaDimension, amount: int, idempotency_key: str) -> QuotaDecision:
        policy = self.policy_for(tenant_id, dimension)
        return await self.store.reserve(policy, run_id=run_id, amount=amount, idempotency_key=idempotency_key)

    async def commit(self, reservation_id: str, *, actual_amount: int | None = None) -> QuotaSettlement:
        return await self.store.commit(reservation_id, actual_amount=actual_amount)

    async def release(self, reservation_id: str) -> bool:
        return await self.store.release(reservation_id)
