"""Tenant-scoped webhook subscription administration API."""
from __future__ import annotations

import inspect
from typing import Any, Callable
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request

from agent_platform.events.admin import WebhookSubscriptionService
from agent_platform.identity.models import Principal


def create_webhook_admin_router(*, service: WebhookSubscriptionService,
                                authorize_manage: Callable[[Principal], Any],
                                audit_writer: Any | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/webhooks", tags=["webhooks"])

    def principal(request: Request) -> Principal:
        actor = getattr(request.state, "identity.principal", None)
        if not isinstance(actor, Principal):
            raise HTTPException(401, "authenticated user required")
        return actor

    async def audit(
        actor: Principal, action: str, detail: dict[str, Any], *,
        outcome: str = "succeeded", subject_id: str = "webhook-subscription",
    ) -> None:
        if audit_writer is None or not callable(getattr(audit_writer, "record", None)):
            raise HTTPException(503, "webhook audit writer is not configured")
        await audit_writer.record(
            tenant_id=actor.tenant_id, actor_id=actor.user_id,
            action=action, subject_id=subject_id,
            outcome=outcome, detail=detail,
        )

    def destination_host(url: str) -> str:
        try:
            return (urlsplit(url).hostname or "invalid").lower()
        except ValueError:
            return "invalid"

    async def can_manage(actor: Principal) -> bool:
        allowed = authorize_manage(actor)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        return allowed is True

    @router.post("", status_code=201)
    async def create_subscription(request: Request):
        actor = principal(request)
        if not await can_manage(actor):
            await audit(
                actor, "webhook.subscription.create.denied",
                {"operation": "create"}, outcome="denied",
            )
            raise HTTPException(403, "webhook management permission required")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            secret = body.get("signing_secret")
            topics = body.get("topics")
            if not isinstance(secret, str) or not isinstance(topics, list) or not all(isinstance(x, str) for x in topics):
                raise ValueError("signing_secret and topics are required")
            target_url = str(body.get("url") or "")
            await audit(actor, "webhook.subscription.create.requested", {
                "destination_host": destination_host(target_url),
                "topics": topics,
            })
            subscription_id = await service.create(
                tenant_id=actor.tenant_id, actor_id=actor.user_id,
                url=target_url, topics=tuple(topics),
                signing_secret=secret.encode("utf-8"),
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"subscription_id": subscription_id, "status": "active"}

    @router.get("")
    async def list_subscriptions(request: Request):
        actor = principal(request)
        if not await can_manage(actor):
            await audit(
                actor, "webhook.subscription.list.denied",
                {"operation": "list"}, outcome="denied",
            )
            raise HTTPException(403, "webhook management permission required")
        items = await service.list(tenant_id=actor.tenant_id)
        return {"items": items}

    @router.post("/{subscription_id}/rotate")
    async def rotate_secret(subscription_id: str, request: Request):
        actor = principal(request)
        if not await can_manage(actor):
            await audit(
                actor, "webhook.subscription.secret_rotate.denied",
                {"subscription_id": subscription_id}, outcome="denied",
                subject_id=subscription_id,
            )
            raise HTTPException(403, "webhook management permission required")
        try:
            body = await request.json()
            secret = body.get("signing_secret") if isinstance(body, dict) else None
            if not isinstance(secret, str):
                raise ValueError("signing_secret is required")
            await audit(
                actor, "webhook.subscription.secret_rotate.requested",
                {"subscription_id": subscription_id}, subject_id=subscription_id,
            )
            changed = await service.rotate(
                tenant_id=actor.tenant_id, subscription_id=subscription_id,
                signing_secret=secret.encode("utf-8"),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not changed:
            raise HTTPException(404, "webhook subscription not found")
        return {"subscription_id": subscription_id, "rotated": True}

    @router.post("/{subscription_id}/disable")
    async def disable_subscription(subscription_id: str, request: Request):
        actor = principal(request)
        if not await can_manage(actor):
            await audit(
                actor, "webhook.subscription.disable.denied",
                {"subscription_id": subscription_id}, outcome="denied",
                subject_id=subscription_id,
            )
            raise HTTPException(403, "webhook management permission required")
        await audit(
            actor, "webhook.subscription.disable.requested",
            {"subscription_id": subscription_id}, subject_id=subscription_id,
        )
        changed = await service.disable(
            tenant_id=actor.tenant_id, subscription_id=subscription_id,
        )
        if not changed:
            raise HTTPException(404, "webhook subscription not found")
        return {"subscription_id": subscription_id, "status": "disabled"}

    return router
