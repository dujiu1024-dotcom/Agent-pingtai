"""Tenant-scoped knowledge ingestion and retrieval."""

from agent_platform.knowledge.contracts import (
    Document,
    DocumentChunk,
    KnowledgeCollection,
    KnowledgeIndex,
    KnowledgeScope,
    RetrievalHit,
)
from agent_platform.knowledge.service import KnowledgeService

__all__ = ["Document", "DocumentChunk", "KnowledgeCollection", "KnowledgeIndex", "KnowledgeScope", "RetrievalHit", "KnowledgeService"]
