"""Knowledge collection and index catalog domain contracts.

The catalog deliberately keeps tenant/project identity on every record.  It
provides a small repository port so API adapters can use PostgreSQL or a test
adapter without weakening the authorization boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol
from uuid import uuid4


def _now() -> datetime:
    return datetime.now(timezone.utc)


class IndexStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"


@dataclass(frozen=True)
class KnowledgeCollection:
    tenant_id: str
    name: str
    description: str = ""
    project_id: str | None = None
    collection_id: str = field(default_factory=lambda: str(uuid4()))
    created_by: str = ""
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.name.strip():
            raise ValueError("tenant_id and collection name are required")


@dataclass(frozen=True)
class KnowledgeIndex:
    tenant_id: str
    collection_id: str
    index_name: str
    embedding_model: str
    dimensions: int
    status: IndexStatus = IndexStatus.PENDING
    index_id: str = field(default_factory=lambda: str(uuid4()))
    schema_version: int = 1
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (
            self.tenant_id, self.collection_id, self.index_name, self.embedding_model,
        )):
            raise ValueError("index identity fields are required")
        if self.dimensions < 1 or self.schema_version < 1:
            raise ValueError("index dimensions and schema_version must be positive")


class KnowledgeCatalogRepository(Protocol):
    async def create_collection(self, collection: KnowledgeCollection) -> bool: ...
    async def get_collection(
        self, tenant_id: str, collection_id: str,
    ) -> KnowledgeCollection | None: ...
    async def list_collections(
        self, tenant_id: str, project_ids: frozenset[str], limit: int = 100,
    ) -> list[KnowledgeCollection]: ...
    async def attach_document(
        self, tenant_id: str, collection_id: str, document_id: str,
    ) -> bool: ...
    async def create_index(self, index: KnowledgeIndex) -> bool: ...


class InMemoryKnowledgeCatalog:
    """Deterministic test adapter; keys always include tenant identity."""

    def __init__(self) -> None:
        self.collections: dict[tuple[str, str], KnowledgeCollection] = {}
        self.members: set[tuple[str, str, str]] = set()
        self.indexes: dict[tuple[str, str], KnowledgeIndex] = {}

    async def create_collection(self, collection: KnowledgeCollection) -> bool:
        key = (collection.tenant_id, collection.collection_id)
        if key in self.collections:
            return False
        self.collections[key] = collection
        return True

    async def get_collection(
        self, tenant_id: str, collection_id: str,
    ) -> KnowledgeCollection | None:
        collection = self.collections.get((tenant_id, collection_id))
        return collection if collection and collection.deleted_at is None else None

    async def list_collections(
        self, tenant_id: str, project_ids: frozenset[str], limit: int = 100,
    ) -> list[KnowledgeCollection]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        items = [
            item for (tenant, _), item in self.collections.items()
            if tenant == tenant_id and item.deleted_at is None
            and (item.project_id is None or item.project_id in project_ids)
        ]
        return sorted(items, key=lambda item: item.created_at, reverse=True)[:limit]

    async def attach_document(
        self, tenant_id: str, collection_id: str, document_id: str,
    ) -> bool:
        if await self.get_collection(tenant_id, collection_id) is None:
            raise ValueError("collection not found")
        key = (tenant_id, collection_id, document_id)
        if key in self.members:
            return False
        self.members.add(key)
        return True

    async def create_index(self, index: KnowledgeIndex) -> bool:
        if await self.get_collection(index.tenant_id, index.collection_id) is None:
            raise ValueError("collection not found")
        key = (index.tenant_id, index.index_id)
        if key in self.indexes:
            return False
        self.indexes[key] = index
        return True
