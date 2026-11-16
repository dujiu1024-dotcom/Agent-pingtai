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
    hard_input_tokens: int | None = None
    hard_output_tokens: int | None = None

    def __post_init__(self) -> None:
        if not self.tenant_id or self.scope_type not in {"tenant", "project", "agent", "model"}:
            raise ValueError("invalid budget scope")
        if not self.scope_id:
            raise ValueError("scope_id is required")
        if self.period_start.tzinfo is None or self.period_end.tzinfo is None:
            raise ValueError("budget period must be timezone-aware")
        if self.period_end <= self.period_start:
            raise ValueError("budget period end must follow start")
        for value in (self.hard_tokens, self.hard_concurrency, self.soft_tokens, self.hard_input_tokens, self.hard_output_tokens):
            if value is not None and value < 0:
                raise ValueError("token and concurrency limits must be non-negative")
        for cost in (self.hard_cost_usd, self.soft_cost_usd):
            if cost is not None and cost < 0:
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
    reserved_input_tokens: int = 0
    reserved_output_tokens: int = 0

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
    tool_calls: int = 0
    runtime_ms: int = 0
    storage_bytes: int = 0
    price_card_version: str | None = None
    source: str = "estimated"

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class QuotaStore(Protocol):
    async def put_budget(self, budget: Budget) -> None: ...
    async def get_budget(self, tenant_id: str, scope_type: str, scope_id: str, now: datetime) -> Budget | None: ...
    async def reserve(self, reservation: QuotaReservation) -> tuple[QuotaReservation, tuple[str, ...]]: ...
    async def settle(self, reservation_id: str, event: UsageEvent) -> None: ...
    async def release(self, reservation_id: str) -> bool: ...
    async def usage(self, tenant_id: str, scope_type: str, scope_id: str, start: datetime, end: datetime) -> tuple[int, Decimal, int]: ...
    async def list_usage(self, tenant_id: str, start: datetime, end: datetime, *, run_id: str | None = None, model_key: str | None = None, limit: int = 1000) -> list[UsageEvent]: ...


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

    async def get_budget(self, tenant_id: str, scope_type: str, scope_id: str, now: datetime) -> Budget | None:
        async with self._lock:
            candidates = [b for b in self._budgets.values() if b.tenant_id == tenant_id and b.scope_type == scope_type and b.scope_id == scope_id and b.period_start <= now < b.period_end]
            return max(candidates, key=lambda b: b.period_start) if candidates else None

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

    async def reserve(self, reservation: QuotaReservation) -> tuple[QuotaReservation, tuple[str, ...]]:
        async with self._lock:
            if reservation.reservation_id in self._reservations or reservation.reservation_id in self._settled:
                raise ValueError("reservation already exists")
            if any(
                r.tenant_id == reservation.tenant_id and r.run_id == reservation.run_id
                and r.scope_type == reservation.scope_type and r.scope_id == reservation.scope_id
                for r in self._reservations.values()
            ):
                raise ValueError("active reservation already exists for this Run and quota scope")
            budget = self._budgets.get((reservation.tenant_id, reservation.scope_type, reservation.scope_id, reservation.period_start))
            if budget is None:
                raise BudgetExceeded("quota budget not configured")
            tokens, cost, concurrency = self._totals(budget.tenant_id, budget.scope_type, budget.scope_id, budget.period_start, budget.period_end)
            events = [
                event for event in self._events.values()
                if event.tenant_id == budget.tenant_id
                and budget.period_start <= event.occurred_at < budget.period_end
                and self._matches(event, budget.scope_type, budget.scope_id)
            ]
            reservations = [
                item for item in self._reservations.values()
                if item.tenant_id == budget.tenant_id
                and item.scope_type == budget.scope_type
                and item.scope_id == budget.scope_id
                and item.period_start == budget.period_start
            ]
            input_used = sum(event.input_tokens for event in events) + sum(
                item.reserved_input_tokens for item in reservations
            )
            output_used = sum(event.output_tokens for event in events) + sum(
                item.reserved_output_tokens for item in reservations
            )
            if budget.hard_input_tokens is not None and input_used + reservation.reserved_input_tokens > budget.hard_input_tokens:
                raise BudgetExceeded("hard input token budget exceeded")
            if budget.hard_output_tokens is not None and output_used + reservation.reserved_output_tokens > budget.hard_output_tokens:
                raise BudgetExceeded("hard output token budget exceeded")
            if budget.hard_tokens is not None and tokens + reservation.reserved_tokens > budget.hard_tokens:
                raise BudgetExceeded("hard token budget exceeded")
            if budget.hard_cost_usd is not None and cost + reservation.reserved_cost_usd > budget.hard_cost_usd:
                raise BudgetExceeded("hard cost budget exceeded")
            if budget.hard_concurrency is not None and concurrency + 1 > budget.hard_concurrency:
                raise BudgetExceeded("concurrency quota exceeded")
            self._reservations[reservation.reservation_id] = reservation
            warnings = []
            if budget.soft_tokens is not None and tokens + reservation.reserved_tokens >= budget.soft_tokens:
                warnings.append("soft token budget exceeded")
            if budget.soft_cost_usd is not None and cost + reservation.reserved_cost_usd >= budget.soft_cost_usd:
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
            if not reservation.period_start <= event.occurred_at < reservation.period_end:
                raise ValueError("usage event is outside the reserved budget period")
            if not self._matches(event, reservation.scope_type, reservation.scope_id):
                raise PermissionError("usage event does not match reserved quota scope")
            self._events[event.event_id] = event
            self._reservations.pop(reservation_id)
            self._settled.add(reservation_id)

    async def release(self, reservation_id: str) -> bool:
        async with self._lock:
            return self._reservations.pop(reservation_id, None) is not None

    async def usage(self, tenant_id: str, scope_type: str, scope_id: str, start: datetime, end: datetime) -> tuple[int, Decimal, int]:
        async with self._lock:
            return self._totals(tenant_id, scope_type, scope_id, start, end)

    async def list_usage(
        self, tenant_id: str, start: datetime, end: datetime, *,
        run_id: str | None = None, model_key: str | None = None, limit: int = 1000,
    ) -> list[UsageEvent]:
        if limit < 1 or limit > 10000 or end <= start:
            raise ValueError("invalid usage export range or limit")
        async with self._lock:
            rows = [
                event for event in self._events.values()
                if event.tenant_id == tenant_id and start <= event.occurred_at < end
                and (run_id is None or event.run_id == run_id)
                and (model_key is None or event.model_key == model_key)
            ]
            return sorted(rows, key=lambda event: event.occurred_at)[:limit]


class QuotaService:
    """Call before dispatch; reserve estimated usage before any model/tool execution."""

    def __init__(self, store: QuotaStore) -> None:
        self.store = store

    async def configure(self, budget: Budget) -> None:
        await self.store.put_budget(budget)

    async def reserve_run(
        self, *, tenant_id: str, run_id: str, scope_type: str, scope_id: str,
        estimated_tokens: int, estimated_cost_usd: Decimal,
        estimated_input_tokens: int | None = None,
        estimated_output_tokens: int | None = None,
    ) -> tuple[QuotaReservation, tuple[str, ...]]:
        if not tenant_id or not run_id:
            raise ValueError("tenant_id and run_id are required")
        budget = await self.store.get_budget(tenant_id, scope_type, scope_id, datetime.now(timezone.utc))
        if budget is None:
            raise BudgetExceeded("quota budget not configured")
        if estimated_tokens < 0 or estimated_cost_usd < 0:
            raise ValueError("reservation estimates must be non-negative")
        if budget.hard_input_tokens is not None or budget.hard_output_tokens is not None:
            if estimated_input_tokens is None or estimated_output_tokens is None:
                raise BudgetExceeded("separate input and output estimates are required by this budget")
        if estimated_input_tokens is None:
            estimated_input_tokens = estimated_tokens
        if estimated_output_tokens is None:
            estimated_output_tokens = 0
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in (estimated_input_tokens, estimated_output_tokens)
        ):
            raise ValueError("input/output estimates must be non-negative integers")
        if estimated_input_tokens + estimated_output_tokens != estimated_tokens:
            raise ValueError("total estimate must equal input plus output estimates")
        reservation = QuotaReservation(
            reservation_id=str(uuid4()), tenant_id=tenant_id, run_id=run_id,
            scope_type=budget.scope_type, scope_id=budget.scope_id,
            reserved_tokens=estimated_tokens, reserved_cost_usd=estimated_cost_usd,
            created_at=datetime.now(timezone.utc),
            period_start=budget.period_start, period_end=budget.period_end,
            reserved_input_tokens=estimated_input_tokens,
            reserved_output_tokens=estimated_output_tokens,
        )
        return await self.store.reserve(reservation)

    async def settle_run(
        self, reservation: QuotaReservation, *, model_key: str,
        input_tokens: int, output_tokens: int, cost_usd: Decimal,
        occurred_at: datetime | None = None, project_id: str | None = None,
        agent_id: str | None = None, tool_calls: int = 0, runtime_ms: int = 0,
        storage_bytes: int = 0, price_card_version: str | None = None,
        source: str = "estimated",
    ) -> UsageEvent:
        if min(input_tokens, output_tokens, tool_calls, runtime_ms, storage_bytes) < 0 or cost_usd < 0:
            raise ValueError("actual usage must be non-negative")
        if source not in {"estimated", "provider_reported", "reconciled"}:
            raise ValueError("invalid usage source")
        event = UsageEvent(
            event_id=str(uuid4()), tenant_id=reservation.tenant_id, run_id=reservation.run_id,
            model_key=model_key, input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=cost_usd, occurred_at=occurred_at or datetime.now(timezone.utc),
            project_id=project_id, agent_id=agent_id, tool_calls=tool_calls,
            runtime_ms=runtime_ms, storage_bytes=storage_bytes,
            price_card_version=price_card_version, source=source,
        )
        await self.store.settle(reservation.reservation_id, event)
        return event

    async def release_run(self, reservation: QuotaReservation) -> bool:
        return await self.store.release(reservation.reservation_id)
