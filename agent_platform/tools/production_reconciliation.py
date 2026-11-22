"""Production composition helpers for operator reconciliation of tool effects."""

from __future__ import annotations

from typing import Any, Callable

from agent_platform.identity.models import Principal
from agent_platform.identity.postgres import PostgresIdentityRepository
from agent_platform.security.audit import PostgresAuditWriter
from agent_platform.tools.idempotency import (
    PostgresToolEffectStore,
    ToolEffectReconciliationService,
)


class _ToolEffectAuditAdapter:
    """Translate reconciliation events into the append-only identity audit ledger."""

    def __init__(self, writer: PostgresAuditWriter) -> None:
        self._writer = writer

    async def record(
        self, *, tenant_id: str, actor_id: str, action: str, resource: str,
        action_digest: str, outcome: str, run_id: str, detail: str,
    ) -> None:
        audit_outcome = {
            "reconciliation_started": "started",
            "reconciliation_completed": "succeeded",
        }.get(outcome)
        if audit_outcome is None:
            raise ValueError("unsupported tool-effect reconciliation audit outcome")
        await self._writer.record(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            subject_id=resource,
            outcome=audit_outcome,
            detail={
                "run_id": run_id,
                "action_digest": action_digest,
                "reconciliation_detail": detail,
            },
        )


def build_tool_effect_reconciliation_service(
    *,
    principal: Principal,
    identity_repository: PostgresIdentityRepository,
    authorize_run: Callable[[Any, str, str, str], Any],
    connection_factory: Callable[[], Any],
    audit_writer: PostgresAuditWriter,
) -> ToolEffectReconciliationService:
    """Bind reconciliation to the authenticated operator's live tenant/Run access."""

    async def authorize_operator(
        operator_id: str, tenant_id: str, run_id: str, _call_id: str,
    ) -> bool:
        if tenant_id != principal.tenant_id or operator_id != principal.user_id:
            return False
        if not await identity_repository.is_active_member(
            tenant_id=tenant_id, user_id=operator_id,
        ):
            return False
        operator = Principal(
            user_id=operator_id,
            tenant_id=tenant_id,
            session_id=principal.session_id,
            email=principal.email,
            display_name=principal.display_name,
        )
        return bool(await authorize_run(operator, "run", run_id, "reconcile"))

    return ToolEffectReconciliationService(
        store=PostgresToolEffectStore(connection_factory=connection_factory),
        authorize_operator=authorize_operator,
        audit_sink=_ToolEffectAuditAdapter(audit_writer),
    )
