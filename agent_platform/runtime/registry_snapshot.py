"""Immutable registry-version snapshot pinned to a Run before execution."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from agent_platform.control_plane.registry import RegistryNotFound, RegistryService


@dataclass(frozen=True)
class RunRegistrySnapshot:
    tenant_id: str
    run_id: str
    entries: tuple[Mapping[str, Any], ...]
    digest: str


class RunSnapshotStore(Protocol):
    async def save_once(self, snapshot: RunRegistrySnapshot) -> RunRegistrySnapshot: ...
    async def get(self, tenant_id: str, run_id: str) -> RunRegistrySnapshot | None: ...


class InMemoryRunSnapshotStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], RunRegistrySnapshot] = {}

    async def save_once(self, snapshot: RunRegistrySnapshot) -> RunRegistrySnapshot:
        key = (snapshot.tenant_id, snapshot.run_id)
        existing = self._rows.get(key)
        if existing is not None:
            if existing.digest != snapshot.digest:
                raise ValueError("Run registry snapshot is immutable")
            return copy.deepcopy(existing)
        self._rows[key] = copy.deepcopy(snapshot)
        return copy.deepcopy(snapshot)

    async def get(self, tenant_id: str, run_id: str) -> RunRegistrySnapshot | None:
        item = self._rows.get((tenant_id, run_id))
        return copy.deepcopy(item) if item else None


class RunRegistrySnapshotService:
    def __init__(self, *, registry: RegistryService, store: RunSnapshotStore) -> None:
        self.registry = registry
        self.store = store

    async def pin(
        self, *, tenant_id: str, run_id: str,
        references: Sequence[tuple[str, str]],
    ) -> RunRegistrySnapshot:
        entries: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for kind, item_id in references:
            key = (kind, item_id)
            if key in seen:
                continue
            seen.add(key)
            item = await self.registry.resolve_active(
                tenant_id=tenant_id, kind=kind, item_id=item_id,
            )
            entries.append({
                "kind": item.kind, "item_id": item.item_id,
                "version": item.version, "definition": copy.deepcopy(dict(item.definition)),
            })
        entries.sort(key=lambda entry: (entry["kind"], entry["item_id"]))
        canonical = json.dumps(
            {"tenant_id": tenant_id, "run_id": run_id, "entries": entries},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        snapshot = RunRegistrySnapshot(
            tenant_id=tenant_id, run_id=run_id, entries=tuple(entries),
            digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )
        return await self.store.save_once(snapshot)

    async def pin_versions(
        self, *, tenant_id: str, run_id: str,
        references: Sequence[tuple[str, str, int]],
    ) -> RunRegistrySnapshot:
        """Pin explicit published versions captured during task validation."""
        entries: list[dict[str, Any]] = []
        seen: dict[tuple[str, str], int] = {}
        for kind, item_id, version in references:
            key = (kind, item_id)
            previous = seen.get(key)
            if previous is not None:
                if previous != version:
                    raise ValueError("Run snapshot contains conflicting versions for one registry item")
                continue
            if version < 1:
                raise ValueError("registry version must be positive")
            seen[key] = version
            item = await self.registry.repository.get(
                tenant_id, kind, item_id, version,
            )
            if item is None or item.status != "published":
                raise RegistryNotFound(
                    f"published registry version not found: {kind}:{item_id}@{version}"
                )
            entries.append({
                "kind": item.kind, "item_id": item.item_id,
                "version": item.version,
                "definition": copy.deepcopy(dict(item.definition)),
            })
        entries.sort(key=lambda entry: (entry["kind"], entry["item_id"]))
        canonical = json.dumps(
            {"tenant_id": tenant_id, "run_id": run_id, "entries": entries},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        snapshot = RunRegistrySnapshot(
            tenant_id=tenant_id, run_id=run_id, entries=tuple(entries),
            digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )
        return await self.store.save_once(snapshot)

    async def get(self, *, tenant_id: str, run_id: str) -> RunRegistrySnapshot:
        snapshot = await self.store.get(tenant_id, run_id)
        if snapshot is None:
            raise RegistryNotFound("Run registry snapshot has not been pinned")
        return snapshot
