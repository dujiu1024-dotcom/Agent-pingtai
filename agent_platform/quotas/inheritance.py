"""Resolve and enforce root-Run quota inheritance for every execution."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from agent_platform.quotas.groups import (
    QuotaReservationGroup,
    QuotaReservationGroupService,
    QuotaScope,
)


@dataclass(frozen=True)
class RunBudgetLineage:
    tenant_id: str
    run_id: str
    parent_run_id: str | None
    root_run_id: str


class RunBudgetInheritanceService:
    """Reserve child/retry usage against the persisted root Run budget.

    Recovery continues with the same Run and same model-call idempotency key.
    A new retry/delegated execution gets its own key but remains charged to the
    same root budget. Stable reservation IDs make replay fail closed rather
    than creating a second active/settled charge.
    """

    def __init__(self, *, lineage_store: Any, reservation_groups: QuotaReservationGroupService):
        self.lineage_store = lineage_store
        self.reservation_groups = reservation_groups

    async def lineage(self, *, tenant_id: str, run_id: str) -> RunBudgetLineage:
        getter = getattr(self.lineage_store, "get_budget_lineage", None)
        if not callable(getter):
            raise TypeError("Run store must provide persisted budget lineage")
        row = await getter(tenant_id, run_id)
        if not isinstance(row, dict):
            raise KeyError("Run budget lineage not found")
        root = row.get("budget_root_run_id")
        if not isinstance(root, str) or not root:
            raise ValueError("Run budget root is missing")
        return RunBudgetLineage(
            tenant_id=tenant_id, run_id=run_id,
            parent_run_id=row.get("parent_run_id"), root_run_id=root,
        )

    async def reserve_model_call(
        self, *, tenant_id: str, run_id: str, call_id: str,
        scopes: tuple[QuotaScope, ...], estimated_tokens: int,
        estimated_cost_usd: Decimal, estimated_input_tokens: int,
        estimated_output_tokens: int,
    ) -> tuple[QuotaReservationGroup, tuple[str, ...]]:
        if not call_id.strip():
            raise ValueError("call_id is required for idempotent budget reservation")
        lineage = await self.lineage(tenant_id=tenant_id, run_id=run_id)
        run_scopes = [scope for scope in scopes if scope.scope_type == "run"]
        if len(run_scopes) != 1 or run_scopes[0].scope_id != lineage.root_run_id:
            raise PermissionError("reservation must include exactly the persisted root Run budget")
        return await self.reservation_groups.reserve_run(
            tenant_id=tenant_id, run_id=run_id, root_run_id=lineage.root_run_id,
            scopes=scopes, estimated_tokens=estimated_tokens,
            estimated_cost_usd=estimated_cost_usd,
            estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=estimated_output_tokens,
            idempotency_key=call_id,
        )

    async def settle_model_call(
        self, group: QuotaReservationGroup, *, model_key: str,
        input_tokens: int, output_tokens: int, cost_usd: Decimal,
        project_id: str | None = None, agent_id: str | None = None,
    ):
        # The group service atomically settles the tenant scope once and
        # releases sibling root/project/Agent reservations in one transaction.
        return await self.reservation_groups.settle_run(
            group, model_key=model_key, input_tokens=input_tokens,
            output_tokens=output_tokens, cost_usd=cost_usd,
            project_id=project_id, agent_id=agent_id,
        )
