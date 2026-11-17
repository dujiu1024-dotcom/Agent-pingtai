"""Atomic quota reservations and usage ledger reference implementation.

The in-memory adapter is for tests and single-process development only. Production
must use a shared transactional store implementing QuotaStore (see migration 012).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import AsyncIterator, Mapping, Protocol
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
    hard_context_window_tokens: int | None = None

    def __post_init__(self) -> None:
        if not self.tenant_id or self.scope_type not in {"tenant", "project", "agent", "model", "run", "queue", "worker_pool"}:
            raise ValueError("invalid budget scope")
        if not self.scope_id:
            raise ValueError("scope_id is required")
        if self.period_start.tzinfo is None or self.period_end.tzinfo is None:
            raise ValueError("budget period must be timezone-aware")
        if self.period_end <= self.period_start:
            raise ValueError("budget period end must follow start")
        for value in (self.hard_tokens, self.hard_concurrency, self.soft_tokens, self.hard_input_tokens, self.hard_output_tokens, self.hard_context_window_tokens):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError("token and concurrency limits must be non-negative integers")
        if self.scope_type in {"queue", "worker_pool"}:
            if self.hard_concurrency is None or any(value is not None for value in (
                self.hard_tokens, self.hard_cost_usd, self.soft_tokens,
                self.soft_cost_usd, self.hard_input_tokens, self.hard_output_tokens,
                self.hard_context_window_tokens,
            )):
                raise ValueError("queue and worker_pool budgets must define only hard_concurrency")
        for cost in (self.hard_cost_usd, self.soft_cost_usd):
            if cost is not None and (
                not isinstance(cost, Decimal) or not cost.is_finite() or cost < 0
            ):
                raise ValueError("cost limits must be finite non-negative Decimals")
        if self.scope_type in {"queue", "worker"} and (
            self.hard_concurrency is None or self.hard_tokens is not None
            or self.hard_cost_usd is not None or self.soft_tokens is not None
            or self.soft_cost_usd is not None or self.hard_input_tokens is not None
            or self.hard_output_tokens is not None
        ):
            raise ValueError("queue/worker scopes support hard_concurrency limits only")


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
    root_run_id: str | None = None

    def __post_init__(self) -> None:
        if not self.reservation_id or not self.tenant_id or not self.run_id:
            raise ValueError("reservation, tenant and run ids are required")
        if self.root_run_id is not None and not self.root_run_id:
            raise ValueError("root_run_id must be a non-empty string when supplied")
        if self.scope_type not in {"tenant", "project", "agent", "model", "run", "queue", "worker_pool"} or not self.scope_id:
            raise ValueError("invalid reservation quota scope")
        counters = (
            self.reserved_tokens, self.reserved_input_tokens, self.reserved_output_tokens,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counters):
            raise ValueError("reserved token counters must be non-negative integers")
        if self.reserved_input_tokens + self.reserved_output_tokens != self.reserved_tokens:
            raise ValueError("total reserved tokens must equal input plus output tokens")
        if not isinstance(self.reserved_cost_usd, Decimal) or not self.reserved_cost_usd.is_finite() or self.reserved_cost_usd < 0:
            raise ValueError("reserved cost must be a finite non-negative Decimal")
        if (
            self.created_at.tzinfo is None or self.period_start.tzinfo is None
            or self.period_end.tzinfo is None or self.period_end <= self.period_start
        ):
            raise ValueError("reservation timestamps must be ordered and timezone-aware")

    def __post_init__(self) -> None:
        if not self.reservation_id or not self.tenant_id or not self.run_id:
            raise ValueError("reservation, tenant and run ids are required")
        if self.scope_type not in {"tenant", "project", "agent", "model"} or not self.scope_id:
            raise ValueError("invalid reservation quota scope")
        counters = (
            self.reserved_tokens, self.reserved_input_tokens, self.reserved_output_tokens,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counters):
            raise ValueError("reserved token counters must be non-negative integers")
        if self.reserved_input_tokens + self.reserved_output_tokens != self.reserved_tokens:
            raise ValueError("total reserved tokens must equal input plus output tokens")
        if not isinstance(self.reserved_cost_usd, Decimal) or not self.reserved_cost_usd.is_finite() or self.reserved_cost_usd < 0:
            raise ValueError("reserved cost must be a finite non-negative Decimal")
        if (
            self.created_at.tzinfo is None or self.period_start.tzinfo is None
            or self.period_end.tzinfo is None or self.period_end <= self.period_start
        ):
            raise ValueError("reservation timestamps must be ordered and timezone-aware")


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
    tool_calls: int = 0
    runtime_ms: int = 0
    storage_bytes: int = 0
    price_card_version: str | None = None
    source: str = "estimated"
    root_run_id: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id or not self.tenant_id or not self.run_id or not self.model_key:
            raise ValueError("usage event identifiers and model_key are required")
        if self.root_run_id is not None and not self.root_run_id:
            raise ValueError("root_run_id must be a non-empty string when supplied")
        counters = (
            self.input_tokens, self.output_tokens, self.tool_calls,
            self.runtime_ms, self.storage_bytes,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counters):
            raise ValueError("usage counters must be non-negative integers")
        if not isinstance(self.cost_usd, Decimal) or not self.cost_usd.is_finite() or self.cost_usd < 0:
            raise ValueError("usage cost must be a finite non-negative Decimal")
        if self.occurred_at.tzinfo is None:
            raise ValueError("usage timestamp must be timezone-aware")
        if self.source not in {"estimated", "provider_reported", "reconciled"}:
            raise ValueError("invalid usage source")

    def __post_init__(self) -> None:
        if not self.event_id or not self.tenant_id or not self.run_id or not self.model_key:
            raise ValueError("usage event identifiers and model_key are required")
        if self.root_run_id is not None and not self.root_run_id:
            raise ValueError("root_run_id must be a non-empty string when supplied")
        counters = (
            self.input_tokens, self.output_tokens, self.tool_calls,
            self.runtime_ms, self.storage_bytes,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counters):
            raise ValueError("usage counters must be non-negative integers")
        if not isinstance(self.cost_usd, Decimal) or not self.cost_usd.is_finite() or self.cost_usd < 0:
            raise ValueError("usage cost must be a finite non-negative Decimal")
        if self.occurred_at.tzinfo is None:
            raise ValueError("usage timestamp must be timezone-aware")
        if self.source not in {"estimated", "provider_reported", "reconciled"}:
            raise ValueError("invalid usage source")

    def __post_init__(self) -> None:
        if not self.event_id or not self.tenant_id or not self.run_id or not self.model_key:
            raise ValueError("usage event identifiers and model_key are required")
        for value in (self.input_tokens, self.output_tokens, self.tool_calls, self.runtime_ms, self.storage_bytes):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("usage counters must be non-negative integers")
        if not isinstance(self.cost_usd, Decimal) or not self.cost_usd.is_finite() or self.cost_usd < 0:
            raise ValueError("usage cost must be a finite non-negative Decimal")
        if self.occurred_at.tzinfo is None:
            raise ValueError("usage timestamp must be timezone-aware")
        if self.source not in {"estimated", "provider_reported", "reconciled"}:
            raise ValueError("invalid usage source")

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
        self._released: set[str] = set()
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
            or (scope_type == "run" and (event.root_run_id or event.run_id) == scope_id)
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
            removed = self._reservations.pop(reservation_id, None) is not None
            if removed:
                self._released.add(reservation_id)
            return removed

    async def usage(self, tenant_id: str, scope_type: str, scope_id: str, start: datetime, end: datetime) -> tuple[int, Decimal, int]:
        async with self._lock:
            return self._totals(tenant_id, scope_type, scope_id, start, end)

    async def list_usage(
        self, tenant_id: str, start: datetime, end: datetime, *,
        run_id: str | None = None, model_key: str | None = None, limit: int = 1000,
    ) -> list[UsageEvent]:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if (
            isinstance(limit, bool) or not isinstance(limit, int)
            or limit < 1 or limit > 10000
        ):
            raise ValueError("usage export limit must be an integer between 1 and 10000")
        if (
            not isinstance(start, datetime) or not isinstance(end, datetime)
            or start.tzinfo is None or end.tzinfo is None or end <= start
        ):
            raise ValueError("usage export range must be ordered and timezone-aware")
        if run_id is not None and (not isinstance(run_id, str) or not run_id.strip()):
            raise ValueError("run_id filter must be a non-empty string")
        if model_key is not None and (not isinstance(model_key, str) or not model_key.strip()):
            raise ValueError("model_key filter must be a non-empty string")
        async with self._lock:
            rows = [
                event for event in self._events.values()
                if event.tenant_id == tenant_id and start <= event.occurred_at < end
                and (run_id is None or event.run_id == run_id)
                and (model_key is None or event.model_key == model_key)
            ]
            return sorted(rows, key=lambda event: event.occurred_at)[:limit]


@dataclass(frozen=True)
class ScopeBudgetDefaults:
    """Conservative hard limits applied when a tenant has not configured a scope budget.

    Input/output limits are cumulative for the UTC calendar month. The context-window
    limit is a per-model-request ceiling and must still be intersected with the model's
    advertised context window by the model router.
    """

    input_tokens: int
    context_window_tokens: int
    output_tokens: int
    cost_usd: Decimal

    def __post_init__(self) -> None:
        for value in (self.input_tokens, self.context_window_tokens, self.output_tokens):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("default token budgets must be positive integers")
        if not isinstance(self.cost_usd, Decimal) or not self.cost_usd.is_finite() or self.cost_usd <= 0:
            raise ValueError("default cost budget must be a positive finite Decimal")


# Explicit, intentionally conservative defaults. These are guardrails, not pricing
# estimates; costs are USD per UTC calendar month and may be overridden by an authorized
# tenant/project/Agent/Run budget. No default is silently created for other scope types.
SAFE_DEFAULT_BUDGETS: Mapping[str, ScopeBudgetDefaults] = MappingProxyType({
    "tenant": ScopeBudgetDefaults(500_000, 32_768, 100_000, Decimal("25.00")),
    "project": ScopeBudgetDefaults(250_000, 32_768, 50_000, Decimal("10.00")),
    "agent": ScopeBudgetDefaults(100_000, 16_384, 25_000, Decimal("5.00")),
    "run": ScopeBudgetDefaults(50_000, 16_384, 12_000, Decimal("1.00")),
})


def safe_default_budget(
    *, tenant_id: str, scope_type: str, scope_id: str,
    now: datetime | None = None,
    defaults: Mapping[str, ScopeBudgetDefaults] = SAFE_DEFAULT_BUDGETS,
) -> Budget | None:
    """Build a default hard budget for one scope over the current UTC calendar month."""
    policy = defaults.get(scope_type)
    if policy is None:
        return None
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("default budget clock must be timezone-aware")
    instant = instant.astimezone(timezone.utc)
    start = datetime(instant.year, instant.month, 1, tzinfo=timezone.utc)
    if instant.month == 12:
        end = datetime(instant.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(instant.year, instant.month + 1, 1, tzinfo=timezone.utc)
    return Budget(
        tenant_id=tenant_id, scope_type=scope_type, scope_id=scope_id,
        period_start=start, period_end=end,
        hard_input_tokens=policy.input_tokens,
        hard_context_window_tokens=policy.context_window_tokens,
        hard_output_tokens=policy.output_tokens,
        hard_cost_usd=policy.cost_usd,
    )


class QuotaService:
    """Call before dispatch; reserve estimated usage before any model/tool execution."""

    def __init__(
        self, store: QuotaStore, *,
        defaults: Mapping[str, ScopeBudgetDefaults] = SAFE_DEFAULT_BUDGETS,
    ) -> None:
        self.store = store
        self.defaults = defaults

    async def configure(self, budget: Budget) -> None:
        await self.store.put_budget(budget)

    async def _budget_or_safe_default(
        self, *, tenant_id: str, scope_type: str, scope_id: str,
        now: datetime,
    ) -> Budget:
        budget = await self.store.get_budget(tenant_id, scope_type, scope_id, now)
        if budget is not None:
            return budget
        budget = safe_default_budget(
            tenant_id=tenant_id, scope_type=scope_type, scope_id=scope_id,
            now=now, defaults=self.defaults,
        )
        if budget is None:
            raise BudgetExceeded("quota budget not configured and no safe default exists for this scope")
        # Persist defaults so PostgreSQL's atomic reservation function sees the same
        # policy and all workers share one effective budget for this scope and period.
        await self.store.put_budget(budget)
        return budget

    async def reserve_run(
        self, *, tenant_id: str, run_id: str, scope_type: str, scope_id: str,
        estimated_tokens: int, estimated_cost_usd: Decimal,
        estimated_input_tokens: int | None = None,
        estimated_output_tokens: int | None = None,
        root_run_id: str | None = None,
    ) -> tuple[QuotaReservation, tuple[str, ...]]:
        if not tenant_id or not run_id:
            raise ValueError("tenant_id and run_id are required")
        if root_run_id is not None and (not isinstance(root_run_id, str) or not root_run_id.strip()):
            raise ValueError("root_run_id must be a non-empty string")
        root_run_id = root_run_id or run_id
        if scope_type == "run" and scope_id != root_run_id:
            raise ValueError("Run budget scope_id must equal root_run_id")
        budget = await self._budget_or_safe_default(
            tenant_id=tenant_id, scope_type=scope_type, scope_id=scope_id,
            now=datetime.now(timezone.utc),
        )
        if (
            not isinstance(estimated_tokens, int) or isinstance(estimated_tokens, bool)
            or estimated_tokens < 0 or not isinstance(estimated_cost_usd, Decimal)
            or not estimated_cost_usd.is_finite() or estimated_cost_usd < 0
        ):
            raise ValueError("reservation estimates must be non-negative integers and finite Decimal")
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
            root_run_id=root_run_id,
        )
        return await self.store.reserve(reservation)


    async def reserve_concurrency(
        self, *, tenant_id: str, run_id: str,
        scope_type: str, scope_id: str,
    ) -> QuotaReservation:
        """Acquire a shared queue/worker-pool slot; callers must release it in finally."""
        if not tenant_id or not run_id or not scope_id:
            raise ValueError("tenant_id, run_id and scope_id are required")
        if scope_type not in {"queue", "worker_pool"}:
            raise ValueError("concurrency scope must be queue or worker_pool")
        budget = await self.store.get_budget(
            tenant_id, scope_type, scope_id, datetime.now(timezone.utc),
        )
        if budget is None or budget.hard_concurrency is None:
            raise BudgetExceeded("queue/worker_pool concurrency budget not configured")
        reservation = QuotaReservation(
            reservation_id=str(uuid4()), tenant_id=tenant_id, run_id=run_id,
            scope_type=scope_type, scope_id=scope_id,
            reserved_tokens=0, reserved_cost_usd=Decimal("0"),
            created_at=datetime.now(timezone.utc),
            period_start=budget.period_start, period_end=budget.period_end,
            reserved_input_tokens=0, reserved_output_tokens=0,
            root_run_id=run_id,
        )
        await self.store.reserve(reservation)
        return reservation

    async def settle_run(
        self, reservation: QuotaReservation, *, model_key: str,
        input_tokens: int, output_tokens: int, cost_usd: Decimal,
        occurred_at: datetime | None = None, project_id: str | None = None,
        agent_id: str | None = None, tool_calls: int = 0, runtime_ms: int = 0,
        storage_bytes: int = 0, price_card_version: str | None = None,
        source: str = "estimated",
    ) -> UsageEvent:
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
               for value in (input_tokens, output_tokens, tool_calls, runtime_ms, storage_bytes)):
            raise ValueError("actual usage counters must be non-negative integers")
        if not isinstance(cost_usd, Decimal) or not cost_usd.is_finite() or cost_usd < 0:
            raise ValueError("actual cost must be a finite non-negative Decimal")
        if occurred_at is not None and occurred_at.tzinfo is None:
            raise ValueError("usage timestamp must be timezone-aware")
        if not model_key.strip():
            raise ValueError("model_key is required")
        if source not in {"estimated", "provider_reported", "reconciled"}:
            raise ValueError("invalid usage source")
        event = UsageEvent(
            event_id=str(uuid4()), tenant_id=reservation.tenant_id, run_id=reservation.run_id,
            model_key=model_key, input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=cost_usd, occurred_at=occurred_at or datetime.now(timezone.utc),
            project_id=project_id, agent_id=agent_id, tool_calls=tool_calls,
            runtime_ms=runtime_ms, storage_bytes=storage_bytes,
            price_card_version=price_card_version, source=source,
            root_run_id=reservation.root_run_id or reservation.run_id,
        )
        await self.store.settle(reservation.reservation_id, event)
        return event

    async def release_run(self, reservation: QuotaReservation) -> bool:
        return await self.store.release(reservation.reservation_id)
    async def heartbeat_reservation(
        self, reservation: QuotaReservation, *, lease_seconds: int = 300,
    ) -> bool:
        """Renew a live quota reservation; expired reservations cannot be resurrected."""
        heartbeat = getattr(self.store, "heartbeat_reservation", None)
        if not callable(heartbeat):
            return True
        return bool(await heartbeat(reservation.reservation_id, lease_seconds=lease_seconds))

    async def expire_stale_reservations(self, *, limit: int = 500) -> int:
        """Reclaim expired reservations; production stores must implement the operation."""
        expire = getattr(self.store, "expire_stale_reservations", None)
        if not callable(expire):
            raise RuntimeError("quota store does not support reservation lease recovery")
        return int(await expire(limit=limit))

    @asynccontextmanager
    async def reservation_lease(
        self, reservation: QuotaReservation, *,
        lease_seconds: int = 300, heartbeat_interval_seconds: float = 60.0,
    ) -> AsyncIterator[QuotaReservation]:
        """Keep any active Run or concurrency reservation alive during long work."""
        if not 15 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 15 and 3600")
        if not 0 < heartbeat_interval_seconds < lease_seconds:
            raise ValueError("heartbeat interval must be positive and shorter than the lease")
        if not callable(getattr(self.store, "heartbeat_reservation", None)):
            yield reservation
            return

        async def renew() -> None:
            while True:
                await asyncio.sleep(heartbeat_interval_seconds)
                if not await self.heartbeat_reservation(
                    reservation, lease_seconds=lease_seconds,
                ):
                    raise RuntimeError("quota reservation lease could not be renewed")

        async with asyncio.TaskGroup() as group:
            heartbeat = group.create_task(
                renew(), name=f"quota-reservation-heartbeat-{reservation.reservation_id}",
            )
            try:
                yield reservation
            finally:
                heartbeat.cancel()
