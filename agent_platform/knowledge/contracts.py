"""Knowledge domain contracts. Knowledge is versioned source material, not memory or chat history."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


def now_utc() -> datetime:
    return datetime.now(timezone.utc)

class KnowledgeScope(str, Enum):
    TENANT = "tenant"
    PROJECT = "project"
    PRIVATE = "private"


@dataclass(frozen=True)
class KnowledgeCollection:
    """Tenant-owned knowledge namespace with optional project boundary."""

    tenant_id: str
    name: str
    collection_id: str = field(default_factory=lambda: str(uuid4()))
    description: str = ""
    project_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=now_utc)
    updated_at: datetime = field(default_factory=now_utc)
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 200:
            raise ValueError("collection name must contain 1-200 characters")
        if not isinstance(self.collection_id, str) or not self.collection_id.strip():
            raise ValueError("collection_id must not be empty")
        if self.project_id is not None and not self.project_id.strip():
            raise ValueError("project_id must not be empty when provided")


@dataclass(frozen=True)
class KnowledgeIndex:
    tenant_id: str
    collection_id: str
    index_id: str = field(default_factory=lambda: str(uuid4()))
    name: str = "default"
    index_type: str = "hybrid"
    embedding_model: str | None = None
    dimensions: int | None = None
    status: str = "ready"
    created_at: datetime = field(default_factory=now_utc)

    def __post_init__(self) -> None:
        if self.index_type not in {"lexical", "vector", "hybrid"}:
            raise ValueError("index_type must be lexical, vector or hybrid")
        if self.dimensions is not None and (
            isinstance(self.dimensions, bool) or self.dimensions < 1
        ):
            raise ValueError("dimensions must be a positive integer")

@dataclass(frozen=True)
class Document:
    tenant_id: str
    title: str
    source_uri: str
    content_hash: str
    scope: KnowledgeScope = KnowledgeScope.TENANT
    project_id: str | None = None
    owner_user_id: str | None = None
    document_id: str = field(default_factory=lambda: str(uuid4()))
    version: int = 1
    media_type: str = "text/plain"
    metadata: dict[str, Any] = field(default_factory=dict)
    collection_id: str | None = None
    created_at: datetime = field(default_factory=now_utc)
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        for key in ("tenant_id", "title", "source_uri", "content_hash"):
            if not getattr(self, key).strip():
                raise ValueError(f"{key} must not be empty")
        if self.version < 1:
            raise ValueError("version must be >= 1")
        if self.scope is KnowledgeScope.PROJECT and not self.project_id:
            raise ValueError("project-scoped documents require project_id")
        if self.scope is KnowledgeScope.PRIVATE and not self.owner_user_id:
            raise ValueError("private documents require owner_user_id")

@dataclass(frozen=True)
class DocumentChunk:
    tenant_id: str
    document_id: str
    document_version: int
    ordinal: int
    text: str
    content_hash: str
    start_offset: int
    end_offset: int
    page_number: int | None = None
    chunk_id: str = field(default_factory=lambda: str(uuid4()))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("chunk text must not be empty")
        if self.ordinal < 0 or self.start_offset < 0 or self.end_offset < self.start_offset:
            raise ValueError("invalid chunk position")

@dataclass(frozen=True)
class KnowledgePrincipal:
    tenant_id: str
    user_id: str
    project_ids: frozenset[str] = frozenset()
    roles: frozenset[str] = frozenset()
    permissions: frozenset[str] = frozenset()
    manage_project_ids: frozenset[str] = frozenset()

@dataclass(frozen=True)
class RetrievalHit:
    document_id: str
    document_version: int
    chunk_id: str
    text: str
    source_uri: str
    title: str
    score: float
    page_number: int | None = None
    start_offset: int = 0
    end_offset: int = 0
    citation_id: str = field(default_factory=lambda: str(uuid4()))
    retrieval_trace: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class IngestResult:
    document: Document
    chunks: tuple[DocumentChunk, ...]
    skipped_unchanged: bool = False
