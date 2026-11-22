"""Pinned Agent/RAG evaluation plans, bounded execution and quality metrics.

The module deliberately stores endpoint identity (not credentials) and keeps
all run inputs immutable so a report can be reproduced from its dataset version.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from math import log2
from statistics import mean
from typing import Any, Callable, Protocol, Sequence
from uuid import uuid4


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    query: str
    expected_answer_terms: tuple[str, ...] = ()
    forbidden_answer_terms: tuple[str, ...] = ()
    relevant_document_ids: tuple[str, ...] = ()
    required_citation_document_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id is required")
        if not self.query.strip():
            raise ValueError("query is required")
        for name, values in (
            ("expected_answer_terms", self.expected_answer_terms),
            ("forbidden_answer_terms", self.forbidden_answer_terms),
            ("relevant_document_ids", self.relevant_document_ids),
            ("required_citation_document_ids", self.required_citation_document_ids),
        ):
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"{name} must contain non-empty strings")


@dataclass(frozen=True)
class EvaluationDataset:
    dataset_id: str
    version: int
    cases: tuple[EvaluationCase, ...]

    def __post_init__(self) -> None:
        if not self.dataset_id.strip() or self.version < 1 or not self.cases:
            raise ValueError("dataset id, positive version and cases are required")
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case ids must be unique")


@dataclass(frozen=True)
class ModelEndpoint:
    endpoint_id: str
    model_key: str
    endpoint_url: str
    model_version: str

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (
            self.endpoint_id, self.model_key, self.endpoint_url, self.model_version,
        )):
            raise ValueError("endpoint identity, URL and model version are required")
        if not self.endpoint_url.startswith(("https://", "http://")):
            raise ValueError("endpoint_url must be an HTTP(S) URL")


@dataclass(frozen=True)
class EvaluationPlan:
    tenant_id: str
    dataset_id: str
    dataset_version: int
    endpoint: ModelEndpoint
    concurrency_limit: int = 4
    retrieval_k: int = 5

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.dataset_id.strip():
            raise ValueError("tenant_id and dataset_id are required")
        if isinstance(self.dataset_version, bool) or not isinstance(self.dataset_version, int) or self.dataset_version < 1:
            raise ValueError("dataset_version must be positive")
        if isinstance(self.concurrency_limit, bool) or not isinstance(self.concurrency_limit, int) or not 1 <= self.concurrency_limit <= 32:
            raise ValueError("concurrency_limit must be between 1 and 32")
        if isinstance(self.retrieval_k, bool) or not isinstance(self.retrieval_k, int) or not 1 <= self.retrieval_k <= 100:
            raise ValueError("retrieval_k must be between 1 and 100")


@dataclass(frozen=True)
class Citation:
    document_id: str
    source_uri: str = ""
    quote: str = ""


@dataclass(frozen=True)
class CaseOutput:
    answer: str
    retrieved_document_ids: tuple[str, ...] = ()
    citations: tuple[Citation, ...] = ()


@dataclass(frozen=True)
class CaseMetrics:
    case_id: str
    retrieval_recall_at_k: float | None
    retrieval_precision_at_k: float | None
    retrieval_mrr: float | None
    retrieval_ndcg_at_k: float | None
    answer_term_precision: float
    answer_term_recall: float
    answer_term_f1: float
    forbidden_term_rate: float
    citation_precision: float
    citation_recall: float
    citation_coverage: float
    citation_validity: float


@dataclass(frozen=True)
class EvaluationReport:
    run_id: str
    tenant_id: str
    dataset_id: str
    dataset_version: int
    dataset_sha256: str
    endpoint_id: str
    model_key: str
    model_version: str
    concurrency_limit: int
    case_metrics: tuple[CaseMetrics, ...]
    metrics: dict[str, float | None]


def dataset_fingerprint(dataset: EvaluationDataset) -> str:
    """Return a canonical SHA-256 for the exact ordered dataset contents."""
    payload = {
        "dataset_id": dataset.dataset_id,
        "version": dataset.version,
        "cases": [
            {
                "case_id": case.case_id,
                "query": case.query,
                "expected_answer_terms": list(case.expected_answer_terms),
                "forbidden_answer_terms": list(case.forbidden_answer_terms),
                "relevant_document_ids": list(case.relevant_document_ids),
                "required_citation_document_ids": list(case.required_citation_document_ids),
            }
            for case in dataset.cases
        ],
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class AgentRAGEvaluator(Protocol):
    async def evaluate(
        self, case: EvaluationCase, *, tenant_id: str, endpoint: ModelEndpoint,
    ) -> CaseOutput: ...


def _retrieval_metrics(case: EvaluationCase, ranked_ids: Sequence[str], k: int) -> tuple[
    float | None, float | None, float | None, float | None,
]:
    relevant = set(case.relevant_document_ids)
    if not relevant:
        return None, None, None, None
    ranked = list(dict.fromkeys(ranked_ids))[:k]
    hits = [1 if item in relevant else 0 for item in ranked]
    recall = sum(hits) / len(relevant)
    precision = sum(hits) / len(ranked) if ranked else 0.0
    reciprocal_rank = next((1.0 / rank for rank, hit in enumerate(hits, 1) if hit), 0.0)
    ideal = min(len(relevant), k)
    dcg = sum(hit / log2(rank + 1) for rank, hit in enumerate(hits, 1))
    idcg = sum(1.0 / log2(rank + 1) for rank in range(1, ideal + 1))
    return recall, precision, reciprocal_rank, dcg / idcg


def score_case(case: EvaluationCase, output: CaseOutput, *, k: int = 5) -> CaseMetrics:
    if not isinstance(output.answer, str):
        raise TypeError("answer must be a string")
    answer = output.answer.casefold()
    expected = {term.casefold() for term in case.expected_answer_terms}
    forbidden = {term.casefold() for term in case.forbidden_answer_terms}
    found = {term for term in expected if term in answer}
    answer_recall = len(found) / len(expected) if expected else 1.0
    forbidden_found = {term for term in forbidden if term in answer}
    answer_precision = len(found) / (len(found) + len(forbidden_found)) if found else (
        1.0 if not expected else 0.0
    )
    f1 = (
        2 * answer_precision * answer_recall / (answer_precision + answer_recall)
        if answer_precision + answer_recall else 0.0
    )
    forbidden_rate = len(forbidden_found) / len(forbidden) if forbidden else 0.0

    retrieval = _retrieval_metrics(case, output.retrieved_document_ids, k)
    citation_ids = [item.document_id for item in output.citations]
    unique_citations = list(dict.fromkeys(citation_ids))
    required = set(case.required_citation_document_ids)
    relevant = set(case.relevant_document_ids)
    valid = [item for item in output.citations if item.document_id in relevant and item.source_uri.strip()]
    citation_precision = len(set(item.document_id for item in valid)) / len(unique_citations) if unique_citations else 0.0
    citation_recall = len(required.intersection(unique_citations)) / len(required) if required else 1.0
    citation_coverage = len(unique_citations) / len(output.retrieved_document_ids) if output.retrieved_document_ids else (
        1.0 if not required and not unique_citations else 0.0
    )
    citation_validity = len(valid) / len(output.citations) if output.citations else (
        1.0 if not required else 0.0
    )
    return CaseMetrics(
        case_id=case.case_id,
        retrieval_recall_at_k=retrieval[0],
        retrieval_precision_at_k=retrieval[1],
        retrieval_mrr=retrieval[2],
        retrieval_ndcg_at_k=retrieval[3],
        answer_term_precision=answer_precision,
        answer_term_recall=answer_recall,
        answer_term_f1=f1,
        forbidden_term_rate=forbidden_rate,
        citation_precision=citation_precision,
        citation_recall=citation_recall,
        citation_coverage=citation_coverage,
        citation_validity=citation_validity,
    )


class EvaluationReportRepository(Protocol):
    async def save_evaluation_report(self, report: EvaluationReport) -> None: ...


class AgentRAGEvaluationService:
    """Executes one immutable dataset version against one pinned model endpoint."""

    def __init__(
        self, *, dataset_loader, evaluator: AgentRAGEvaluator,
        repository: EvaluationReportRepository,
    ) -> None:
        self._load_dataset = dataset_loader
        self._evaluator = evaluator
        self._repository = repository

    async def run(self, plan: EvaluationPlan) -> EvaluationReport:
        dataset = await self._load_dataset(plan.dataset_id, plan.dataset_version)
        if not isinstance(dataset, EvaluationDataset):
            raise TypeError("dataset loader returned an invalid dataset")
        if (dataset.dataset_id, dataset.version) != (plan.dataset_id, plan.dataset_version):
            raise ValueError("loaded dataset does not match the pinned version")
        semaphore = asyncio.Semaphore(plan.concurrency_limit)

        async def run_case(case: EvaluationCase) -> CaseMetrics:
            async with semaphore:
                output = await self._evaluator.evaluate(
                    case, tenant_id=plan.tenant_id, endpoint=plan.endpoint,
                )
            if not isinstance(output, CaseOutput):
                raise TypeError("evaluator must return CaseOutput")
            return score_case(case, output, k=plan.retrieval_k)

        # gather preserves dataset order; any case failure aborts persistence.
        metrics = tuple(await asyncio.gather(*(run_case(case) for case in dataset.cases)))
        metric_names = (
            "retrieval_recall_at_k", "retrieval_precision_at_k", "retrieval_mrr",
            "retrieval_ndcg_at_k", "answer_term_precision", "answer_term_recall",
            "answer_term_f1", "forbidden_term_rate", "citation_precision",
            "citation_recall", "citation_coverage", "citation_validity",
        )
        summary = {
            name: mean(values) if values else None
            for name in metric_names
            if (values := [getattr(item, name) for item in metrics if getattr(item, name) is not None])
            or name in metric_names
        }
        report = EvaluationReport(
            run_id=str(uuid4()), tenant_id=plan.tenant_id,
            dataset_id=dataset.dataset_id, dataset_version=dataset.version,
            dataset_sha256=dataset_fingerprint(dataset),
            endpoint_id=plan.endpoint.endpoint_id, model_key=plan.endpoint.model_key,
            model_version=plan.endpoint.model_version,
            concurrency_limit=plan.concurrency_limit, case_metrics=metrics,
            metrics=summary,
        )
        await self._repository.save_evaluation_report(report)
        return report


class PostgresAgentRAGEvaluationRepository:
    """Tenant-scoped immutable persistence for Agent/RAG evaluation reports."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def save_evaluation_report(self, report: EvaluationReport) -> None:
        if not report.tenant_id.strip() or not report.run_id.strip():
            raise ValueError("report tenant_id and run_id are required")
        if len(report.dataset_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in report.dataset_sha256
        ):
            raise ValueError("report dataset_sha256 must be a lowercase SHA-256")
        payload = {
            "run_id": report.run_id,
            "tenant_id": report.tenant_id,
            "dataset_id": report.dataset_id,
            "dataset_version": report.dataset_version,
            "dataset_sha256": report.dataset_sha256,
            "endpoint_id": report.endpoint_id,
            "model_key": report.model_key,
            "model_version": report.model_version,
            "concurrency_limit": report.concurrency_limit,
            "case_metrics": [
                {
                    "case_id": item.case_id,
                    "retrieval_recall_at_k": item.retrieval_recall_at_k,
                    "retrieval_precision_at_k": item.retrieval_precision_at_k,
                    "retrieval_mrr": item.retrieval_mrr,
                    "retrieval_ndcg_at_k": item.retrieval_ndcg_at_k,
                    "answer_term_precision": item.answer_term_precision,
                    "answer_term_recall": item.answer_term_recall,
                    "answer_term_f1": item.answer_term_f1,
                    "forbidden_term_rate": item.forbidden_term_rate,
                    "citation_precision": item.citation_precision,
                    "citation_recall": item.citation_recall,
                    "citation_coverage": item.citation_coverage,
                    "citation_validity": item.citation_validity,
                }
                for item in report.case_metrics
            ],
            "metrics": report.metrics,
        }
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_agent_rag_eval_reports
                   (run_id, tenant_id, dataset_id, dataset_version,
                    dataset_sha256, endpoint_id, model_key, model_version,
                    concurrency_limit, report)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)""",
                (
                    report.run_id, report.tenant_id, report.dataset_id,
                    report.dataset_version, report.dataset_sha256,
                    report.endpoint_id, report.model_key, report.model_version,
                    report.concurrency_limit,
                    json.dumps(payload, ensure_ascii=False, allow_nan=False),
                ),
            )

    async def get_report(self, *, tenant_id: str, run_id: str) -> dict[str, Any]:
        if not tenant_id.strip() or not run_id.strip():
            raise ValueError("tenant_id and run_id are required")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT run_id, tenant_id, dataset_id, dataset_version,
                          dataset_sha256, endpoint_id, model_key, model_version,
                          concurrency_limit, report, created_at
                   FROM platform_agent_rag_eval_reports
                   WHERE tenant_id = %s AND run_id = %s""",
                (tenant_id, run_id),
            )
            row = await cursor.fetchone()
        if row is None:
            raise KeyError("Agent/RAG evaluation report not found in this tenant")
        result = dict(row)
        if isinstance(result.get("report"), str):
            result["report"] = json.loads(result["report"])
        result["run_id"] = str(result["run_id"])
        return result

    async def list_reports(
        self, *, tenant_id: str, dataset_id: str | None = None, limit: int = 20,
    ) -> list[dict[str, Any]]:
        if not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        connection = await self._connect()
        async with connection:
            if dataset_id is None:
                cursor = await connection.execute(
                    """SELECT run_id, tenant_id, dataset_id, dataset_version,
                              dataset_sha256, endpoint_id, model_key, model_version,
                              concurrency_limit, report, created_at
                       FROM platform_agent_rag_eval_reports
                       WHERE tenant_id = %s ORDER BY created_at DESC LIMIT %s""",
                    (tenant_id, limit),
                )
            else:
                cursor = await connection.execute(
                    """SELECT run_id, tenant_id, dataset_id, dataset_version,
                              dataset_sha256, endpoint_id, model_key, model_version,
                              concurrency_limit, report, created_at
                       FROM platform_agent_rag_eval_reports
                       WHERE tenant_id = %s AND dataset_id = %s
                       ORDER BY created_at DESC LIMIT %s""",
                    (tenant_id, dataset_id, limit),
                )
            rows = await cursor.fetchall()
        results = []
        for row in rows:
            item = dict(row)
            if isinstance(item.get("report"), str):
                item["report"] = json.loads(item["report"])
            item["run_id"] = str(item["run_id"])
            results.append(item)
        return results
