"""Repeatable retrieval evaluation with Recall@K and citation coverage."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence
from agent_platform.knowledge.contracts import KnowledgePrincipal
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
