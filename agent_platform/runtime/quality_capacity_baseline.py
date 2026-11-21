"""Deterministic quality/capacity benchmark primitives (no provider or network calls).

Callers supply an async workload adapter. The runner bounds concurrency, repeats a
versioned synthetic dataset, and emits latency, throughput, token-cost and RAG
quality metrics suitable for CI comparison. Production/provider measurements are
opt-in and are never initiated by this module.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal
from statistics import mean
from typing import Awaitable, Callable, Sequence

from agent_platform.observability.evaluation import EvalCase, evaluate_case


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    prompt: str
    expected_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    expected_document_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.case_id.strip() or not self.prompt.strip():
            raise ValueError("case id and prompt are required")


@dataclass(frozen=True)
class WorkloadResult:
    output_text: str
    input_tokens: int
    output_tokens: int
    retrieved_document_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class BenchmarkObservation:
    case_id: str
    latency_ms: float
    passed: bool
    score: float
    recall_at_k: float | None
    reciprocal_rank: float | None
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: str


@dataclass(frozen=True)
class QualityCapacityReport:
    suite_version: int
    dataset_sha256: str
    iterations: int
    concurrency: int
    completed: int
    failed: int
    duration_ms: float
    throughput_per_second: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    average_score: float
    pass_rate: float
    recall_at_k: float | None
    mean_reciprocal_rank: float | None
    total_input_tokens: int
    total_output_tokens: int
    estimated_cost_usd: str
    cost_per_1k_cases_usd: str
    observations: tuple[BenchmarkObservation, ...]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["observations"] = [asdict(item) for item in self.observations]
        return payload


@dataclass(frozen=True)
class RegressionPolicy:
    max_latency_p95_ratio: float = 1.20
    max_cost_ratio: float = 1.15
    min_pass_rate_delta: float = -0.02
    min_recall_delta: float = -0.02

    def __post_init__(self) -> None:
        if self.max_latency_p95_ratio < 1 or self.max_cost_ratio < 1:
            raise ValueError("latency and cost regression ratios must be >= 1")
        if not -1 <= self.min_pass_rate_delta <= 1 or not -1 <= self.min_recall_delta <= 1:
            raise ValueError("quality regression deltas must be between -1 and 1")


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def dataset_fingerprint(cases: Sequence[BenchmarkCase], *, suite_version: int) -> str:
    if suite_version < 1 or not cases:
        raise ValueError("positive suite version and non-empty dataset are required")
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("benchmark case ids must be unique")
    canonical = {
        "suite_version": suite_version,
        "cases": [
            {
                "case_id": case.case_id,
                "prompt": case.prompt,
                "expected_terms": list(case.expected_terms),
                "forbidden_terms": list(case.forbidden_terms),
                "expected_document_ids": list(case.expected_document_ids),
            }
            for case in cases
        ],
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def run_quality_capacity_benchmark(
    *,
    cases: Sequence[BenchmarkCase],
    workload: Callable[[BenchmarkCase], Awaitable[WorkloadResult]],
    iterations: int = 3,
    concurrency: int = 4,
    suite_version: int = 1,
    input_price_per_million: Decimal = Decimal("0"),
    output_price_per_million: Decimal = Decimal("0"),
) -> QualityCapacityReport:
    """Run fixed cases with bounded concurrency and deterministic quality scoring."""
    if not cases or iterations < 1 or concurrency < 1 or suite_version < 1:
        raise ValueError("cases, iterations, concurrency and suite_version must be positive")
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("benchmark case ids must be unique")
    for price in (input_price_per_million, output_price_per_million):
        if not isinstance(price, Decimal) or not price.is_finite() or price < 0:
            raise ValueError("token prices must be finite non-negative Decimals")

    semaphore = asyncio.Semaphore(concurrency)
    observations: list[BenchmarkObservation] = []
    failures = 0
    total_input = 0
    total_output = 0
    cost = Decimal("0")
    started = time.perf_counter()

    async def execute(case: BenchmarkCase) -> None:
        nonlocal failures, total_input, total_output, cost
        async with semaphore:
            tick = time.perf_counter()
            try:
                result = await workload(case)
                elapsed = (time.perf_counter() - tick) * 1000
                if (
                    isinstance(result.input_tokens, bool)
                    or isinstance(result.output_tokens, bool)
                    or not isinstance(result.input_tokens, int)
                    or not isinstance(result.output_tokens, int)
                    or result.input_tokens < 0 or result.output_tokens < 0
                ):
                    raise ValueError("workload token usage must be non-negative integers")
                evaluated = evaluate_case(
                    EvalCase(
                        case_id=case.case_id,
                        expected_terms=case.expected_terms,
                        forbidden_terms=case.forbidden_terms,
                        expected_document_ids=case.expected_document_ids,
                    ),
                    output_text=result.output_text,
                    retrieved_document_ids=result.retrieved_document_ids,
                    k=5,
                )
                item_cost = (
                    Decimal(result.input_tokens) * input_price_per_million
                    + Decimal(result.output_tokens) * output_price_per_million
                ) / Decimal(1_000_000)
                total_input += result.input_tokens
                total_output += result.output_tokens
                cost += item_cost
                observations.append(BenchmarkObservation(
                    case.case_id, round(elapsed, 3), evaluated.passed, evaluated.score,
                    evaluated.recall_at_k, evaluated.reciprocal_rank,
                    result.input_tokens, result.output_tokens, _money(item_cost),
                ))
            except Exception:
                failures += 1
                observations.append(BenchmarkObservation(
                    case.case_id, round((time.perf_counter() - tick) * 1000, 3),
                    False, 0.0, None, None, 0, 0, "0.000000",
                ))

    jobs = [execute(case) for _ in range(iterations) for case in cases]
    await asyncio.gather(*jobs)
    duration_ms = (time.perf_counter() - started) * 1000
    latencies = [item.latency_ms for item in observations]
    recalls = [item.recall_at_k for item in observations if item.recall_at_k is not None]
    mrr = [item.reciprocal_rank for item in observations if item.reciprocal_rank is not None]
    completed = len(observations) - failures
    total_cases = len(jobs)
    cost_per_1k = cost * Decimal(1000) / Decimal(max(1, total_cases))
    return QualityCapacityReport(
        suite_version=suite_version,
        dataset_sha256=dataset_fingerprint(cases, suite_version=suite_version),
        iterations=iterations,
        concurrency=concurrency,
        completed=completed,
        failed=failures,
        duration_ms=round(duration_ms, 3),
        throughput_per_second=round(completed / (duration_ms / 1000), 3) if duration_ms else 0.0,
        latency_p50_ms=_percentile(latencies, 0.50),
        latency_p95_ms=_percentile(latencies, 0.95),
        latency_p99_ms=_percentile(latencies, 0.99),
        average_score=round(mean(item.score for item in observations), 4) if observations else 0.0,
        pass_rate=round(sum(item.passed for item in observations) / total_cases, 4),
        recall_at_k=round(mean(recalls), 4) if recalls else None,
        mean_reciprocal_rank=round(mean(mrr), 4) if mrr else None,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        estimated_cost_usd=_money(cost),
        cost_per_1k_cases_usd=_money(cost_per_1k),
        observations=tuple(sorted(observations, key=lambda item: (item.case_id, item.latency_ms))),
    )


def compare_quality_capacity(
    candidate: QualityCapacityReport,
    baseline: QualityCapacityReport,
    policy: RegressionPolicy = RegressionPolicy(),
) -> tuple[bool, tuple[str, ...]]:
    """Fail on material latency/cost regressions or quality drops."""
    failures: list[str] = []
    if candidate.dataset_sha256 != baseline.dataset_sha256:
        failures.append("dataset_fingerprint_mismatch")
    if baseline.latency_p95_ms > 0 and (
        candidate.latency_p95_ms / baseline.latency_p95_ms > policy.max_latency_p95_ratio
    ):
        failures.append("p95_latency_regression")
    baseline_cost = Decimal(baseline.estimated_cost_usd)
    candidate_cost = Decimal(candidate.estimated_cost_usd)
    if baseline_cost > 0 and candidate_cost / baseline_cost > Decimal(str(policy.max_cost_ratio)):
        failures.append("estimated_cost_regression")
    if candidate.pass_rate - baseline.pass_rate < policy.min_pass_rate_delta:
        failures.append("answer_quality_regression")
    if baseline.recall_at_k is not None and candidate.recall_at_k is not None:
        if candidate.recall_at_k - baseline.recall_at_k < policy.min_recall_delta:
            failures.append("rag_recall_regression")
    return not failures, tuple(failures)


__all__ = [
    "BenchmarkCase", "WorkloadResult", "BenchmarkObservation",
    "QualityCapacityReport", "RegressionPolicy", "dataset_fingerprint",
    "run_quality_capacity_benchmark", "compare_quality_capacity",
]
