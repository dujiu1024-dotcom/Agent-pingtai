"""Atomic quota reservations and usage ledger reference implementation.

The in-memory adapter is for tests and single-process development only. Production
must use a shared transactional store implementing QuotaStore (see migration 012).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol
from uuid import uuid4


class BudgetExceeded(RuntimeError):
    """A server-side hard quota would be exceeded."""


@dataclass(frozen=True)
class Budget:
    tenant_id: str
    scope_type: str
    scope_id: str
    period_start: datetime
    period_end: datetime
    hard_tokens: int | None = None
    hard_cost_usd: Decimal | None = None
    hard_concurrency: int | None = None
    soft_tokens: int | None = None
    soft_cost_usd: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.tenant_id or self.scope_type not in {"tenant", "project", "agent", "model"}:
            raise ValueError("invalid budget scope")
        if not self.scope_id:
            raise ValueError("scope_id is required")
        if self.period_start.tzinfo is None or self.period_end.tzinfo is None:
            raise ValueError("budget period must be timezone-aware")
        if self.period_end <= self.period_start:
            raise ValueError("budget period end must follow start")
        for value in (self.hard_tokens, self.hard_concurrency, self.soft_tokens):
            if value is not None and value < 0:
                raise ValueError("token and concurrency limits must be non-negative")
        for value in (self.hard_cost_usd, self.soft_cost_usd):
            if value is not None and value < 0:
                raise ValueError("cost limits must be non-negative")


@dataclass(frozen=True)
class QuotaReservation:
    reservation_id: str
    tenant_id: str
    run_id: str
    scope_type: str
    scope_id: str
    reserved_tokens: int
    reserved_cost_usd: Decimal
    created_at: datetime
    period_start: datetime
    period_end: datetime


@dataclass(frozen=True)
class UsageEvent:
    event_id: str
    tenant_id: str
    run_id: str
    model_key: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    occurred_at: datetime
    project_id: str | None = None
    agent_id: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class QuotaStore(Protocol):
    async def put_budget(self, budget: Budget) -> None: ...
    async def reserve(self, reservation: QuotaReservation, budget: Budget) -> tuple[QuotaReservation, tuple[str, ...]]: ...
    async def settle(self, reservation_id: str, event: UsageEvent) -> None: ...
    async def release(self, reservation_id: str) -> bool: ...
    async def usage(self, tenant_id: str, scope_type: str, scope_id: str, start: datetime, end: datetime) -> tuple[int, Decimal, int]: ...


class InMemoryQuotaStore:
    """Atomic process-local adapter. Never use for multi-worker production."""

    def __init__(self) -> None:
        self._budgets: dict[tuple[str, str, str, datetime], Budget] = {}
        self._reservations: dict[str, QuotaReservation] = {}
        self._events: dict[str, UsageEvent] = {}
        self._settled: set[str] = set()
        self._lock = asyncio.Lock()

    async def put_budget(self, budget: Budget) -> None:
        async with self._lock:
            self._budgets[(budget.tenant_id, budget.scope_type, budget.scope_id, budget.period_start)] = budget

    def _totals(self, tenant_id: str, scope_type: str, scope_id: str, start: datetime, end: datetime) -> tuple[int, Decimal, int]:
        events = [e for e in self._events.values() if e.tenant_id == tenant_id and start <= e.occurred_at < end and self._matches(e, scope_type, scope_id)]
        reservations = [r for r in self._reservations.values() if r.tenant_id == tenant_id and r.scope_type == scope_type and r.scope_id == scope_id and r.period_start == start and r.period_end == end]
        tokens = sum(e.total_tokens for e in events) + sum(r.reserved_tokens for r in reservations)
        cost = sum((e.cost_usd for e in events), Decimal("0")) + sum((r.reserved_cost_usd for r in reservations), Decimal("0"))
        return tokens, cost, len(reservations)

    @staticmethod
    def _matches(event: UsageEvent, scope_type: str, scope_id: str) -> bool:
        return (
            (scope_type == "tenant" and event.tenant_id == scope_id)
            or (scope_type == "model" and event.model_key == scope_id)
            or (scope_type == "project" and event.project_id == scope_id)
            or (scope_type == "agent" and event.agent_id == scope_id)
        )

    async def reserve(self, reservation: QuotaReservation, budget: Budget) -> tuple[QuotaReservation, tuple[str, ...]]:
        async with self._lock:
            if reservation.reservation_id in self._reservations or reservation.reservation_id in self._settled:
                raise ValueError("reservation already exists")
            tokens, cost, concurrency = self._totals(budget.tenant_id, budget.scope_type, budget.scope_id, budget.period_start, budget.period_end)
            if budget.hard_tokens is not None and tokens + reservation.reserved_tokens > budget.hard_tokens:
                raise BudgetExceeded("hard token budget exceeded")
            if budget.hard_cost_usd is not None and cost + reservation.reserved_cost_usd > budget.hard_cost_usd:
                raise BudgetExceeded("hard cost budget exceeded")
            if budget.hard_concurrency is not None and concurrency + 1 > budget.hard_concurrency:
                raise BudgetExceeded("concurrency quota exceeded")
            self._reservations[reservation.reservation_id] = reservation
            warnings = []
            if budget.soft_tokens is not None and tokens + reservation.reserved_tokens > budget.soft_tokens:
                warnings.append("soft token budget exceeded")
            if budget.soft_cost_usd is not None and cost + reservation.reserved_cost_usd > budget.soft_cost_usd:
                warnings.append("soft cost budget exceeded")
            return reservation, tuple(warnings)

    async def settle(self, reservation_id: str, event: UsageEvent) -> None:
        async with self._lock:
            if event.event_id in self._events:
                return
            if reservation_id not in self._reservations:
                raise KeyError("active quota reservation not found")
            reservation = self._reservations[reservation_id]
            if (reservation.tenant_id, reservation.run_id) != (event.tenant_id, event.run_id):
                raise PermissionError("usage event does not match reservation tenant and Run")
            self._events[event.event_id] = event
            self._reservations.pop(reservation_id)
            self._settled.add(reservation_id)

    async def release(self, reservation_id: str) -> bool:
        async with self._lock:
            return self._reservations.pop(reservation_id, None) is not None

    async def usage(self, tenant_id: str, scope_type: str, scope_id: str, start: datetime, end: datetime) -> tuple[int, Decimal, int]:
        async with self._lock:
            return self._totals(tenant_id, scope_type, scope_id, start, end)


class QuotaService:
    """Call before dispatch; reserve estimated usage before any model/tool execution."""

    def __init__(self, store: QuotaStore) -> None:
        self.store = store

    async def configure(self, budget: Budget) -> None:
        await self.store.put_budget(budget)

    async def reserve_run(
        self, *, budget: Budget, tenant_id: str, run_id: str,
        estimated_tokens: int, estimated_cost_usd: Decimal,
    ) -> tuple[QuotaReservation, tuple[str, ...]]:
        if tenant_id != budget.tenant_id:
            raise PermissionError("budget tenant mismatch")
        if estimated_tokens < 0 or estimated_cost_usd < 0:
            raise ValueError("reservation estimates must be non-negative")
        reservation = QuotaReservation(
            reservation_id=str(uuid4()), tenant_id=tenant_id, run_id=run_id,
            scope_type=budget.scope_type, scope_id=budget.scope_id,
            reserved_tokens=estimated_tokens, reserved_cost_usd=estimated_cost_usd,
            created_at=datetime.now(timezone.utc),
            period_start=budget.period_start, period_end=budget.period_end,
        )
        return await self.store.reserve(reservation, budget)

    async def settle_run(
        self, reservation: QuotaReservation, *, model_key: str,
        input_tokens: int, output_tokens: int, cost_usd: Decimal,
        occurred_at: datetime | None = None,
    ) -> UsageEvent:
        if min(input_tokens, output_tokens) < 0 or cost_usd < 0:
            raise ValueError("actual usage must be non-negative")
        event = UsageEvent(
            event_id=str(uuid4()), tenant_id=reservation.tenant_id, run_id=reservation.run_id,
            model_key=model_key, input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=cost_usd, occurred_at=occurred_at or datetime.now(timezone.utc),
        )
        await self.store.settle(reservation.reservation_id, event)
        return event

    async def release_run(self, reservation: QuotaReservation) -> bool:
        return await self.store.release(reservation.reservation_id)
