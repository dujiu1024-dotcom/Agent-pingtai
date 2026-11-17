"""Composable retrieval policy contracts and validation."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RetrievalStrategy(str, Enum):
    HYBRID = "hybrid"
    LEXICAL = "lexical"
    SEMANTIC = "semantic"


@dataclass(frozen=True)
class RetrievalFilters:
    """Post-ACL result constraints; never replace tenant or document ACL checks."""

    document_ids: frozenset[str] = field(default_factory=frozenset)
    source_uri_prefix: str | None = None
    title_contains: str | None = None
    min_score: float | None = None

    def __post_init__(self) -> None:
        if any(not value.strip() for value in self.document_ids):
            raise ValueError("document_ids must contain non-empty IDs")
        if self.source_uri_prefix is not None and not self.source_uri_prefix.strip():
            raise ValueError("source_uri_prefix must not be empty")
        if self.title_contains is not None and not self.title_contains.strip():
            raise ValueError("title_contains must not be empty")
        if self.min_score is not None and not 0 <= self.min_score <= 1:
            raise ValueError("min_score must be between 0 and 1")

    def matches(self, hit) -> bool:
        return (
            (not self.document_ids or hit.document_id in self.document_ids)
            and (self.source_uri_prefix is None or hit.source_uri.startswith(self.source_uri_prefix))
            and (self.title_contains is None or self.title_contains.casefold() in hit.title.casefold())
            and (self.min_score is None or hit.score >= self.min_score)
        )
