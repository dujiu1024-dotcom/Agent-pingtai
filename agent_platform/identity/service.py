"""Supervised identity and resource authorization application service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from agent_platform.identity.models import (
    AuthenticatedApiCredential,
    IssuedApiCredential,
    IssuedSession,
    Principal,
    ResourceRef,
)
from agent_platform.identity.protocols import IdentityRepository
from agent_platform.identity.security import (
    CredentialError,
    PasswordHasher,
    issue_opaque_token,
    token_digest,
)


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
        touched = await self._repository.touch_session(
            session_id=record.session_id, tenant_id=record.tenant_id
        )
        if not touched:
            raise AuthenticationError("invalid session")
        return Principal(user_id=record.user_id, tenant_id=record.tenant_id, session_id=record.session_id, email=record.email, display_name=record.display_name)

    async def logout(self, principal: Principal) -> bool:
        return await self._repository.revoke_session(session_id=principal.session_id, tenant_id=principal.tenant_id, user_id=principal.user_id, revoked_at=datetime.now(timezone.utc))

    async def authorize(
        self, *, principal: Principal | AuthenticatedApiCredential,
        resource: ResourceRef, permission_key: str,
    ) -> None:
        if not permission_key.strip():
            raise ValueError("permission_key must not be empty")
        # No-auth mode (auth.enabled=false): dummy principal seeded by
        # identity middleware bypasses all RBAC checks.
        if isinstance(principal, Principal) and principal.user_id == "anon":
            return
        if isinstance(principal, AuthenticatedApiCredential):
            await self.authorize_api_credential(
                credential=principal, resource=resource,
                permission_key=permission_key,
            )
            return
        allowed = await self._repository.has_permission(
            tenant_id=principal.tenant_id, user_id=principal.user_id,
            resource_type=resource.resource_type,
            resource_id=resource.resource_id, permission_key=permission_key,
        )
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
            binding_id=binding_id, resource_type=resource.resource_type,
            resource_id=resource.resource_id,
        )


    async def create_api_credential(
        self, *, principal: Principal, name: str, scopes: tuple[str, ...],
        ttl: timedelta | None = timedelta(days=90),
    ) -> IssuedApiCredential:
        clean_name = name.strip()
        normalized_scopes = tuple(sorted(set(scope.strip() for scope in scopes if scope.strip())))
        if not clean_name or len(clean_name) > 100:
            raise ValueError("credential name must contain 1 to 100 characters")
        if not normalized_scopes or len(normalized_scopes) > 50:
            raise ValueError("between 1 and 50 non-empty scopes are required")
        if any(len(scope) > 128 for scope in normalized_scopes):
            raise ValueError("scope is too long")
        if ttl is not None and (ttl <= timedelta(0) or ttl > timedelta(days=365)):
            raise ValueError("credential ttl must be at most 365 days")
        token = issue_opaque_token("api")
        credential_id = str(uuid4())
        expires_at = datetime.now(timezone.utc) + ttl if ttl is not None else None
        await self._repository.create_api_credential(
            credential_id=credential_id, tenant_id=principal.tenant_id,
            user_id=principal.user_id, name=clean_name,
            token_hash=token_digest(token), scopes=normalized_scopes,
            expires_at=expires_at,
        )
        return IssuedApiCredential(
            credential_id=credential_id, tenant_id=principal.tenant_id,
            user_id=principal.user_id, name=clean_name, access_token=token,
            scopes=normalized_scopes, expires_at=expires_at,
        )

    async def authenticate_api_credential(
        self, access_token: str,
    ) -> AuthenticatedApiCredential:
        if not access_token or len(access_token) > 512:
            raise AuthenticationError("invalid API credential")
        record = await self._repository.get_api_credential(token_digest(access_token))
        now = datetime.now(timezone.utc)
        if (
            record is None or record.revoked_at is not None
            or (record.expires_at is not None and record.expires_at <= now)
            or record.user_status != "active" or record.membership_status != "active"
        ):
            raise AuthenticationError("invalid API credential")
        if not await self._repository.touch_api_credential(
            credential_id=record.credential_id, tenant_id=record.tenant_id,
        ):
            raise AuthenticationError("invalid API credential")
        return AuthenticatedApiCredential(
            credential_id=record.credential_id, tenant_id=record.tenant_id,
            user_id=record.user_id, scopes=record.scopes,
        )

    async def revoke_api_credential(
        self, *, principal: Principal, credential_id: str,
    ) -> bool:
        if not credential_id.strip():
            raise ValueError("credential_id is required")
        return await self._repository.revoke_api_credential(
            credential_id=credential_id, tenant_id=principal.tenant_id,
            user_id=principal.user_id,
        )

    async def create_invitation(
        self, *, principal: Principal, email: str, role_id: str,
        ttl: timedelta = timedelta(days=7),
    ) -> tuple[str, str, datetime]:
        normalized_email = self.normalize_email(email)
        clean_role = role_id.strip()
        if not clean_role or len(clean_role) > 100:
            raise ValueError("role_id is required")
        if ttl <= timedelta(0) or ttl > timedelta(days=30):
            raise ValueError("invitation ttl must be at most 30 days")
        await self.authorize(
            principal=principal,
            resource=ResourceRef("tenant", principal.tenant_id),
            permission_key="user.manage",
        )
        token = issue_opaque_token("inv")
        invitation_id = str(uuid4())
        expires_at = datetime.now(timezone.utc) + ttl
        await self._repository.create_invitation(
            invitation_id=invitation_id, tenant_id=principal.tenant_id,
            email_normalized=normalized_email, role_id=clean_role,
            token_hash=token_digest(token), invited_by=principal.user_id,
            expires_at=expires_at,
        )
        return invitation_id, token, expires_at

    async def accept_invitation(
        self, *, principal: Principal, invitation_token: str,
    ) -> bool:
        if not invitation_token or len(invitation_token) > 512:
            raise AuthenticationError("invalid invitation")
        token_hash = token_digest(invitation_token)
        invitation = await self._repository.get_invitation(token_hash)
        now = datetime.now(timezone.utc)
        if (
            invitation is None or invitation.accepted_at is not None
            or invitation.expires_at <= now
            or invitation.email_normalized != self.normalize_email(principal.email)
        ):
            raise AuthenticationError("invalid invitation")
        return await self._repository.accept_invitation(
            invitation_id=invitation.invitation_id, token_hash=token_hash,
            tenant_id=invitation.tenant_id, user_id=principal.user_id,
            email_normalized=invitation.email_normalized,
        )


    async def authorize_api_credential(
        self, *, credential: AuthenticatedApiCredential,
        resource: ResourceRef, permission_key: str,
    ) -> None:
        if permission_key not in credential.scopes:
            raise AuthorizationDenied("API credential scope denied")
        allowed = await self._repository.has_permission(
            tenant_id=credential.tenant_id, user_id=credential.user_id,
            resource_type=resource.resource_type, resource_id=resource.resource_id,
            permission_key=permission_key,
        )
        if not allowed:
            raise AuthorizationDenied("permission denied")


    async def rotate_api_credential(
        self, *, principal: Principal, old_credential_id: str, name: str,
        scopes: tuple[str, ...], ttl: timedelta | None = timedelta(days=90),
    ) -> IssuedApiCredential:
        clean_name = name.strip()
        normalized_scopes = tuple(sorted(set(scope.strip() for scope in scopes if scope.strip())))
        if not old_credential_id.strip() or not clean_name or len(clean_name) > 100:
            raise ValueError("credential id and a valid name are required")
        if not normalized_scopes or len(normalized_scopes) > 50:
            raise ValueError("between 1 and 50 non-empty scopes are required")
        if any(len(scope) > 128 for scope in normalized_scopes):
            raise ValueError("scope is too long")
        if ttl is not None and (ttl <= timedelta(0) or ttl > timedelta(days=365)):
            raise ValueError("credential ttl must be at most 365 days")
        token = issue_opaque_token("api")
        credential_id = str(uuid4())
        expires_at = datetime.now(timezone.utc) + ttl if ttl is not None else None
        rotated = await self._repository.rotate_api_credential(
            old_credential_id=old_credential_id, new_credential_id=credential_id,
            tenant_id=principal.tenant_id, user_id=principal.user_id,
            name=clean_name, token_hash=token_digest(token),
            scopes=normalized_scopes, expires_at=expires_at,
        )
        if not rotated:
            raise AuthenticationError("API credential is invalid, expired, or already revoked")
        return IssuedApiCredential(
            credential_id=credential_id, tenant_id=principal.tenant_id,
            user_id=principal.user_id, name=clean_name, access_token=token,
            scopes=normalized_scopes, expires_at=expires_at,
        )
