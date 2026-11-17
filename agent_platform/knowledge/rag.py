"""Grounded RAG answer orchestration with strict citation validation and tracing."""
from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4

from agent_platform.knowledge.citations import citation_context
from agent_platform.knowledge.contracts import KnowledgePrincipal, RetrievalHit
from agent_platform.knowledge.retrieval import RetrievalFilters, RetrievalStrategy
from agent_platform.knowledge.service import KnowledgeService
from agent_platform.observability.instrumentation import safe_metric, safe_observation, safe_update
from agent_platform.observability.trace_spans import trace_span


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
    def __init__(
        self, *, knowledge: KnowledgeService, generator: RAGGenerator,
        observer: Any | None = None, metrics: Any | None = None,
    ) -> None:
        self.knowledge = knowledge
        self.generator = generator
        self.observer = observer
        self.metrics = metrics

    @staticmethod
    def _trace_metadata(
        principal: KnowledgePrincipal, trace_context: Mapping[str, str] | None
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {"tenant_id": principal.tenant_id}
        for key in ("run_id", "request_id", "agent_id"):
            value = (trace_context or {}).get(key)
            if isinstance(value, str) and value:
                metadata[key] = value[:128]
        return metadata

    async def answer(
        self, *, principal: KnowledgePrincipal, query: str, limit: int = 8,
        trace_context: Mapping[str, str] | None = None,
        filters: RetrievalFilters | None = None,
        strategy: RetrievalStrategy | str = RetrievalStrategy.HYBRID,
    ) -> RAGAnswer:
        metadata = self._trace_metadata(principal, trace_context)
        started = monotonic()
        status = "failed"
        with trace_span(
            "rag.answer", component="retrieval", tenant_id=principal.tenant_id,
            run_id=metadata.get("run_id"), request_id=metadata.get("request_id"),
            agent_id=metadata.get("agent_id"),
            correlation_id=(trace_context or {}).get("correlation_id"),
        ), safe_observation(
            self.observer, name="rag.answer",
            input={"query_chars": len(query), "retrieval_limit": limit},
            metadata=metadata,
        ) as root_span:
            try:
                retrieval_started = monotonic()
                with trace_span(
                    "knowledge.retrieve", component="knowledge",
                    tenant_id=principal.tenant_id, run_id=metadata.get("run_id"),
                    request_id=metadata.get("request_id"), agent_id=metadata.get("agent_id"),
                    retrieval_id=str(uuid4()),
                    knowledge_collection_id=(trace_context or {}).get("knowledge_collection_id"),
                    attributes={"query_chars": len(query), "limit": limit},
                ), safe_observation(
                    self.observer, name="rag.retrieve",
                    input={"query_chars": len(query), "limit": limit},
                    metadata=metadata,
                ) as retrieve_span:
                    hits = await self.knowledge.retrieve(
                        principal=principal, query=query, limit=limit,
                        filters=filters, strategy=strategy,
                    )
                    if filters is not None:
                        hits = [hit for hit in hits if filters.matches(hit)]
                    safe_update(retrieve_span, output={"hit_count": len(hits)})
                safe_metric(
                    self.metrics, "observe", "rag_retrieval_hits",
                    float(len(hits)), labels={"component": "rag"},
                )
                safe_metric(
                    self.metrics, "observe", "rag_retrieval_duration_seconds",
                    monotonic() - retrieval_started, labels={"component": "rag"},
                )
                if not hits:
                    status = "abstained"
                    result = RAGAnswer(
                        answer="", citations=(), retrieved=(),
                        abstained=True, reason="no_authorized_evidence",
                    )
                    safe_update(root_span, output={"status": status, "reason": result.reason})
                    return result

                context = citation_context(hits)
                context_chars = sum(len(str(item.get("text", ""))) for item in context)
                generation_started = monotonic()
                with trace_span(
                    "rag.generate", component="agent", tenant_id=principal.tenant_id,
                    run_id=metadata.get("run_id"), request_id=metadata.get("request_id"),
                    agent_id=metadata.get("agent_id"),
                    attributes={"context_document_count": len(context),
                                "context_chars": context_chars},
                ), safe_observation(
                    self.observer, name="rag.generate",
                    input={
                        "context_document_count": len(context),
                        "context_chars": context_chars,
                    },
                    metadata=metadata,
                ) as generate_span:
                    generated = await self.generator.generate(query=query, context=context)
                    safe_update(generate_span, output={
                        "status": "response_received",
                        "answer_chars": len(generated.get("answer", ""))
                        if isinstance(generated.get("answer"), str) else 0,
                        "citation_count": len(generated.get("citation_ids", []))
                        if isinstance(generated.get("citation_ids"), list) else 0,
                    })
                safe_metric(
                    self.metrics, "observe", "rag_generation_duration_seconds",
                    monotonic() - generation_started, labels={"component": "rag"},
                )
                answer = generated.get("answer")
                citation_ids = generated.get("citation_ids")
                reason = None
                if not isinstance(answer, str) or not answer.strip():
                    reason = "empty_answer"
                elif not isinstance(citation_ids, list) or not citation_ids:
                    reason = "citations_required"
                else:
                    allowed = {hit.citation_id for hit in hits}
                    if any(not isinstance(item, str) or item not in allowed for item in citation_ids):
                        reason = "unknown_citation"
                if reason is not None:
                    status = "abstained"
                    result = RAGAnswer(
                        answer="", citations=(), retrieved=tuple(hits),
                        abstained=True, reason=reason,
                    )
                else:
                    selected = set(citation_ids)
                    citations = tuple(
                        item for item in context if item["citation_id"] in selected
                    )
                    result = RAGAnswer(
                        answer=answer.strip(), citations=citations,
                        retrieved=tuple(hits), abstained=False,
                    )
                    status = "grounded"
                safe_update(root_span, output={
                    "status": status, "reason": result.reason,
                    "retrieved_count": len(hits), "citation_count": len(result.citations),
                })
                return result
            except Exception as exc:
                safe_update(root_span, output={
                    "status": "failed", "error_type": type(exc).__name__,
                })
                raise
            finally:
                safe_metric(
                    self.metrics, "increment", "rag_requests_total",
                    labels={"component": "rag", "status": status},
                )
                safe_metric(
                    self.metrics, "observe", "rag_answer_duration_seconds",
                    monotonic() - started, labels={"component": "rag"},
                )
