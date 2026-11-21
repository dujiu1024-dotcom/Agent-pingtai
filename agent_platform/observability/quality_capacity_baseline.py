"""Repeatable synthetic quality/capacity baseline; no provider or external services."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from math import ceil
from typing import Any

from agent_platform.observability.agent_rag_evaluation import (
    CaseOutput,
    Citation,
    EvaluationCase,
    score_case,
)

BASELINE_VERSION = "c21-04-v1"
DEFAULT_INPUT_TOKENS = 120_000
DEFAULT_OUTPUT_TOKENS = 24_000
DEFAULT_INPUT_USD_PER_MILLION = 0.50
DEFAULT_OUTPUT_USD_PER_MILLION = 1.50


@dataclass(frozen=True)
class CapacityConfig:
    operations: int = 256
    concurrency: int = 16

    def __post_init__(self) -> None:
        if isinstance(self.operations, bool) or not isinstance(self.operations, int) or not 1 <= self.operations <= 100_000:
            raise ValueError("operations must be between 1 and 100000")
        if isinstance(self.concurrency, bool) or not isinstance(self.concurrency, int) or not 1 <= self.concurrency <= 256:
            raise ValueError("concurrency must be between 1 and 256")


@dataclass(frozen=True)
class QualityCapacityReport:
    baseline_version: str
    fixture_sha256: str
    config: dict[str, int]
    capacity: dict[str, float | int]
    cost: dict[str, float | int]
    quality: dict[str, float]
    environment: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)


def _percentile(samples: list[float], quantile: float) -> float:
    if not samples or not 0 <= quantile <= 1:
        raise ValueError("samples and a quantile between zero and one are required")
    ordered = sorted(samples)
    # Nearest-rank percentile: stable for small CI sample sizes.
    index = max(0, min(len(ordered) - 1, ceil(len(ordered) * quantile) - 1))
    return ordered[index]


def _quality_fixture() -> tuple[EvaluationCase, CaseOutput]:
    case = EvaluationCase(
        case_id="rag-grounded-answer-v1",
        query="Which document supports the 12% revenue increase?",
        expected_answer_terms=("revenue increased", "12%"),
        forbidden_answer_terms=("guaranteed",),
        relevant_document_ids=("doc-finance-2026", "doc-audit-2026"),
        required_citation_document_ids=("doc-finance-2026",),
    )
    output = CaseOutput(
        answer="The report says revenue increased by 12%.",
        retrieved_document_ids=("doc-finance-2026", "doc-unrelated"),
        citations=(Citation("doc-finance-2026", "https://fixture.invalid/finance"),),
    )
    return case, output


def fixture_fingerprint() -> str:
    case, output = _quality_fixture()
    payload = {
        "version": BASELINE_VERSION,
        "case": asdict(case),
        "output": asdict(output),
        "input_tokens": DEFAULT_INPUT_TOKENS,
        "output_tokens": DEFAULT_OUTPUT_TOKENS,
        "input_price": DEFAULT_INPUT_USD_PER_MILLION,
        "output_price": DEFAULT_OUTPUT_USD_PER_MILLION,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


async def run_quality_capacity_baseline(
    config: CapacityConfig = CapacityConfig(),
) -> QualityCapacityReport:
    """Measure local synthetic scheduling overhead and deterministic quality/cost."""
    semaphore = asyncio.Semaphore(config.concurrency)
    active = 0
    max_active = 0
    latencies_ms: list[float] = []

    async def operation(index: int) -> int:
        nonlocal active, max_active
        queued_at = time.perf_counter()
        async with semaphore:
            active += 1
            max_active = max(max_active, active)
            # Include semaphore queue time in end-to-end operation latency.
            await asyncio.sleep(0)
            checksum = (index * 31 + 17) % 997
            latencies_ms.append((time.perf_counter() - queued_at) * 1000)
            active -= 1
            return checksum

    started = time.perf_counter()
    results = await asyncio.gather(*(operation(index) for index in range(config.operations)))
    elapsed = max(time.perf_counter() - started, 1e-9)

    input_cost = DEFAULT_INPUT_TOKENS * DEFAULT_INPUT_USD_PER_MILLION / 1_000_000
    output_cost = DEFAULT_OUTPUT_TOKENS * DEFAULT_OUTPUT_USD_PER_MILLION / 1_000_000
    case, output = _quality_fixture()
    metrics = score_case(case, output, k=5)
    quality = {
        "retrieval_recall_at_k": float(metrics.retrieval_recall_at_k or 0.0),
        "retrieval_precision_at_k": float(metrics.retrieval_precision_at_k or 0.0),
        "answer_term_f1": metrics.answer_term_f1,
        "forbidden_term_rate": metrics.forbidden_term_rate,
        "citation_precision": metrics.citation_precision,
        "citation_recall": metrics.citation_recall,
        "citation_validity": metrics.citation_validity,
    }
    return QualityCapacityReport(
        baseline_version=BASELINE_VERSION,
        fixture_sha256=fixture_fingerprint(),
        config={"operations": config.operations, "concurrency": config.concurrency},
        capacity={
            "completed_operations": len(results),
            "throughput_ops_per_second": round(len(results) / elapsed, 3),
            "elapsed_ms": round(elapsed * 1000, 3),
            "latency_p50_ms": round(_percentile(latencies_ms, 0.50), 3),
            "latency_p95_ms": round(_percentile(latencies_ms, 0.95), 3),
            "latency_max_ms": round(max(latencies_ms), 3),
            "max_observed_concurrency": max_active,
            "checksum": sum(results),
        },
        cost={
            "input_tokens": DEFAULT_INPUT_TOKENS,
            "output_tokens": DEFAULT_OUTPUT_TOKENS,
            "input_usd_per_million": DEFAULT_INPUT_USD_PER_MILLION,
            "output_usd_per_million": DEFAULT_OUTPUT_USD_PER_MILLION,
            "estimated_total_usd": round(input_cost + output_cost, 6),
            "estimated_usd_per_1k_output_tokens": round(
                (input_cost + output_cost) / DEFAULT_OUTPUT_TOKENS * 1000, 6
            ),
        },
        quality=quality,
        environment={"runtime": "python-asyncio", "mode": "synthetic-no-network"},
    )


__all__ = [
    "BASELINE_VERSION",
    "CapacityConfig",
    "QualityCapacityReport",
    "fixture_fingerprint",
    "run_quality_capacity_baseline",
]
