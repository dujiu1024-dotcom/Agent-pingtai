"""Tenant-scoped, versioned registry for Agent, workflow, tool and Skill definitions.

The registry stores declarative metadata only. Secrets are references to a host
secret manager; executable tool handlers and runtime objects are never loaded
from user-authored definitions.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol


class RegistryError(ValueError):
    """Invalid registry operation or definition."""


class RegistryNotFound(RegistryError):
    """Requested registry item does not exist."""


class RegistryConflict(RegistryError):
    """Optimistic concurrency or immutable-version conflict."""


_KINDS = frozenset({
    "agent", "workflow", "tool", "skill",
    "model_provider", "model_template", "routing_policy",
})
_STATUSES = frozenset({"draft", "published", "disabled", "deprecated"})
_SECRET_KEYS = frozenset({
    "api_key", "api_secret", "access_token", "refresh_token", "password",
    "secret", "secret_key", "token", "credential", "credentials",
    "client_secret", "private_key", "authorization",
})
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _validate_definition(value: Any, path: str = "definition") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            raw_name = str(key).strip()
            name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", raw_name).lower().replace("-", "_")
            secret_suffix = name.endswith(("_token", "_secret", "_password", "_private_key"))
            if name in _SECRET_KEYS or secret_suffix:
                raise RegistryError(f"secret material is forbidden at {path}.{key}; use a secret reference")
            _validate_definition(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_definition(child, f"{path}[{index}]")
    elif isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        raise RegistryError(f"non-finite number is forbidden at {path}")


@dataclass(frozen=True)
class RegistryVersion:
    tenant_id: str
    kind: str
    item_id: str
    version: int
    definition: Mapping[str, Any]
    status: str = "draft"
    created_by: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.created_by.strip() or not _ID.fullmatch(self.item_id):
            raise RegistryError("tenant_id, actor_id and valid item_id are required")
        if self.kind not in _KINDS or self.status not in _STATUSES or self.version < 1:
            raise RegistryError("invalid kind, status or version")
        _validate_definition(self.definition)


class RegistryRepository(Protocol):
    async def get(self, tenant_id: str, kind: str, item_id: str, version: int) -> RegistryVersion | None: ...
    async def list_versions(self, tenant_id: str, kind: str, item_id: str) -> tuple[RegistryVersion, ...]: ...
    async def list_items(self, tenant_id: str, kind: str, *, limit: int = 100) -> tuple[RegistryVersion, ...]: ...
    async def put(self, item: RegistryVersion, *, expected_latest: int | None) -> None: ...
    async def set_active(self, tenant_id: str, kind: str, item_id: str, version: int) -> None: ...
    async def get_active(self, tenant_id: str, kind: str, item_id: str) -> RegistryVersion | None: ...
    async def clear_active(self, tenant_id: str, kind: str, item_id: str) -> None: ...


class InMemoryRegistryRepository:
    """Test adapter. Production must use a tenant-bound PostgreSQL repository."""
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str, int], RegistryVersion] = {}
        self._active: dict[tuple[str, str, str], int] = {}

    async def get(self, tenant_id: str, kind: str, item_id: str, version: int) -> RegistryVersion | None:
        item = self._items.get((tenant_id, kind, item_id, version))
        return copy.deepcopy(item) if item else None

    async def list_versions(self, tenant_id: str, kind: str, item_id: str) -> tuple[RegistryVersion, ...]:
        values = [v for k, v in self._items.items() if k[:3] == (tenant_id, kind, item_id)]
        return tuple(copy.deepcopy(sorted(values, key=lambda x: x.version)))

    async def list_items(self, tenant_id: str, kind: str, *, limit: int = 100) -> tuple[RegistryVersion, ...]:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        latest: dict[str, RegistryVersion] = {}
        for (row_tenant, row_kind, item_id, _), item in self._items.items():
            if row_tenant != tenant_id or row_kind != kind:
                continue
            current = latest.get(item_id)
            if current is None or item.version > current.version:
                latest[item_id] = item
        return tuple(copy.deepcopy(sorted(
            latest.values(), key=lambda item: (item.created_at, item.item_id), reverse=True,
        )[:limit]))

    async def put(self, item: RegistryVersion, *, expected_latest: int | None) -> None:
        key = (item.tenant_id, item.kind, item.item_id)
        versions = [v for k, v in self._items.items() if k[:3] == key]
        latest = max((v.version for v in versions), default=None)
        if latest != expected_latest:
            raise RegistryConflict(f"latest version changed: expected {expected_latest}, got {latest}")
        if (item.tenant_id, item.kind, item.item_id, item.version) in self._items:
            raise RegistryConflict("registry versions are immutable")
        self._items[(item.tenant_id, item.kind, item.item_id, item.version)] = copy.deepcopy(item)

    async def set_active(self, tenant_id: str, kind: str, item_id: str, version: int) -> None:
        item = await self.get(tenant_id, kind, item_id, version)
        if item is None:
            raise RegistryNotFound("registry version not found")
        if item.status != "published":
            raise RegistryError("only published versions can be activated")
        self._active[(tenant_id, kind, item_id)] = version

    async def get_active(self, tenant_id: str, kind: str, item_id: str) -> RegistryVersion | None:
        version = self._active.get((tenant_id, kind, item_id))
        return await self.get(tenant_id, kind, item_id, version) if version else None

    async def clear_active(self, tenant_id: str, kind: str, item_id: str) -> None:
        self._active.pop((tenant_id, kind, item_id), None)


class RegistryService:
    def __init__(self, repository: RegistryRepository) -> None:
        self.repository = repository

    async def create_draft(
        self, *, tenant_id: str, kind: str, item_id: str,
        definition: Mapping[str, Any], actor_id: str,
    ) -> RegistryVersion:
        versions = await self.repository.list_versions(tenant_id, kind, item_id)
        version = RegistryVersion(
            tenant_id=tenant_id, kind=kind, item_id=item_id,
            version=(versions[-1].version + 1 if versions else 1),
            definition=copy.deepcopy(dict(definition)), status="draft", created_by=actor_id,
        )
        await self.repository.put(version, expected_latest=(versions[-1].version if versions else None))
        return version

    async def publish(self, *, tenant_id: str, kind: str, item_id: str, version: int) -> RegistryVersion:
        draft = await self.repository.get(tenant_id, kind, item_id, version)
        if draft is None:
            raise RegistryNotFound("registry version not found")
        if draft.status != "draft":
            raise RegistryError("only draft versions can be published")
        published = RegistryVersion(
            tenant_id=draft.tenant_id, kind=draft.kind, item_id=draft.item_id,
            version=draft.version, definition=draft.definition, status="published",
            created_by=draft.created_by, created_at=draft.created_at,
            published_at=datetime.now(timezone.utc),
        )
        # Versions are immutable: publication is represented by a new version.
        versions = await self.repository.list_versions(tenant_id, kind, item_id)
        next_version = RegistryVersion(
            tenant_id=tenant_id, kind=kind, item_id=item_id,
            version=versions[-1].version + 1, definition=published.definition,
            status="published", created_by=draft.created_by,
            published_at=published.published_at,
        )
        await self.repository.put(next_version, expected_latest=versions[-1].version)
        await self.repository.set_active(tenant_id, kind, item_id, next_version.version)
        return next_version

    async def rollback(self, *, tenant_id: str, kind: str, item_id: str, target_version: int) -> RegistryVersion:
        target = await self.repository.get(tenant_id, kind, item_id, target_version)
        if target is None or target.status != "published":
            raise RegistryNotFound("published rollback target not found")
        versions = await self.repository.list_versions(tenant_id, kind, item_id)
        rollback_version = RegistryVersion(
            tenant_id=tenant_id, kind=kind, item_id=item_id,
            version=versions[-1].version + 1, definition=target.definition,
            status="published", created_by="rollback",
            published_at=datetime.now(timezone.utc),
        )
        await self.repository.put(rollback_version, expected_latest=versions[-1].version)
        await self.repository.set_active(tenant_id, kind, item_id, rollback_version.version)
        return rollback_version

    async def disable(self, *, tenant_id: str, kind: str, item_id: str, actor_id: str) -> RegistryVersion:
        active = await self.resolve_active(tenant_id=tenant_id, kind=kind, item_id=item_id)
        versions = await self.repository.list_versions(tenant_id, kind, item_id)
        disabled = RegistryVersion(
            tenant_id=tenant_id, kind=kind, item_id=item_id,
            version=versions[-1].version + 1, definition=active.definition,
            status="disabled", created_by=actor_id,
        )
        # Fail closed: remove the active pointer before writing the audit version.
        await self.repository.clear_active(tenant_id, kind, item_id)
        await self.repository.put(disabled, expected_latest=versions[-1].version)
        return disabled

    async def resolve_active(self, *, tenant_id: str, kind: str, item_id: str) -> RegistryVersion:
        item = await self.repository.get_active(tenant_id, kind, item_id)
        if item is None:
            raise RegistryNotFound("no active registry version")
        return item
