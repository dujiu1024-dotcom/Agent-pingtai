"""Independent service-account and Agent identity authentication.

Machine credentials are deliberately separate from human sessions/API keys.
Authorization is scope-limited and also requires a live RBAC binding.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

from agent_platform.identity.models import ResourceRef
from agent_platform.identity.security import issue_opaque_token, token_digest


class ServiceIdentityError(PermissionError):
    """Invalid, expired, revoked, or unauthorized machine identity."""


@dataclass(frozen=True)
class ServicePrincipal:
    tenant_id: str
    service_account_id: str
    agent_id: str | None
    credential_id: str
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class IssuedServiceCredential:
    credential_id: str
    tenant_id: str
    service_account_id: str
    agent_id: str | None
    token: str
    scopes: tuple[str, ...]
    expires_at: datetime | None


@dataclass(frozen=True)
class _Credential:
    credential_id: str
    tenant_id: str
    service_account_id: str
    agent_id: str | None
    scopes: tuple[str, ...]
    expires_at: datetime | None
    revoked_at: datetime | None
    service_status: str
    agent_status: str | None


ConnectionFactory = Callable[[], Any]


class PostgresServiceIdentityRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def issue(
        self, *, credential_id: str, tenant_id: str, service_account_id: str,
        agent_id: str | None, name: str, token_hash: str,
        scopes: tuple[str, ...], expires_at: datetime | None,
    ) -> None:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """INSERT INTO platform_service_credentials
                       (credential_id, tenant_id, service_account_id, agent_id,
                        name, token_hash, scopes, expires_at)
                       SELECT %s,%s,%s,%s,%s,%s,%s,%s
                       WHERE EXISTS (
                         SELECT 1 FROM platform_service_accounts sa
                         JOIN platform_tenants t ON t.tenant_id=sa.tenant_id
                         WHERE sa.tenant_id=%s AND sa.service_account_id=%s
                           AND sa.status='active' AND t.status='active'
                       )
                       AND (%s IS NULL OR EXISTS (
                         SELECT 1 FROM platform_agent_identities ai
                         WHERE ai.tenant_id=%s AND ai.agent_id=%s
                           AND ai.service_account_id=%s AND ai.status='active'
                       ))
                       RETURNING credential_id""",
                    (credential_id, tenant_id, service_account_id, agent_id,
                     name, token_hash, list(scopes), expires_at,
                     tenant_id, service_account_id, agent_id, tenant_id,
                     agent_id, service_account_id),
                )
                if await cursor.fetchone() is None:
                    raise ServiceIdentityError("active service account and Agent binding required")

    async def get(self, token_hash: str) -> _Credential | None:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT c.credential_id,c.tenant_id,c.service_account_id,c.agent_id,
                          c.scopes,c.expires_at,c.revoked_at,sa.status AS service_status,
                          ai.status AS agent_status
                   FROM platform_service_credentials c
                   JOIN platform_service_accounts sa
                     ON sa.tenant_id=c.tenant_id AND sa.service_account_id=c.service_account_id
                   JOIN platform_tenants t ON t.tenant_id=c.tenant_id AND t.status='active'
                   LEFT JOIN platform_agent_identities ai
                     ON ai.tenant_id=c.tenant_id AND ai.agent_id=c.agent_id
                    AND ai.service_account_id=c.service_account_id
                   WHERE c.token_hash=%s""", (token_hash,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return _Credential(
            credential_id=str(row["credential_id"]), tenant_id=row["tenant_id"],
            service_account_id=row["service_account_id"], agent_id=row["agent_id"],
            scopes=tuple(row["scopes"] or ()), expires_at=row["expires_at"],
            revoked_at=row["revoked_at"], service_status=row["service_status"],
            agent_status=row["agent_status"],
        )

    async def touch(self, *, credential_id: str, tenant_id: str) -> bool:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_service_credentials c SET last_used_at=now()
                   FROM platform_service_accounts sa, platform_tenants t
                   WHERE c.credential_id=%s AND c.tenant_id=%s
                     AND c.revoked_at IS NULL
                     AND (c.expires_at IS NULL OR c.expires_at>now())
                     AND sa.tenant_id=c.tenant_id
                     AND sa.service_account_id=c.service_account_id
                     AND sa.status='active' AND t.tenant_id=c.tenant_id
                     AND t.status='active'
                     AND (c.agent_id IS NULL OR EXISTS (
                       SELECT 1 FROM platform_agent_identities ai
                       WHERE ai.tenant_id=c.tenant_id AND ai.agent_id=c.agent_id
                         AND ai.service_account_id=c.service_account_id
                         AND ai.status='active'
                     ))
                   RETURNING c.credential_id""", (credential_id, tenant_id),
            )
            return await cursor.fetchone() is not None

    async def revoke(
        self, *, credential_id: str, tenant_id: str, service_account_id: str,
    ) -> bool:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_service_credentials SET revoked_at=now()
                   WHERE credential_id=%s AND tenant_id=%s
                     AND service_account_id=%s AND revoked_at IS NULL
                   RETURNING credential_id""",
                (credential_id, tenant_id, service_account_id),
            )
            return await cursor.fetchone() is not None

    async def has_permission(
        self, *, principal: ServicePrincipal, resource: ResourceRef,
        permission_key: str,
    ) -> bool:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT EXISTS (
                   SELECT 1 FROM platform_role_bindings b
                   JOIN platform_role_permissions rp
                     ON rp.tenant_id=b.tenant_id AND rp.role_id=b.role_id
                   WHERE b.tenant_id=%s AND b.revoked_at IS NULL
                     AND rp.permission_key=%s
                     AND (
                       (b.subject_type='service_account'
                         AND b.subject_id=%s)
                       OR (%s IS NOT NULL AND b.subject_type='agent'
                         AND b.subject_id=%s)
                     )
                     AND (
                       (b.resource_type=%s AND b.resource_id=%s)
                       OR (b.resource_type='tenant' AND b.resource_id=%s)
                       OR (%s='project' AND b.resource_type='organization'
                         AND b.resource_id=(
                           SELECT p.organization_id FROM platform_projects p
                           JOIN platform_organizations o
                             ON o.tenant_id=p.tenant_id
                            AND o.organization_id=p.organization_id
                            AND o.status='active'
                           WHERE p.tenant_id=%s AND p.project_id=%s
                         ))
                     )
                   AND EXISTS (
                     SELECT 1 FROM platform_service_credentials c
                     JOIN platform_service_accounts sa
                       ON sa.tenant_id=c.tenant_id
                      AND sa.service_account_id=c.service_account_id
                     JOIN platform_tenants t ON t.tenant_id=c.tenant_id
                     WHERE c.credential_id=%s AND c.tenant_id=%s
                       AND c.service_account_id=%s AND c.revoked_at IS NULL
                       AND (c.expires_at IS NULL OR c.expires_at>now())
                       AND sa.status='active' AND t.status='active'
                   )
                )""",
                (principal.tenant_id, permission_key,
                 principal.service_account_id, principal.agent_id, principal.agent_id,
                 resource.resource_type, resource.resource_id, principal.tenant_id,
                 resource.resource_type, principal.tenant_id, resource.resource_id,
                 principal.credential_id, principal.tenant_id,
                 principal.service_account_id),
            )
            return bool((await cursor.fetchone())["exists"])


class ServiceIdentityService:
    def __init__(self, repository: PostgresServiceIdentityRepository) -> None:
        self._repository = repository

    async def issue_credential(
        self, *, tenant_id: str, service_account_id: str, name: str,
        scopes: tuple[str, ...], agent_id: str | None = None,
        ttl: timedelta | None = timedelta(days=90),
    ) -> IssuedServiceCredential:
        clean_name = name.strip()
        normalized_scopes = tuple(sorted(set(s.strip() for s in scopes if s.strip())))
        if not tenant_id.strip() or not service_account_id.strip():
            raise ValueError("tenant_id and service_account_id are required")
        if not clean_name or len(clean_name) > 100:
            raise ValueError("credential name must contain 1 to 100 characters")
        if not normalized_scopes or len(normalized_scopes) > 50:
            raise ValueError("between 1 and 50 scopes are required")
        if any(len(scope) > 128 for scope in normalized_scopes):
            raise ValueError("scope is too long")
        if ttl is not None and (ttl <= timedelta(0) or ttl > timedelta(days=365)):
            raise ValueError("ttl must be at most 365 days")
        token = issue_opaque_token("svc")
        credential_id = str(uuid4())
        expires_at = datetime.now(timezone.utc) + ttl if ttl is not None else None
        await self._repository.issue(
            credential_id=credential_id, tenant_id=tenant_id,
            service_account_id=service_account_id, agent_id=agent_id,
            name=clean_name, token_hash=token_digest(token),
            scopes=normalized_scopes, expires_at=expires_at,
        )
        return IssuedServiceCredential(
            credential_id=credential_id, tenant_id=tenant_id,
            service_account_id=service_account_id, agent_id=agent_id,
            token=token, scopes=normalized_scopes, expires_at=expires_at,
        )

    async def authenticate(self, token: str) -> ServicePrincipal:
        if not token or len(token) > 512:
            raise ServiceIdentityError("invalid service credential")
        record = await self._repository.get(token_digest(token))
        now = datetime.now(timezone.utc)
        if (
            record is None or record.revoked_at is not None
            or (record.expires_at is not None and record.expires_at <= now)
            or record.service_status != "active"
            or (record.agent_id is not None and record.agent_status != "active")
        ):
            raise ServiceIdentityError("invalid service credential")
        if not await self._repository.touch(
            credential_id=record.credential_id, tenant_id=record.tenant_id,
        ):
            raise ServiceIdentityError("invalid service credential")
        return ServicePrincipal(
            tenant_id=record.tenant_id, service_account_id=record.service_account_id,
            agent_id=record.agent_id, credential_id=record.credential_id,
            scopes=record.scopes,
        )

    async def authorize(
        self, *, principal: ServicePrincipal, resource: ResourceRef,
        permission_key: str,
    ) -> None:
        if permission_key not in principal.scopes:
            raise ServiceIdentityError("service credential scope denied")
        if not await self._repository.has_permission(
            principal=principal, resource=resource, permission_key=permission_key,
        ):
            raise ServiceIdentityError("service identity permission denied")

    async def revoke(
        self, *, tenant_id: str, service_account_id: str, credential_id: str,
    ) -> bool:
        return await self._repository.revoke(
            credential_id=credential_id, tenant_id=tenant_id,
            service_account_id=service_account_id,
        )
