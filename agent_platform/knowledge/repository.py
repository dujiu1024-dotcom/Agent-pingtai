"""Tenant- and ACL-scoped knowledge repository ports plus in-memory adapter."""
from __future__ import annotations

from typing import Protocol, Sequence

from agent_platform.knowledge.contracts import (
    Document,
    DocumentChunk,
    KnowledgePrincipal,
    RetrievalHit,
)


class KnowledgeRepository(Protocol):
    async def save_document(
        self, document: Document, chunks: Sequence[DocumentChunk]
    ) -> bool: ...

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

    async def replace_document_access(
        self, tenant_id: str, document_id: str,
        grants: Sequence[tuple[str, str, str]], granted_by: str,
    ) -> None: ...

    async def delete_document(self, tenant_id: str, document_id: str) -> bool: ...


class InMemoryKnowledgeRepository:
    """Test adapter; production uses PostgreSQL ACL predicates before returning chunk text."""

    def __init__(self) -> None:
        self.documents: dict[tuple[str, str, int], Document] = {}
        self.chunks: dict[tuple[str, str, int], list[DocumentChunk]] = {}
        self.connector_cursors: dict[tuple[str, str], str] = {}
        self.document_acl: dict[tuple[str, str], set[tuple[str, str, str]]] = {}
        self.document_acl_enforced: set[tuple[str, str]] = set()

    async def save_document(
        self, document: Document, chunks: Sequence[DocumentChunk]
    ) -> bool:
        key = (document.tenant_id, document.document_id, document.version)
        if key in self.documents:
            return False
        self.documents[key] = document
        self.chunks[key] = list(chunks)
        return True

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

    async def revoke_document_access(
        self, tenant_id: str, document_id: str, principal_type: str,
        principal_id: str, permission: str,
    ) -> bool:
        grants = self.document_acl.get((tenant_id, document_id), set())
        key = (principal_type, principal_id, permission)
        if key not in grants:
            return False
        grants.remove(key)
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

    async def delete_document(self, tenant_id: str, document_id: str) -> bool:
        from dataclasses import replace
        from datetime import datetime, timezone

        found = False
        for key, document in list(self.documents.items()):
            if key[0] == tenant_id and key[1] == document_id and document.deleted_at is None:
                self.documents[key] = replace(
                    document, deleted_at=datetime.now(timezone.utc)
                )
                self.chunks.pop(key, None)
                found = True
        return found
