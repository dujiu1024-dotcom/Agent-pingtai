"""Lightweight admin endpoints backed directly by Postgres."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request


def _principal(request: Request):
    actor = getattr(request.state, "identity.principal", None)
    if actor is None:
        actor = getattr(getattr(request.state, "identity", None), "principal", None)
    if actor is None:
        raise HTTPException(401, "authenticated user required")
    return actor


def create_admin_router(*, connection_factory: Any) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/admin", tags=["admin"])

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
    async def list_organization(request: Request):
        _principal(request)
        tenants = await _query(
            """SELECT tenant_id, display_name, status, created_at
               FROM platform_tenants ORDER BY tenant_id"""
        )
        users = await _query(
            """SELECT user_id, email, display_name, status,
                      created_at, updated_at, last_login_at
               FROM platform_users ORDER BY created_at"""
        )
        memberships = await _query(
            """SELECT tenant_id, user_id, status, joined_at
               FROM platform_tenant_memberships ORDER BY tenant_id, joined_at"""
        )
        return {"items": [
            {"section": "tenants", "rows": tenants, "count": len(tenants)},
            {"section": "users", "rows": users, "count": len(users)},
            {"section": "tenant_memberships", "rows": memberships, "count": len(memberships)},
        ], "count": len(tenants) + len(users) + len(memberships)}

    @router.get("/access-control")
    async def list_access_control(request: Request):
        _principal(request)
        roles = await _query(
            """SELECT tenant_id, role_id, name, description, is_system, created_at
               FROM platform_roles ORDER BY tenant_id, role_id"""
        )
        bindings = await _query(
            """SELECT tenant_id, role_id, subject_type, subject_id,
                      resource_type, resource_id, created_at, revoked_at
               FROM platform_role_bindings ORDER BY role_id, subject_id"""
        )
        permissions = await _query(
            """SELECT tenant_id, role_id, permission_key
               FROM platform_role_permissions ORDER BY role_id, permission_key"""
        )
        return {"items": [
            {"section": "roles", "rows": roles, "count": len(roles)},
            {"section": "role_bindings", "rows": bindings, "count": len(bindings)},
            {"section": "role_permissions", "rows": permissions, "count": len(permissions)},
        ], "count": len(roles) + len(bindings) + len(permissions)}

    @router.get("/settings")
    async def list_settings(request: Request):
        _principal(request)
        quotas = await _query(
            """SELECT tenant_id, scope_type, scope_id,
                      period_start, period_end,
                      hard_tokens, hard_cost_usd, hard_concurrency,
                      soft_tokens, soft_cost_usd, priority,
                      created_at, updated_at
               FROM platform_quota_budgets ORDER BY tenant_id, scope_type"""
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
