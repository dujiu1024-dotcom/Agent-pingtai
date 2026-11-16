"""Repeatable retrieval evaluation with Recall@K and citation coverage."""
from __future__ import annotations

from dataclasses import dataclass
from math import log2
from typing import Sequence

from agent_platform.knowledge.contracts import KnowledgePrincipal
from agent_platform.knowledge.rag import RAGService
from agent_platform.knowledge.service import KnowledgeService


@dataclass(frozen=True)
class RAGEvalCase:
    case_id: str
    query: str
    expected_document_ids: frozenset[str]
    principal: KnowledgePrincipal
@dataclass(frozen=True)
class RAGEvalResult:
    case_id: str
    retrieved_document_ids: tuple[str,...]
    recall_at_k: float
    citation_coverage: float
    precision_at_k: float
    reciprocal_rank: float
    ndcg_at_k: float
async def evaluate_retrieval(service: KnowledgeService,cases: Sequence[RAGEvalCase],*,k: int=5)->list[RAGEvalResult]:
    if k<1: raise ValueError("k must be >= 1")
    results=[]
    for case in cases:
        hits=await service.retrieve(principal=case.principal,query=case.query,limit=k)
        expected = case.expected_document_ids
        # Rank documents, not chunks: multiple chunks from one source must not
        # inflate precision, reciprocal rank, or nDCG.
        ranked_document_ids = list(dict.fromkeys(hit.document_id for hit in hits))
        ranked_relevant = [
            1 if document_id in expected else 0 for document_id in ranked_document_ids
        ]
        found = set(ranked_document_ids)
        recall = len(found & expected) / len(expected) if expected else 1.0
        precision = (
            sum(ranked_relevant) / len(ranked_document_ids)
            if ranked_document_ids else 0.0
        ) if expected else (1.0 if not ranked_document_ids else 0.0)
        reciprocal_rank = next(
            (1.0 / index for index, relevant in enumerate(ranked_relevant, 1) if relevant),
            0.0,
        )
        ideal_count = min(len(expected), k)
        if ideal_count:
            dcg = sum(relevant / log2(index + 1) for index, relevant in enumerate(ranked_relevant, 1))
            idcg = sum(1.0 / log2(index + 1) for index in range(1, ideal_count + 1))
            ndcg = dcg / idcg
        else:
            ndcg = 1.0 if not hits else 0.0
        cited = sum(bool(h.source_uri and h.document_version and h.chunk_id) for h in hits) / len(hits) if hits else 0.0
        results.append(RAGEvalResult(
            case_id=case.case_id, retrieved_document_ids=tuple(ranked_document_ids),
            recall_at_k=recall, citation_coverage=cited, precision_at_k=precision,
            reciprocal_rank=reciprocal_rank, ndcg_at_k=ndcg,
        ))
    return results


@dataclass(frozen=True)
class RAGAnswerEvalCase:
    case_id: str
    query: str
    principal: KnowledgePrincipal
    expected_answer_terms: frozenset[str] = frozenset()
    expected_citation_document_ids: frozenset[str] = frozenset()
    expect_abstain: bool = False


@dataclass(frozen=True)
class RAGAnswerEvalResult:
    case_id: str
    abstained: bool
    answer_term_coverage: float
    citation_precision: float
    citation_recall: float
    grounded: bool


async def evaluate_answers(rag: RAGService, cases: Sequence[RAGAnswerEvalCase], *, k: int = 5) -> list[RAGAnswerEvalResult]:
    """Deterministic answer/citation checks for release gates; not a substitute for human quality review."""
    if k < 1:
        raise ValueError("k must be >= 1")
    results = []
    for case in cases:
        response = await rag.answer(principal=case.principal, query=case.query, limit=k)
        answer = response.answer.casefold()
        terms = {term.casefold() for term in case.expected_answer_terms}
        term_coverage = len({term for term in terms if term in answer}) / len(terms) if terms else 1.0
        cited = {str(item["document_id"]) for item in response.citations}
        expected = set(case.expected_citation_document_ids)
        overlap = len(cited & expected)
        citation_precision = overlap / len(cited) if cited else (1.0 if not expected else 0.0)
        citation_recall = overlap / len(expected) if expected else (1.0 if not cited else 0.0)
        abstention_correct = response.abstained is case.expect_abstain
        grounded = (
            abstention_correct
            and (response.abstained or (term_coverage == 1.0 and citation_precision == 1.0 and citation_recall == 1.0))
        )
        results.append(RAGAnswerEvalResult(
            case_id=case.case_id,
            abstained=response.abstained,
            answer_term_coverage=term_coverage,
            citation_precision=citation_precision,
            citation_recall=citation_recall,
            grounded=grounded,
        ))
    return results
