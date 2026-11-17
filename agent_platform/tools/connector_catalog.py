"""Manifest-driven MCP connector catalog with trusted, on-demand loading.

Registry metadata is declarative only: it never supplies commands, URLs, credentials,
or import paths. A host-owned factory decides how a validated connector is started.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol


class ConnectorCatalogError(RuntimeError):
    """Base error for connector catalog operations."""


class ConnectorManifestError(ConnectorCatalogError, ValueError):
    """A manifest is malformed or attempts to define executable configuration."""


_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
_AUTH_TYPES = frozenset({"none", "oauth", "api_key", "service_account"})


@dataclass(frozen=True)
class ConnectorOperation:
    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True)
class ConnectorManifest:
    connector_id: str
    name: str
    version: str
    description: str
    auth_type: str
    operations: tuple[ConnectorOperation, ...]
    source: str = "local"
    published_at: datetime | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, source: str = "local") -> "ConnectorManifest":
        if not isinstance(value, Mapping):
            raise ConnectorManifestError("manifest must be an object")
        allowed_manifest_fields = {
            "connector_id", "name", "version", "description", "auth_type", "operations",
        }
        if set(value) - allowed_manifest_fields:
            raise ConnectorManifestError("manifest contains unsupported or runtime configuration fields")
        connector_id = value.get("connector_id")
        name, version = value.get("name"), value.get("version")
        description, auth_type = value.get("description", ""), value.get("auth_type")
        if not isinstance(connector_id, str) or not _IDENTIFIER.fullmatch(connector_id):
            raise ConnectorManifestError("connector_id is invalid")
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ConnectorManifestError("name must contain 1 to 120 characters")
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise ConnectorManifestError("version must be semantic MAJOR.MINOR.PATCH")
        if not isinstance(description, str) or len(description) > 2000:
            raise ConnectorManifestError("description must be a string of at most 2000 characters")
        if not isinstance(auth_type, str) or auth_type not in _AUTH_TYPES:
            raise ConnectorManifestError("auth_type is unsupported")
        raw_operations = value.get("operations")
        if not isinstance(raw_operations, list) or not raw_operations:
            raise ConnectorManifestError("operations must be a non-empty list")
        operations: list[ConnectorOperation] = []
        seen: set[str] = set()
        for item in raw_operations:
            if not isinstance(item, Mapping):
                raise ConnectorManifestError("operation must be an object")
            if set(item) - {"name", "description", "input_schema"}:
                raise ConnectorManifestError("operation contains unsupported or executable configuration")
            op_name, op_desc, schema = item.get("name"), item.get("description", ""), item.get("input_schema")
            if not isinstance(op_name, str) or not _IDENTIFIER.fullmatch(op_name):
                raise ConnectorManifestError("operation name is invalid")
            if op_name in seen:
                raise ConnectorManifestError("operation names must be unique")
            if not isinstance(op_desc, str) or len(op_desc) > 1000:
                raise ConnectorManifestError("operation description is invalid")
            if not isinstance(schema, Mapping) or schema.get("type") != "object":
                raise ConnectorManifestError("operation input_schema must be a JSON object schema")
            if schema.get("additionalProperties") is not False:
                raise ConnectorManifestError("operation schema must set additionalProperties=false")
            try:
                import jsonschema
                jsonschema.Draft202012Validator.check_schema(dict(schema))
            except ImportError:
                # Structural validation remains available without the optional validator;
                # the Gateway performs full schema validation before tool dispatch.
                pass
            except Exception as exc:
                raise ConnectorManifestError("operation input_schema is invalid") from exc
            operations.append(ConnectorOperation(
                name=op_name, description=op_desc,
                input_schema=copy.deepcopy(dict(schema)),
            ))
            seen.add(op_name)
        return cls(
            connector_id=connector_id, name=name.strip(), version=version,
            description=description, auth_type=auth_type,
            operations=tuple(sorted(operations, key=lambda operation: operation.name)),
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id, "name": self.name,
            "version": self.version, "description": self.description,
            "auth_type": self.auth_type, "source": self.source,
            "operations": [
                {"name": item.name, "description": item.description,
                 "input_schema": copy.deepcopy(dict(item.input_schema))}
                for item in self.operations
            ],
        }


class ConnectorManifestRegistry(Protocol):
    async def upsert(self, tenant_id: str, manifest: ConnectorManifest) -> None: ...
    async def get(self, tenant_id: str, connector_id: str) -> ConnectorManifest | None: ...
    async def list(self, tenant_id: str) -> tuple[ConnectorManifest, ...]: ...


class ConnectorManifestSource(Protocol):
    async def list_manifests(self) -> list[Mapping[str, Any]]: ...


class InMemoryConnectorManifestRegistry:
    """Tenant-scoped registry adapter for tests and single-process deployments."""
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], ConnectorManifest] = {}

    async def upsert(self, tenant_id: str, manifest: ConnectorManifest) -> None:
        self._items[(tenant_id, manifest.connector_id)] = copy.deepcopy(manifest)

    async def get(self, tenant_id: str, connector_id: str) -> ConnectorManifest | None:
        item = self._items.get((tenant_id, connector_id))
        return copy.deepcopy(item) if item else None

    async def list(self, tenant_id: str) -> tuple[ConnectorManifest, ...]:
        return tuple(
            copy.deepcopy(item) for (owner, _), item in sorted(self._items.items())
            if owner == tenant_id
        )


class PostgresConnectorManifestRegistry:
    """Persist validated declarative manifests; never stores credentials or endpoints."""
    def __init__(self, connection_factory: Any) -> None:
        self._connect = connection_factory

    async def upsert(self, tenant_id: str, manifest: ConnectorManifest) -> None:
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL registry storage") from exc
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_connector_manifests
                   (tenant_id,connector_id,name,version,manifest,source,updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (tenant_id,connector_id) DO UPDATE SET
                     name=EXCLUDED.name,version=EXCLUDED.version,
                     manifest=EXCLUDED.manifest,source=EXCLUDED.source,
                     updated_at=EXCLUDED.updated_at""",
                (tenant_id, manifest.connector_id, manifest.name, manifest.version,
                 Jsonb(manifest.to_dict()), manifest.source, datetime.now(timezone.utc)),
            )

    async def get(self, tenant_id: str, connector_id: str) -> ConnectorManifest | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT manifest FROM platform_connector_manifests
                   WHERE tenant_id=%s AND connector_id=%s""",
                (tenant_id, connector_id),
            )
            row = await cursor.fetchone()
        return ConnectorManifest.from_mapping(row["manifest"], source=row["manifest"].get("source", "registry")) if row else None

    async def list(self, tenant_id: str) -> tuple[ConnectorManifest, ...]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT manifest FROM platform_connector_manifests
                   WHERE tenant_id=%s ORDER BY connector_id""",
                (tenant_id,),
            )
            rows = await cursor.fetchall()
        return tuple(
            ConnectorManifest.from_mapping(row["manifest"], source=row["manifest"].get("source", "registry"))
            for row in rows
        )


class MCPRegistryManifestSource:
    """Thin adapter over an official MCP Registry client supplied by the host.

    The client must expose async list_servers(); transport/authentication remain
    host-owned and are deliberately not read from registry manifest content.
    """
    def __init__(self, registry_client: Any) -> None:
        self._client = registry_client

    async def list_manifests(self) -> list[Mapping[str, Any]]:
        result = await self._client.list_servers()
        if not isinstance(result, (list, tuple)):
            raise ConnectorCatalogError("MCP Registry returned an invalid server list")
        manifests: list[Mapping[str, Any]] = []
        for item in result:
            candidate = item.get("server", item) if isinstance(item, Mapping) else None
            if not isinstance(candidate, Mapping):
                raise ConnectorManifestError("MCP Registry entry is not an object")
            # Registry metadata can be wrapped in the official {server: ...} envelope.
            # A host-side normalizer maps MCP package metadata to this platform contract.
            normalized = candidate.get("platform_manifest")
            if not isinstance(normalized, Mapping):
                continue
            manifests.append(normalized)
        return manifests


class ConnectorCatalog:
    """Tenant-scoped manifest registry and lazy connector lifecycle manager.

    The factory is trusted host code. A connector is instantiated only on load(),
    cached only after successful startup, and closed/evicted on unload().
    """
    def __init__(
        self, *, registry: ConnectorManifestRegistry, connector_factory: Any,
        health_checker: Any | None = None,
    ) -> None:
        self.registry = registry
        self.connector_factory = connector_factory
        self.health_checker = health_checker
        self._loaded: dict[tuple[str, str], Any] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _lock(self, key: tuple[str, str]) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def register(self, *, tenant_id: str, manifest: Mapping[str, Any], source: str = "local") -> ConnectorManifest:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        validated = ConnectorManifest.from_mapping(manifest, source=source)
        await self.registry.upsert(tenant_id, validated)
        return validated

    async def refresh(self, *, tenant_id: str, source: ConnectorManifestSource) -> tuple[ConnectorManifest, ...]:
        imported: list[ConnectorManifest] = []
        for item in await source.list_manifests():
            imported.append(await self.register(
                tenant_id=tenant_id, manifest=item, source="mcp-registry",
            ))
        return tuple(imported)

    async def list_manifests(self, *, tenant_id: str) -> tuple[ConnectorManifest, ...]:
        return await self.registry.list(tenant_id)

    async def get_manifest(self, *, tenant_id: str, connector_id: str) -> ConnectorManifest:
        item = await self.registry.get(tenant_id, connector_id)
        if item is None:
            raise KeyError("connector manifest not found")
        return item

    async def load(self, *, tenant_id: str, connector_id: str) -> Any:
        if not tenant_id.strip():
            raise ValueError("tenant_id is required")
        key = (tenant_id, connector_id)
        async with self._lock(key):
            loaded = self._loaded.get(key)
            if loaded is not None:
                return loaded
            manifest = await self.get_manifest(tenant_id=tenant_id, connector_id=connector_id)
            connector = self.connector_factory(manifest)
            if inspect.isawaitable(connector):
                connector = await connector
            start = getattr(connector, "start", None)
            try:
                if callable(start):
                    result = start()
                    if inspect.isawaitable(result):
                        await result
            except Exception:
                close = getattr(connector, "close", None) or getattr(connector, "stop", None)
                if callable(close):
                    try:
                        result = close()
                        if inspect.isawaitable(result):
                            await result
                    except Exception:
                        pass
                raise
            self._loaded[key] = connector
            return connector

    async def unload(self, *, tenant_id: str, connector_id: str) -> bool:
        key = (tenant_id, connector_id)
        async with self._lock(key):
            connector = self._loaded.get(key)
            if connector is None:
                return False
            close = getattr(connector, "close", None) or getattr(connector, "stop", None)
            if callable(close):
                result = close()
                if asyncio.iscoroutine(result):
                    await result
            self._loaded.pop(key, None)
            return True

    async def health_check(self, *, tenant_id: str, connector_id: str) -> Any:
        if self.health_checker is None:
            raise ConnectorCatalogError("connector health checker is not configured")
        manifest = await self.get_manifest(tenant_id=tenant_id, connector_id=connector_id)
        if (tenant_id, connector_id) not in self._loaded:
            raise ConnectorCatalogError("connector must be loaded before health check")
        return await self.health_checker.check(
            tenant_id=tenant_id, server_id=connector_id,
            expected_tools=tuple(operation.name for operation in manifest.operations),
        )

    async def loaded_ids(self, *, tenant_id: str) -> tuple[str, ...]:
        return tuple(sorted(
            connector_id for owner, connector_id in self._loaded if owner == tenant_id
        ))
