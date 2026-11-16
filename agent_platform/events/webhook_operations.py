"""Tenant-scoped dead-letter inspection and replay operations."""
from __future__ import annotations

import inspect
from typing import Any, Callable
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from agent_platform.identity.models import Principal


class DeadLetterReplayRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=256)


class PostgresWebhookOperationsStore:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def list_dead_letters(self, tenant_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        if not tenant_id or not 1 <= limit <= 200:
            raise ValueError("tenant_id and a limit between 1 and 200 are required")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT delivery_id,subscription_id,event_id,topic,attempts,response_code,
                          last_error_code,created_at
                   FROM platform_webhook_deliveries
                   WHERE tenant_id=%s AND status='dead_lettered'
                   ORDER BY created_at DESC LIMIT %s""",
                (tenant_id, limit),
            )
            rows = await cursor.fetchall()
        return [{
            "delivery_id": str(row["delivery_id"]),
            "subscription_id": str(row["subscription_id"]),
            "event_id": str(row["event_id"]),
            "topic": row["topic"],
            "attempts": row["attempts"],
            "response_code": row["response_code"],
            "last_error_code": row["last_error_code"],
            "created_at": row["created_at"],
        } for row in rows]

    async def replay_dead_letter(self, tenant_id: str, delivery_id: str) -> bool:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        try:
            parsed_id = UUID(delivery_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("delivery_id must be a UUID") from exc
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """UPDATE platform_webhook_deliveries d
                       SET status='pending',attempts=0,next_attempt_at=NULL,
                           lease_token=NULL,lease_until=NULL,response_code=NULL,
                           last_error_code=NULL
                       FROM platform_webhook_subscriptions s
                       WHERE d.tenant_id=%s AND d.delivery_id=%s
                         AND d.status='dead_lettered'
                         AND s.tenant_id=d.tenant_id
                         AND s.subscription_id=d.subscription_id
                         AND s.status='active' AND d.topic=ANY(s.topics)
                       RETURNING d.delivery_id""",
                    (tenant_id, parsed_id),
                )
                return await cursor.fetchone() is not None


def create_webhook_delivery_admin_router(
    *,
    store: Any,
    authorize_manage: Callable[[Principal], Any],
    replay_service: Any | None = None,

) -> APIRouter:
    if replay_service is not None and not callable(getattr(replay_service, "replay", None)):
        raise TypeError("replay_service must expose async replay()")
    router = APIRouter(prefix="/api/platform/v1/webhooks", tags=["webhooks"])

    def principal(request: Request) -> Principal:
        actor = getattr(request.state, "identity.principal", None)
        if not isinstance(actor, Principal):
            raise HTTPException(401, "authenticated user required")
        return actor

    async def can_manage(actor: Principal) -> bool:
        allowed = authorize_manage(actor)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        return allowed is True

    @router.get("/dead-letters")
    async def list_dead_letters(request: Request, limit: int = Query(50, ge=1, le=200)):
        actor = principal(request)
        if not await can_manage(actor):
            raise HTTPException(403, "webhook management permission required")
        return {"items": await store.list_dead_letters(actor.tenant_id, limit=limit)}

    @router.post("/dead-letters/{delivery_id}/replay")
    async def replay_dead_letter(
        delivery_id: str,
        request: Request,
        payload: DeadLetterReplayRequest,
    ):
        actor = principal(request)
        if not await can_manage(actor):
            raise HTTPException(403, "webhook management permission required")
        if replay_service is None:
            raise HTTPException(503, "audited webhook replay service is not configured")
        try:
            replayed = await replay_service.replay(
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                delivery_id=delivery_id,
                reason=payload.reason,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, "webhook replay permission required") from exc
        if not replayed:
            raise HTTPException(404, "dead-letter delivery not found or subscription disabled")
        return {"delivery_id": delivery_id, "status": "pending"}

    return router
