"""Grounded RAG answer orchestration with strict citation validation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from agent_platform.knowledge.citations import citation_context
from agent_platform.knowledge.contracts import KnowledgePrincipal, RetrievalHit
from agent_platform.knowledge.service import KnowledgeService


class RAGGenerator(Protocol):
    async def generate(
        self, *, query: str, context: Sequence[Mapping[str, object]]
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class RAGAnswer:
    answer: str
    citations: tuple[dict[str, object], ...]
    retrieved: tuple[RetrievalHit, ...]
    abstained: bool
    reason: str | None = None


class RAGService:
    def __init__(self, *, knowledge: KnowledgeService, generator: RAGGenerator) -> None:
        self.knowledge = knowledge
        self.generator = generator

    async def answer(
        self, *, principal: KnowledgePrincipal, query: str, limit: int = 8
    ) -> RAGAnswer:
        hits = await self.knowledge.retrieve(
            principal=principal, query=query, limit=limit
        )
        if not hits:
            return RAGAnswer(
                answer="",
                citations=(),
                retrieved=(),
                abstained=True,
                reason="no_authorized_evidence",
            )
        context = citation_context(hits)
        generated = await self.generator.generate(query=query, context=context)
        answer = generated.get("answer")
        citation_ids = generated.get("citation_ids")
        if not isinstance(answer, str) or not answer.strip():
            return RAGAnswer(
                answer="",
                citations=(),
                retrieved=tuple(hits),
                abstained=True,
                reason="empty_answer",
            )
        if not isinstance(citation_ids, list) or not citation_ids:
            return RAGAnswer(
                answer="",
                citations=(),
                retrieved=tuple(hits),
                abstained=True,
                reason="citations_required",
            )
        allowed = {hit.citation_id for hit in hits}
        if any(not isinstance(item, str) or item not in allowed for item in citation_ids):
            return RAGAnswer(
                answer="",
                citations=(),
                retrieved=tuple(hits),
                abstained=True,
                reason="unknown_citation",
            )
        selected = set(citation_ids)
        citations = tuple(
            item for item in context if item["citation_id"] in selected
        )
        return RAGAnswer(
            answer=answer.strip(),
            citations=citations,
            retrieved=tuple(hits),
            abstained=False,
        )
