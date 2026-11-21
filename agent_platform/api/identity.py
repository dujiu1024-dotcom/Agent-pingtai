"""FastAPI login/logout endpoints backed by the platform identity service."""
from __future__ import annotations

from datetime import timedelta
from secrets import token_urlsafe
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from agent_platform.identity.bootstrap import BootstrapError
from agent_platform.identity.http import auth_cookie_headers, clear_auth_cookie_headers
from agent_platform.identity.models import Principal
from agent_platform.identity.service import AuthenticationError, IdentityService
from agent_platform.identity.tenants import create_tenant_with_owner


def create_identity_router(
    *, identity: IdentityService, session_ttl: timedelta = timedelta(hours=12),
    connection_factory: Any | None = None,
) -> APIRouter:
    if session_ttl <= timedelta(0) or session_ttl > timedelta(days=30):
        raise ValueError("session_ttl must be greater than zero and at most 30 days")
    router = APIRouter(prefix="/auth", tags=["identity"])

    @router.post("/login")
    async def login(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON body is required") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object is required")
        try:
            issued = await identity.login(
                email=str(body.get("email") or ""),
                password=str(body.get("password") or ""),
                tenant_id=str(body.get("tenant_id") or ""),
                session_ttl=session_ttl,
            )
        except AuthenticationError as exc:
            raise HTTPException(401, "invalid credentials") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        csrf_token = token_urlsafe(32)
        response = JSONResponse({
            "authenticated": True,
            "tenant_id": issued.tenant_id,
            "expires_at": issued.expires_at.isoformat(),
            "csrf_header": "X-CSRF-Token",
        })
        response.raw_headers.extend(auth_cookie_headers(
            session_token=issued.access_token,
            csrf_token=csrf_token,
            max_age_seconds=int(session_ttl.total_seconds()),
        ))
        return response

    @router.post("/initialize")
    async def initialize(request: Request) -> JSONResponse:
        """Create the first tenant owner. Only available before any tenant exists."""
        if connection_factory is None:
            raise HTTPException(503, "identity initialization is not configured")
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON body is required") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object is required")
        tenant_id = str(body.get("tenant_id") or "").strip()
        tenant_name = str(body.get("tenant_name") or "").strip() or tenant_id
        email = str(body.get("email") or "").strip()
        display_name = str(body.get("display_name") or "").strip() or email
        password = str(body.get("password") or "")
        if not tenant_id or not email:
            raise HTTPException(400, "tenant_id and email are required")
        if len(password) < 12:
            raise HTTPException(400, "password must contain at least 12 characters")

        connection = await connection_factory()
        try:
            async with connection:
                cursor = await connection.execute(
                    "SELECT count(*) AS total FROM platform_tenants"
                )
                row = await cursor.fetchone()
                total = int(dict(row)["total"] if row is not None else 0)
                if total > 0:
                    raise HTTPException(409, "platform is already initialized")
                user_id = await create_tenant_with_owner(
                    connection,
                    tenant_id=tenant_id,
                    display_name=tenant_name,
                    owner_email=email,
                    owner_display_name=display_name,
                    owner_password=password,
                )
        except HTTPException:
            raise
        except BootstrapError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            try:
                await connection.close()
            except Exception:
                pass
        return JSONResponse({
            "initialized": True,
            "tenant_id": tenant_id,
            "user_id": user_id,
        }, status_code=201)

    @router.post("/logout")
    async def logout(request: Request) -> JSONResponse:
        principal: Any = getattr(request.state, "identity.principal", None)
        if not isinstance(principal, Principal):
            raise HTTPException(401, "authenticated user session required")
        await identity.logout(principal)
        response = JSONResponse({"authenticated": False})
        response.raw_headers.extend(clear_auth_cookie_headers())
        return response

    @router.get("/me")
    async def me(request: Request) -> dict[str, Any]:
        principal: Any = getattr(request.state, "identity.principal", None)
        if principal is None:
            raise HTTPException(401, "authentication required")
        result: dict[str, Any] = {
            "tenant_id": principal.tenant_id,
            "principal_type": "user",
        }
        if isinstance(principal, Principal):
            result.update({
                "user_id": principal.user_id,
                "email": principal.email,
                "display_name": principal.display_name,
            })
        elif getattr(principal, "service_account_id", None):
            result.update({
                "principal_type": "service_account",
                "service_account_id": principal.service_account_id,
                "agent_id": principal.agent_id,
            })
        return result

    return router
