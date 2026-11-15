"""Bridge authenticated HTTP identities to tenant-scoped Knowledge/Memory principals."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from agent_platform.identity.models import Principal
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
        if not isinstance(authenticated, Principal):
            raise HTTPException(
                status_code=403,
                detail="Knowledge/Memory routes currently require a human session",
            )

        context = await self.identity_repository.get_authorization_context(
            tenant_id=authenticated.tenant_id,
            user_id=authenticated.user_id,
        )
        if context is None:
            raise HTTPException(status_code=403, detail="active tenant membership required")
        return context

    async def knowledge(self, request: Request) -> KnowledgePrincipal:
        context = await self._context(request)
        return KnowledgePrincipal(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            project_ids=context.project_ids,
            roles=context.effective_role_ids,
            permissions=context.tenant_permissions,
            manage_project_ids=context.knowledge_manage_project_ids,
        )

    async def memory(self, request: Request) -> MemoryPrincipal:
        context = await self._context(request)
        return MemoryPrincipal(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            project_ids=context.project_ids,
            agent_ids=context.agent_ids,
            roles=context.tenant_permissions,
        )
