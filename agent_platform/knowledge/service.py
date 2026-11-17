"""Knowledge ingestion, connector sync and ACL-first hybrid retrieval."""
from __future__ import annotations

import asyncio
from typing import Protocol, Sequence

from agent_platform.knowledge.chunking import clean_document_text, chunk_document, sha256_text
from agent_platform.knowledge.connectors import KnowledgeConnector
from agent_platform.knowledge.contracts import (
    Document,
    IngestResult,
    KnowledgeCollection,
    KnowledgeIndex,
    KnowledgePrincipal,
    RetrievalHit,
)
from agent_platform.knowledge.ingestion import ParserRegistry
from agent_platform.knowledge.repository import KnowledgeRepository
from agent_platform.knowledge.retrieval import RetrievalFilters, RetrievalStrategy
from agent_platform.observability.instrumentation import safe_metric, safe_observation, safe_update


class Embedder(Protocol):
    model_id: str
    async def embed_query(self,text: str)->Sequence[float]: ...
    async def embed_documents(self,texts: Sequence[str])->Sequence[Sequence[float]]: ...
class Reranker(Protocol):
    async def rerank(self,query: str,hits: Sequence[RetrievalHit])->Sequence[RetrievalHit]: ...
class QueryRewriter(Protocol):
    async def rewrite(self,query: str)->str: ...
class KnowledgeService:
    def __init__(
        self, repository: KnowledgeRepository, *, parsers: ParserRegistry | None = None,
        embedder: Embedder | None = None, reranker: Reranker | None = None,
        query_rewriter: QueryRewriter | None = None,
        max_upload_bytes: int = 25 * 1024 * 1024, max_query_chars: int = 4096,
        observer=None, metrics=None,
    ) -> None:
        if (
            isinstance(max_upload_bytes, bool)
            or not isinstance(max_upload_bytes, int)
            or max_upload_bytes < 1024
        ):
            raise ValueError("max_upload_bytes must be an integer of at least 1024")
        if (
            isinstance(max_query_chars, bool)
            or not isinstance(max_query_chars, int)
            or not 1 <= max_query_chars <= 32768
        ):
            raise ValueError("max_query_chars must be an integer between 1 and 32768")
        self.repository=repository;self.parsers=parsers or ParserRegistry();self.embedder=embedder;self.reranker=reranker;self.query_rewriter=query_rewriter;self.max_upload_bytes=max_upload_bytes;self.max_query_chars=max_query_chars;self.observer=observer;self.metrics=metrics
        if embedder is not None and hasattr(repository,"embedding_model"):
            repository.embedding_model=getattr(embedder,"storage_key",embedder.model_id)
            if hasattr(repository,"embedding_model_version"):
                repository.embedding_model_version=getattr(embedder,"model_version","1")
            if hasattr(repository,"embedding_dimensions"):
                repository.embedding_dimensions=getattr(embedder,"dimensions",1536)
    async def create_collection(
        self, *, principal: KnowledgePrincipal, name: str,
        description: str = "", project_id: str | None = None,
        metadata: dict | None = None,
    ) -> KnowledgeCollection:
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("collection name must contain 1-200 characters")
        if project_id is not None and project_id not in principal.manage_project_ids:
            raise PermissionError("project knowledge.manage permission required")
        if project_id is None and "knowledge.manage" not in principal.permissions:
            raise PermissionError("knowledge.manage permission required")
        collection = KnowledgeCollection(
            tenant_id=principal.tenant_id, name=name.strip(),
            description=description, project_id=project_id,
            metadata=dict(metadata or {}),
        )
        return await self.repository.create_collection(collection)

    async def list_collections(
        self, *, principal: KnowledgePrincipal, limit: int = 100,
    ) -> list[KnowledgeCollection]:
        return await self.repository.list_collections(principal, limit=limit)

    @staticmethod
    def _can_manage_collection(collection: KnowledgeCollection, principal: KnowledgePrincipal) -> bool:
        if collection.project_id is not None:
            return (
                collection.project_id in principal.manage_project_ids
                or "knowledge.manage" in principal.permissions
            )
        return "knowledge.manage" in principal.permissions

    async def update_collection(
        self, *, principal: KnowledgePrincipal, collection_id: str,
        name: str | None = None, description: str | None = None,
        metadata: dict | None = None,
    ) -> KnowledgeCollection:
        current = await self.repository.get_collection(principal.tenant_id, collection_id)
        if current is None:
            raise ValueError("collection not found")
        if not self._can_manage_collection(current, principal):
            raise PermissionError("collection management is not authorized")
        if name is not None and not isinstance(name, str):
            raise ValueError("collection name must be a string")
        updated_name = current.name if name is None else name.strip()
        updated_description = current.description if description is None else description
        updated_metadata = current.metadata if metadata is None else metadata
        if not updated_name or len(updated_name) > 200:
            raise ValueError("collection name must contain 1-200 characters")
        if not isinstance(updated_description, str) or not isinstance(updated_metadata, dict):
            raise ValueError("description must be a string and metadata an object")
        updated = await self.repository.update_collection(
            principal.tenant_id, collection_id, name=updated_name,
            description=updated_description, metadata=updated_metadata,
        )
        if updated is None:
            raise ValueError("collection not found")
        return updated

    async def delete_collection(
        self, *, principal: KnowledgePrincipal, collection_id: str,
    ) -> bool:
        current = await self.repository.get_collection(principal.tenant_id, collection_id)
        if current is None:
            return False
        if not self._can_manage_collection(current, principal):
            raise PermissionError("collection management is not authorized")
        return await self.repository.delete_collection(principal.tenant_id, collection_id)

    async def list_collection_indexes(
        self, *, principal: KnowledgePrincipal, collection_id: str,
    ) -> list[KnowledgeIndex]:
        collection = await self.repository.get_collection(principal.tenant_id, collection_id)
        if collection is None:
            raise ValueError("collection not found")
        if collection.project_id is not None and collection.project_id not in principal.project_ids and collection.project_id not in principal.manage_project_ids:
            raise PermissionError("collection access denied")
        return await self.repository.list_indexes(principal.tenant_id, collection_id)

    async def ingest(
        self, *, document: Document, content: bytes, max_chars: int = 1200, overlap: int = 160,
    ) -> IngestResult:
        if not isinstance(content, bytes):
            raise TypeError("content must be bytes")
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 100:
            raise ValueError("max_chars must be an integer >= 100")
        if isinstance(overlap, bool) or not isinstance(overlap, int) or overlap < 0:
            raise ValueError("overlap must be a non-negative integer")
        if overlap >= max_chars:
            raise ValueError("overlap must be smaller than max_chars")
        if len(content) > self.max_upload_bytes:
            raise ValueError("document exceeds configured upload size limit")
        from dataclasses import replace

        extracted = await asyncio.to_thread(self.parsers.parse, content, document.media_type)
        text = clean_document_text(extracted)
        digest = sha256_text(text)
        if not text:
            raise ValueError("document contains no text after cleaning")
        if document.collection_id is not None:
            collection = await self.repository.get_collection(document.tenant_id, document.collection_id)
            if collection is None:
                raise ValueError("collection not found in tenant")
            if collection.project_id is not None and (
                collection.project_id != document.project_id or document.scope.value != "project"
            ):
                raise ValueError("project collections require project-scoped documents in the same project")

        existing = await self.repository.get_document(document.tenant_id, document.document_id)
        unchanged = False
        if existing is not None:
            unchanged = (
                existing.content_hash == digest
                and existing.title == document.title
                and existing.source_uri == document.source_uri
                and existing.scope is document.scope
                and existing.project_id == document.project_id
                and existing.owner_user_id == document.owner_user_id
                and existing.media_type == document.media_type
                and existing.metadata == document.metadata
                and existing.collection_id == document.collection_id
            )
            if unchanged:
                document = existing
            elif document.version <= existing.version:
                document = replace(document, version=existing.version + 1)
        document = replace(document, content_hash=digest)
        if unchanged:
            chunks = tuple(await self.repository.list_chunks(
                document.tenant_id, document.document_id, document.version,
            ))
        else:
            chunks = chunk_document(document, text, max_chars=max_chars, overlap=overlap)
            await self.repository.save_document(document, chunks)

        # Deterministic chunk IDs let retries repair missing or stale embeddings.
        if chunks and self.embedder is not None:
            save_embeddings = getattr(self.repository, "save_embeddings", None)
            if save_embeddings is None:
                raise RuntimeError("repository does not support embedding persistence")
            vectors = await self.embedder.embed_documents([chunk.text for chunk in chunks])
            if len(vectors) != len(chunks):
                raise ValueError("embedding count must match chunks")
            dimensions = getattr(self.embedder, "dimensions", None)
            if dimensions is not None and any(len(vector) != dimensions for vector in vectors):
                raise ValueError("embedding dimension mismatch")
            await save_embeddings(tenant_id=document.tenant_id, chunks=chunks, vectors=vectors)
        return IngestResult(document=document, chunks=chunks, skipped_unchanged=unchanged)

    async def list_documents(self,*,principal: KnowledgePrincipal,limit: int=100):
        return await self.repository.list_documents(principal,limit=limit)
    @staticmethod
    def _can_manage_document(document: Document, principal: KnowledgePrincipal) -> bool:
        if document.scope.value == "private":
            return document.owner_user_id == principal.user_id or "knowledge.manage" in principal.permissions
        if document.scope.value == "project":
            return "knowledge.manage" in principal.permissions or document.project_id in principal.manage_project_ids
        return "knowledge.manage" in principal.permissions

    async def grant_document_access(
        self, *, principal: KnowledgePrincipal, document_id: str,
        principal_type: str, principal_id: str, permission: str = "read",
    ) -> None:
        if principal_type not in {"user", "role", "project"}:
            raise ValueError("principal_type must be user, role or project")
        if permission not in {"read", "manage"}:
            raise ValueError("permission must be read or manage")
        if not principal_id.strip():
            raise ValueError("principal_id must not be empty")
        document = await self.repository.get_document(principal.tenant_id, document_id)
        if document is None:
            raise ValueError("document not found")
        if not self._can_manage_document(document, principal):
            raise PermissionError("document ACL management is not authorized")
        await self.repository.grant_document_access(
            principal.tenant_id, document_id, principal_type, principal_id,
            permission, principal.user_id,
        )

    async def revoke_document_access(
        self, *, principal: KnowledgePrincipal, document_id: str,
        principal_type: str, principal_id: str, permission: str = "read",
    ) -> bool:
        if principal_type not in {"user", "role", "project"}:
            raise ValueError("principal_type must be user, role or project")
        if permission not in {"read", "manage"}:
            raise ValueError("permission must be read or manage")
        document = await self.repository.get_document(principal.tenant_id, document_id)
        if document is None:
            raise ValueError("document not found")
        if not self._can_manage_document(document, principal):
            raise PermissionError("document ACL management is not authorized")
        return await self.repository.revoke_document_access(
            principal.tenant_id, document_id, principal_type, principal_id, permission,
        )

    async def grant_chunk_access(
        self, *, principal: KnowledgePrincipal, document_id: str, chunk_id: str,
        principal_type: str, principal_id: str, permission: str = "read",
    ) -> None:
        if principal_type not in {"user", "role", "project"} or permission not in {"read", "manage"}:
            raise ValueError("invalid chunk ACL")
        if not principal_id.strip():
            raise ValueError("principal_id must not be empty")
        document = await self.repository.get_document(principal.tenant_id, document_id)
        if document is None:
            raise ValueError("document not found")
        if not self._can_manage_document(document, principal):
            raise PermissionError("chunk ACL management is not authorized")
        grant = getattr(self.repository, "grant_chunk_access", None)
        if not callable(grant):
            raise RuntimeError("repository does not support chunk ACLs")
        await grant(principal.tenant_id, document_id, chunk_id, principal_type, principal_id, permission, principal.user_id)

    async def revoke_chunk_access(
        self, *, principal: KnowledgePrincipal, document_id: str, chunk_id: str,
        principal_type: str, principal_id: str, permission: str = "read",
    ) -> bool:
        if principal_type not in {"user", "role", "project"} or permission not in {"read", "manage"}:
            raise ValueError("invalid chunk ACL")
        document = await self.repository.get_document(principal.tenant_id, document_id)
        if document is None:
            raise ValueError("document not found")
        if not self._can_manage_document(document, principal):
            raise PermissionError("chunk ACL management is not authorized")
        revoke = getattr(self.repository, "revoke_chunk_access", None)
        if not callable(revoke):
            raise RuntimeError("repository does not support chunk ACLs")
        return await revoke(principal.tenant_id, chunk_id, principal_type, principal_id, permission)

    async def list_document_chunks(self, *, principal: KnowledgePrincipal, document_id: str):
        document = await self.repository.get_document(principal.tenant_id, document_id)
        if document is None: raise ValueError("document not found")
        visible = await self.repository.list_documents(principal, limit=500)
        if not any(item.document_id == document_id and item.version == document.version for item in visible):
            raise PermissionError("document read is not authorized")
        list_visible = getattr(self.repository, "list_visible_chunks", None)
        if callable(list_visible):
            return await list_visible(principal, document_id, document.version)
        return await self.repository.list_chunks(principal.tenant_id, document_id, document.version)

    async def update_document_chunk(self, *, principal: KnowledgePrincipal, document_id: str, chunk_id: str, text: str):
        document = await self.repository.get_document(principal.tenant_id, document_id)
        if document is None: raise ValueError("document not found")
        if not self._can_manage_document(document, principal): raise PermissionError("chunk editing is not authorized")
        update = getattr(self.repository, "update_chunk", None)
        if not callable(update): raise RuntimeError("repository does not support chunk editing")
        return await update(principal.tenant_id, document_id, document.version, chunk_id, text)

    async def delete_document(self,*,principal: KnowledgePrincipal,document_id: str)->bool:
        document=await self.repository.get_document(principal.tenant_id,document_id)
        if document is None:
            return False
        if not self._can_manage_document(document, principal):
            raise PermissionError("document deletion is not authorized")
        return await self.repository.delete_document(principal.tenant_id,document_id)
    @staticmethod
    def _connector_document(connector: KnowledgeConnector, tenant_id: str, document: Document) -> Document:
        from dataclasses import replace
        if not connector.connector_id.strip():
            raise ValueError("connector_id must not be empty")
        if document.tenant_id != tenant_id:
            raise PermissionError("connector returned a document for another tenant")
        metadata = dict(document.metadata)
        owner = metadata.get("_knowledge_connector_id")
        if owner is not None and owner != connector.connector_id:
            raise PermissionError("document is already owned by another connector")
        metadata["_knowledge_connector_id"] = connector.connector_id
        return replace(document, metadata=metadata)

    @staticmethod
    def _connector_acl_grants(document: Document) -> tuple[tuple[str, str, str], ...]:
        """Read the host-mapped ACL contract emitted by a connector document factory.

        External identities must already be resolved to platform user/role/project
        IDs by the host. Missing mappings are rejected rather than widening access.
        """
        if document.scope.value != "private" or not document.owner_user_id:
            raise PermissionError("connector documents must use a mapped private owner scope")
        raw = document.metadata.get("_knowledge_acl")
        if not isinstance(raw, list):
            raise PermissionError("connector document is missing its host-mapped _knowledge_acl")
        grants: set[tuple[str, str, str]] = set()
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("connector ACL entries must be objects")
            kind = item.get("principal_type")
            identifier = item.get("principal_id")
            permission = item.get("permission")
            if (
                not isinstance(kind, str) or kind not in {"user", "role", "project"}
                or not isinstance(identifier, str) or not identifier.strip()
                or not isinstance(permission, str) or permission not in {"read", "manage"}
            ):
                raise ValueError("connector ACL contains an unmapped or invalid principal")
            grants.add((kind, identifier, permission))
        return tuple(sorted(grants))

    async def _validated_connector_acl(
        self, *, connector: KnowledgeConnector, tenant_id: str, document: Document,
    ) -> tuple[tuple[str, str, str], ...]:
        try:
            return self._connector_acl_grants(document)
        except (PermissionError, ValueError):
            # A source ACL that can no longer be represented must not leave a
            # previously synchronized copy searchable under stale permissions.
            existing = await self.repository.get_document(tenant_id, document.document_id)
            if existing is not None and existing.metadata.get("_knowledge_connector_id") == connector.connector_id:
                await self.repository.delete_document(tenant_id, document.document_id)
            raise

    async def _apply_connector_acl(
        self, *, tenant_id: str, document: Document,
        grants: Sequence[tuple[str, str, str]],
    ) -> None:
        replace_access = getattr(self.repository, "replace_document_access", None)
        if not callable(replace_access):
            raise RuntimeError("knowledge repository does not support atomic connector ACL replacement")
        await replace_access(
            tenant_id, document.document_id, grants, "connector-sync",
        )

    async def _apply_connector_deletions(
        self, *, connector: KnowledgeConnector, tenant_id: str, document_ids: Sequence[str]
    ) -> int:
        deleted = 0
        for document_id in document_ids:
            if not isinstance(document_id, str) or not document_id.strip():
                raise ValueError("connector deletion IDs must be non-empty strings")
            document = await self.repository.get_document(tenant_id, document_id)
            # Replayed tombstones are idempotent.
            if document is None:
                continue
            if document.metadata.get("_knowledge_connector_id") != connector.connector_id:
                raise PermissionError("connector cannot delete a document it does not own")
            if await self.repository.delete_document(tenant_id, document_id):
                deleted += 1
        return deleted

    async def ingest_connector_page(self,*,connector: KnowledgeConnector,tenant_id: str,cursor: str|None=None,limit: int=100)->tuple[list[IngestResult],str|None,bool]:
        page=await connector.list_changed(tenant_id=tenant_id,cursor=cursor,limit=limit)
        results=[]
        for item in page.items:
            document=self._connector_document(connector,tenant_id,item.document)
            grants = await self._validated_connector_acl(
                connector=connector, tenant_id=tenant_id, document=document,
            )
            result = await self.ingest(document=document,content=item.content)
            await self._apply_connector_acl(
                tenant_id=tenant_id, document=document, grants=grants,
            )
            results.append(result)
        await self._apply_connector_deletions(
            connector=connector, tenant_id=tenant_id,
            document_ids=page.deleted_document_ids,
        )
        return results,page.next_cursor,page.has_more
    async def sync_connector(self,*,connector: KnowledgeConnector,tenant_id: str,limit: int=100,max_pages: int=10)->dict[str,object]:
        if limit<1 or limit>500:
            raise ValueError("limit must be between 1 and 500")
        if max_pages<1 or max_pages>100:
            raise ValueError("max_pages must be between 1 and 100")
        cursor=await self.repository.get_connector_cursor(tenant_id,connector.connector_id)
        page_count=0
        document_count=0
        deleted_count=0
        while page_count<max_pages:
            page=await connector.list_changed(tenant_id=tenant_id,cursor=cursor,limit=limit)
            for item in page.items:
                document=self._connector_document(connector,tenant_id,item.document)
                grants = await self._validated_connector_acl(
                    connector=connector, tenant_id=tenant_id, document=document,
                )
                await self.ingest(document=document,content=item.content)
                await self._apply_connector_acl(
                    tenant_id=tenant_id, document=document, grants=grants,
                )
                document_count+=1
            deleted_count+=await self._apply_connector_deletions(
                connector=connector, tenant_id=tenant_id,
                document_ids=page.deleted_document_ids,
            )
            if page.next_cursor is not None:
                if page.has_more and page.next_cursor==cursor:
                    raise ValueError("connector cursor did not advance while more pages remain")
                await self.repository.save_connector_cursor(tenant_id,connector.connector_id,page.next_cursor)
                cursor=page.next_cursor
            page_count+=1
            if not page.has_more:
                break
            if page.next_cursor is None:
                raise ValueError("connector must return next_cursor when has_more is true")
        return {"connector_id":connector.connector_id,"pages":page_count,"documents":document_count,"deleted_documents":deleted_count,"cursor":cursor,"has_more":page_count==max_pages and bool(page.has_more)}

    async def retrieve(self,*,principal: KnowledgePrincipal,query: str,limit: int=8,filters: RetrievalFilters|None=None,strategy: RetrievalStrategy|str=RetrievalStrategy.HYBRID)->list[RetrievalHit]:
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        if not query.strip():
            raise ValueError("query must not be empty")
        if len(query) > self.max_query_chars:
            raise ValueError("query exceeds configured character limit")
        if limit<1 or limit>50:raise ValueError("limit must be between 1 and 50")
        try:
            strategy=RetrievalStrategy(strategy)
        except ValueError as exc:
            raise ValueError("strategy must be hybrid, lexical or semantic") from exc
        if filters is not None and not isinstance(filters, RetrievalFilters):
            raise TypeError("filters must be RetrievalFilters")
        from time import monotonic
        started=monotonic()
        status="failed"
        metadata={"tenant_id":principal.tenant_id,"query_chars":len(query),"limit":limit}
        try:
            with safe_observation(
                self.observer,name="knowledge.retrieve",
                input={"query_chars":len(query),"limit":limit},metadata=metadata,
            ) as span:
                rewritten = query.strip()
                rewrite_applied = False
                if self.query_rewriter is not None:
                    rewritten = await self.query_rewriter.rewrite(rewritten)
                    if not isinstance(rewritten, str):
                        raise TypeError("query rewriter must return a string")
                    if not rewritten.strip():
                        raise ValueError("query rewriter returned an empty query")
                    if len(rewritten) > self.max_query_chars:
                        raise ValueError("rewritten query exceeds configured character limit")
                    rewritten = rewritten.strip()
                    rewrite_applied = rewritten != query.strip()
                vector=await self.embedder.embed_query(rewritten) if self.embedder else None
                dimensions=getattr(self.embedder,"dimensions",None) if self.embedder else None
                if vector is not None and dimensions is not None and len(vector)!=dimensions:
                    raise ValueError("embedding dimension mismatch")
                # Tenant and resource ACLs are applied inside the repository query before chunk bodies leave storage.
                hits=await self.repository.search(principal=principal,query=rewritten,query_vector=vector,limit=max(limit*4,20))
                if self.reranker and hits:hits=list(await self.reranker.rerank(query,hits))
                result=list(hits[:limit])
                status="succeeded"
                safe_update(span,output={"status":status,"result_count":len(result),"query_rewritten":rewrite_applied})
                return result
        except Exception as exc:
            # Do not put the query or provider exception text into trace payloads.
            safe_update(span,output={"status":"failed","error_type":type(exc).__name__})
            raise
        finally:
            safe_metric(self.metrics,"increment","knowledge_retrieval_total",
                        labels={"component":"knowledge","status":status})
            safe_metric(self.metrics,"observe","knowledge_retrieval_duration_seconds",
                        monotonic()-started,labels={"component":"knowledge"})
