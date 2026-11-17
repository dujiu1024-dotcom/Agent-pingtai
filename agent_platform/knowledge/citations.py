"""Stable, user-displayable citation serialization for retrieved chunks."""
from __future__ import annotations

from typing import Sequence

from agent_platform.knowledge.contracts import RetrievalHit


def citation_context(hits: Sequence[RetrievalHit]) -> list[dict[str, object]]:
    """Serialize immutable source coordinates alongside the exact retrieved chunk."""
    return [
        {
            "citation_id": hit.citation_id,
            "document_id": hit.document_id,
            "document_version": hit.document_version,
            "chunk_id": hit.chunk_id,
            "title": hit.title,
            "source_uri": hit.source_uri,
            "source_locator": {
                "page_number": hit.page_number,
                "start_offset": hit.start_offset,
                "end_offset": hit.end_offset,
            },
            "page_number": hit.page_number,
            "start_offset": hit.start_offset,
            "end_offset": hit.end_offset,
            "text": hit.text,
            "score": hit.score,
            "retrieval_trace": dict(hit.retrieval_trace),
        }
        for hit in hits
    ]
