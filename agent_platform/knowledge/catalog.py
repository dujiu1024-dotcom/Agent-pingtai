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
        if (
            isinstance(self.dimensions, bool)
            or not isinstance(self.dimensions, int)
            or self.dimensions < 1
            or isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version < 1
        ):
            raise ValueError("index dimensions and schema_version must be positive integers")


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



class PostgresKnowledgeCatalog:
    """PostgreSQL-backed collection/index catalog with tenant-first predicates."""

    def __init__(self, connection_factory) -> None:
        self._connect = connection_factory

    @staticmethod
    def _collection(row) -> KnowledgeCollection:
        return KnowledgeCollection(
            tenant_id=row["tenant_id"],
            collection_id=str(row["collection_id"]),
            name=row["name"],
            description=row["description"],
            project_id=row["project_id"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            deleted_at=row["deleted_at"],
        )

    async def create_collection(self, collection: KnowledgeCollection) -> bool:
        if not collection.created_by.strip():
            raise ValueError("created_by is required")
        conn = await self._connect()
        async with conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """INSERT INTO knowledge_collections
                    (tenant_id, collection_id, name, description, project_id, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, collection_id) DO NOTHING
                    RETURNING collection_id""",
                    (collection.tenant_id, collection.collection_id, collection.name,
                     collection.description, collection.project_id, collection.created_by),
                )
                return await cur.fetchone() is not None

    async def get_collection(
        self, tenant_id: str, collection_id: str,
    ) -> KnowledgeCollection | None:
        conn = await self._connect()
        async with conn:
            cur = await conn.execute(
                """SELECT tenant_id, collection_id, name, description, project_id,
                          created_by, created_at, updated_at, deleted_at
                   FROM knowledge_collections
                   WHERE tenant_id = %s AND collection_id = %s AND deleted_at IS NULL""",
                (tenant_id, collection_id),
            )
            row = await cur.fetchone()
        return self._collection(row) if row is not None else None

    async def list_collections(
        self, tenant_id: str, project_ids: frozenset[str], limit: int = 100,
    ) -> list[KnowledgeCollection]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        conn = await self._connect()
        async with conn:
            cur = await conn.execute(
                """SELECT tenant_id, collection_id, name, description, project_id,
                          created_by, created_at, updated_at, deleted_at
                   FROM knowledge_collections
                   WHERE tenant_id = %s AND deleted_at IS NULL
                     AND (project_id IS NULL OR project_id = ANY(%s))
                   ORDER BY created_at DESC, collection_id
                   LIMIT %s""",
                (tenant_id, sorted(project_ids), limit),
            )
            rows = await cur.fetchall()
        return [self._collection(row) for row in rows]

    async def attach_document(
        self, tenant_id: str, collection_id: str, document_id: str,
    ) -> bool:
        """Attach only a live document compatible with the collection scope.

        Shared collections accept tenant-scoped documents. Project collections
        accept documents from that exact project. Private documents are never
        attached to a collection.
        """
        conn = await self._connect()
        async with conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """INSERT INTO knowledge_collection_documents
                       (tenant_id, collection_id, document_id)
                       SELECT c.tenant_id, c.collection_id, d.document_id
                       FROM knowledge_collections c
                       JOIN knowledge_documents d
                         ON d.tenant_id = c.tenant_id
                        AND d.document_id = %s
                        AND d.deleted_at IS NULL
                        AND d.version = (
                            SELECT max(latest.version)
                            FROM knowledge_documents latest
                            WHERE latest.tenant_id = d.tenant_id
                              AND latest.document_id = d.document_id
                              AND latest.deleted_at IS NULL
                        )
                       WHERE c.tenant_id = %s
                         AND c.collection_id = %s
                         AND c.deleted_at IS NULL
                         AND (
                             (c.project_id IS NULL AND d.scope = 'tenant')
                             OR
                             (c.project_id IS NOT NULL
                              AND d.scope = 'project'
                              AND d.project_id = c.project_id)
                         )
                       ON CONFLICT (tenant_id, collection_id, document_id) DO NOTHING
                       RETURNING document_id""",
                    (document_id, tenant_id, collection_id),
                )
                row = await cur.fetchone()
                if row is not None:
                    return True
                # Distinguish idempotent membership from an invalid or
                # inaccessible document without leaking cross-tenant records.
                cur = await conn.execute(
                    """SELECT 1
                       FROM knowledge_collection_documents m
                       JOIN knowledge_collections c
                         ON c.tenant_id = m.tenant_id
                        AND c.collection_id = m.collection_id
                        AND c.deleted_at IS NULL
                       JOIN knowledge_documents d
                         ON d.tenant_id = m.tenant_id
                        AND d.document_id = m.document_id
                        AND d.deleted_at IS NULL
                       WHERE m.tenant_id = %s
                         AND m.collection_id = %s
                         AND m.document_id = %s""",
                    (tenant_id, collection_id, document_id),
                )
                if await cur.fetchone() is not None:
                    return False
                raise ValueError("collection or compatible document not found")

    async def create_index(self, index: KnowledgeIndex) -> bool:
        conn = await self._connect()
        async with conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """INSERT INTO knowledge_indexes
                       (tenant_id, index_id, collection_id, index_name,
                        embedding_model, dimensions, schema_version, status)
                       SELECT c.tenant_id, %s, c.collection_id, %s, %s, %s, %s, %s
                       FROM knowledge_collections c
                       WHERE c.tenant_id = %s AND c.collection_id = %s
                         AND c.deleted_at IS NULL
                       ON CONFLICT (tenant_id, collection_id, index_name) DO NOTHING
                       RETURNING index_id""",
                    (index.index_id, index.index_name, index.embedding_model,
                     index.dimensions, index.schema_version, index.status.value,
                     index.tenant_id, index.collection_id),
                )
                created = await cur.fetchone()
                if created is not None:
                    return True
                cur = await conn.execute(
                    """SELECT 1 FROM knowledge_collections
                       WHERE tenant_id = %s AND collection_id = %s AND deleted_at IS NULL""",
                    (index.tenant_id, index.collection_id),
                )
                if await cur.fetchone() is None:
                    raise ValueError("collection not found")
                return False

    async def list_indexes(
        self, tenant_id: str, collection_id: str,
    ) -> list[KnowledgeIndex]:
        conn = await self._connect()
        async with conn:
            cur = await conn.execute(
                """SELECT i.tenant_id, i.collection_id, i.index_id,
                          i.index_name, i.embedding_model, i.dimensions,
                          i.schema_version, i.status, i.created_at
                   FROM knowledge_indexes i
                   JOIN knowledge_collections c
                     ON c.tenant_id = i.tenant_id
                    AND c.collection_id = i.collection_id
                    AND c.deleted_at IS NULL
                   WHERE i.tenant_id = %s AND i.collection_id = %s
                   ORDER BY i.created_at, i.index_id""",
                (tenant_id, collection_id),
            )
            rows = await cur.fetchall()
        return [KnowledgeIndex(
            tenant_id=row["tenant_id"],
            collection_id=str(row["collection_id"]),
            index_id=str(row["index_id"]),
            index_name=row["index_name"],
            embedding_model=row["embedding_model"],
            dimensions=row["dimensions"],
            schema_version=row["schema_version"],
            status=IndexStatus(row["status"]),
            created_at=row["created_at"],
        ) for row in rows]
