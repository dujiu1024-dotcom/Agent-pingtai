"""PostgreSQL repositories for extension state and publisher trust."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from agent_platform.extensions.manager import InstalledExtension
from agent_platform.extensions.manifest import ExtensionManifest
from agent_platform.extensions.oci_runtime import TrustedPublisherKey


def _manifest_dict(manifest: ExtensionManifest) -> dict[str, Any]:
    return {
        "extension_id": manifest.extension_id,
        "version": manifest.version,
        "platform_api": manifest.platform_api,
        "permissions": list(manifest.permissions),
        "dependencies": list(manifest.dependencies),
        "entrypoint": manifest.entrypoint,
        "dependency_versions": dict(manifest.dependency_versions),
    }


def _manifest_from(value: Any) -> ExtensionManifest:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise ValueError("stored extension manifest is invalid")
    return ExtensionManifest(
        extension_id=value["extension_id"],
        version=value["version"],
        platform_api=value["platform_api"],
        permissions=tuple(value["permissions"]),
        dependencies=tuple(value.get("dependencies", ())),
        entrypoint=value["entrypoint"],
        dependency_versions=dict(value.get("dependency_versions", {})),
    )


class PostgresExtensionStateStore:
    """Tenant-bound durable lifecycle state; artifact values must be immutable refs."""

    def __init__(self, *, connection_factory: Any, tenant_id: str) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        self._connect = connection_factory
        self.tenant_id = tenant_id

    @staticmethod
    def _item(row: Mapping[str, Any]) -> InstalledExtension:
        return InstalledExtension(
            manifest=_manifest_from(row["manifest"]),
            status=row["status"],
            artifact=row["artifact_ref"],
        )

    async def list(self) -> Mapping[str, InstalledExtension]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT manifest,status,artifact_ref
                   FROM platform_installed_extensions
                   WHERE tenant_id=%s ORDER BY extension_id""",
                (self.tenant_id,),
            )
            rows = await cursor.fetchall()
        return {item.manifest.extension_id: item for item in (self._item(row) for row in rows)}

    async def get(self, extension_id: str) -> InstalledExtension | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT manifest,status,artifact_ref
                   FROM platform_installed_extensions
                   WHERE tenant_id=%s AND extension_id=%s""",
                (self.tenant_id, extension_id),
            )
            row = await cursor.fetchone()
        return self._item(row) if row is not None else None

    async def put(self, item: InstalledExtension) -> None:
        if not isinstance(item.artifact, str) or not item.artifact.strip() or len(item.artifact) > 512:
            raise ValueError("production extension state requires an immutable artifact reference")
        manifest = json.dumps(_manifest_dict(item.manifest), sort_keys=True, separators=(",", ":"))
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    """INSERT INTO platform_installed_extensions
                       (tenant_id,extension_id,manifest,status,artifact_ref,updated_at)
                       VALUES (%s,%s,%s::jsonb,%s,%s,now())
                       ON CONFLICT (tenant_id,extension_id) DO UPDATE SET
                         manifest=EXCLUDED.manifest,status=EXCLUDED.status,
                         artifact_ref=EXCLUDED.artifact_ref,updated_at=now()""",
                    (self.tenant_id, item.manifest.extension_id, manifest,
                     item.status, item.artifact),
                )

    async def remove(self, extension_id: str) -> None:
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """DELETE FROM platform_installed_extensions
                   WHERE tenant_id=%s AND extension_id=%s""",
                (self.tenant_id, extension_id),
            )


class PostgresTrustedPublisherKeyRepository:
    """Tenant- and extension-scoped public-key registry with atomic rotation."""

    def __init__(self, connection_factory: Any) -> None:
        self._connect = connection_factory

    @staticmethod
    def _to_key(row: Mapping[str, Any]) -> TrustedPublisherKey:
        not_before = row["not_before"]
        expires_at = row["expires_at"]
        return TrustedPublisherKey(
            publisher_id=row["publisher_id"],
            key_id=row["key_id"],
            public_key_pem=bytes(row["public_key_pem"]),
            extension_ids=frozenset({row["extension_id"]}),
            tenant_ids=frozenset({row["tenant_id"]}),
            not_before=not_before.timestamp(),
            expires_at=expires_at.timestamp() if expires_at is not None else None,
            revoked=row["revoked_at"] is not None,
        )

    async def _insert(self, connection: Any, *, tenant_id: str, extension_id: str,
                      publisher_id: str, key_id: str, public_key_pem: bytes,
                      not_before: float, expires_at: float | None,
                      created_by: str) -> TrustedPublisherKey:
        start = datetime.fromtimestamp(not_before, timezone.utc)
        expiry = datetime.fromtimestamp(expires_at, timezone.utc) if expires_at is not None else None
        cursor = await connection.execute(
            """INSERT INTO platform_trusted_publisher_keys
               (tenant_id,extension_id,publisher_id,key_id,public_key_pem,
                not_before,expires_at,created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (tenant_id,extension_id,key_id) DO NOTHING
               RETURNING tenant_id,extension_id,publisher_id,key_id,public_key_pem,
                         not_before,expires_at,revoked_at""",
            (tenant_id, extension_id, publisher_id, key_id, public_key_pem,
             start, expiry, created_by),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError("publisher key_id already exists for this tenant and extension")
        return self._to_key(row)

    async def register(self, *, tenant_id: str, extension_id: str, publisher_id: str,
                       key_id: str, public_key_pem: bytes, not_before: float,
                       expires_at: float | None, created_by: str) -> TrustedPublisherKey:
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                return await self._insert(
                    connection, tenant_id=tenant_id, extension_id=extension_id,
                    publisher_id=publisher_id, key_id=key_id, public_key_pem=public_key_pem,
                    not_before=not_before, expires_at=expires_at, created_by=created_by,
                )

    async def rotate(self, *, tenant_id: str, extension_id: str, publisher_id: str,
                     key_id: str, public_key_pem: bytes, not_before: float,
                     expires_at: float | None, created_by: str,
                     revoke_key_ids: tuple[str, ...]) -> TrustedPublisherKey:
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                key = await self._insert(
                    connection, tenant_id=tenant_id, extension_id=extension_id,
                    publisher_id=publisher_id, key_id=key_id, public_key_pem=public_key_pem,
                    not_before=not_before, expires_at=expires_at, created_by=created_by,
                )
                if revoke_key_ids:
                    cursor = await connection.execute(
                        """UPDATE platform_trusted_publisher_keys
                           SET revoked_at=COALESCE(revoked_at,now()), revoked_by=%s
                           WHERE tenant_id=%s AND extension_id=%s
                             AND key_id=ANY(%s)""",
                        (created_by, tenant_id, extension_id, list(revoke_key_ids)),
                    )
                    if cursor.rowcount != len(revoke_key_ids):
                        raise ValueError("one or more publisher keys to revoke were not found")
                return key

    async def revoke(self, *, tenant_id: str, extension_id: str, key_id: str,
                     revoked_by: str) -> bool:
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """UPDATE platform_trusted_publisher_keys
                       SET revoked_at=COALESCE(revoked_at,now()), revoked_by=%s
                       WHERE tenant_id=%s AND extension_id=%s AND key_id=%s
                       RETURNING key_id""",
                    (revoked_by, tenant_id, extension_id, key_id),
                )
                return await cursor.fetchone() is not None

    async def resolve(self, tenant_id: str, extension_id: str,
                      key_id: str) -> TrustedPublisherKey | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT tenant_id,extension_id,publisher_id,key_id,public_key_pem,
                          not_before,expires_at,revoked_at
                   FROM platform_trusted_publisher_keys
                   WHERE tenant_id=%s AND extension_id=%s AND key_id=%s""",
                (tenant_id, extension_id, key_id),
            )
            row = await cursor.fetchone()
        return self._to_key(row) if row is not None else None
