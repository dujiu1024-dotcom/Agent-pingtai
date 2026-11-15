"""Reproducible deterministic evaluation primitives for Agent and RAG releases."""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import Sequence


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    expected_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    expected_document_ids: tuple[str, ...] = ()
    query: str = ""

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id is required")


@dataclass(frozen=True)
class EvalResult:
    case_id: str
    passed: bool
    score: float
    missing_terms: tuple[str, ...] = ()
    forbidden_terms_found: tuple[str, ...] = ()
    recall_at_k: float | None = None
    reciprocal_rank: float | None = None


@dataclass(frozen=True)
class EvalReport:
    dataset_id: str
    dataset_version: int
    results: tuple[EvalResult, ...]
    pass_rate: float
    average_score: float
    average_recall_at_k: float | None


@dataclass(frozen=True)
class EvalGatePolicy:
    min_pass_rate: float = 0.95
    min_average_score: float = 0.9
    min_average_recall_at_k: float = 0.8

    def __post_init__(self) -> None:
        for value in (self.min_pass_rate, self.min_average_score, self.min_average_recall_at_k):
            if not 0 <= value <= 1:
                raise ValueError("evaluation thresholds must be between 0 and 1")


@dataclass(frozen=True)
class EvalDataset:
    dataset_id: str
    version: int
    cases: tuple[EvalCase, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.dataset_id.strip() or self.version < 1 or not self.cases:
            raise ValueError("dataset id, positive version and at least one case are required")
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("evaluation case ids must be unique")


def evaluate_case(
    case: EvalCase,
    *,
    output_text: str,
    retrieved_document_ids: Sequence[str] = (),
    k: int = 5,
) -> EvalResult:
    if k < 1:
        raise ValueError("k must be positive")
    normalized = output_text.casefold()
    missing = tuple(term for term in case.expected_terms if term.casefold() not in normalized)
    forbidden = tuple(term for term in case.forbidden_terms if term.casefold() in normalized)
    recall: float | None = None
    reciprocal_rank: float | None = None
    if case.expected_document_ids:
        expected = set(case.expected_document_ids)
        top = list(retrieved_document_ids[:k])
        recall = len(expected.intersection(top)) / len(expected)
        reciprocal_rank = next(
            (1.0 / rank for rank, doc_id in enumerate(top, start=1) if doc_id in expected),
            0.0,
        )
    term_checks = len(case.expected_terms) + len(case.forbidden_terms)
    passed_terms = (len(case.expected_terms) - len(missing)) + (len(case.forbidden_terms) - len(forbidden))
    term_score = passed_terms / term_checks if term_checks else 1.0
    score = mean([term_score, recall]) if recall is not None else term_score
    passed = not missing and not forbidden and (recall is None or recall == 1.0)
    return EvalResult(case.case_id, passed, score, missing, forbidden, recall, reciprocal_rank)


def build_report(dataset: EvalDataset, results: Sequence[EvalResult]) -> EvalReport:
    by_id = {result.case_id: result for result in results}
    if set(by_id) != {case.case_id for case in dataset.cases}:
        raise ValueError("results must contain exactly one result for every dataset case")
    ordered = tuple(by_id[case.case_id] for case in dataset.cases)
    recalls = [result.recall_at_k for result in ordered if result.recall_at_k is not None]
    return EvalReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        results=ordered,
        pass_rate=sum(result.passed for result in ordered) / len(ordered),
        average_score=mean(result.score for result in ordered),
        average_recall_at_k=mean(recalls) if recalls else None,
    )


def gate_report(report: EvalReport, policy: EvalGatePolicy) -> tuple[bool, tuple[str, ...]]:
    failures: list[str] = []
    if report.pass_rate < policy.min_pass_rate:
        failures.append("pass_rate_below_threshold")
    if report.average_score < policy.min_average_score:
        failures.append("average_score_below_threshold")
    if report.average_recall_at_k is not None and report.average_recall_at_k < policy.min_average_recall_at_k:
        failures.append("retrieval_recall_below_threshold")
    return not failures, tuple(failures)
