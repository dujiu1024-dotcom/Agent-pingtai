"""Secret lifecycle facade: secret material stays in an injected vault backend."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol


@dataclass(frozen=True)
class SecretVersion:
    secret_ref: str
    version: int
    status: str
    created_at: datetime
    backend_revoked_at: datetime | None = None


class SecretBackend(Protocol):
    async def put(self, secret_ref: str, version: int, value: str) -> None: ...
    async def get(self, secret_ref: str, version: int) -> str | None: ...
    async def revoke(self, secret_ref: str, version: int) -> None: ...


class SecretMetadataRepository(Protocol):
    async def list_versions(self, secret_ref: str) -> list[SecretVersion]: ...
    async def create_version(self, secret_ref: str, version: int, created_at: datetime) -> None: ...
    async def activate_version(self, secret_ref: str, version: int) -> None: ...
    async def revoke_version(self, secret_ref: str, version: int, revoked_at: datetime) -> None: ...
    async def revoke_all(self, secret_ref: str, revoked_at: datetime) -> list[int]: ...
    async def list_pending_revocations(self, secret_ref: str | None = None) -> list[int]: ...
    async def mark_backend_revoked(self, secret_ref: str, version: int, revoked_at: datetime) -> None: ...


class SecretManager:
    """Versioned secret API; callers receive refs/metadata, never secret values."""

    def __init__(self, *, backend: SecretBackend,
                 metadata: SecretMetadataRepository) -> None:
        self.backend = backend
        self.metadata = metadata

    @staticmethod
    def _validate_ref(secret_ref: str) -> str:
        if not isinstance(secret_ref, str) or not re.fullmatch(
            r"secret://[a-zA-Z0-9_-]{1,64}/[a-zA-Z0-9._/-]{1,240}",
            secret_ref,
        ):
            raise ValueError("secret_ref must be a scoped secret:// reference")
        if ".." in secret_ref or "//" in secret_ref[len("secret://"):]:
            raise ValueError("secret_ref contains an invalid path")
        return secret_ref

    @staticmethod
    def _validate_value(value: str) -> str:
        if not isinstance(value, str) or not value or len(value) > 65536 or "\x00" in value:
            raise ValueError("secret value must be non-empty, NUL-free, and at most 64 KiB")
        return value

    async def _write_version(self, secret_ref: str, value: str) -> SecretVersion:
        versions = await self.metadata.list_versions(secret_ref)
        version = max((item.version for item in versions), default=0) + 1
        now = datetime.now(timezone.utc)
        # Reserve the version in the metadata store before touching the vault.
        # The unique (secret_ref, version) key serializes concurrent writers.
        await self.metadata.create_version(secret_ref, version, now)
        try:
            await self.backend.put(secret_ref, version, value)
            await self.metadata.activate_version(secret_ref, version)
        except Exception:
            # Best-effort mark revoked and cleanup. If either dependency is
            # unavailable, pending metadata remains eligible for retry.
            try:
                await self.metadata.revoke_version(secret_ref, version, datetime.now(timezone.utc))
            except Exception:
                pass
            try:
                await self.backend.revoke(secret_ref, version)
                await self.metadata.mark_backend_revoked(secret_ref, version, datetime.now(timezone.utc))
            except Exception:
                pass
            raise
        return SecretVersion(secret_ref, version, "active", now)

    async def create(self, *, secret_ref: str, value: str) -> SecretVersion:
        ref = self._validate_ref(secret_ref)
        clean_value = self._validate_value(value)
        if await self.metadata.list_versions(ref):
            raise ValueError("secret_ref already exists; use rotate")
        return await self._write_version(ref, clean_value)

    async def rotate(self, *, secret_ref: str, value: str) -> SecretVersion:
        ref = self._validate_ref(secret_ref)
        clean_value = self._validate_value(value)
        current = await self.metadata.list_versions(ref)
        if not any(item.status == "active" for item in current):
            raise KeyError("active secret version not found")
        created = await self._write_version(ref, clean_value)
        # Metadata activation revokes all older versions before backend cleanup.
        await self.retry_revocations(secret_ref=ref)
        return created

    async def resolve(self, *, secret_ref: str) -> str:
        ref = self._validate_ref(secret_ref)
        active = [item for item in await self.metadata.list_versions(ref)
                  if item.status == "active"]
        if len(active) != 1:
            raise KeyError("exactly one active secret version is required")
        value = await self.backend.get(ref, active[0].version)
        if value is None:
            raise RuntimeError("active secret material is unavailable; refusing fallback")
        return value

    async def describe(self, *, secret_ref: str) -> tuple[SecretVersion, ...]:
        ref = self._validate_ref(secret_ref)
        return tuple(await self.metadata.list_versions(ref))

    async def revoke(self, *, secret_ref: str) -> int:
        ref = self._validate_ref(secret_ref)
        await self.metadata.revoke_all(ref, datetime.now(timezone.utc))
        return await self.retry_revocations(secret_ref=ref)

    async def retry_revocations(self, *, secret_ref: str) -> int:
        ref = self._validate_ref(secret_ref)
        pending = await self.metadata.list_pending_revocations(ref)
        cleaned = 0
        failures = 0
        for version in pending:
            try:
                await self.backend.revoke(ref, version)
                await self.metadata.mark_backend_revoked(ref, version, datetime.now(timezone.utc))
                cleaned += 1
            except Exception:
                failures += 1
        if failures:
            raise RuntimeError(f"{failures} secret backend revocation(s) remain pending")
        return cleaned


class PostgresSecretMetadataRepository:
    """Persist references and lifecycle metadata only; never persist secret values."""

    def __init__(self, connection_factory: Any) -> None:
        self._connect = connection_factory

    async def list_versions(self, secret_ref: str) -> list[SecretVersion]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT secret_ref,version,status,created_at,backend_revoked_at
                   FROM platform_secret_versions WHERE secret_ref=%s
                   ORDER BY version""",
                (secret_ref,),
            )
            rows = await cursor.fetchall()
        return [SecretVersion(
            secret_ref=row["secret_ref"], version=row["version"],
            status=row["status"], created_at=row["created_at"],
            backend_revoked_at=row["backend_revoked_at"],
        ) for row in rows]

    async def create_version(self, secret_ref: str, version: int,
                             created_at: datetime) -> None:
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    """INSERT INTO platform_secret_versions
                       (secret_ref,version,status,created_at)
                       VALUES (%s,%s,'pending',%s)""",
                    (secret_ref, version, created_at),
                )

    async def activate_version(self, secret_ref: str, version: int) -> None:
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """UPDATE platform_secret_versions SET status='revoked',
                              revoked_at=now()
                       WHERE secret_ref=%s AND status='active'""",
                    (secret_ref,),
                )
                cursor = await connection.execute(
                    """UPDATE platform_secret_versions SET status='active'
                       WHERE secret_ref=%s AND version=%s AND status='pending'
                       RETURNING version""",
                    (secret_ref, version),
                )
                if await cursor.fetchone() is None:
                    raise RuntimeError("secret version activation failed")

    async def revoke_version(self, secret_ref: str, version: int,
                             revoked_at: datetime) -> None:
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """UPDATE platform_secret_versions SET status='revoked',
                          revoked_at=%s
                   WHERE secret_ref=%s AND version=%s AND status='pending'""",
                (revoked_at, secret_ref, version),
            )

    async def revoke_all(self, secret_ref: str, revoked_at: datetime) -> list[int]:
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    """UPDATE platform_secret_versions SET status='revoked',
                              revoked_at=COALESCE(revoked_at,%s)
                       WHERE secret_ref=%s AND status IN ('active','pending')""",
                    (revoked_at, secret_ref),
                )
                cursor = await connection.execute(
                    """SELECT version FROM platform_secret_versions
                       WHERE secret_ref=%s AND status='revoked'
                         AND backend_revoked_at IS NULL
                       ORDER BY version""",
                    (secret_ref,),
                )
                rows = await cursor.fetchall()
        return [int(row["version"]) for row in rows]

    async def list_pending_revocations(self, secret_ref: str | None = None) -> list[int]:
        if secret_ref is None:
            raise ValueError("secret_ref is required for scoped cleanup")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT version FROM platform_secret_versions
                   WHERE secret_ref=%s AND status IN ('pending','revoked')
                     AND backend_revoked_at IS NULL ORDER BY version""",
                (secret_ref,),
            )
            rows = await cursor.fetchall()
        return [int(row["version"]) for row in rows]

    async def mark_backend_revoked(
        self, secret_ref: str, version: int, revoked_at: datetime
    ) -> None:
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """UPDATE platform_secret_versions
                   SET status='revoked',revoked_at=COALESCE(revoked_at,%s),
                       backend_revoked_at=%s
                   WHERE secret_ref=%s AND version=%s AND status IN ('pending','revoked')""",
                (revoked_at,revoked_at,secret_ref,version),
            )
