"""Deterministic batch retrieval metrics for the RAG test workbench.

Ranking metrics use unique source documents (not chunks) to avoid inflating
scores when several chunks from one document are retrieved. Token coverage is
a lexical reference-answer coverage metric using the versioned unicode-word-v1
tokenizer; it does not require an external tokenizer or model call.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from math import log2
from statistics import mean
from typing import Sequence

from agent_platform.knowledge.contracts import KnowledgePrincipal
from agent_platform.knowledge.evaluation_dataset import RAGEvaluationDataset
from agent_platform.knowledge.retrieval import RetrievalFilters, RetrievalStrategy
from agent_platform.knowledge.service import KnowledgeService

_TOKENIZER = "unicode-word-v1"
_TOKEN_PATTERN = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)


@dataclass(frozen=True)
class RAGMetricCaseResult:
    case_id: str
    hit_rate_at_k: float | None
    reciprocal_rank: float | None
    ndcg_at_k: float | None
    token_coverage: float | None
    retrieved_source_uris: tuple[str, ...]
    retrieved_document_ids: tuple[str, ...]


@dataclass(frozen=True)
class RAGMetricReport:
    dataset_id: str
    dataset_version: int
    dataset_fingerprint: str
    top_k: int
    embedding_model: str
    rerank: bool
    min_score: float
    tokenizer: str
    evaluated_cases: int
    ranking_scored_cases: int
    token_scored_cases: int
    hit_rate_at_k: float | None
    mrr: float | None
    ndcg_at_k: float | None
    token_coverage: float | None
    cases: tuple[RAGMetricCaseResult, ...]
    run_fingerprint: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["cases"] = [asdict(case) for case in self.cases]
        return payload


def _tokens(text: str) -> list[str]:
    return [token.casefold() for token in _TOKEN_PATTERN.findall(text)]


def _ranking_scores(
    expected_source_uris: Sequence[str],
    retrieved_source_uris: Sequence[str],
    *,
    k: int,
) -> tuple[float | None, float | None, float | None]:
    expected = set(expected_source_uris)
    # Deduplicate by source document while preserving the first retrieved rank.
    ranked: list[str] = []
    seen: set[str] = set()
    for uri in retrieved_source_uris[:k]:
        if uri not in seen:
            seen.add(uri)
            ranked.append(uri)
    if not expected:
        # No positive labels (typically an expected-abstention case): ranking
        # metrics are not applicable and are excluded from aggregate means.
        return None, None, None
    relevant = [uri in expected for uri in ranked]
    hit = 1.0 if any(relevant) else 0.0
    reciprocal_rank = next(
        (1.0 / rank for rank, is_relevant in enumerate(relevant, 1) if is_relevant),
        0.0,
    )
    ideal_count = min(len(expected), k)
    dcg = sum(
        (1.0 / log2(rank + 1))
        for rank, is_relevant in enumerate(relevant, 1)
        if is_relevant
    )
    idcg = sum(1.0 / log2(rank + 1) for rank in range(1, ideal_count + 1))
    ndcg = dcg / idcg if idcg else 0.0
    return hit, reciprocal_rank, ndcg


def _token_coverage(reference_answer: str, retrieved_contexts: Sequence[str]) -> float | None:
    expected = Counter(_tokens(reference_answer))
    if not expected:
        return None
    available = Counter(token for context in retrieved_contexts for token in _tokens(context))
    covered = sum(min(count, available[token]) for token, count in expected.items())
    return covered / sum(expected.values())


async def evaluate_rag_dataset(
    *,
    knowledge: KnowledgeService,
    principal: KnowledgePrincipal,
    dataset: RAGEvaluationDataset,
    top_k: int,
    embedding_model: str,
    rerank: bool,
    min_score: float,
) -> RAGMetricReport:
    """Retrieve every case and build a repeatable, reference-based metric report."""
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 20:
        raise ValueError("top_k must be an integer between 1 and 20")
    if not isinstance(embedding_model, str) or not embedding_model.strip():
        raise ValueError("embedding_model is required")
    if not isinstance(rerank, bool):
        raise ValueError("rerank must be a boolean")
    if isinstance(min_score, bool) or not isinstance(min_score, (int, float)) or not 0 <= min_score <= 1:
        raise ValueError("min_score must be between 0 and 1")

    documents = {document.key: document for document in dataset.documents}
    case_results: list[RAGMetricCaseResult] = []
    for case in dataset.cases:
        hits = await knowledge.retrieve(
            principal=principal,
            query=case.query,
            limit=top_k,
            filters=RetrievalFilters(min_score=float(min_score)),
            strategy=RetrievalStrategy.HYBRID,
        )
        expected_sources = tuple(
            documents[key].source_uri for key in case.expected_document_keys
        )
        source_uris = tuple(hit.source_uri for hit in hits)
        hit_rate, reciprocal_rank, ndcg = _ranking_scores(
            expected_sources, source_uris, k=top_k,
        )
        token_coverage = _token_coverage(
            case.answer, tuple(hit.text for hit in hits),
        )
        case_results.append(RAGMetricCaseResult(
            case_id=case.case_id,
            hit_rate_at_k=hit_rate,
            reciprocal_rank=reciprocal_rank,
            ndcg_at_k=ndcg,
            token_coverage=token_coverage,
            retrieved_source_uris=source_uris,
            retrieved_document_ids=tuple(hit.document_id for hit in hits),
        ))

    def average(field: str) -> float | None:
        values = [
            getattr(item, field) for item in case_results
            if getattr(item, field) is not None
        ]
        return mean(values) if values else None

    ranking_scored = sum(item.hit_rate_at_k is not None for item in case_results)
    token_scored = sum(item.token_coverage is not None for item in case_results)
    from agent_platform.knowledge.evaluation import evaluation_fingerprint

    run_fingerprint = evaluation_fingerprint({
        "dataset_fingerprint": dataset.fingerprint,
        "top_k": top_k,
        "embedding_model": embedding_model,
        "rerank": rerank,
        "min_score": float(min_score),
        "tokenizer": _TOKENIZER,
    })
    return RAGMetricReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        dataset_fingerprint=dataset.fingerprint,
        top_k=top_k,
        embedding_model=embedding_model,
        rerank=rerank,
        min_score=float(min_score),
        tokenizer=_TOKENIZER,
        evaluated_cases=len(case_results),
        ranking_scored_cases=ranking_scored,
        token_scored_cases=token_scored,
        hit_rate_at_k=average("hit_rate_at_k"),
        mrr=average("reciprocal_rank"),
        ndcg_at_k=average("ndcg_at_k"),
        token_coverage=average("token_coverage"),
        cases=tuple(case_results),
        run_fingerprint=run_fingerprint,
    )
