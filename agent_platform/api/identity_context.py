"""Bridge authenticated HTTP identities to tenant-scoped Knowledge/Memory principals."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from agent_platform.identity.models import AuthenticatedApiCredential, Principal
from agent_platform.knowledge.contracts import KnowledgePrincipal
from agent_platform.memory.service import MemoryPrincipal


class IdentityContextResolver:
    """Resolve resource access from persisted RBAC, never from request-supplied IDs."""

    def __init__(self, identity_repository: Any) -> None:
        self.identity_repository = identity_repository

    async def _context(self, request: Request):
        authenticated = getattr(request.state, "identity.principal", None)
        if authenticated is None:
            raise HTTPException(status_code=401, detail="authentication required")
        if not isinstance(authenticated, (Principal, AuthenticatedApiCredential)):
            raise HTTPException(status_code=403, detail="unsupported identity for Knowledge/Memory routes")

        # No-auth mode (auth.enabled=false): skip RBAC lookup, grant full access
        # to the dummy principal seeded by identity middleware.
        is_no_auth = getattr(request.state, "identity.authenticated", True) is False
        if is_no_auth and isinstance(authenticated, Principal) and authenticated.user_id == "anon":
            ALL_KNOWLEDGE_PERMS = frozenset({
                "knowledge.read", "knowledge.manage",
            })
            ALL_MEMORY_PERMS = frozenset({
                "memory.read", "memory.manage",
            })
            ALL_PERMS = ALL_KNOWLEDGE_PERMS | ALL_MEMORY_PERMS
            return authenticated, None, ALL_PERMS

        context = await self.identity_repository.get_authorization_context(
            tenant_id=authenticated.tenant_id,
            user_id=authenticated.user_id,
        )
        if context is None:
            raise HTTPException(status_code=403, detail="active tenant membership required")
        permissions = context.tenant_permissions
        if isinstance(authenticated, AuthenticatedApiCredential):
            # Credential scopes can only narrow persisted RBAC, never grant it.
            permissions = permissions.intersection(authenticated.scopes)
            if not permissions:
                raise HTTPException(status_code=403, detail="API credential has no matching Knowledge/Memory permissions")
        return authenticated, context, permissions

    async def knowledge(self, request: Request) -> KnowledgePrincipal:
        authenticated, context, permissions = await self._context(request)
        is_no_auth = getattr(request.state, "identity.authenticated", True) is False
        if is_no_auth:
            # No-auth: full-access dummy knowledge principal with default tenant
            return KnowledgePrincipal(
                tenant_id=authenticated.tenant_id,
                user_id=authenticated.user_id,
                project_ids=frozenset(),
                roles=frozenset({"tenant.admin"}),
                permissions=frozenset({"knowledge.read", "knowledge.manage"}),
                manage_project_ids=frozenset(),
            )
        if isinstance(authenticated, AuthenticatedApiCredential) and not (
            {"knowledge.read", "knowledge.manage"} & permissions
        ):
            raise HTTPException(status_code=403, detail="API credential lacks Knowledge read scope")
        return KnowledgePrincipal(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            project_ids=context.project_ids,
            roles=context.effective_role_ids,
            permissions=permissions,
            manage_project_ids=(
                context.knowledge_manage_project_ids
                if "knowledge.manage" in permissions else frozenset()
            ),
        )

    async def memory(self, request: Request) -> MemoryPrincipal:
        authenticated, context, permissions = await self._context(request)
        is_no_auth = getattr(request.state, "identity.authenticated", True) is False
        if is_no_auth:
            # No-auth: full-access dummy memory principal with default tenant
            return MemoryPrincipal(
                tenant_id=authenticated.tenant_id,
                user_id=authenticated.user_id,
                project_ids=frozenset(),
                agent_ids=frozenset(),
                permissions=frozenset({"memory.read", "memory.manage"}),
                manage_project_ids=frozenset(),
                manage_agent_ids=frozenset(),
            )
        if isinstance(authenticated, AuthenticatedApiCredential) and not (
            {"memory.read", "memory.manage"} & permissions
        ):
            raise HTTPException(status_code=403, detail="API credential lacks Memory read scope")
        return MemoryPrincipal(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            project_ids=context.project_ids,
            agent_ids=context.agent_ids,
            permissions=permissions,
            manage_project_ids=(
                context.memory_manage_project_ids
                if not isinstance(authenticated, AuthenticatedApiCredential)
                or "memory.manage" in permissions else frozenset()
            ),
            manage_agent_ids=(
                context.memory_manage_agent_ids
                if not isinstance(authenticated, AuthenticatedApiCredential)
                or "memory.manage" in permissions else frozenset()
            ),
        )
