"""PostgreSQL adapter for the quota service."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Callable
from uuid import UUID, uuid4

from agent_platform.quotas.service import Budget, BudgetExceeded, QuotaReservation, UsageEvent

ConnectionFactory = Callable[[], Any]


class PostgresQuotaStore:
    """Shared multi-worker adapter; quota decisions execute in PostgreSQL transactions."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    @classmethod
    def from_dsn(cls, dsn: str) -> "PostgresQuotaStore":
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")

        async def connect() -> Any:
            try:
                from psycopg import AsyncConnection
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("Install psycopg[binary] for quota storage") from exc
            return await AsyncConnection.connect(dsn, row_factory=dict_row)

        return cls(connect)

    async def put_budget(self, budget: Budget) -> None:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    """INSERT INTO platform_quota_budgets
                    (tenant_id,scope_type,scope_id,period_start,period_end,hard_tokens,
                     hard_cost_usd,hard_concurrency,soft_tokens,soft_cost_usd,
                     hard_input_tokens,hard_output_tokens)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (tenant_id,scope_type,scope_id,period_start)
                    DO UPDATE SET period_end=EXCLUDED.period_end,
                    hard_tokens=EXCLUDED.hard_tokens,hard_cost_usd=EXCLUDED.hard_cost_usd,
                    hard_concurrency=EXCLUDED.hard_concurrency,soft_tokens=EXCLUDED.soft_tokens,
                    soft_cost_usd=EXCLUDED.soft_cost_usd,hard_input_tokens=EXCLUDED.hard_input_tokens,
                    hard_output_tokens=EXCLUDED.hard_output_tokens,updated_at=now()""",
                    (budget.tenant_id,budget.scope_type,budget.scope_id,budget.period_start,
                     budget.period_end,budget.hard_tokens,budget.hard_cost_usd,
                     budget.hard_concurrency,budget.soft_tokens,budget.soft_cost_usd,
                     budget.hard_input_tokens,budget.hard_output_tokens),
                )

    async def get_budget(self, tenant_id: str, scope_type: str, scope_id: str, now: datetime) -> Budget | None:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT * FROM platform_quota_budgets
                WHERE tenant_id=%s AND scope_type=%s AND scope_id=%s
                AND period_start<=%s AND period_end>%s
                ORDER BY period_start DESC LIMIT 1""",
                (tenant_id,scope_type,scope_id,now,now),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return Budget(
            tenant_id=row["tenant_id"],scope_type=row["scope_type"],scope_id=row["scope_id"],
            period_start=row["period_start"],period_end=row["period_end"],
            hard_tokens=row["hard_tokens"],
            hard_cost_usd=Decimal(row["hard_cost_usd"]) if row["hard_cost_usd"] is not None else None,
            hard_concurrency=row["hard_concurrency"],soft_tokens=row["soft_tokens"],
            soft_cost_usd=Decimal(row["soft_cost_usd"]) if row["soft_cost_usd"] is not None else None,
            hard_input_tokens=row.get("hard_input_tokens"),
            hard_output_tokens=row.get("hard_output_tokens"),
        )

    async def reserve(self, reservation: QuotaReservation) -> tuple[QuotaReservation, tuple[str, ...]]:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                try:
                    cursor = await connection.execute(
                        """SELECT warning_soft_tokens,warning_soft_cost
                        FROM platform_quota_reserve_split(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (UUID(reservation.reservation_id),reservation.tenant_id,UUID(reservation.run_id),
                         reservation.scope_type,reservation.scope_id,reservation.period_start,
                         reservation.reserved_tokens,reservation.reserved_cost_usd,
                         reservation.reserved_input_tokens,reservation.reserved_output_tokens),
                    )
                    row = await cursor.fetchone()
                except Exception as exc:
                    message = str(exc)
                    if any(reason in message for reason in (
                        "quota budget not configured", "hard token budget exceeded",
                        "hard cost budget exceeded", "concurrency quota exceeded",
                        "hard input token budget exceeded", "hard output token budget exceeded",
                    )):
                        raise BudgetExceeded(message) from exc
                    raise
        warnings = []
        if row["warning_soft_tokens"]:
            warnings.append("soft token budget exceeded")
        if row["warning_soft_cost"]:
            warnings.append("soft cost budget exceeded")
        return reservation, tuple(warnings)

    async def settle(self, reservation_id: str, event: UsageEvent) -> None:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """SELECT platform_quota_settle(
                    %s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s) AS settled""",
                    (UUID(reservation_id),UUID(event.event_id),event.model_key,event.input_tokens,
                     event.output_tokens,event.cost_usd,event.project_id,event.agent_id,event.tool_calls,
                     event.runtime_ms,event.storage_bytes,event.price_card_version,event.source,event.occurred_at),
                )
                row = await cursor.fetchone()
                if not row["settled"]:
                    raise KeyError("active quota reservation not found or already settled")

    async def release(self, reservation_id: str) -> bool:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    "SELECT platform_quota_release(%s) AS released",(UUID(reservation_id),)
                )
                row = await cursor.fetchone()
        return bool(row["released"])

    async def usage(self, tenant_id: str, scope_type: str, scope_id: str,
                    start: datetime, end: datetime) -> tuple[int, Decimal, int]:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT COALESCE(SUM(u.input_tokens+u.output_tokens),0)
                  + COALESCE((SELECT SUM(r.reserved_tokens) FROM platform_quota_reservations r
                    WHERE r.tenant_id=%s AND r.scope_type=%s AND r.scope_id=%s
                    AND r.period_start=%s AND r.status='active'),0) AS tokens,
                COALESCE(SUM(u.cost_usd),0)
                  + COALESCE((SELECT SUM(r.reserved_cost_usd) FROM platform_quota_reservations r
                    WHERE r.tenant_id=%s AND r.scope_type=%s AND r.scope_id=%s
                    AND r.period_start=%s AND r.status='active'),0) AS cost,
                (SELECT COUNT(*) FROM platform_quota_reservations r
                 WHERE r.tenant_id=%s AND r.scope_type=%s AND r.scope_id=%s
                 AND r.period_start=%s AND r.status='active') AS concurrency
                FROM platform_usage_ledger u
                WHERE u.tenant_id=%s AND u.occurred_at>=%s AND u.occurred_at<%s
                AND ((%s='tenant' AND u.tenant_id=%s) OR (%s='model' AND u.model_key=%s)
                OR (%s='project' AND u.project_id=%s) OR (%s='agent' AND u.agent_id=%s))""",
                (tenant_id,scope_type,scope_id,start,tenant_id,scope_type,scope_id,start,
                 tenant_id,scope_type,scope_id,start,tenant_id,start,end,
                 scope_type,scope_id,scope_type,scope_id,scope_type,scope_id,scope_type,scope_id),
            )
            row = await cursor.fetchone()
        return int(row["tokens"]), Decimal(row["cost"]), int(row["concurrency"])


    async def reconcile_cost(
        self, tenant_id: str, start: datetime, end: datetime, *,
        source: str, actual_cost_usd: Decimal,
    ) -> dict[str, Any]:
        """Persist an idempotent tenant invoice reconciliation snapshot."""
        if not tenant_id or not source.strip() or len(source) > 128:
            raise ValueError("tenant_id and a source label of at most 128 characters are required")
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("reconciliation period must be ordered and timezone-aware")
        if not isinstance(actual_cost_usd, Decimal) or not actual_cost_usd.is_finite() or actual_cost_usd < 0:
            raise ValueError("actual_cost_usd must be a finite non-negative Decimal")
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """SELECT COALESCE(SUM(cost_usd),0) AS estimated_cost_usd
                       FROM platform_usage_ledger
                       WHERE tenant_id=%s AND occurred_at >= %s AND occurred_at < %s""",
                    (tenant_id, start, end),
                )
                estimated = Decimal((await cursor.fetchone())["estimated_cost_usd"])
                reconciliation_id = uuid4()
                cursor = await connection.execute(
                    """INSERT INTO platform_usage_reconciliation
                       (reconciliation_id,tenant_id,period_start,period_end,source,
                        estimated_cost_usd,actual_cost_usd)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (tenant_id,period_start,period_end,source) DO NOTHING
                       RETURNING reconciliation_id,estimated_cost_usd,actual_cost_usd,variance_usd""",
                    (reconciliation_id,tenant_id,start,end,source,estimated,actual_cost_usd),
                )
                row = await cursor.fetchone()
                if row is None:
                    cursor = await connection.execute(
                        """SELECT reconciliation_id,estimated_cost_usd,actual_cost_usd,variance_usd
                           FROM platform_usage_reconciliation
                           WHERE tenant_id=%s AND period_start=%s AND period_end=%s AND source=%s
                           FOR UPDATE""",
                        (tenant_id,start,end,source),
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        raise RuntimeError("cost reconciliation record disappeared")
                    if Decimal(row["actual_cost_usd"]) != actual_cost_usd:
                        raise ValueError("this billing source and period were already reconciled with a different actual cost")
        return {
            "reconciliation_id": str(row["reconciliation_id"]),
            "tenant_id": tenant_id,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "source": source,
            "estimated_cost_usd": str(row["estimated_cost_usd"]),
            "actual_cost_usd": str(row["actual_cost_usd"]),
            "variance_usd": str(row["variance_usd"]),
        }

    async def list_usage(
        self, tenant_id: str, start: datetime, end: datetime, *,
        run_id: str | None = None, model_key: str | None = None, limit: int = 1000,
    ) -> list[UsageEvent]:
        if limit < 1 or limit > 10000 or end <= start:
            raise ValueError("invalid usage export range or limit")
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT event_id,tenant_id,run_id,model_key,input_tokens,output_tokens,
                          cost_usd,occurred_at,project_id,agent_id,tool_calls,runtime_ms,
                          storage_bytes,price_card_version,source
                   FROM platform_usage_ledger
                   WHERE tenant_id=%s AND occurred_at >= %s AND occurred_at < %s
                     AND (%s IS NULL OR run_id=%s)
                     AND (%s IS NULL OR model_key=%s)
                   ORDER BY occurred_at,event_id LIMIT %s""",
                (tenant_id,start,end,run_id,run_id,model_key,model_key,limit),
            )
            rows = await cursor.fetchall()
        return [
            UsageEvent(
                event_id=str(row["event_id"]),tenant_id=row["tenant_id"],
                run_id=str(row["run_id"]),model_key=row["model_key"],
                input_tokens=row["input_tokens"],output_tokens=row["output_tokens"],
                cost_usd=Decimal(row["cost_usd"]),occurred_at=row["occurred_at"],
                project_id=row["project_id"],agent_id=row["agent_id"],
                tool_calls=row["tool_calls"],runtime_ms=row["runtime_ms"],
                storage_bytes=row["storage_bytes"],price_card_version=row["price_card_version"],
                source=row["source"],
            )
            for row in rows
        ]
