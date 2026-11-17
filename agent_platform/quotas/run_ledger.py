"""Durable per-Run/model-call budget reservations and usage reconciliation.

This ledger complements the period quota ledger with a Run-scoped snapshot. The
host must call reserve before dispatch and settle after receiving provider usage.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


class RunBudgetExceeded(RuntimeError):
    """A Run budget cannot cover a model-call reservation."""


@dataclass(frozen=True)
class RunBudgetSnapshot:
    tenant_id: str
    run_id: str
    max_tokens: int
    max_cost_usd: Decimal
    reserved_tokens: int
    reserved_cost_usd: Decimal
    actual_tokens: int
    actual_cost_usd: Decimal
    remaining_tokens: int
    remaining_cost_usd: Decimal
    overrun_reason: str | None = None


@dataclass(frozen=True)
class ModelCallUsage:
    call_id: str
    tenant_id: str
    run_id: str
    model_key: str
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_usd: Decimal
    actual_input_tokens: int
    actual_output_tokens: int
    actual_cost_usd: Decimal
    token_delta: int
    cost_delta_usd: Decimal
    remaining_tokens: int
    remaining_cost_usd: Decimal
    overrun_reason: str | None
    settled_at: datetime


class PostgresRunBudgetLedger:
    """Transactional PostgreSQL ledger; never substitute process-local counters."""

    def __init__(self, connection_factory: Any) -> None:
        self._connection_factory = connection_factory

    async def configure(
        self, *, tenant_id: str, run_id: str, max_tokens: int,
        max_cost_usd: Decimal,
    ) -> RunBudgetSnapshot:
        _validate_budget(tenant_id, run_id, max_tokens, max_cost_usd)
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    """INSERT INTO platform_run_budget_accounts
                       (tenant_id,run_id,max_tokens,max_cost_usd)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (tenant_id,run_id) DO UPDATE SET
                         max_tokens=EXCLUDED.max_tokens,
                         max_cost_usd=EXCLUDED.max_cost_usd,updated_at=now()""",
                    (tenant_id, UUID(run_id), max_tokens, max_cost_usd),
                )
                return await self._snapshot(connection, tenant_id, run_id, lock=True)

    async def reserve(
        self, *, call_id: str, tenant_id: str, run_id: str, model_key: str,
        estimated_input_tokens: int, estimated_output_tokens: int,
        estimated_cost_usd: Decimal,
    ) -> RunBudgetSnapshot:
        _validate_counter(estimated_input_tokens, "estimated_input_tokens")
        _validate_counter(estimated_output_tokens, "estimated_output_tokens")
        _validate_money(estimated_cost_usd, "estimated_cost_usd")
        if not model_key.strip():
            raise ValueError("model_key is required")
        call_uuid = UUID(call_id)
        connection = await self._connection_factory()
        rejected: str | None = None
        async with connection:
            async with connection.transaction():
                account = await self._snapshot(connection, tenant_id, run_id, lock=True)
                tokens = estimated_input_tokens + estimated_output_tokens
                if tokens > account.remaining_tokens:
                    rejected = "estimated token reservation exceeds remaining Run token budget"
                elif estimated_cost_usd > account.remaining_cost_usd:
                    rejected = "estimated cost reservation exceeds remaining Run cost budget"
                await connection.execute(
                    """INSERT INTO platform_model_call_budget_ledger
                       (call_id,tenant_id,run_id,model_key,estimated_input_tokens,
                        estimated_output_tokens,estimated_cost_usd,status,overrun_reason)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (call_uuid,tenant_id,UUID(run_id),model_key,estimated_input_tokens,
                     estimated_output_tokens,estimated_cost_usd,
                     "rejected" if rejected else "reserved",rejected),
                )
                if rejected:
                    await connection.execute(
                        """UPDATE platform_run_budget_accounts SET
                           overrun_reason=%s,updated_at=now()
                           WHERE tenant_id=%s AND run_id=%s""",
                        (rejected,tenant_id,UUID(run_id)),
                    )
                    snapshot = await self._snapshot(connection, tenant_id, run_id)
                else:
                    await connection.execute(
                        """UPDATE platform_run_budget_accounts SET
                           reserved_tokens=reserved_tokens+%s,
                           reserved_cost_usd=reserved_cost_usd+%s,updated_at=now()
                           WHERE tenant_id=%s AND run_id=%s""",
                        (tokens,estimated_cost_usd,tenant_id,UUID(run_id)),
                    )
                    snapshot = await self._snapshot(connection, tenant_id, run_id)
        if rejected:
            raise RunBudgetExceeded(rejected)
        return snapshot

    async def settle(
        self, *, call_id: str, actual_input_tokens: int,
        actual_output_tokens: int, actual_cost_usd: Decimal,
    ) -> ModelCallUsage:
        _validate_counter(actual_input_tokens, "actual_input_tokens")
        _validate_counter(actual_output_tokens, "actual_output_tokens")
        _validate_money(actual_cost_usd, "actual_cost_usd")
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """SELECT tenant_id,run_id FROM platform_model_call_budget_ledger
                       WHERE call_id=%s""", (UUID(call_id),),
                )
                identity = await cursor.fetchone()
                if identity is None:
                    raise KeyError("model-call budget reservation not found")
                # All paths lock the Run account before the call row to avoid lock inversion.
                await self._snapshot(
                    connection,identity["tenant_id"],str(identity["run_id"]),lock=True,
                )
                cursor = await connection.execute(
                    """SELECT * FROM platform_model_call_budget_ledger
                       WHERE call_id=%s FOR UPDATE""", (UUID(call_id),),
                )
                call = await cursor.fetchone()
                if call["status"] == "settled":
                    if (call["actual_input_tokens"],call["actual_output_tokens"],
                        Decimal(call["actual_cost_usd"])) != (
                        actual_input_tokens,actual_output_tokens,actual_cost_usd
                    ):
                        raise ValueError("model call was already settled with different usage")
                    return await self._usage(connection, call)
                if call["status"] != "reserved":
                    raise ValueError("only a reserved model call can be settled")
                account = await self._snapshot(
                    connection,call["tenant_id"],str(call["run_id"]),lock=True,
                )
                est_tokens = call["estimated_input_tokens"] + call["estimated_output_tokens"]
                actual_tokens = actual_input_tokens + actual_output_tokens
                token_delta = actual_tokens - est_tokens
                cost_delta = actual_cost_usd - Decimal(call["estimated_cost_usd"])
                remaining_tokens = account.max_tokens - account.actual_tokens - actual_tokens - (
                    account.reserved_tokens - est_tokens
                )
                remaining_cost = account.max_cost_usd - account.actual_cost_usd - actual_cost_usd - (
                    account.reserved_cost_usd - Decimal(call["estimated_cost_usd"])
                )
                reasons = []
                if remaining_tokens < 0:
                    reasons.append("actual token usage exceeded Run budget")
                if remaining_cost < 0:
                    reasons.append("actual cost exceeded Run budget")
                reason = "; ".join(reasons) or None
                await connection.execute(
                    """UPDATE platform_run_budget_accounts SET
                       reserved_tokens=reserved_tokens-%s,
                       reserved_cost_usd=reserved_cost_usd-%s,
                       actual_tokens=actual_tokens+%s,
                       actual_cost_usd=actual_cost_usd+%s,
                       overrun_reason=COALESCE(%s,overrun_reason),updated_at=now()
                       WHERE tenant_id=%s AND run_id=%s""",
                    (est_tokens,call["estimated_cost_usd"],actual_tokens,actual_cost_usd,
                     reason,call["tenant_id"],call["run_id"]),
                )
                await connection.execute(
                    """UPDATE platform_model_call_budget_ledger SET
                       actual_input_tokens=%s,actual_output_tokens=%s,actual_cost_usd=%s,
                       token_delta=%s,cost_delta_usd=%s,remaining_tokens=%s,
                       remaining_cost_usd=%s,overrun_reason=%s,status='settled',settled_at=now()
                       WHERE call_id=%s""",
                    (actual_input_tokens,actual_output_tokens,actual_cost_usd,token_delta,
                     cost_delta,remaining_tokens,remaining_cost,reason,UUID(call_id)),
                )
                cursor = await connection.execute(
                    "SELECT * FROM platform_model_call_budget_ledger WHERE call_id=%s",
                    (UUID(call_id),),
                )
                return await self._usage(connection, await cursor.fetchone())

    async def get_run_budget_report(self, tenant_id: str, run_id: str) -> dict[str, Any]:
        """Read the immutable budget limit and per-call estimates/provider actuals.

        Results are tenant-bound, ordered deterministically, and retain reserved,
        rejected and settled calls so an audit can explain both dispatch decisions
        and final provider usage.
        """
        UUID(run_id)
        connection = await self._connection_factory()
        async with connection:
            account = await connection.execute(
                """SELECT max_tokens,max_cost_usd,reserved_tokens,reserved_cost_usd,
                          actual_tokens,actual_cost_usd,overrun_reason,created_at,updated_at
                   FROM platform_run_budget_accounts
                   WHERE tenant_id=%s AND run_id=%s""",
                (tenant_id, UUID(run_id)),
            )
            budget = await account.fetchone()
            if budget is None:
                raise KeyError("Run budget account not configured")
            cursor = await connection.execute(
                """SELECT call_id,model_key,estimated_input_tokens,estimated_output_tokens,
                          estimated_cost_usd,actual_input_tokens,actual_output_tokens,
                          actual_cost_usd,token_delta,cost_delta_usd,remaining_tokens,
                          remaining_cost_usd,status,overrun_reason,created_at,settled_at
                   FROM platform_model_call_budget_ledger
                   WHERE tenant_id=%s AND run_id=%s
                   ORDER BY created_at,call_id""",
                (tenant_id, UUID(run_id)),
            )
            calls = await cursor.fetchall()
        max_tokens = int(budget["max_tokens"])
        max_cost = Decimal(budget["max_cost_usd"])
        actual_tokens = int(budget["actual_tokens"])
        actual_cost = Decimal(budget["actual_cost_usd"])
        reserved_tokens = int(budget["reserved_tokens"])
        reserved_cost = Decimal(budget["reserved_cost_usd"])
        serialized_calls = []
        for call in calls:
            estimated_tokens = int(call["estimated_input_tokens"]) + int(call["estimated_output_tokens"])
            actual_input = call["actual_input_tokens"]
            actual_output = call["actual_output_tokens"]
            actual_call_tokens = (
                int(actual_input) + int(actual_output)
                if actual_input is not None and actual_output is not None else None
            )
            serialized_calls.append({
                "call_id": str(call["call_id"]),
                "model_key": call["model_key"],
                "status": call["status"],
                "estimated_input_tokens": int(call["estimated_input_tokens"]),
                "estimated_output_tokens": int(call["estimated_output_tokens"]),
                "estimated_total_tokens": estimated_tokens,
                "estimated_cost_usd": str(Decimal(call["estimated_cost_usd"])),
                "actual_input_tokens": actual_input,
                "actual_output_tokens": actual_output,
                "actual_total_tokens": actual_call_tokens,
                "actual_cost_usd": (
                    str(Decimal(call["actual_cost_usd"]))
                    if call["actual_cost_usd"] is not None else None
                ),
                "token_delta": call["token_delta"],
                "cost_delta_usd": (
                    str(Decimal(call["cost_delta_usd"]))
                    if call["cost_delta_usd"] is not None else None
                ),
                "remaining_tokens": call["remaining_tokens"],
                "remaining_cost_usd": (
                    str(Decimal(call["remaining_cost_usd"]))
                    if call["remaining_cost_usd"] is not None else None
                ),
                "overrun_reason": call["overrun_reason"],
                "created_at": call["created_at"].isoformat() if call["created_at"] else None,
                "settled_at": call["settled_at"].isoformat() if call["settled_at"] else None,
            })
        return {
            "tenant_id": tenant_id,
            "run_id": run_id,
            "budget": {
                "max_tokens": max_tokens,
                "max_cost_usd": str(max_cost),
                "reserved_tokens": reserved_tokens,
                "reserved_cost_usd": str(reserved_cost),
                "actual_tokens": actual_tokens,
                "actual_cost_usd": str(actual_cost),
                "remaining_tokens": max_tokens - actual_tokens - reserved_tokens,
                "remaining_cost_usd": str(max_cost - actual_cost - reserved_cost),
                "overrun_reason": budget["overrun_reason"],
                "created_at": budget["created_at"].isoformat() if budget["created_at"] else None,
                "updated_at": budget["updated_at"].isoformat() if budget["updated_at"] else None,
            },
            "calls": serialized_calls,
            "summary": {
                "call_count": len(serialized_calls),
                "settled_calls": sum(item["status"] == "settled" for item in serialized_calls),
                "reserved_calls": sum(item["status"] == "reserved" for item in serialized_calls),
                "rejected_calls": sum(item["status"] == "rejected" for item in serialized_calls),
                "estimated_tokens": sum(item["estimated_total_tokens"] for item in serialized_calls),
                "actual_tokens": sum(item["actual_total_tokens"] or 0 for item in serialized_calls),
                "estimated_cost_usd": str(sum(
                    (Decimal(item["estimated_cost_usd"]) for item in serialized_calls), Decimal("0")
                )),
                "actual_cost_usd": str(sum(
                    (Decimal(item["actual_cost_usd"]) for item in serialized_calls if item["actual_cost_usd"] is not None),
                    Decimal("0"),
                )),
            },
        }

    async def snapshot(self, tenant_id: str, run_id: str) -> RunBudgetSnapshot:
        connection = await self._connection_factory()
        async with connection:
            return await self._snapshot(connection, tenant_id, run_id)

    async def _snapshot(
        self, connection: Any, tenant_id: str, run_id: str, *, lock: bool = False,
    ) -> RunBudgetSnapshot:
        cursor = await connection.execute(
            "SELECT * FROM platform_run_budget_accounts WHERE tenant_id=%s AND run_id=%s"
            + (" FOR UPDATE" if lock else ""),
            (tenant_id,UUID(run_id)),
        )
        row = await cursor.fetchone()
        if row is None:
            raise KeyError("Run budget account not configured")
        max_tokens = row["max_tokens"]
        max_cost = Decimal(row["max_cost_usd"])
        actual_tokens = row["actual_tokens"]
        actual_cost = Decimal(row["actual_cost_usd"])
        reserved_tokens = row["reserved_tokens"]
        reserved_cost = Decimal(row["reserved_cost_usd"])
        return RunBudgetSnapshot(
            tenant_id,run_id,max_tokens,max_cost,reserved_tokens,reserved_cost,
            actual_tokens,actual_cost,
            max_tokens-actual_tokens-reserved_tokens,
            max_cost-actual_cost-reserved_cost,row["overrun_reason"],
        )

    @staticmethod
    async def _usage(connection: Any, row: Any) -> ModelCallUsage:
        return ModelCallUsage(
            call_id=str(row["call_id"]),tenant_id=row["tenant_id"],
            run_id=str(row["run_id"]),model_key=row["model_key"],
            estimated_input_tokens=row["estimated_input_tokens"],
            estimated_output_tokens=row["estimated_output_tokens"],
            estimated_cost_usd=Decimal(row["estimated_cost_usd"]),
            actual_input_tokens=row["actual_input_tokens"],
            actual_output_tokens=row["actual_output_tokens"],
            actual_cost_usd=Decimal(row["actual_cost_usd"]),
            token_delta=row["token_delta"],cost_delta_usd=Decimal(row["cost_delta_usd"]),
            remaining_tokens=row["remaining_tokens"],
            remaining_cost_usd=Decimal(row["remaining_cost_usd"]),
            overrun_reason=row["overrun_reason"],settled_at=row["settled_at"],
        )


def _validate_counter(value: int, name: str) -> None:
    if isinstance(value,bool) or not isinstance(value,int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _validate_money(value: Decimal, name: str) -> None:
    if not isinstance(value,Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a finite non-negative Decimal")


def _validate_budget(tenant_id: str, run_id: str, tokens: int, cost: Decimal) -> None:
    if not tenant_id:
        raise ValueError("tenant_id is required")
    UUID(run_id)
    _validate_counter(tokens,"max_tokens")
    _validate_money(cost,"max_cost_usd")
