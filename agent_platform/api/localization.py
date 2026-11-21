"""Authenticated tenant locale preference API."""
from __future__ import annotations

import inspect
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.i18n.preferences import TenantLocalePreferenceService
from agent_platform.identity.models import Principal


def create_locale_preferences_router(
    *, service: TenantLocalePreferenceService,
    authorize_manage: Callable[[Principal], Any],
    audit_writer: Any,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/tenant/locale", tags=["localization"])

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

    def serialize(item: Any) -> dict[str, str | None]:
        return {
            "tenant_id": item.tenant_id, "language": item.language,
            "timezone": item.timezone_name, "region": item.region,
            "updated_by": item.updated_by,
        }

    @router.get("")
    async def get_locale(request: Request):
        actor = principal(request)
        return serialize(await service.get(actor.tenant_id))

    @router.put("")
    async def update_locale(request: Request):
        actor = principal(request)
        if not await can_manage(actor):
            raise HTTPException(403, "tenant locale management permission required")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            language = body.get("language")
            timezone_name = body.get("timezone")
            region = body.get("region")
            if not all(isinstance(value, str) for value in (language, timezone_name, region)):
                raise ValueError("language, timezone and region are required strings")
            if audit_writer is None or not callable(getattr(audit_writer, "record", None)):
                raise HTTPException(503, "locale audit writer is not configured")
            try:
                await audit_writer.record(
                    tenant_id=actor.tenant_id, actor_id=actor.user_id,
                    action="tenant.locale.updated.requested", subject_id=actor.tenant_id,
                    outcome="succeeded",
                    detail={"language": language, "timezone": timezone_name, "region": region},
                )
            except Exception as exc:
                raise HTTPException(503, "locale audit write failed; preference was not changed") from exc
            preference = await service.update(
                tenant_id=actor.tenant_id, actor_id=actor.user_id,
                language=language, timezone_name=timezone_name, region=region,
            )
        except HTTPException:
            raise
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return serialize(preference)

    return router
