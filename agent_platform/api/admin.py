"""Lightweight admin endpoints backed directly by Postgres."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from agent_platform.identity.models import AuthenticatedApiCredential, Principal


def _principal(request: Request):
    actor = getattr(request.state, "identity.principal", None)
    if actor is None:
        actor = getattr(getattr(request.state, "identity", None), "principal", None)
    if actor is None:
        raise HTTPException(401, "authenticated user required")
    return actor


def create_admin_router(
    *, connection_factory: Any, identity_repository: Any | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/admin", tags=["admin"])

    async def require_admin(request: Request):
        """Require the current tenant's persisted tenant.manage permission."""
        principal = _principal(request)
        if identity_repository is None:
            raise HTTPException(503, "admin authorization is not configured")
        if not isinstance(principal, (Principal, AuthenticatedApiCredential)):
            raise HTTPException(403, "administrator permission required")
        if (
            isinstance(principal, AuthenticatedApiCredential)
            and "tenant.manage" not in principal.scopes
        ):
            raise HTTPException(403, "administrator permission required")
        allowed = await identity_repository.has_permission(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            resource_type="tenant",
            resource_id=principal.tenant_id,
            permission_key="tenant.manage",
        )
        if not allowed:
            raise HTTPException(403, "administrator permission required")
        return principal

    async def _query(sql: str, params: tuple = ()) -> list[dict]:
        connection = await connection_factory()
        try:
            async with connection:
                cursor = await connection.execute(sql, params)
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            try:
                await connection.close()
            except Exception:
                pass

    @router.get("/organization")
    async def list_organization(principal: Any = Depends(require_admin)):
        tenant_id = principal.tenant_id
        tenants = await _query(
            """SELECT display_name, status, created_at
               FROM platform_tenants WHERE tenant_id=%s ORDER BY tenant_id""",
            (tenant_id,),
        )
        users = await _query(
            """SELECT display_name, email, status,
                      last_login_at, created_at
               FROM platform_users
               WHERE user_id IN (
                 SELECT user_id FROM platform_tenant_memberships WHERE tenant_id=%s
               ) ORDER BY created_at""",
            (tenant_id,),
        )
        return {"items": [
            {"section": "tenants", "rows": tenants, "count": len(tenants)},
            {"section": "users", "rows": users, "count": len(users)},
        ], "count": len(tenants) + len(users)}

    @router.get("/access-control")
    async def list_access_control(principal: Any = Depends(require_admin)):
        tenant_id = principal.tenant_id
        roles = await _query(
            """SELECT name, description, is_system, created_at
               FROM platform_roles WHERE tenant_id=%s ORDER BY role_id""",
            (tenant_id,),
        )
        bindings = await _query(
            """SELECT r.name AS role_name,
                      b.subject_type,
                      COALESCE(NULLIF(u.display_name, ''), u.email, b.subject_id) AS subject,
                      b.resource_type,
                      CASE WHEN b.resource_type = 'tenant' THEN '当前租户'
                           ELSE b.resource_id END AS resource,
                      b.created_at, b.revoked_at
               FROM platform_role_bindings b
               LEFT JOIN platform_roles r
                      ON r.tenant_id = b.tenant_id AND r.role_id = b.role_id
               LEFT JOIN platform_users u
                      ON b.subject_type = 'user' AND u.user_id::text = b.subject_id
               WHERE b.tenant_id=%s
               ORDER BY r.name, subject""",
            (tenant_id,),
        )
        permissions = await _query(
            """SELECT r.name AS role_name, p.permission_key
               FROM platform_role_permissions p
               LEFT JOIN platform_roles r
                      ON r.tenant_id = p.tenant_id AND r.role_id = p.role_id
               WHERE p.tenant_id=%s
               ORDER BY r.name, p.permission_key""",
            (tenant_id,),
        )
        return {"items": [
            {"section": "roles", "rows": roles, "count": len(roles)},
            {"section": "role_bindings", "rows": bindings, "count": len(bindings)},
            {"section": "role_permissions", "rows": permissions, "count": len(permissions)},
        ], "count": len(roles) + len(bindings) + len(permissions)}

    @router.get("/settings")
    async def list_settings(principal: Any = Depends(require_admin)):
        quotas = await _query(
            """SELECT scope_type, scope_id,
                      period_start, period_end,
                      hard_tokens, hard_cost_usd, hard_concurrency,
                      soft_tokens, soft_cost_usd, priority,
                      created_at, updated_at
               FROM platform_quota_budgets
               WHERE tenant_id=%s
               ORDER BY scope_type, scope_id""",
            (principal.tenant_id,),
        )
        counts = await _query(
            """SELECT 'platform_tenants' AS table_name, COUNT(*) AS row_count FROM platform_tenants
               UNION ALL SELECT 'platform_users', COUNT(*) FROM platform_users
               UNION ALL SELECT 'platform_agents', COUNT(*) FROM platform_agents
               UNION ALL SELECT 'platform_runs', COUNT(*) FROM platform_runs
               UNION ALL SELECT 'platform_prompt_versions', COUNT(*) FROM platform_prompt_versions
               UNION ALL SELECT 'platform_prompt_deployments', COUNT(*) FROM platform_prompt_deployments
            """
        )
        return {"items": [
            {"section": "quota_budgets", "rows": quotas, "count": len(quotas)},
            {"section": "storage_counts", "rows": counts, "count": len(counts)},
        ], "count": len(quotas) + len(counts)}

    return router
