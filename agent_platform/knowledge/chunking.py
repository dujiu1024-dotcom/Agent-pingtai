"""Deterministic document cleaning, chunking, provenance and content hashes."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from bisect import bisect_right
from uuid import NAMESPACE_URL, uuid5

from agent_platform.knowledge.contracts import Document, DocumentChunk

CLEANING_VERSION = "knowledge-clean-v1"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def clean_document_text(value: str) -> str:
    """Canonicalize extracted text before hashing or splitting it."""
    if not isinstance(value, str):
        raise TypeError("document text must be a string")
    text = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", "").replace("\ufeff", "")
    text = "".join(
        char for char in text
        if char in "\n\t" or unicodedata.category(char) not in {"Cc", "Cf"}
    )
    text = text.replace("\t", " ")
    lines = [re.sub(r"[ \u00a0]+", " ", line).strip() for line in text.split("\n")]
    cleaned: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if cleaned and not blank:
                cleaned.append("")
            blank = True
            continue
        cleaned.append(line)
        blank = False
    return "\n".join(cleaned).strip()


def chunk_document(
    document: Document, text: str, *, max_chars: int = 1200, overlap: int = 160,
) -> tuple[DocumentChunk, ...]:
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 100:
        raise ValueError("max_chars must be an integer >= 100")
    if isinstance(overlap, bool) or not isinstance(overlap, int) or overlap < 0 or overlap >= max_chars:
        raise ValueError("overlap must be an integer in [0, max_chars)")
    normalized = clean_document_text(text)
    # Parse page markers once. The previous per-chunk regex scan made large,
    # page-rich PDFs pay O(chunks * document_length) just to recover provenance.
    page_markers = [
        (match.start(), int(match.group(1)))
        for match in re.finditer(r"\[page:(\d+)\]", normalized)
    ]
    page_offsets = [offset for offset, _ in page_markers]
    chunks: list[DocumentChunk] = []
    start = 0
    while start < len(normalized):
        hard_end = min(len(normalized), start + max_chars)
        end = hard_end
        page_split = False
        page_boundary = normalized.find("[page:", start + 1, hard_end)
        if page_boundary > start:
            end = page_boundary
            page_split = True
        elif hard_end < len(normalized):
            boundary = max(
                normalized.rfind("\n", start, hard_end),
                normalized.rfind("。", start, hard_end),
                normalized.rfind(". ", start, hard_end),
            )
            if boundary > start + max_chars // 2:
                end = boundary + 1
        raw = normalized[start:end]
        body = raw.strip()
        if body:
            offset = start + len(raw) - len(raw.lstrip())
            marker_index = bisect_right(page_offsets, offset) - 1
            page_number = page_markers[marker_index][1] if marker_index >= 0 else None
            ordinal = len(chunks)
            digest = sha256_text(body)
            identity = ":".join((
                document.tenant_id, document.document_id, str(document.version),
                str(ordinal), digest,
            ))
            chunk_id = str(uuid5(NAMESPACE_URL, identity))
            metadata = {
                "cleaning_version": CLEANING_VERSION,
                "source_uri": document.source_uri,
                "source_locator": {
                    "kind": "text_offset",
                    "start_offset": offset,
                    "end_offset": offset + len(body),
                    "page_number": page_number,
                },
            }
            chunks.append(DocumentChunk(
                tenant_id=document.tenant_id, document_id=document.document_id,
                document_version=document.version, ordinal=ordinal, text=body,
                content_hash=digest, start_offset=offset, end_offset=offset + len(body),
                page_number=page_number, chunk_id=chunk_id, metadata=metadata,
            ))
        if end >= len(normalized):
            break
        start = end if page_split else max(start + 1, end - overlap)
    return tuple(chunks)
