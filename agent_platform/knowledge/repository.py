"""Tenant- and ACL-scoped knowledge repository ports plus in-memory adapter."""
from __future__ import annotations

from typing import Protocol, Sequence

from agent_platform.knowledge.contracts import (
    Document,
    DocumentChunk,
    KnowledgeCollection,
    KnowledgeIndex,
    KnowledgePrincipal,
    RetrievalHit,
)


class KnowledgeRepository(Protocol):
    async def create_collection(self, collection: KnowledgeCollection) -> KnowledgeCollection: ...
    async def get_collection(self, tenant_id: str, collection_id: str) -> KnowledgeCollection | None: ...
    async def list_collections(self, principal: KnowledgePrincipal, limit: int = 100) -> list[KnowledgeCollection]: ...
    async def update_collection(self, tenant_id: str, collection_id: str, *, name: str, description: str, metadata: dict) -> KnowledgeCollection | None: ...
    async def delete_collection(self, tenant_id: str, collection_id: str) -> bool: ...
    async def create_index(self, index: KnowledgeIndex) -> KnowledgeIndex: ...
    async def list_indexes(self, tenant_id: str, collection_id: str) -> list[KnowledgeIndex]: ...
    async def save_document(
        self, document: Document, chunks: Sequence[DocumentChunk]
    ) -> bool: ...

    async def save_embeddings(
        self, *, tenant_id: str, chunks: Sequence[DocumentChunk],
        vectors: Sequence[Sequence[float]],
    ) -> None: ...

    async def get_document(
        self, tenant_id: str, document_id: str, version: int | None = None
    ) -> Document | None: ...

    async def list_documents(
        self, principal: KnowledgePrincipal, limit: int = 100
    ) -> list[Document]: ...

    async def search(
        self,
        *,
        principal: KnowledgePrincipal,
        query: str,
        query_vector: Sequence[float] | None,
        limit: int,
        strategy: str = "hybrid",
    ) -> list[RetrievalHit]: ...

    async def get_connector_cursor(self, tenant_id: str, connector_id: str) -> str | None: ...

    async def save_connector_cursor(
        self, tenant_id: str, connector_id: str, cursor: str
    ) -> None: ...

    async def grant_document_access(self, tenant_id: str, document_id: str, principal_type: str, principal_id: str, permission: str, granted_by: str) -> None: ...

    async def revoke_document_access(self, tenant_id: str, document_id: str, principal_type: str, principal_id: str, permission: str) -> bool: ...
    async def grant_chunk_access(self, tenant_id: str, document_id: str, chunk_id: str, principal_type: str, principal_id: str, permission: str, granted_by: str) -> None: ...
    async def revoke_chunk_access(self, tenant_id: str, chunk_id: str, principal_type: str, principal_id: str, permission: str) -> bool: ...

    async def replace_document_access(
        self, tenant_id: str, document_id: str,
        grants: Sequence[tuple[str, str, str]], granted_by: str,
    ) -> None: ...

    async def list_chunks(self, tenant_id: str, document_id: str, version: int) -> list[DocumentChunk]: ...
    async def list_visible_chunks(self, principal: KnowledgePrincipal, document_id: str, version: int) -> list[DocumentChunk]: ...
    async def update_chunk(self, tenant_id: str, document_id: str, version: int, chunk_id: str, text: str) -> DocumentChunk | None: ...

    async def delete_document(self, tenant_id: str, document_id: str) -> bool: ...


class InMemoryKnowledgeRepository:
    """Test adapter; production uses PostgreSQL ACL predicates before returning chunk text."""

    def __init__(self) -> None:
        self.collections: dict[tuple[str, str], KnowledgeCollection] = {}
        self.indexes: dict[tuple[str, str], KnowledgeIndex] = {}
        self.documents: dict[tuple[str, str, int], Document] = {}
        self.chunks: dict[tuple[str, str, int], list[DocumentChunk]] = {}
        self.connector_cursors: dict[tuple[str, str], str] = {}
        self.document_acl: dict[tuple[str, str], set[tuple[str, str, str]]] = {}
        self.document_acl_enforced: set[tuple[str, str]] = set()
        self.chunk_acl: dict[tuple[str, str], set[tuple[str, str, str]]] = {}
        self.chunk_acl_enforced: set[tuple[str, str]] = set()
        self.cache_invalidations: list[dict[str, str]] = []
        self.embeddings: dict[tuple[str, str], tuple[float, ...]] = {}

    async def create_collection(self, collection: KnowledgeCollection) -> KnowledgeCollection:
        key = (collection.tenant_id, collection.collection_id)
        if key in self.collections:
            raise ValueError("collection already exists")
        if any(
            item.tenant_id == collection.tenant_id
            and item.project_id == collection.project_id
            and item.name.casefold() == collection.name.casefold()
            and item.deleted_at is None
            for item in self.collections.values()
        ):
            raise ValueError("collection name already exists in this scope")
        self.collections[key] = collection
        default_index = KnowledgeIndex(
            tenant_id=collection.tenant_id, collection_id=collection.collection_id,
        )
        self.indexes[(default_index.tenant_id, default_index.index_id)] = default_index
        return collection

    async def get_collection(self, tenant_id: str, collection_id: str) -> KnowledgeCollection | None:
        item = self.collections.get((tenant_id, collection_id))
        return item if item is not None and item.deleted_at is None else None

    async def list_collections(self, principal: KnowledgePrincipal, limit: int = 100) -> list[KnowledgeCollection]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        visible = []
        for item in self.collections.values():
            if item.tenant_id != principal.tenant_id or item.deleted_at is not None:
                continue
            if item.project_id is not None and item.project_id not in principal.project_ids and item.project_id not in principal.manage_project_ids:
                continue
            visible.append(item)
        return sorted(visible, key=lambda item: item.created_at, reverse=True)[:limit]

    async def update_collection(
        self, tenant_id: str, collection_id: str, *, name: str,
        description: str, metadata: dict,
    ) -> KnowledgeCollection | None:
        from dataclasses import replace
        from datetime import datetime, timezone

        current = await self.get_collection(tenant_id, collection_id)
        if current is None:
            return None
        if any(
            item.tenant_id == tenant_id and item.collection_id != collection_id
            and item.project_id == current.project_id
            and item.name.casefold() == name.casefold() and item.deleted_at is None
            for item in self.collections.values()
        ):
            raise ValueError("collection name already exists in this scope")
        updated = replace(
            current, name=name, description=description, metadata=dict(metadata),
            updated_at=datetime.now(timezone.utc),
        )
        self.collections[(tenant_id, collection_id)] = updated
        return updated

    async def delete_collection(self, tenant_id: str, collection_id: str) -> bool:
        from dataclasses import replace
        from datetime import datetime, timezone

        current = await self.get_collection(tenant_id, collection_id)
        if current is None:
            return False
        if any(
            document.tenant_id == tenant_id and document.collection_id == collection_id
            and document.deleted_at is None
            for document in self.documents.values()
        ):
            raise ValueError("collection contains active documents")
        self.collections[(tenant_id, collection_id)] = replace(
            current, deleted_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        return True

    async def create_index(self, index: KnowledgeIndex) -> KnowledgeIndex:
        if await self.get_collection(index.tenant_id, index.collection_id) is None:
            raise ValueError("collection not found")
        key = (index.tenant_id, index.index_id)
        if key in self.indexes:
            raise ValueError("index already exists")
        self.indexes[key] = index
        return index

    async def list_indexes(self, tenant_id: str, collection_id: str) -> list[KnowledgeIndex]:
        if await self.get_collection(tenant_id, collection_id) is None:
            raise ValueError("collection not found")
        return sorted(
            (item for item in self.indexes.values()
             if item.tenant_id == tenant_id and item.collection_id == collection_id),
            key=lambda item: item.created_at,
        )

    async def save_document(
        self, document: Document, chunks: Sequence[DocumentChunk]
    ) -> bool:
        if document.collection_id is not None and await self.get_collection(
            document.tenant_id, document.collection_id
        ) is None:
            raise ValueError("collection not found in tenant")
        key = (document.tenant_id, document.document_id, document.version)
        if key in self.documents:
            return False
        self.documents[key] = document
        self.chunks[key] = list(chunks)
        return True

    async def save_embeddings(
        self, *, tenant_id: str, chunks: Sequence[DocumentChunk],
        vectors: Sequence[Sequence[float]],
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("embedding count must match chunks")
        for chunk, vector in zip(chunks, vectors):
            if chunk.tenant_id != tenant_id:
                raise ValueError("embedding tenant must match chunk tenant")
            self.embeddings[(tenant_id, chunk.chunk_id)] = tuple(float(value) for value in vector)

    async def get_document(
        self, tenant_id: str, document_id: str, version: int | None = None
    ) -> Document | None:
        docs = [
            doc
            for (tenant, identifier, current_version), doc in self.documents.items()
            if tenant == tenant_id
            and identifier == document_id
            and (version is None or current_version == version)
            and doc.deleted_at is None
        ]
        return max(docs, key=lambda doc: doc.version) if docs else None

    async def list_documents(
        self, principal: KnowledgePrincipal, limit: int = 100
    ) -> list[Document]:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        latest: dict[tuple[str, str], Document] = {}
        for document in self.documents.values():
            if document.tenant_id != principal.tenant_id or document.deleted_at is not None:
                continue
            key = (document.tenant_id, document.document_id)
            current = latest.get(key)
            if current is None or document.version > current.version:
                latest[key] = document
        visible = []
        for document in latest.values():
            if (
                document.scope.value == "private"
                and document.owner_user_id != principal.user_id
                and (document.tenant_id, document.document_id) not in self.document_acl_enforced
            ):
                continue
            if document.scope.value == "project" and document.project_id not in principal.project_ids:
                continue
            if document.collection_id is not None and await self.get_collection(document.tenant_id, document.collection_id) is None:
                continue
            if document.collection_id is not None and await self.get_collection(document.tenant_id, document.collection_id) is None:
                continue
            grants = self.document_acl.get((document.tenant_id, document.document_id), set())
            acl_key = (document.tenant_id, document.document_id)
            owner_access = document.scope.value == "private" and document.owner_user_id == principal.user_id
            grant_access = any(
                permission in {"read", "manage"} and (
                    (kind == "user" and identifier == principal.user_id)
                    or (kind == "role" and identifier in principal.roles)
                    or (kind == "project" and identifier in principal.project_ids)
                )
                for kind, identifier, permission in grants
            )
            if acl_key in self.document_acl_enforced and not (owner_access or grant_access):
                continue
            visible.append(document)
        return sorted(visible, key=lambda doc: doc.created_at, reverse=True)[:limit]

    async def search(
        self,
        *,
        principal: KnowledgePrincipal,
        query: str,
        query_vector: Sequence[float] | None,
        limit: int,
        strategy: str = "hybrid",
    ) -> list[RetrievalHit]:
        if strategy not in {"hybrid", "lexical", "semantic"}:
            raise ValueError("unsupported retrieval strategy")
        if strategy == "semantic":
            return []
        terms = {term.casefold() for term in query.split() if term}
        hits: list[RetrievalHit] = []
        for key, document in self.documents.items():
            if document.tenant_id != principal.tenant_id or document.deleted_at is not None:
                continue
            if any(other.tenant_id == document.tenant_id and other.document_id == document.document_id and other.version > document.version and other.deleted_at is None for other in self.documents.values()):
                continue
            if (
                document.scope.value == "private"
                and document.owner_user_id != principal.user_id
                and (document.tenant_id, document.document_id) not in self.document_acl_enforced
            ):
                continue
            if document.scope.value == "project" and document.project_id not in principal.project_ids:
                continue
            grants = self.document_acl.get((document.tenant_id, document.document_id), set())
            acl_key = (document.tenant_id, document.document_id)
            owner_access = document.scope.value == "private" and document.owner_user_id == principal.user_id
            grant_access = any(
                permission in {"read", "manage"} and (
                    (kind == "user" and identifier == principal.user_id)
                    or (kind == "role" and identifier in principal.roles)
                    or (kind == "project" and identifier in principal.project_ids)
                )
                for kind, identifier, permission in grants
            )
            if acl_key in self.document_acl_enforced and not (owner_access or grant_access):
                continue
            for chunk in self.chunks.get(key, []):
                chunk_key = (document.tenant_id, chunk.chunk_id)
                chunk_grants = self.chunk_acl.get(chunk_key, set())
                chunk_allowed = any(
                    permission in {"read", "manage"} and (
                        (kind == "user" and identifier == principal.user_id)
                        or (kind == "role" and identifier in principal.roles)
                        or (kind == "project" and identifier in principal.project_ids)
                    )
                    for kind, identifier, permission in chunk_grants
                )
                if chunk_key in self.chunk_acl_enforced and not chunk_allowed:
                    continue
                body = chunk.text.casefold()
                score = sum(1 for term in terms if term in body) / max(1, len(terms))
                if score > 0:
                    hits.append(
                        RetrievalHit(
                            document_id=document.document_id,
                            document_version=document.version,
                            chunk_id=chunk.chunk_id,
                            text=chunk.text,
                            source_uri=document.source_uri,
                            title=document.title,
                            score=score,
                            page_number=chunk.page_number,
                            start_offset=chunk.start_offset,
                            end_offset=chunk.end_offset,
                            retrieval_trace={"strategy": "lexical-test-adapter"},
                        )
                    )
        return sorted(hits, key=lambda hit: hit.score, reverse=True)[:limit]

    async def get_connector_cursor(self, tenant_id: str, connector_id: str) -> str | None:
        return self.connector_cursors.get((tenant_id, connector_id))

    async def save_connector_cursor(
        self, tenant_id: str, connector_id: str, cursor: str
    ) -> None:
        self.connector_cursors[(tenant_id, connector_id)] = cursor

    async def grant_document_access(
        self, tenant_id: str, document_id: str, principal_type: str,
        principal_id: str, permission: str, granted_by: str,
    ) -> None:
        if await self.get_document(tenant_id, document_id) is None:
            raise ValueError("document not found")
        self.document_acl_enforced.add((tenant_id, document_id))
        self.document_acl.setdefault((tenant_id, document_id), set()).add(
            (principal_type, principal_id, permission)
        )
        self.cache_invalidations.append({"tenant_id": tenant_id, "resource_type": "document", "resource_id": document_id, "reason": "acl_changed"})

    async def revoke_document_access(
        self, tenant_id: str, document_id: str, principal_type: str,
        principal_id: str, permission: str,
    ) -> bool:
        grants = self.document_acl.get((tenant_id, document_id), set())
        key = (principal_type, principal_id, permission)
        if key not in grants:
            return False
        grants.remove(key)
        self.cache_invalidations.append({"tenant_id": tenant_id, "resource_type": "document", "resource_id": document_id, "reason": "acl_changed"})
        return True

    async def replace_document_access(
        self, tenant_id: str, document_id: str,
        grants: Sequence[tuple[str, str, str]], granted_by: str,
    ) -> None:
        if await self.get_document(tenant_id, document_id) is None:
            raise ValueError("document not found")
        normalized = set(grants)
        if any(
            kind not in {"user", "role", "project"}
            or permission not in {"read", "manage"}
            or not identifier.strip()
            for kind, identifier, permission in normalized
        ):
            raise ValueError("invalid connector ACL grant")
        key = (tenant_id, document_id)
        self.document_acl_enforced.add(key)
        self.document_acl[key] = normalized
        self.cache_invalidations.append({"tenant_id": tenant_id, "resource_type": "document", "resource_id": document_id, "reason": "acl_changed"})

    async def list_chunks(self, tenant_id: str, document_id: str, version: int) -> list[DocumentChunk]:
        return list(self.chunks.get((tenant_id, document_id, version), []))

    async def list_visible_chunks(
        self, principal: KnowledgePrincipal, document_id: str, version: int,
    ) -> list[DocumentChunk]:
        visible = []
        for chunk in self.chunks.get((principal.tenant_id, document_id, version), []):
            key = (principal.tenant_id, chunk.chunk_id)
            if key not in self.chunk_acl_enforced:
                visible.append(chunk)
                continue
            grants = self.chunk_acl.get(key, set())
            if any(
                permission in {"read", "manage"} and (
                    (kind == "user" and identifier == principal.user_id)
                    or (kind == "role" and identifier in principal.roles)
                    or (kind == "project" and identifier in principal.project_ids)
                )
                for kind, identifier, permission in grants
            ):
                visible.append(chunk)
        return visible

    async def grant_chunk_access(
        self, tenant_id: str, document_id: str, chunk_id: str,
        principal_type: str, principal_id: str, permission: str, granted_by: str,
    ) -> None:
        if principal_type not in {"user", "role", "project"} or permission not in {"read", "manage"}:
            raise ValueError("invalid chunk ACL")
        if not principal_id.strip() or not granted_by.strip():
            raise ValueError("chunk ACL principal and grantor are required")
        found = any(
            key[0] == tenant_id and key[1] == document_id
            and any(chunk.chunk_id == chunk_id for chunk in chunks)
            for key, chunks in self.chunks.items()
        )
        if not found:
            raise ValueError("chunk not found")
        key = (tenant_id, chunk_id)
        self.chunk_acl_enforced.add(key)
        self.chunk_acl.setdefault(key, set()).add((principal_type, principal_id, permission))
        self.cache_invalidations.append({"tenant_id": tenant_id, "resource_type": "chunk", "resource_id": chunk_id, "reason": "acl_changed"})

    async def revoke_chunk_access(
        self, tenant_id: str, chunk_id: str, principal_type: str,
        principal_id: str, permission: str,
    ) -> bool:
        key = (tenant_id, chunk_id)
        grants = self.chunk_acl.get(key, set())
        grant = (principal_type, principal_id, permission)
        if grant not in grants:
            return False
        grants.remove(grant)
        self.cache_invalidations.append({"tenant_id": tenant_id, "resource_type": "chunk", "resource_id": chunk_id, "reason": "acl_changed"})
        return True

    async def update_chunk(self, tenant_id: str, document_id: str, version: int, chunk_id: str, text: str) -> DocumentChunk | None:
        from dataclasses import replace

        from agent_platform.knowledge.chunking import sha256_text
        if not isinstance(text, str) or not text.strip() or len(text) > 20000:
            raise ValueError("chunk text must contain 1-20000 characters")
        items = self.chunks.get((tenant_id, document_id, version), [])
        for index, chunk in enumerate(items):
            if chunk.chunk_id == chunk_id:
                updated = replace(chunk, text=text, content_hash=sha256_text(text), end_offset=chunk.start_offset + len(text), metadata={**chunk.metadata, "embedding_status": "stale"})
                items[index] = updated
                # The old vector no longer represents the edited chunk. Remove it
                # immediately so test/in-memory retrieval cannot observe stale data.
                self.embeddings.pop((tenant_id, chunk_id), None)
                self.cache_invalidations.append({
                    "tenant_id": tenant_id, "resource_type": "chunk",
                    "resource_id": chunk_id, "reason": "updated",
                })
                return updated
        return None

    async def delete_document(self, tenant_id: str, document_id: str) -> bool:
        from dataclasses import replace
        from datetime import datetime, timezone

        found = False
        chunk_ids: set[str] = set()
        for key, document in list(self.documents.items()):
            if key[0] == tenant_id and key[1] == document_id and document.deleted_at is None:
                self.documents[key] = replace(
                    document, deleted_at=datetime.now(timezone.utc)
                )
                chunk_ids.update(chunk.chunk_id for chunk in self.chunks.pop(key, []))
                found = True
        if found:
            self.document_acl.pop((tenant_id, document_id), None)
            self.document_acl_enforced.discard((tenant_id, document_id))
            for chunk_id in chunk_ids:
                acl_key = (tenant_id, chunk_id)
                self.chunk_acl.pop(acl_key, None)
                self.chunk_acl_enforced.discard(acl_key)
                self.embeddings.pop(acl_key, None)
                self.cache_invalidations.append({"tenant_id": tenant_id, "resource_type": "chunk", "resource_id": chunk_id, "reason": "deleted"})
            self.cache_invalidations.append({"tenant_id": tenant_id, "resource_type": "document", "resource_id": document_id, "reason": "deleted"})
        return found
