"""Atomic multi-scope quota reservation groups.

A group reserves the same execution estimate against several independent budgets
(e.g. tenant and root Run) in one storage transaction. Settlement writes exactly
one usage event and consumes every reservation in the group.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from agent_platform.quotas.postgres import PostgresQuotaStore
from agent_platform.quotas.service import (
    BudgetExceeded,
    InMemoryQuotaStore,
    QuotaReservation,
    UsageEvent,
)


@dataclass(frozen=True)
class QuotaScope:
    scope_type: str
    scope_id: str


@dataclass(frozen=True)
class QuotaReservationGroup:
    group_id: str
    tenant_id: str
    run_id: str
    root_run_id: str
    reservations: tuple[QuotaReservation, ...]

    def __post_init__(self) -> None:
        if not self.reservations:
            raise ValueError("reservation group cannot be empty")
        if len({(r.scope_type, r.scope_id) for r in self.reservations}) != len(self.reservations):
            raise ValueError("reservation group contains duplicate scopes")
        if any((r.tenant_id, r.run_id, r.root_run_id or r.run_id) !=
               (self.tenant_id, self.run_id, self.root_run_id) for r in self.reservations):
            raise ValueError("reservation group identity does not match its reservations")


class QuotaReservationGroupService:
    """Atomic Tenant + Root Run reservation API for in-memory and PostgreSQL stores."""

    def __init__(self, store):
        self.store = store

    async def reserve_run(
        self, *, tenant_id: str, run_id: str, root_run_id: str,
        scopes: tuple[QuotaScope, ...], estimated_tokens: int,
        estimated_cost_usd: Decimal, estimated_input_tokens: int | None = None,
        estimated_output_tokens: int | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[QuotaReservationGroup, tuple[str, ...]]:
        if not tenant_id or not run_id or not root_run_id:
            raise ValueError("tenant_id, run_id and root_run_id are required")
        if not scopes:
            raise ValueError("at least one quota scope is required")
        if len({(s.scope_type, s.scope_id) for s in scopes}) != len(scopes):
            raise ValueError("quota scopes must be unique")
        if not any(s.scope_type == "tenant" and s.scope_id == tenant_id for s in scopes):
            raise ValueError("reservation group must include the tenant budget")
        if not any(s.scope_type == "run" and s.scope_id == root_run_id for s in scopes):
            raise ValueError("reservation group must include the root Run budget")
        if any(s.scope_type == "run" and s.scope_id != root_run_id for s in scopes):
            raise ValueError("Run budget scope_id must equal root_run_id")
        if (not isinstance(estimated_tokens, int) or isinstance(estimated_tokens, bool)
                or estimated_tokens < 0 or not isinstance(estimated_cost_usd, Decimal)
                or not estimated_cost_usd.is_finite() or estimated_cost_usd < 0):
            raise ValueError("reservation estimates must be non-negative and finite")
        input_tokens = estimated_tokens if estimated_input_tokens is None else estimated_input_tokens
        output_tokens = 0 if estimated_output_tokens is None else estimated_output_tokens
        if any(not isinstance(v, int) or isinstance(v, bool) or v < 0
               for v in (input_tokens, output_tokens)) or input_tokens + output_tokens != estimated_tokens:
            raise ValueError("input/output estimates must be non-negative and sum to total tokens")

        now = datetime.now(timezone.utc)
        budgets = []
        for scope in scopes:
            budget = await self.store.get_budget(tenant_id, scope.scope_type, scope.scope_id, now)
            if budget is None:
                raise BudgetExceeded(f"quota budget not configured: {scope.scope_type}/{scope.scope_id}")
            if (budget.hard_input_tokens is not None or budget.hard_output_tokens is not None) and (
                estimated_input_tokens is None or estimated_output_tokens is None
            ):
                raise BudgetExceeded("separate input and output estimates are required by this budget")
            budgets.append(budget)
        # Stable scope ordering makes deterministic group IDs replay identically
        # even if callers supply the scope tuple in a different order.
        budgets.sort(key=lambda budget: (budget.scope_type, budget.scope_id))
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not idempotency_key.strip()
        ):
            raise ValueError("idempotency_key must be a non-empty string")
        group_id = str(
            uuid5(NAMESPACE_URL, f"quota-group:{tenant_id}:{run_id}:{root_run_id}:{idempotency_key}")
            if idempotency_key is not None else uuid4()
        )
        reservations = tuple(
            QuotaReservation(
                reservation_id=str(
                    uuid5(NAMESPACE_URL, f"{group_id}:{b.scope_type}:{b.scope_id}")
                    if idempotency_key is not None else uuid4()
                ), tenant_id=tenant_id, run_id=run_id,
                scope_type=b.scope_type, scope_id=b.scope_id,
                reserved_tokens=estimated_tokens, reserved_cost_usd=estimated_cost_usd,
                created_at=now, period_start=b.period_start, period_end=b.period_end,
                reserved_input_tokens=input_tokens, reserved_output_tokens=output_tokens,
                root_run_id=root_run_id,
            ) for b in budgets
        )
        group = QuotaReservationGroup(group_id, tenant_id, run_id, root_run_id, reservations)
        if isinstance(self.store, InMemoryQuotaStore):
            warnings = await self._reserve_memory(group, budgets)
        elif isinstance(self.store, PostgresQuotaStore):
            warnings = await self._reserve_postgres(group)
        else:
            method = getattr(self.store, "reserve_group", None)
            if method is None:
                raise TypeError("quota store does not implement atomic reservation groups")
            warnings = await method(group)
        return group, tuple(warnings)

    async def _reserve_memory(self, group, budgets):
        store = self.store
        warnings = []
        async with store._lock:
            # An idempotent replay must return the original reservation group,
            # not reserve capacity a second time. Partial groups are corruption.
            existing = [store._reservations.get(r.reservation_id) for r in group.reservations]
            settled = [r.reservation_id in store._settled for r in group.reservations]
            released = [r.reservation_id in store._released for r in group.reservations]
            if any(existing) or any(settled) or any(released):
                if all(existing) and not any(settled):
                    for actual, expected in zip(existing, group.reservations):
                        if (
                            actual.tenant_id, actual.run_id, actual.root_run_id,
                            actual.scope_type, actual.scope_id, actual.reserved_tokens,
                            actual.reserved_cost_usd, actual.reserved_input_tokens,
                            actual.reserved_output_tokens, actual.period_start, actual.period_end,
                        ) != (
                            expected.tenant_id, expected.run_id, expected.root_run_id,
                            expected.scope_type, expected.scope_id, expected.reserved_tokens,
                            expected.reserved_cost_usd, expected.reserved_input_tokens,
                            expected.reserved_output_tokens, expected.period_start, expected.period_end,
                        ):
                            raise ValueError("idempotency key was reused with different reservation parameters")
                    return []
                if all(settled) and not any(existing) and not any(released):
                    return []
                raise ValueError("reservation group is partially present, released, or inconsistent")
            # Validate every scope before mutating state; the lock makes the
            # check-and-insert indivisible for concurrent in-process callers.
            for reservation, budget in zip(group.reservations, budgets):
                if reservation.reservation_id in store._reservations or reservation.reservation_id in store._settled:
                    raise ValueError("reservation already exists")
                if any(r.tenant_id == reservation.tenant_id and r.run_id == reservation.run_id
                       and r.scope_type == reservation.scope_type and r.scope_id == reservation.scope_id
                       for r in store._reservations.values()):
                    raise ValueError("active reservation already exists for this Run and quota scope")
                events = [e for e in store._events.values()
                          if e.tenant_id == budget.tenant_id
                          and budget.period_start <= e.occurred_at < budget.period_end
                          and store._matches(e, budget.scope_type, budget.scope_id)]
                active = [r for r in store._reservations.values()
                          if r.tenant_id == budget.tenant_id and r.scope_type == budget.scope_type
                          and r.scope_id == budget.scope_id and r.period_start == budget.period_start]
                tokens = sum(e.total_tokens for e in events) + sum(r.reserved_tokens for r in active)
                cost = sum((e.cost_usd for e in events), Decimal("0")) + sum((r.reserved_cost_usd for r in active), Decimal("0"))
                input_used = sum(e.input_tokens for e in events) + sum(r.reserved_input_tokens for r in active)
                output_used = sum(e.output_tokens for e in events) + sum(r.reserved_output_tokens for r in active)
                concurrency = len(active)
                checks = (
                    (budget.hard_tokens, tokens + reservation.reserved_tokens, "hard token budget exceeded"),
                    (budget.hard_cost_usd, cost + reservation.reserved_cost_usd, "hard cost budget exceeded"),
                    (budget.hard_input_tokens, input_used + reservation.reserved_input_tokens, "hard input token budget exceeded"),
                    (budget.hard_output_tokens, output_used + reservation.reserved_output_tokens, "hard output token budget exceeded"),
                    (budget.hard_concurrency, concurrency + 1, "concurrency quota exceeded"),
                )
                for limit, actual, message in checks:
                    if limit is not None and actual > limit:
                        raise BudgetExceeded(message)
                if budget.soft_tokens is not None and tokens + reservation.reserved_tokens >= budget.soft_tokens:
                    warnings.append("soft token budget exceeded")
                if budget.soft_cost_usd is not None and cost + reservation.reserved_cost_usd >= budget.soft_cost_usd:
                    warnings.append("soft cost budget exceeded")
            for reservation in group.reservations:
                store._reservations[reservation.reservation_id] = reservation
        return warnings

    async def _reserve_postgres(self, group):
        store = self.store
        # One connection + one transaction. Lock budget rows in stable order
        # to avoid deadlocks between groups with overlapping scopes.
        ordered = sorted(group.reservations, key=lambda r: (r.tenant_id, r.scope_type, r.scope_id, r.period_start))
        connection = await store._connection_factory()
        warnings = []
        async with connection:
            async with connection.transaction():
                if group.group_id:
                    # Serialize retries with the same idempotency key before
                    # checking rows; concurrent duplicate submissions then see
                    # the first transaction's complete group.
                    await connection.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (group.group_id,),
                    )
                    cursor = await connection.execute(
                        """SELECT reservation_id,tenant_id,run_id,scope_type,scope_id,
                                  reserved_tokens,reserved_cost_usd,reserved_input_tokens,
                                  reserved_output_tokens,period_start,period_end,status
                           FROM platform_quota_reservations
                           WHERE tenant_id=%s AND reservation_group_id=%s""",
                        (group.tenant_id, UUID(group.group_id)),
                    )
                    rows = await cursor.fetchall()
                    if rows:
                        if len(rows) != len(group.reservations):
                            raise ValueError("reservation group is partially present")
                        by_id = {str(row["reservation_id"]): row for row in rows}
                        states = set()
                        for expected in group.reservations:
                            row = by_id.get(expected.reservation_id)
                            if row is None:
                                raise ValueError("reservation group is missing a scope")
                            if (
                                row["tenant_id"], str(row["run_id"]), row["scope_type"],
                                row["scope_id"], row["reserved_tokens"],
                                Decimal(row["reserved_cost_usd"]),
                                row["reserved_input_tokens"], row["reserved_output_tokens"],
                                row["period_start"], row["period_end"],
                            ) != (
                                expected.tenant_id, expected.run_id, expected.scope_type,
                                expected.scope_id, expected.reserved_tokens,
                                expected.reserved_cost_usd, expected.reserved_input_tokens,
                                expected.reserved_output_tokens, expected.period_start,
                                expected.period_end,
                            ):
                                raise ValueError("idempotency key was reused with different reservation parameters")
                            states.add(row["status"])
                        if states == {"active"} or states == {"settled"}:
                            return warnings
                        raise ValueError("reservation group is released, expired, or inconsistent")
                for r in ordered:
                    try:
                        cursor = await connection.execute(
                            """SELECT warning_soft_tokens,warning_soft_cost
                               FROM platform_quota_reserve_split(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (UUID(r.reservation_id), r.tenant_id, UUID(r.run_id), r.scope_type,
                             r.scope_id, r.period_start, r.reserved_tokens, r.reserved_cost_usd,
                             r.reserved_input_tokens, r.reserved_output_tokens, UUID(r.root_run_id or r.run_id)),
                        )
                        row = await cursor.fetchone()
                        await connection.execute(
                            "UPDATE platform_quota_reservations SET reservation_group_id=%s WHERE reservation_id=%s",
                            (UUID(group.group_id), UUID(r.reservation_id)),
                        )
                    except Exception as exc:
                        message = str(exc)
                        if any(x in message for x in ("quota budget not configured", "hard token budget exceeded",
                            "hard cost budget exceeded", "concurrency quota exceeded",
                            "hard input token budget exceeded", "hard output token budget exceeded")):
                            raise BudgetExceeded(message) from exc
                        raise
                    if row["warning_soft_tokens"]:
                        warnings.append("soft token budget exceeded")
                    if row["warning_soft_cost"]:
                        warnings.append("soft cost budget exceeded")
        return warnings

    async def settle_run(self, group: QuotaReservationGroup, *, model_key: str,
                         input_tokens: int, output_tokens: int, cost_usd: Decimal,
                         project_id: str | None = None, agent_id: str | None = None,
                         occurred_at: datetime | None = None, source: str = "provider_reported") -> UsageEvent:
        primary = next((r for r in group.reservations if r.scope_type == "tenant"),
                       group.reservations[0])
        event_time = occurred_at or datetime.now(timezone.utc)
        event = UsageEvent(
            # Stable event identity makes a retry after a lost settlement response
            # return the original ledger event instead of charging twice.
            event_id=str(uuid5(UUID(group.group_id), "quota-group-settlement")),
            tenant_id=group.tenant_id, run_id=group.run_id,
            model_key=model_key, input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=cost_usd, occurred_at=event_time,
            project_id=project_id, agent_id=agent_id, source=source,
            root_run_id=group.root_run_id,
        )
        if isinstance(self.store, InMemoryQuotaStore):
            async with self.store._lock:
                existing_event = self.store._events.get(event.event_id)
                if existing_event is not None:
                    if (
                        existing_event.tenant_id, existing_event.run_id, existing_event.root_run_id,
                        existing_event.model_key, existing_event.input_tokens,
                        existing_event.output_tokens, existing_event.cost_usd,
                        existing_event.project_id, existing_event.agent_id, existing_event.source,
                    ) != (
                        event.tenant_id, event.run_id, event.root_run_id, event.model_key,
                        event.input_tokens, event.output_tokens, event.cost_usd,
                        event.project_id, event.agent_id, event.source,
                    ):
                        raise ValueError("reservation group was settled with different usage")
                    return existing_event
                active = [self.store._reservations.get(r.reservation_id) for r in group.reservations]
                if any(r is None for r in active):
                    raise KeyError("one or more group reservations are no longer active")
                if any(not r.period_start <= event_time < r.period_end for r in group.reservations):
                    raise ValueError("usage event is outside one or more reserved budget periods")
                if (primary.tenant_id, primary.run_id) != (event.tenant_id, event.run_id):
                    raise PermissionError("usage event does not match reservation group")
                if not primary.period_start <= event.occurred_at < primary.period_end:
                    raise ValueError("usage event is outside the reserved budget period")
                if not self.store._matches(event, primary.scope_type, primary.scope_id):
                    raise PermissionError("usage event does not match primary quota scope")
                self.store._events[event.event_id] = event
                for r in group.reservations:
                    self.store._reservations.pop(r.reservation_id, None)
                    self.store._settled.add(r.reservation_id)
        elif isinstance(self.store, PostgresQuotaStore):
            connection = await self.store._connection_factory()
            async with connection:
                async with connection.transaction():
                    cursor = await connection.execute(
                        """SELECT * FROM platform_usage_ledger WHERE event_id=%s""",
                        (UUID(event.event_id),),
                    )
                    settled_row = await cursor.fetchone()
                    if settled_row is not None:
                        if (
                            settled_row["tenant_id"], str(settled_row["run_id"]),
                            settled_row["model_key"], settled_row["input_tokens"],
                            settled_row["output_tokens"], Decimal(settled_row["cost_usd"]),
                            settled_row["project_id"], settled_row["agent_id"],
                            settled_row["source"],
                        ) != (
                            event.tenant_id, event.run_id, event.model_key,
                            event.input_tokens, event.output_tokens, event.cost_usd,
                            event.project_id, event.agent_id, event.source,
                        ):
                            raise ValueError("reservation group was settled with different usage")
                        return UsageEvent(
                            event_id=str(settled_row["event_id"]),
                            tenant_id=settled_row["tenant_id"], run_id=str(settled_row["run_id"]),
                            model_key=settled_row["model_key"],
                            input_tokens=settled_row["input_tokens"],
                            output_tokens=settled_row["output_tokens"],
                            cost_usd=Decimal(settled_row["cost_usd"]),
                            occurred_at=settled_row["occurred_at"],
                            project_id=settled_row["project_id"], agent_id=settled_row["agent_id"],
                            source=settled_row["source"],
                            root_run_id=str(settled_row["root_run_id"]) if settled_row.get("root_run_id") else group.root_run_id,
                        )
                    if any(not r.period_start <= event_time < r.period_end for r in group.reservations):
                        raise ValueError("usage event is outside one or more reserved budget periods")
                    cursor = await connection.execute(
                        """SELECT COUNT(*) AS active_count
                           FROM platform_quota_reservations
                           WHERE tenant_id=%s AND reservation_group_id=%s AND status='active'""",
                        (group.tenant_id, UUID(group.group_id)),
                    )
                    if (await cursor.fetchone())["active_count"] != len(group.reservations):
                        raise KeyError("reservation group is incomplete or no longer active")
                    cursor = await connection.execute(
                        """SELECT platform_quota_settle(%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s) AS settled""",
                        (UUID(primary.reservation_id), UUID(event.event_id), event.model_key,
                         event.input_tokens, event.output_tokens, event.cost_usd, event.project_id,
                         event.agent_id, event.tool_calls, event.runtime_ms, event.storage_bytes,
                         None, event.source, event.occurred_at),
                    )
                    if not (await cursor.fetchone())["settled"]:
                        raise KeyError("primary reservation is not active")
                    for r in group.reservations:
                        if r.reservation_id == primary.reservation_id:
                            continue
                        cursor = await connection.execute(
                            "SELECT platform_quota_release(%s) AS released", (UUID(r.reservation_id),)
                        )
                        if not (await cursor.fetchone())["released"]:
                            raise KeyError("sibling reservation is not active")
        else:
            raise TypeError("quota store does not implement atomic group settlement")
        return event

    async def release(self, group: QuotaReservationGroup) -> bool:
        if isinstance(self.store, InMemoryQuotaStore):
            async with self.store._lock:
                removed = False
                for r in group.reservations:
                    was_active = self.store._reservations.pop(r.reservation_id, None) is not None
                    if was_active:
                        self.store._released.add(r.reservation_id)
                    removed = was_active or removed
                return removed
        if isinstance(self.store, PostgresQuotaStore):
            connection = await self.store._connection_factory()
            removed = False
            async with connection:
                async with connection.transaction():
                    for r in group.reservations:
                        cursor = await connection.execute(
                            "SELECT platform_quota_release(%s) AS released", (UUID(r.reservation_id),)
                        )
                        removed = bool((await cursor.fetchone())["released"]) or removed
            return removed
        raise TypeError("quota store does not implement atomic group release")
