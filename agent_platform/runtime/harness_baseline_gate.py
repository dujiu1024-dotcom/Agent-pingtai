"""Release thresholds for reproducible Harness quality/cost/latency baselines."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from agent_platform.runtime.harness_benchmark import HarnessBenchmarkReport


@dataclass(frozen=True)
class HarnessBaseline:
    min_pass_rate: float = 0.95
    min_quality_score: float = 0.95
    max_p95_latency_ms: int = 30_000
    max_cost_usd_per_case: float | None = None
    min_eligible_cases: int = 5
    max_skipped_rate: float = 0.25

    def __post_init__(self) -> None:
        if not math.isfinite(self.min_pass_rate) or not 0 <= self.min_pass_rate <= 1:
            raise ValueError("min_pass_rate must be between 0 and 1")
        if not math.isfinite(self.min_quality_score) or not 0 <= self.min_quality_score <= 1:
            raise ValueError("min_quality_score must be between 0 and 1")
        if isinstance(self.max_p95_latency_ms, bool) or self.max_p95_latency_ms <= 0:
            raise ValueError("max_p95_latency_ms must be positive")
        if self.max_cost_usd_per_case is not None and (
            not math.isfinite(self.max_cost_usd_per_case) or self.max_cost_usd_per_case < 0
        ):
            raise ValueError("max_cost_usd_per_case must be finite and non-negative")
        if isinstance(self.min_eligible_cases, bool) or self.min_eligible_cases < 1:
            raise ValueError("min_eligible_cases must be positive")
        if not math.isfinite(self.max_skipped_rate) or not 0 <= self.max_skipped_rate <= 1:
            raise ValueError("max_skipped_rate must be between 0 and 1")


def evaluate_harness_baseline(
    report: HarnessBenchmarkReport,
    *,
    runtime: str,
    baseline: HarnessBaseline = HarnessBaseline(),
) -> dict[str, Any]:
    """Fail closed when evidence is absent or any configured threshold is breached."""
    rows = [row for row in report.observations if row.runtime == runtime]
    eligible = [row for row in rows if not row.skipped]
    distinct_eligible_cases = len({row.case_id for row in eligible})
    skipped_rate = len(rows) and sum(row.skipped for row in rows) / len(rows) or 0.0
    latencies = sorted(row.duration_ms for row in eligible)
    p95_index = max(0, math.ceil(0.95 * len(latencies)) - 1) if latencies else None
    p95 = latencies[p95_index] if p95_index is not None else None
    pass_rate = sum(row.passed for row in eligible) / len(eligible) if eligible else 0.0
    quality = sum(row.quality_score for row in eligible) / len(eligible) if eligible else 0.0
    costs = [row.estimated_cost_usd for row in eligible]
    cost_per_case = (
        sum(value for value in costs if value is not None) / len(costs)
        if costs and all(value is not None for value in costs) else None
    )
    checks = {
        "minimum_eligible_cases": distinct_eligible_cases >= baseline.min_eligible_cases,
        "pass_rate": pass_rate >= baseline.min_pass_rate,
        "quality": quality >= baseline.min_quality_score,
        "p95_latency": p95 is not None and p95 <= baseline.max_p95_latency_ms,
        "skipped_rate": skipped_rate <= baseline.max_skipped_rate,
        "cost": (
            baseline.max_cost_usd_per_case is None
            or (cost_per_case is not None and cost_per_case <= baseline.max_cost_usd_per_case)
        ),
    }
    return {
        "runtime": runtime,
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": {
            "eligible_cases": len(eligible),
            "distinct_eligible_cases": distinct_eligible_cases,
            "skipped_rate": round(skipped_rate, 4),
            "pass_rate": round(pass_rate, 4),
            "average_quality_score": round(quality, 4),
            "p95_latency_ms": p95,
            "average_cost_usd_per_case": (
                round(cost_per_case, 8) if cost_per_case is not None else None
            ),
        },
        "baseline": asdict(baseline),
        "limitations": [
            "Latency is measured sequentially; this gate does not claim a concurrency/load test.",
            "Cost is enforced only when every eligible observation has explicit pricing.",
            "Quality is the benchmark suite's deterministic anchor score, not human evaluation.",
        ],
    }


__all__ = ["HarnessBaseline", "evaluate_harness_baseline"]
