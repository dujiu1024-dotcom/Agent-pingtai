"""Stable, user-displayable citation serialization for retrieved chunks."""
from __future__ import annotations
from dataclasses import asdict
from typing import Sequence
from agent_platform.knowledge.contracts import RetrievalHit
def citation_context(hits: Sequence[RetrievalHit])->list[dict[str,object]]:
    return [{"citation_id":h.citation_id,"document_id":h.document_id,"document_version":h.document_version,"chunk_id":h.chunk_id,"title":h.title,"source_uri":h.source_uri,"page_number":h.page_number,"start_offset":h.start_offset,"end_offset":h.end_offset,"text":h.text,"score":h.score,"retrieval_trace":dict(h.retrieval_trace)} for h in hits]
