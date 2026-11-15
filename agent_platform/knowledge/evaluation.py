"""Repeatable retrieval evaluation with Recall@K and citation coverage."""
from __future__ import annotations
from dataclasses import dataclass
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
async def evaluate_retrieval(service: KnowledgeService,cases: Sequence[RAGEvalCase],*,k: int=5)->list[RAGEvalResult]:
    if k<1: raise ValueError("k must be >= 1")
    results=[]
    for case in cases:
        hits=await service.retrieve(principal=case.principal,query=case.query,limit=k)
        found={h.document_id for h in hits}
        recall=len(found & case.expected_document_ids)/len(case.expected_document_ids) if case.expected_document_ids else 1.0
        cited=sum(bool(h.source_uri and h.document_version and h.chunk_id) for h in hits)/len(hits) if hits else 0.0
        results.append(RAGEvalResult(case_id=case.case_id,retrieved_document_ids=tuple(h.document_id for h in hits),recall_at_k=recall,citation_coverage=cited))
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
