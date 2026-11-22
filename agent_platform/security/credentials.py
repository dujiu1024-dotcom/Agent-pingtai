"""Tenant-scoped reusable connector credentials backed by the #15 SecretManager.

Secret bytes are written only through SecretManager's Vault/KMS backend. This
module stores non-secret metadata and explicit Agent/Workflow grants.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import uuid4

from agent_platform.security.secrets import SecretManager

_CREDENTIAL_TYPES = {"oauth", "api_key", "service_account"}
_CONSUMER_TYPES = {"agent", "workflow"}
_SCOPE = re.compile(r"[a-z][a-z0-9:_.\-]{0,95}\Z")


@dataclass(frozen=True)
class CredentialRecord:
    credential_id: str
    tenant_id: str
    name: str
    credential_type: str
    secret_ref: str
    scopes: tuple[str, ...]
    created_by: str
    created_at: datetime
    revoked_at: datetime | None = None


@dataclass(frozen=True)
class CredentialGrant:
    tenant_id: str
    credential_id: str
    consumer_type: str
    consumer_id: str
    scopes: tuple[str, ...]
    granted_by: str
    created_at: datetime


class CredentialRepository(Protocol):
    async def create(self, record: CredentialRecord) -> None: ...
    async def get(self, *, tenant_id: str, credential_id: str) -> CredentialRecord | None: ...
    async def grant(self, grant: CredentialGrant) -> None: ...
    async def get_grant(
        self, *, tenant_id: str, credential_id: str,
        consumer_type: str, consumer_id: str,
    ) -> CredentialGrant | None: ...
    async def revoke(self, *, tenant_id: str, credential_id: str) -> bool: ...
    async def revoke_grant(
        self, *, tenant_id: str, credential_id: str, consumer_type: str, consumer_id: str,
    ) -> None: ...


class CredentialService:
    """Create, share and resolve credentials with explicit tenant/scope checks."""

    def __init__(self, *, secrets: SecretManager, repository: CredentialRepository,
                 audit: Any) -> None:
        self._secrets = secrets
        self._repository = repository
        self._audit = audit

    @staticmethod
    def _required(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValueError(f"{field} must be a non-empty string of at most 256 characters")
        return value.strip()

    @staticmethod
    def _scopes(scopes: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        if not isinstance(scopes, (tuple, list)) or not scopes:
            raise ValueError("at least one scope tag is required")
        if any(not isinstance(item, str) or not _SCOPE.fullmatch(item) for item in scopes):
            raise ValueError("scope tags must be lowercase names without wildcards")
        normalized = tuple(sorted(set(scopes)))
        if len(normalized) != len(scopes):
            raise ValueError("scope tags must be unique")
        return normalized

    async def create(
        self, *, tenant_id: str, name: str, credential_type: str,
        value: str, scopes: tuple[str, ...] | list[str], created_by: str,
    ) -> CredentialRecord:
        tenant = self._required(tenant_id, "tenant_id")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tenant):
            raise ValueError("tenant_id contains unsupported characters")
        clean_name = self._required(name, "name")
        actor = self._required(created_by, "created_by")
        if not isinstance(credential_type, str) or credential_type not in _CREDENTIAL_TYPES:
            raise ValueError("credential_type must be oauth, api_key or service_account")
        tags = self._scopes(scopes)
        credential_id = str(uuid4())
        secret_ref = f"secret://{tenant}/credentials/{credential_id}"
        secret_version = await self._secrets.create(secret_ref=secret_ref, value=value)
        from datetime import datetime, timezone
        record = CredentialRecord(
            credential_id=credential_id, tenant_id=tenant, name=clean_name,
            credential_type=credential_type, secret_ref=secret_ref, scopes=tags,
            created_by=actor, created_at=secret_version.created_at or datetime.now(timezone.utc),
        )
        atomic_create = getattr(self._repository, "create_with_audit", None)
        try:
            if callable(atomic_create):
                await atomic_create(record, audit=self._audit)
            else:
                await self._repository.create(record)
                await self._audit.record(
                    tenant_id=tenant, actor_id=actor, action="credential.created",
                    subject_id=credential_id,
                    detail={"credential_type": credential_type, "scopes": list(tags)},
                )
        except Exception:
            # A Postgres repository rolls metadata and audit back in one
            # transaction. Revoke the external secret so failed creation
            # cannot leave an active, unindexed credential.
            try:
                await self._secrets.revoke(secret_ref=secret_ref)
            except Exception:
                pass
            if not callable(atomic_create):
                try:
                    await self._repository.revoke(
                        tenant_id=tenant, credential_id=credential_id
                    )
                except Exception:
                    pass
            raise
        return record

    async def grant(
        self, *, tenant_id: str, credential_id: str, consumer_type: str,
        consumer_id: str, scopes: tuple[str, ...] | list[str], granted_by: str,
    ) -> CredentialGrant:
        tenant = self._required(tenant_id, "tenant_id")
        credential = self._required(credential_id, "credential_id")
        consumer = self._required(consumer_id, "consumer_id")
        actor = self._required(granted_by, "granted_by")
        if not isinstance(consumer_type, str) or consumer_type not in _CONSUMER_TYPES:
            raise ValueError("consumer_type must be agent or workflow")
        requested = self._scopes(scopes)
        record = await self._repository.get(tenant_id=tenant, credential_id=credential)
        if record is None or record.revoked_at is not None:
            raise KeyError("active credential not found in tenant")
        if not set(requested).issubset(record.scopes):
            raise PermissionError("grant scopes exceed credential scope tags")
        from datetime import datetime, timezone
        grant = CredentialGrant(
            tenant_id=tenant, credential_id=credential,
            consumer_type=consumer_type, consumer_id=consumer,
            scopes=requested, granted_by=actor, created_at=datetime.now(timezone.utc),
        )
        atomic_grant = getattr(self._repository, "grant_with_audit", None)
        if callable(atomic_grant):
            await atomic_grant(grant, audit=self._audit)
        else:
            await self._repository.grant(grant)
            try:
                await self._audit.record(
                    tenant_id=tenant, actor_id=actor, action="credential.granted",
                    subject_id=credential,
                    detail={"consumer_type": consumer_type, "consumer_id": consumer,
                            "scopes": list(requested)},
                )
            except Exception:
                await self._repository.revoke_grant(
                    tenant_id=tenant, credential_id=credential,
                    consumer_type=consumer_type, consumer_id=consumer,
                )
                raise
        return grant

    async def resolve(
        self, *, tenant_id: str, credential_id: str, consumer_type: str,
        consumer_id: str, required_scopes: tuple[str, ...] | list[str],
        actor_id: str,
    ) -> str:
        tenant = self._required(tenant_id, "tenant_id")
        credential = self._required(credential_id, "credential_id")
        consumer = self._required(consumer_id, "consumer_id")
        actor = self._required(actor_id, "actor_id")
        if not isinstance(consumer_type, str) or consumer_type not in _CONSUMER_TYPES:
            raise ValueError("consumer_type must be agent or workflow")
        required = self._scopes(required_scopes)
        record = await self._repository.get(tenant_id=tenant, credential_id=credential)
        grant = await self._repository.get_grant(
            tenant_id=tenant, credential_id=credential,
            consumer_type=consumer_type, consumer_id=consumer,
        )
        if (
            record is None
            or record.revoked_at is not None
            or grant is None
            or not set(required).issubset(record.scopes)
            or not set(required).issubset(grant.scopes)
        ):
            await self._audit.record(
                tenant_id=tenant, actor_id=actor, action="credential.resolve",
                subject_id=credential, outcome="denied",
                detail={"consumer_type": consumer_type, "consumer_id": consumer,
                        "required_scopes": list(required)},
            )
            raise PermissionError("credential access denied by tenant/scope grant")
        value = await self._secrets.resolve(secret_ref=record.secret_ref)
        # Audit must succeed before returning plaintext to the caller (fail closed).
        await self._audit.record(
            tenant_id=tenant, actor_id=actor, action="credential.resolve",
            subject_id=credential,
            detail={"consumer_type": consumer_type, "consumer_id": consumer,
                    "required_scopes": list(required)},
        )
        return value

    async def revoke(self, *, tenant_id: str, credential_id: str, actor_id: str) -> bool:
        tenant = self._required(tenant_id, "tenant_id")
        credential = self._required(credential_id, "credential_id")
        actor = self._required(actor_id, "actor_id")
        record = await self._repository.get(tenant_id=tenant, credential_id=credential)
        if record is None:
            return False
        atomic_revoke = getattr(self._repository, "revoke_with_audit", None)
        if callable(atomic_revoke):
            changed = await atomic_revoke(
                tenant_id=tenant, credential_id=credential,
                actor_id=actor, audit=self._audit,
            )
            if changed:
                # The metadata+audit transaction has committed. Vault cleanup
                # is a separate external operation and remains retryable.
                await self._secrets.revoke(secret_ref=record.secret_ref)
            return changed

        changed = await self._repository.revoke(tenant_id=tenant, credential_id=credential)
        if changed:
            await self._secrets.revoke(secret_ref=record.secret_ref)
            await self._audit.record(
                tenant_id=tenant, actor_id=actor, action="credential.revoked",
                subject_id=credential,
            )
        return changed


# Public name for callers integrating a shared credential Secret Store.
SecretStore = CredentialService


class PostgresCredentialRepository:
    """PostgreSQL metadata adapter. No secret material is accepted or persisted."""

    def __init__(self, connection_factory: Any) -> None:
        self._connect = connection_factory

    async def create(self, record: CredentialRecord) -> None:
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_credentials
                   (credential_id,tenant_id,name,credential_type,secret_ref,scopes,created_by,created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (record.credential_id, record.tenant_id, record.name, record.credential_type,
                 record.secret_ref, list(record.scopes), record.created_by, record.created_at),
            )

    @staticmethod
    async def _write_audit(
        connection: Any, audit: Any, *, tenant_id: str, actor_id: str,
        action: str, subject_id: str, detail: dict[str, Any] | None = None,
    ) -> None:
        record_on_connection = getattr(audit, "record_on_connection", None)
        if not callable(record_on_connection):
            raise TypeError(
                "Postgres credential mutations require an audit writer "
                "that supports record_on_connection"
            )
        await record_on_connection(
            connection, tenant_id=tenant_id, actor_id=actor_id,
            action=action, subject_id=subject_id, detail=detail,
        )

    async def create_with_audit(self, record: CredentialRecord, *, audit: Any) -> None:
        """Commit credential metadata and its required audit event atomically."""
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    """INSERT INTO platform_credentials
                       (credential_id,tenant_id,name,credential_type,secret_ref,scopes,created_by,created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (record.credential_id, record.tenant_id, record.name,
                     record.credential_type, record.secret_ref, list(record.scopes),
                     record.created_by, record.created_at),
                )
                await self._write_audit(
                    connection, audit, tenant_id=record.tenant_id,
                    actor_id=record.created_by, action="credential.created",
                    subject_id=record.credential_id,
                    detail={"credential_type": record.credential_type,
                            "scopes": list(record.scopes)},
                )

    async def grant_with_audit(self, grant: CredentialGrant, *, audit: Any) -> None:
        """Commit a grant/upsert and its audit event in the same transaction."""
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    """INSERT INTO platform_credential_grants
                       (tenant_id,credential_id,consumer_type,consumer_id,scopes,granted_by,created_at)
                       SELECT %s,%s,%s,%s,%s,%s,%s
                       WHERE EXISTS (
                         SELECT 1 FROM platform_credentials
                         WHERE tenant_id=%s AND credential_id=%s AND revoked_at IS NULL
                       )
                       ON CONFLICT (tenant_id,credential_id,consumer_type,consumer_id)
                       DO UPDATE SET scopes=EXCLUDED.scopes,granted_by=EXCLUDED.granted_by,
                                     created_at=EXCLUDED.created_at""",
                    (grant.tenant_id, grant.credential_id, grant.consumer_type,
                     grant.consumer_id, list(grant.scopes), grant.granted_by,
                     grant.created_at, grant.tenant_id, grant.credential_id),
                )
                await self._write_audit(
                    connection, audit, tenant_id=grant.tenant_id,
                    actor_id=grant.granted_by, action="credential.granted",
                    subject_id=grant.credential_id,
                    detail={"consumer_type": grant.consumer_type,
                            "consumer_id": grant.consumer_id,
                            "scopes": list(grant.scopes)},
                )

    async def revoke_with_audit(
        self, *, tenant_id: str, credential_id: str, actor_id: str, audit: Any,
    ) -> bool:
        """Revoke metadata/grants only if the corresponding audit row commits."""
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """UPDATE platform_credentials SET revoked_at=now()
                       WHERE tenant_id=%s AND credential_id=%s AND revoked_at IS NULL
                       RETURNING credential_id""",
                    (tenant_id, credential_id),
                )
                if await cursor.fetchone() is None:
                    return False
                await connection.execute(
                    """DELETE FROM platform_credential_grants
                       WHERE tenant_id=%s AND credential_id=%s""",
                    (tenant_id, credential_id),
                )
                await self._write_audit(
                    connection, audit, tenant_id=tenant_id, actor_id=actor_id,
                    action="credential.revoked", subject_id=credential_id,
                )
        return True

    @staticmethod
    def _record(row: Any) -> CredentialRecord:
        return CredentialRecord(
            credential_id=str(row["credential_id"]), tenant_id=row["tenant_id"],
            name=row["name"], credential_type=row["credential_type"],
            secret_ref=row["secret_ref"], scopes=tuple(row["scopes"] or ()),
            created_by=row["created_by"], created_at=row["created_at"],
            revoked_at=row["revoked_at"],
        )

    async def get(self, *, tenant_id: str, credential_id: str) -> CredentialRecord | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT credential_id,tenant_id,name,credential_type,secret_ref,scopes,
                          created_by,created_at,revoked_at
                   FROM platform_credentials WHERE tenant_id=%s AND credential_id=%s""",
                (tenant_id, credential_id),
            )
            row = await cursor.fetchone()
        return self._record(row) if row else None

    async def grant(self, grant: CredentialGrant) -> None:
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_credential_grants
                   (tenant_id,credential_id,consumer_type,consumer_id,scopes,granted_by,created_at)
                   SELECT %s,%s,%s,%s,%s,%s,%s
                   WHERE EXISTS (
                     SELECT 1 FROM platform_credentials
                     WHERE tenant_id=%s AND credential_id=%s AND revoked_at IS NULL
                   )
                   ON CONFLICT (tenant_id,credential_id,consumer_type,consumer_id)
                   DO UPDATE SET scopes=EXCLUDED.scopes,granted_by=EXCLUDED.granted_by,
                                 created_at=EXCLUDED.created_at""",
                (grant.tenant_id, grant.credential_id, grant.consumer_type, grant.consumer_id,
                 list(grant.scopes), grant.granted_by, grant.created_at,
                 grant.tenant_id, grant.credential_id),
            )

    async def get_grant(self, *, tenant_id: str, credential_id: str,
                        consumer_type: str, consumer_id: str) -> CredentialGrant | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT tenant_id,credential_id,consumer_type,consumer_id,scopes,
                          granted_by,created_at
                   FROM platform_credential_grants
                   WHERE tenant_id=%s AND credential_id=%s
                     AND consumer_type=%s AND consumer_id=%s""",
                (tenant_id, credential_id, consumer_type, consumer_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return CredentialGrant(
            tenant_id=row["tenant_id"], credential_id=str(row["credential_id"]),
            consumer_type=row["consumer_type"], consumer_id=row["consumer_id"],
            scopes=tuple(row["scopes"] or ()), granted_by=row["granted_by"],
            created_at=row["created_at"],
        )

    async def revoke_grant(self, *, tenant_id: str, credential_id: str,
                           consumer_type: str, consumer_id: str) -> None:
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """DELETE FROM platform_credential_grants
                   WHERE tenant_id=%s AND credential_id=%s
                     AND consumer_type=%s AND consumer_id=%s""",
                (tenant_id, credential_id, consumer_type, consumer_id),
            )

    async def revoke(self, *, tenant_id: str, credential_id: str) -> bool:
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """UPDATE platform_credentials SET revoked_at=now()
                       WHERE tenant_id=%s AND credential_id=%s AND revoked_at IS NULL
                       RETURNING credential_id""",
                    (tenant_id, credential_id),
                )
                if await cursor.fetchone() is None:
                    return False
                await connection.execute(
                    """DELETE FROM platform_credential_grants
                       WHERE tenant_id=%s AND credential_id=%s""",
                    (tenant_id, credential_id),
                )
        return True
