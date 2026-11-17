"""Shared tenant/resource authorization adapters for API, Gateway and Harness paths.
All adapters bind authorization to a trusted principal and server-resolved resource.
"""
from __future__ import annotations
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from agent_platform.identity.models import AuthenticatedApiCredential, Principal, ResourceRef
from agent_platform.identity.service import AuthorizationDenied, IdentityService
from agent_platform.identity.service_accounts import ServiceIdentityError, ServiceIdentityService, ServicePrincipal

@dataclass(frozen=True)
class ResourceScope:
    tenant_id: str
    resource_type: str
    resource_id: str
    permission: str
    def __post_init__(self) -> None:
        for field in ("tenant_id", "resource_type", "resource_id", "permission"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")

class ResourceScopeAuthorizer:
    """One fail-closed authorization entry point for human and machine actors."""
    def __init__(self, *, identity: IdentityService, service_identity: ServiceIdentityService | None = None) -> None:
        self.identity = identity
        self.service_identity = service_identity

    async def authorize(self, *, principal: Any, scope: ResourceScope) -> None:
        if getattr(principal, "tenant_id", None) != scope.tenant_id:
            raise AuthorizationDenied("principal tenant does not match resource tenant")
        resource = ResourceRef(scope.resource_type, scope.resource_id)
        if isinstance(principal, ServicePrincipal):
            if self.service_identity is None:
                raise ServiceIdentityError("machine identity authorization is not configured")
            await self.service_identity.authorize(principal=principal, resource=resource, permission_key=scope.permission)
            return
        if not isinstance(principal, (Principal, AuthenticatedApiCredential)):
            raise AuthorizationDenied("unsupported principal type")
        await self.identity.authorize(principal=principal, resource=resource, permission_key=scope.permission)

    def gateway_authorizer(
        self, resolve_principal: Callable[[Any], Awaitable[Any]],
        permission_for_resource: Callable[[str], str] | None = None,
    ) -> Callable[[Any, Mapping[str, Any]], Awaitable[bool]]:
        """Build a Tool Gateway ACL callback from trusted execution context."""
        permission_map = {
            "document": "knowledge.read", "file": "file.read",
            "project": "project.read", "session": "session.read",
            "agent": "agent.read", "tenant": "tenant.read",
        }
        permission = permission_for_resource or (lambda kind: permission_map.get(kind, f"{kind}.read"))
        async def authorize(context: Any, _arguments: Mapping[str, Any]) -> bool:
            principal = await resolve_principal(context)
            actor_id = getattr(principal, "user_id", None) or getattr(principal, "service_account_id", None)
            if actor_id != context.actor_id or principal.tenant_id != context.tenant_id:
                return False
            resource_type, separator, resource_id = context.resource.partition(":")
            if not separator or not resource_type or not resource_id:
                return False
            try:
                await self.authorize(principal=principal, scope=ResourceScope(
                    context.tenant_id, resource_type, resource_id, permission(resource_type)))
            except (PermissionError, ValueError):
                return False
            return True
        return authorize

    def harness_media_resolver(self, *, resolve_principal: Callable[[Any], Awaitable[Any]], read_resource: Callable[[Any, Any], Awaitable[bytes]]) -> Callable[[Any, Any], Awaitable[bytes]]:
        """Authorize a file/media resource before the underlying store is read."""
        async def resolve(run_context: Any, media: Any) -> bytes:
            principal = await resolve_principal(run_context)
            if principal.tenant_id != run_context.tenant_id:
                raise AuthorizationDenied("Harness principal tenant mismatch")
            await self.authorize(principal=principal, scope=ResourceScope(
                run_context.tenant_id, "file", media.resource_id, "file.read"))
            content = await read_resource(run_context, media)
            if not isinstance(content, bytes):
                raise TypeError("media resource reader must return bytes")
            return content
        return resolve

    def harness_media_output_writer(self, *, resolve_principal: Callable[[Any], Awaitable[Any]], write_resource: Callable[[Any, str, str, bytes, str | None], Awaitable[Any]]) -> Callable[[Any, str, str, bytes, str | None], Awaitable[Any]]:
        """Authorize the target file scope before persisting Harness artifacts."""
        async def write(run_context: Any, resource_id: str, modality: str, content: bytes, filename: str | None) -> Any:
            principal = await resolve_principal(run_context)
            if principal.tenant_id != run_context.tenant_id:
                raise AuthorizationDenied("Harness principal tenant mismatch")
            await self.authorize(principal=principal, scope=ResourceScope(
                run_context.tenant_id, "file", resource_id, "file.write"))
            return await write_resource(run_context, resource_id, modality, content, filename)
        return write
