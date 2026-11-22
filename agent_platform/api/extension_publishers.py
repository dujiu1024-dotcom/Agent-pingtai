"""Authenticated and audited management API for extension publisher trust roots."""
from __future__ import annotations

import inspect
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.extensions.publishers import TrustedPublisherKeyRegistry
from agent_platform.identity.models import Principal


def create_extension_publisher_router(
    *,
    registry: TrustedPublisherKeyRegistry,
    authorize_manage: Callable[[Principal, str, str], Any],
    audit_writer: Any,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/platform/v1/extensions/publishers",
        tags=["extensions"],
    )

    def principal(request: Request) -> Principal:
        # 兼容两种注入方式：
        #   a) request.state.identity.principal  （dict 或带属性的对象）
        #   b) request.state.principal
        actor = None
        actor = getattr(request.state, "identity.principal", None)
        if actor is None:
            identity = getattr(request.state, "identity", None)
            if identity is not None:
                actor = getattr(identity, "principal", None)
                if actor is None and isinstance(identity, dict):
                    actor = identity.get("principal")
        if actor is None:
            actor = getattr(request.state, "principal", None)
        if not isinstance(actor, Principal):
            raise HTTPException(401, "authenticated user required")
        return actor

    async def authorize(actor: Principal, extension_id: str, action: str) -> None:
        allowed = authorize_manage(actor, extension_id, action)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if allowed is not True:
            raise HTTPException(403, "extension publisher administration denied")

    async def audit(actor: Principal, extension_id: str, key_id: str,
                    action: str, outcome: str) -> None:
        if audit_writer is None or not callable(getattr(audit_writer, "record", None)):
            raise HTTPException(503, "extension publisher audit writer is not configured")
        try:
            await audit_writer.record(
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action=f"extension.publisher_key.{action}",
                subject_id=f"{extension_id}:{key_id}",
                outcome=outcome,
                detail={"extension_id": extension_id, "key_id": key_id},
            )
        except Exception as exc:
            raise HTTPException(503, "extension publisher audit write failed") from exc

    @router.post("/keys", status_code=201)
    async def register_key(request: Request):
        actor = principal(request)
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object is required") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object is required")
        extension_id = body.get("extension_id")
        publisher_id = body.get("publisher_id")
        key_id = body.get("key_id")
        pem = body.get("public_key_pem")
        not_before = body.get("not_before", 0)
        expires_at = body.get("expires_at")
        previous = body.get("revoke_key_ids", [])
        if not isinstance(pem, str):
            raise HTTPException(400, "public_key_pem must be a string")
        if not isinstance(previous, list) or any(not isinstance(item, str) for item in previous):
            raise HTTPException(400, "revoke_key_ids must be a string array")
        if not isinstance(extension_id, str):
            raise HTTPException(400, "extension_id is required")
        await authorize(actor, extension_id, "publisher_key.rotate" if previous else "publisher_key.register")
        # Write-ahead audit: record the attempted registration before side effect.
        action = "rotate" if previous else "register"
        await audit(actor, extension_id, key_id or "pending", action, "requested")
        try:
            pem_bytes = pem.encode("ascii")
            values = {
                "tenant_id": actor.tenant_id,
                "extension_id": extension_id,
                "publisher_id": publisher_id,
                "key_id": key_id,
                "public_key_pem": pem_bytes,
                "not_before": not_before,
                "expires_at": expires_at,
                "created_by": actor.user_id,
            }
            if previous:
                key = await registry.rotate(**values, revoke_key_ids=tuple(previous))
            else:
                key = await registry.register(**values)
        except HTTPException:
            raise
        except (UnicodeEncodeError, ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(503, "publisher key registry is unavailable") from exc
        await audit(actor, extension_id, key.key_id, action, "succeeded")
        return {
            "tenant_id": actor.tenant_id,
            "extension_id": extension_id,
            "publisher_id": key.publisher_id,
            "key_id": key.key_id,
            "not_before": key.not_before,
            "expires_at": key.expires_at,
            "revoked": key.revoked,
        }

    @router.post("/{extension_id}/keys/{key_id}/revoke")
    async def revoke_key(extension_id: str, key_id: str, request: Request):
        actor = principal(request)
        await authorize(actor, extension_id, "publisher_key.revoke")
        try:
            revoked = await registry.revoke(
                tenant_id=actor.tenant_id,
                extension_id=extension_id,
                key_id=key_id,
                revoked_by=actor.user_id,
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(503, "publisher key registry is unavailable") from exc
        if not revoked:
            raise HTTPException(404, "publisher key not found")
        await audit(actor, extension_id, key_id, "revoke", "succeeded")
        return {"extension_id": extension_id, "key_id": key_id, "revoked": True}

    return router
