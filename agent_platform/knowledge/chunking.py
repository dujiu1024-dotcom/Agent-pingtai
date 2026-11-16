"""Deterministic text chunking with source offsets and content hashes."""
from __future__ import annotations

import hashlib
import re
from uuid import NAMESPACE_URL, uuid5

from agent_platform.knowledge.contracts import Document, DocumentChunk


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def chunk_document(document: Document, text: str, *, max_chars: int = 1200, overlap: int = 160) -> tuple[DocumentChunk, ...]:
    if max_chars < 100 or overlap < 0 or overlap >= max_chars:
        raise ValueError("max_chars must be >=100 and overlap must be in [0, max_chars)")
    normalized = re.sub(r"\r\n?", "\n", text).strip()
    chunks: list[DocumentChunk] = []
    start = 0
    while start < len(normalized):
        hard_end = min(len(normalized), start + max_chars)
        end = hard_end
        # Keep explicit page markers at chunk boundaries so citations never
        # attribute text from a later page to the previous page, including when
        # the page marker falls inside the final chunk.
        page_boundary = normalized.find("[page:", start + 1, hard_end)
        if page_boundary > start:
            end = page_boundary
        elif hard_end < len(normalized):
            boundary = max(normalized.rfind("\n", start, hard_end), normalized.rfind("。", start, hard_end), normalized.rfind(". ", start, hard_end))
            if boundary > start + max_chars // 2: end = boundary + 1
        raw = normalized[start:end]
        body = raw.strip()
        if body:
            offset = start + len(raw) - len(raw.lstrip())
            # Cite the page at the chunk's first source offset, even when
            # the chunk overlaps a later page boundary.
            page_number = None
            for marker in re.finditer(r"\[page:(\d+)\]", normalized):
                if marker.start() > start:
                    break
                page_number = int(marker.group(1))
            ordinal = len(chunks)
            content_hash = sha256_text(body)
            identity = ":".join((document.tenant_id, document.document_id, str(document.version), str(ordinal), content_hash))
            chunk_id = str(uuid5(NAMESPACE_URL, identity))
            chunks.append(DocumentChunk(tenant_id=document.tenant_id, document_id=document.document_id, document_version=document.version, ordinal=ordinal, text=body, content_hash=content_hash, start_offset=offset, end_offset=offset + len(body), page_number=page_number, chunk_id=chunk_id))
        if end >= len(normalized): break
        start = max(start + 1, end - overlap)
    return tuple(chunks)
