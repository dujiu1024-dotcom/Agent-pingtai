"""Supervised identity and resource authorization application service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_platform.identity.models import IssuedSession, Principal, ResourceRef
from agent_platform.identity.protocols import IdentityRepository
from agent_platform.identity.security import CredentialError, PasswordHasher, issue_opaque_token, token_digest

class AuthenticationError(PermissionError):
    """Invalid, expired, revoked, or suspended identity."""

class AuthorizationDenied(PermissionError):
    """Default-deny authorization result."""

class IdentityService:
    def __init__(self, repository: IdentityRepository) -> None:
        self._repository = repository

    @staticmethod
    def normalize_email(email: str) -> str:
        normalized = email.strip().casefold()
        if not normalized or "@" not in normalized or len(normalized) > 320:
            raise CredentialError("a valid email address is required")
        return normalized

    async def login(self, *, email: str, password: str, tenant_id: str, session_ttl: timedelta = timedelta(hours=12)) -> IssuedSession:
        if session_ttl <= timedelta(0) or session_ttl > timedelta(days=30):
            raise ValueError("session_ttl must be greater than zero and at most 30 days")
        normalized = self.normalize_email(email)
        user = await self._repository.get_user_by_email(normalized)
        if user is None or user.status != "active" or not PasswordHasher.verify(password, user.password_hash):
            raise AuthenticationError("invalid credentials")
        if not await self._repository.is_active_member(tenant_id=tenant_id, user_id=user.user_id):
            raise AuthenticationError("invalid credentials")
        now = datetime.now(timezone.utc)
        expires_at = now + session_ttl
        token = issue_opaque_token("aps")
        session_id = issue_opaque_token("sid")
        await self._repository.create_session(session_id=session_id, tenant_id=tenant_id, user_id=user.user_id, token_hash=token_digest(token), expires_at=expires_at)
        return IssuedSession(session_id=session_id, tenant_id=tenant_id, user_id=user.user_id, access_token=token, expires_at=expires_at)

    async def authenticate(self, access_token: str) -> Principal:
        if not access_token or len(access_token) > 512:
            raise AuthenticationError("invalid session")
        record = await self._repository.get_session_by_token_hash(token_digest(access_token))
        now = datetime.now(timezone.utc)
        if record is None or record.revoked_at is not None or record.expires_at <= now or record.user_status != "active" or record.membership_status != "active":
            raise AuthenticationError("invalid session")
        await self._repository.touch_session(session_id=record.session_id, tenant_id=record.tenant_id)
        return Principal(user_id=record.user_id, tenant_id=record.tenant_id, session_id=record.session_id, email=record.email, display_name=record.display_name)

    async def logout(self, principal: Principal) -> bool:
        return await self._repository.revoke_session(session_id=principal.session_id, tenant_id=principal.tenant_id, user_id=principal.user_id, revoked_at=datetime.now(timezone.utc))

    async def authorize(self, *, principal: Principal, resource: ResourceRef, permission_key: str) -> None:
        if not permission_key.strip():
            raise ValueError("permission_key must not be empty")
        allowed = await self._repository.has_permission(tenant_id=principal.tenant_id, user_id=principal.user_id, resource_type=resource.resource_type, resource_id=resource.resource_id, permission_key=permission_key)
        if not allowed:
            raise AuthorizationDenied("permission denied")

    async def grant_role(
        self, *, principal: Principal, subject_type: str, subject_id: str,
        resource: ResourceRef, role_id: str,
    ) -> str:
        if subject_type not in {"user", "team", "service_account", "agent"}:
            raise ValueError("unsupported role binding subject_type")
        if not subject_id.strip() or not role_id.strip():
            raise ValueError("subject_id and role_id must not be empty")
        await self.authorize(
            principal=principal, resource=resource,
            permission_key="identity.role.manage",
        )
        return await self._repository.grant_role(
            tenant_id=principal.tenant_id, actor_id=principal.user_id,
            subject_type=subject_type, subject_id=subject_id,
            resource_type=resource.resource_type, resource_id=resource.resource_id,
            role_id=role_id,
        )

    async def revoke_role(
        self, *, principal: Principal, binding_id: str, resource: ResourceRef
    ) -> bool:
        await self.authorize(
            principal=principal, resource=resource,
            permission_key="identity.role.manage",
        )
        return await self._repository.revoke_role(
            tenant_id=principal.tenant_id, actor_id=principal.user_id,
            binding_id=binding_id,
        )
