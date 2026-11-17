"""Credential management API. Plaintext is accepted only on create and never returned."""
from __future__ import annotations

import inspect
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.credentials.service import CredentialError
from agent_platform.identity.models import Principal


def create_credentials_router(
    *, resolve_service: Callable[[Principal], Any],
    authorize: Callable[[Principal, str, str, str], Any],
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/credentials", tags=["credentials"])

    def principal(request: Request) -> Principal:
        value = getattr(request.state, "identity.principal", None)
        if not isinstance(value, Principal):
            raise HTTPException(401, "authentication required")
        return value

    async def allowed(actor: Principal, resource_type: str, resource_id: str, action: str) -> bool:
        result = authorize(actor, resource_type, resource_id, action)
        if inspect.isawaitable(result):
            result = await result
        return result is True

    async def service_for(actor: Principal):
        service = resolve_service(actor)
        if inspect.isawaitable(service):
            service = await service
        if not callable(getattr(getattr(service, "audit_writer", None), "record", None)):
            raise HTTPException(503, "credential audit service is not configured")
        return service

    def serialize(item: Any) -> dict[str, Any]:
        return {
            "credential_id": item.credential_id, "provider": item.provider,
            "display_name": item.display_name, "scopes": list(item.scopes),
            "status": item.status, "created_by": item.created_by,
            "created_at": item.created_at.isoformat(),
            "revoked_at": item.revoked_at.isoformat() if item.revoked_at else None,
        }

    @router.post("", status_code=201)
    async def create_credential(request: Request):
        actor = principal(request)
        if not await allowed(actor, "credential", "*", "create"):
            raise HTTPException(403, "credential management permission required")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            if set(body) - {"credential_id", "provider", "display_name", "value", "scopes"}:
                raise ValueError("unknown credential fields")
            service = await service_for(actor)
            item = await service.create(
                tenant_id=actor.tenant_id, credential_id=body.get("credential_id"),
                provider=body.get("provider"), display_name=body.get("display_name"),
                value=body.get("value"), scopes=body.get("scopes"), actor_id=actor.user_id,
            )
        except (CredentialError, ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(503, "credential creation failed") from exc
        return serialize(item)

    @router.get("")
    async def list_credentials(request: Request, limit: int = 100):
        actor = principal(request)
        if not await allowed(actor, "credential", "*", "read"):
            raise HTTPException(403, "credential read permission required")
        try:
            service = await service_for(actor)
            return {"items": [serialize(item) for item in await service.list(
                tenant_id=actor.tenant_id, limit=limit,
            )]}
        except CredentialError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/{credential_id}")
    async def get_credential(request: Request, credential_id: str):
        actor = principal(request)
        if not await allowed(actor, "credential", credential_id, "read"):
            raise HTTPException(403, "credential read permission required")
        item = await (await service_for(actor)).get(
            tenant_id=actor.tenant_id, credential_id=credential_id,
        )
        if item is None:
            raise HTTPException(404, "credential not found")
        return serialize(item)

    @router.post("/{credential_id}/grants", status_code=201)
    async def grant_credential(request: Request, credential_id: str):
        actor = principal(request)
        if not await allowed(actor, "credential", credential_id, "grant"):
            raise HTTPException(403, "credential grant permission required")
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) - {"grantee_type", "grantee_id", "scopes"}:
                raise ValueError("grantee_type, grantee_id and scopes are required")
            grantee_type, grantee_id = body.get("grantee_type"), body.get("grantee_id")
            if not isinstance(grantee_type, str) or not isinstance(grantee_id, str):
                raise ValueError("grantee_type and grantee_id must be strings")
            if not await allowed(actor, grantee_type, grantee_id, "credential.use"):
                raise HTTPException(403, "target Agent or Workflow is not accessible")
            grant = await (await service_for(actor)).grant(
                tenant_id=actor.tenant_id, credential_id=credential_id,
                grantee_type=grantee_type, grantee_id=grantee_id,
                scopes=body.get("scopes"), actor_id=actor.user_id,
            )
        except HTTPException:
            raise
        except (CredentialError, ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "active credential not found") from exc
        except Exception as exc:
            raise HTTPException(503, "credential grant failed") from exc
        return {
            "credential_id": grant.credential_id, "grantee_type": grant.grantee_type,
            "grantee_id": grant.grantee_id, "scopes": list(grant.scopes),
            "granted_by": grant.granted_by, "created_at": grant.created_at.isoformat(),
        }

    @router.get("/{credential_id}/grants")
    async def list_grants(request: Request, credential_id: str):
        actor = principal(request)
        if not await allowed(actor, "credential", credential_id, "read"):
            raise HTTPException(403, "credential read permission required")
        try:
            grants = await (await service_for(actor)).grants(
                tenant_id=actor.tenant_id, credential_id=credential_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "credential not found") from exc
        return {"items": [{
            "grantee_type": item.grantee_type, "grantee_id": item.grantee_id,
            "scopes": list(item.scopes), "granted_by": item.granted_by,
            "created_at": item.created_at.isoformat(),
            "revoked_at": item.revoked_at.isoformat() if item.revoked_at else None,
        } for item in grants]}

    @router.delete("/{credential_id}")
    async def revoke_credential(request: Request, credential_id: str):
        actor = principal(request)
        if not await allowed(actor, "credential", credential_id, "revoke"):
            raise HTTPException(403, "credential revoke permission required")
        try:
            revoked = await (await service_for(actor)).revoke(
                tenant_id=actor.tenant_id, credential_id=credential_id, actor_id=actor.user_id,
            )
        except Exception as exc:
            raise HTTPException(503, "credential revocation failed") from exc
        if not revoked:
            raise HTTPException(404, "credential not found")
        return {"credential_id": credential_id, "status": "revoked"}

    return router
