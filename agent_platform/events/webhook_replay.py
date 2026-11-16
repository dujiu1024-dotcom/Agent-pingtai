"""Authorized, audited operator replay for dead-lettered webhook deliveries."""
from __future__ import annotations

import inspect
from typing import Any, Callable, Protocol


class DeadLetterReplayStore(Protocol):
    async def replay_dead_letter(self, tenant_id: str, delivery_id: str) -> bool: ...


class ReplayAuditWriter(Protocol):
    async def record(
        self, *, tenant_id: str, actor_id: str, action: str, subject_id: str,
        outcome: str = "succeeded", detail: dict[str, Any] | None = None,
    ) -> None: ...


class WebhookDeadLetterReplayService:
    def __init__(
        self, *, store: DeadLetterReplayStore, audit: ReplayAuditWriter,
        authorize: Callable[[str, str], Any],
    ) -> None:
        self.store = store
        self.audit = audit
        self.authorize = authorize

    async def replay(
        self, *, tenant_id: str, actor_id: str, delivery_id: str, reason: str,
    ) -> bool:
        if any(not isinstance(value, str) or not value.strip() for value in (
            tenant_id, actor_id, delivery_id, reason,
        )):
            raise ValueError("tenant, actor, delivery and reason are required")
        if len(reason) > 256:
            raise ValueError("replay reason must not exceed 256 characters")
        allowed = self.authorize(tenant_id, actor_id)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if allowed is not True:
            await self.audit.record(
                tenant_id=tenant_id, actor_id=actor_id,
                action="webhook.dead_letter.replay",
                subject_id=delivery_id, outcome="denied",
                detail={"reason": reason},
            )
            raise PermissionError("webhook replay permission is required")

        detail = {"reason": reason}
        await self.audit.record(
            tenant_id=tenant_id, actor_id=actor_id,
            action="webhook.dead_letter.replay.requested",
            subject_id=delivery_id, outcome="succeeded", detail=detail,
        )
        try:
            replayed = await self.store.replay_dead_letter(tenant_id, delivery_id)
        except Exception as exc:
            await self.audit.record(
                tenant_id=tenant_id, actor_id=actor_id,
                action="webhook.dead_letter.replay",
                subject_id=delivery_id, outcome="failed",
                detail={**detail, "error_type": type(exc).__name__},
            )
            raise

        await self.audit.record(
            tenant_id=tenant_id, actor_id=actor_id,
            action="webhook.dead_letter.replay",
            subject_id=delivery_id,
            outcome="succeeded" if replayed else "denied",
            detail={**detail, "replayed": replayed},
        )
        return replayed
