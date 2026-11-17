"""Tenant-scoped credential catalog layered over the platform SecretManager.

Credential metadata and grants contain references only. Secret material is encrypted
by the configured SecretManager backend (Vault KV v2 or KMS envelope); it is never
returned by management APIs or written to audit metadata.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_GRANTEE_TYPES = {"agent", "workflow"}


@dataclass(frozen=True)
class Credential:
    tenant_id: str
    credential_id: str
    provider: str
    display_name: str
    secret_ref: str
    scopes: tuple[str, ...]
    created_by: str
    created_at: datetime
    status: str = "active"
    revoked_at: datetime | None = None


@dataclass(frozen=True)
class CredentialGrant:
    tenant_id: str
    credential_id: str
    grantee_type: str
    grantee_id: str
    scopes: tuple[str, ...]
    granted_by: str
    created_at: datetime
    revoked_at: datetime | None = None


class CredentialRepository(Protocol):
    async def create(self, credential: Credential) -> None: ...
    async def get(self, tenant_id: str, credential_id: str) -> Credential | None: ...
    async def list(self, tenant_id: str, *, limit: int = 100) -> list[Credential]: ...
    async def grant(self, grant: CredentialGrant) -> None: ...
    async def list_grants(self, tenant_id: str, credential_id: str) -> list[CredentialGrant]: ...
    async def get_grant(
        self, tenant_id: str, credential_id: str, grantee_type: str, grantee_id: str,
    ) -> CredentialGrant | None: ...
    async def revoke(self, tenant_id: str, credential_id: str, at: datetime) -> bool: ...


class CredentialAuditWriter(Protocol):
    async def record(
        self, *, tenant_id: str, actor_id: str, action: str, subject_id: str,
        outcome: str = "succeeded", detail: Mapping[str, Any] | None = None,
    ) -> None: ...


class CredentialError(ValueError):
    """Invalid credential metadata or a denied credential operation."""


def _required_id(value: str, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise CredentialError(f"{field} is invalid")
    return value


def _labels(values: tuple[str, ...] | list[str], field: str = "scopes") -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)) or not values or len(values) > 64:
        raise CredentialError(f"{field} must contain 1 to 64 labels")
    if any(not isinstance(value, str) or not _LABEL.fullmatch(value) for value in values):
        raise CredentialError(f"{field} contains an invalid label")
    normalized = tuple(sorted(set(values)))
    if len(normalized) != len(values):
        raise CredentialError(f"{field} must not contain duplicate labels")
    return normalized


class CredentialService:
    """Manage encrypted credential material and explicit Agent/Workflow grants.

    The service is the only runtime path that resolves plaintext. A grantee must
    have an active grant whose labels cover every requested operation.
    """

    def __init__(self, *, repository: CredentialRepository, secret_manager: Any,
                 audit_writer: CredentialAuditWriter) -> None:
        if not callable(getattr(secret_manager, "create", None)) or not callable(
            getattr(secret_manager, "resolve", None)
        ) or not callable(getattr(secret_manager, "revoke", None)):
            raise TypeError("credential service requires the platform SecretManager")
        if not callable(getattr(audit_writer, "record", None)):
            raise TypeError("credential service requires an append-only audit writer")
        self.repository = repository
        self.secret_manager = secret_manager
        self.audit_writer = audit_writer

    async def _audit(self, *, tenant_id: str, actor_id: str, action: str,
                     credential_id: str, detail: Mapping[str, Any] | None = None) -> None:
        # Audit is write-ahead and fail-closed: no secret operation occurs if this fails.
        await self.audit_writer.record(
            tenant_id=tenant_id, actor_id=actor_id, action=action,
            subject_id=credential_id, outcome="succeeded", detail=detail,
        )

    @staticmethod
    def _secret_ref(tenant_id: str, credential_id: str) -> str:
        _required_id(tenant_id, "tenant_id")
        _required_id(credential_id, "credential_id")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tenant_id):
            raise CredentialError("tenant_id cannot be represented by the platform secret reference")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", credential_id):
            raise CredentialError("credential_id cannot be represented by the platform secret reference")
        return f"secret://{tenant_id}/credentials/{credential_id}"

    async def create(
        self, *, tenant_id: str, credential_id: str, provider: str,
        display_name: str, value: str, scopes: tuple[str, ...] | list[str],
        actor_id: str,
    ) -> Credential:
        _required_id(tenant_id, "tenant_id")
        _required_id(credential_id, "credential_id")
        _required_id(actor_id, "actor_id")
        if not isinstance(provider, str) or not _ID.fullmatch(provider):
            raise CredentialError("provider is invalid")
        if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 120:
            raise CredentialError("display_name must contain 1 to 120 characters")
        normalized = _labels(scopes)
        ref = self._secret_ref(tenant_id, credential_id)
        if await self.repository.get(tenant_id, credential_id) is not None:
            raise CredentialError("credential already exists")
        await self._audit(
            tenant_id=tenant_id, actor_id=actor_id, action="credential.create.requested",
            credential_id=credential_id, detail={"provider": provider, "scopes": list(normalized)},
        )
        await self.secret_manager.create(secret_ref=ref, value=value)
        now = datetime.now(timezone.utc)
        record = Credential(
            tenant_id=tenant_id, credential_id=credential_id, provider=provider,
            display_name=display_name.strip(), secret_ref=ref, scopes=normalized,
            created_by=actor_id, created_at=now,
        )
        try:
            await self.repository.create(record)
        except Exception:
            # Metadata failure must not leave an unreferenced live secret behind.
            await self.secret_manager.revoke(secret_ref=ref)
            raise
        await self._audit(
            tenant_id=tenant_id, actor_id=actor_id, action="credential.created",
            credential_id=credential_id, detail={"provider": provider, "scopes": list(normalized)},
        )
        return record

    async def get(self, *, tenant_id: str, credential_id: str) -> Credential | None:
        _required_id(tenant_id, "tenant_id")
        _required_id(credential_id, "credential_id")
        return await self.repository.get(tenant_id, credential_id)

    async def list(self, *, tenant_id: str, limit: int = 100) -> list[Credential]:
        _required_id(tenant_id, "tenant_id")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise CredentialError("limit must be an integer between 1 and 200")
        return await self.repository.list(tenant_id, limit=limit)

    async def grant(
        self, *, tenant_id: str, credential_id: str, grantee_type: str,
        grantee_id: str, scopes: tuple[str, ...] | list[str], actor_id: str,
    ) -> CredentialGrant:
        _required_id(tenant_id, "tenant_id")
        _required_id(credential_id, "credential_id")
        _required_id(grantee_id, "grantee_id")
        _required_id(actor_id, "actor_id")
        if grantee_type not in _GRANTEE_TYPES:
            raise CredentialError("grantee_type must be agent or workflow")
        normalized = _labels(scopes)
        credential = await self.repository.get(tenant_id, credential_id)
        if credential is None or credential.status != "active":
            raise KeyError("active credential not found")
        if not set(normalized).issubset(credential.scopes):
            raise CredentialError("grant scopes exceed credential authorization labels")
        await self._audit(
            tenant_id=tenant_id, actor_id=actor_id, action="credential.grant.requested",
            credential_id=credential_id,
            detail={"grantee_type": grantee_type, "grantee_id": grantee_id, "scopes": list(normalized)},
        )
        grant = CredentialGrant(
            tenant_id=tenant_id, credential_id=credential_id, grantee_type=grantee_type,
            grantee_id=grantee_id, scopes=normalized, granted_by=actor_id,
            created_at=datetime.now(timezone.utc),
        )
        await self.repository.grant(grant)
        await self._audit(
            tenant_id=tenant_id, actor_id=actor_id, action="credential.granted",
            credential_id=credential_id,
            detail={"grantee_type": grantee_type, "grantee_id": grantee_id, "scopes": list(normalized)},
        )
        return grant

    async def grants(self, *, tenant_id: str, credential_id: str) -> list[CredentialGrant]:
        _required_id(tenant_id, "tenant_id")
        _required_id(credential_id, "credential_id")
        if await self.repository.get(tenant_id, credential_id) is None:
            raise KeyError("credential not found")
        return await self.repository.list_grants(tenant_id, credential_id)

    async def revoke(self, *, tenant_id: str, credential_id: str, actor_id: str) -> bool:
        _required_id(tenant_id, "tenant_id")
        _required_id(credential_id, "credential_id")
        _required_id(actor_id, "actor_id")
        credential = await self.repository.get(tenant_id, credential_id)
        if credential is None:
            return False
        if credential.status == "revoked":
            return True
        await self._audit(
            tenant_id=tenant_id, actor_id=actor_id, action="credential.revoke.requested",
            credential_id=credential_id, detail={"provider": credential.provider},
        )
        # Disable grants first, then revoke every SecretManager version.
        changed = await self.repository.revoke(tenant_id, credential_id, datetime.now(timezone.utc))
        if changed:
            await self.secret_manager.revoke(secret_ref=credential.secret_ref)
        await self._audit(
            tenant_id=tenant_id, actor_id=actor_id, action="credential.revoked",
            credential_id=credential_id, detail={"provider": credential.provider},
        )
        return True

    async def resolve_for_runtime(
        self, *, tenant_id: str, credential_id: str, grantee_type: str,
        grantee_id: str, required_scopes: tuple[str, ...] | list[str],
    ) -> str:
        """Resolve plaintext only after tenant, active-state, grant and scope checks."""
        _required_id(tenant_id, "tenant_id")
        _required_id(credential_id, "credential_id")
        _required_id(grantee_id, "grantee_id")
        if grantee_type not in _GRANTEE_TYPES:
            raise CredentialError("grantee_type must be agent or workflow")
        required = _labels(required_scopes, "required_scopes")
        credential = await self.repository.get(tenant_id, credential_id)
        if credential is None or credential.status != "active":
            raise KeyError("active credential not found")
        grant = await self.repository.get_grant(
            tenant_id, credential_id, grantee_type, grantee_id,
        )
        if grant is None or grant.revoked_at is not None or not set(required).issubset(grant.scopes):
            await self.audit_writer.record(
                tenant_id=tenant_id, actor_id=f"{grantee_type}:{grantee_id}",
                action="credential.resolve.denied", subject_id=credential_id,
                outcome="denied", detail={"grantee_type": grantee_type, "required_scopes": list(required)},
            )
            raise PermissionError("credential grant does not authorize requested scopes")
        await self._audit(
            tenant_id=tenant_id, actor_id=f"{grantee_type}:{grantee_id}",
            action="credential.resolve", credential_id=credential_id,
            detail={"grantee_type": grantee_type, "grantee_id": grantee_id, "scopes": list(required)},
        )
        value = await self.secret_manager.resolve(secret_ref=credential.secret_ref)
        if not isinstance(value, str) or not value:
            raise RuntimeError("credential secret is unavailable")
        return value


class PostgresCredentialRepository:
    """Tenant-filtered credential and grant metadata; plaintext is never stored."""

    def __init__(self, connection_factory: Any) -> None:
        self._connect = connection_factory

    @staticmethod
    def _credential(row: Mapping[str, Any]) -> Credential:
        scopes = row["scopes"]
        if isinstance(scopes, str):
            import json
            scopes = json.loads(scopes)
        return Credential(
            tenant_id=row["tenant_id"], credential_id=row["credential_id"],
            provider=row["provider"], display_name=row["display_name"],
            secret_ref=row["secret_ref"], scopes=tuple(scopes),
            created_by=row["created_by"], created_at=row["created_at"],
            status=row["status"], revoked_at=row["revoked_at"],
        )

    @staticmethod
    def _grant(row: Mapping[str, Any]) -> CredentialGrant:
        scopes = row["scopes"]
        if isinstance(scopes, str):
            import json
            scopes = json.loads(scopes)
        return CredentialGrant(
            tenant_id=row["tenant_id"], credential_id=row["credential_id"],
            grantee_type=row["grantee_type"], grantee_id=row["grantee_id"],
            scopes=tuple(scopes), granted_by=row["granted_by"],
            created_at=row["created_at"], revoked_at=row["revoked_at"],
        )

    async def create(self, credential: Credential) -> None:
        import json
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_credentials
                   (tenant_id,credential_id,provider,display_name,secret_ref,scopes,created_by,created_at)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s)""",
                (credential.tenant_id, credential.credential_id, credential.provider,
                 credential.display_name, credential.secret_ref,
                 json.dumps(list(credential.scopes)), credential.created_by, credential.created_at),
            )

    async def get(self, tenant_id: str, credential_id: str) -> Credential | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT tenant_id,credential_id,provider,display_name,secret_ref,scopes,
                          created_by,created_at,status,revoked_at
                   FROM platform_credentials WHERE tenant_id=%s AND credential_id=%s""",
                (tenant_id, credential_id),
            )
            row = await cursor.fetchone()
        return self._credential(row) if row else None

    async def list(self, tenant_id: str, *, limit: int = 100) -> list[Credential]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT tenant_id,credential_id,provider,display_name,secret_ref,scopes,
                          created_by,created_at,status,revoked_at
                   FROM platform_credentials WHERE tenant_id=%s
                   ORDER BY created_at DESC,credential_id LIMIT %s""",
                (tenant_id, limit),
            )
            rows = await cursor.fetchall()
        return [self._credential(row) for row in rows]

    async def grant(self, grant: CredentialGrant) -> None:
        import json
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """INSERT INTO platform_credential_grants
                   (tenant_id,credential_id,grantee_type,grantee_id,scopes,granted_by,created_at)
                   SELECT c.tenant_id,c.credential_id,%s,%s,%s::jsonb,%s,%s
                   FROM platform_credentials c
                   WHERE c.tenant_id=%s AND c.credential_id=%s AND c.status='active'
                     AND %s::jsonb <@ c.scopes
                   ON CONFLICT (tenant_id,credential_id,grantee_type,grantee_id)
                   DO UPDATE SET scopes=EXCLUDED.scopes,granted_by=EXCLUDED.granted_by,
                                 created_at=EXCLUDED.created_at,revoked_at=NULL
                   RETURNING credential_id""",
                (grant.grantee_type, grant.grantee_id, json.dumps(list(grant.scopes)),
                 grant.granted_by, grant.created_at, grant.tenant_id, grant.credential_id,
                 json.dumps(list(grant.scopes))),
            )
            if await cursor.fetchone() is None:
                raise PermissionError("credential is inactive or grant scopes exceed credential labels")

    async def list_grants(self, tenant_id: str, credential_id: str) -> list[CredentialGrant]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT tenant_id,credential_id,grantee_type,grantee_id,scopes,
                          granted_by,created_at,revoked_at
                   FROM platform_credential_grants
                   WHERE tenant_id=%s AND credential_id=%s
                   ORDER BY grantee_type,grantee_id""",
                (tenant_id, credential_id),
            )
            rows = await cursor.fetchall()
        return [self._grant(row) for row in rows]

    async def get_grant(
        self, tenant_id: str, credential_id: str, grantee_type: str, grantee_id: str,
    ) -> CredentialGrant | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT tenant_id,credential_id,grantee_type,grantee_id,scopes,
                          granted_by,created_at,revoked_at
                   FROM platform_credential_grants
                   WHERE tenant_id=%s AND credential_id=%s
                     AND grantee_type=%s AND grantee_id=%s""",
                (tenant_id, credential_id, grantee_type, grantee_id),
            )
            row = await cursor.fetchone()
        return self._grant(row) if row else None

    async def revoke(self, tenant_id: str, credential_id: str, at: datetime) -> bool:
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """UPDATE platform_credentials SET status='revoked',revoked_at=COALESCE(revoked_at,%s)
                       WHERE tenant_id=%s AND credential_id=%s AND status='active'
                       RETURNING credential_id""",
                    (at, tenant_id, credential_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    return False
                await connection.execute(
                    """UPDATE platform_credential_grants SET revoked_at=COALESCE(revoked_at,%s)
                       WHERE tenant_id=%s AND credential_id=%s AND revoked_at IS NULL""",
                    (at, tenant_id, credential_id),
                )
        return True
