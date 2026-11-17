"""Evaluation governance: human-review privacy, regression comparisons and gate evidence."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol
from uuid import uuid4

LOWER_IS_BETTER = frozenset({"latency_ms", "cost_per_run", "forbidden_term_rate", "error_rate"})
HIGHER_IS_BETTER = frozenset({
    "pass_rate", "average_score", "average_recall_at_k", "retrieval_recall_at_k",
    "retrieval_precision_at_k", "retrieval_mrr", "retrieval_ndcg_at_k",
    "answer_term_f1", "citation_precision", "citation_recall", "citation_validity",
})


@dataclass(frozen=True)
class EvaluationThresholdPolicy:
    policy_id: str
    version: int
    minimums: Mapping[str, float]
    maximums: Mapping[str, float]

    def __post_init__(self) -> None:
        if not self.policy_id.strip() or isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("policy id and positive integer version are required")
        for name, value in (*self.minimums.items(), *self.maximums.items()):
            if not isinstance(name, str) or not name.strip():
                raise ValueError("metric names must be non-empty strings")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value:
                raise ValueError("thresholds must be non-negative numbers")
        if set(self.minimums) & set(self.maximums):
            raise ValueError("a metric cannot have both minimum and maximum thresholds")


@dataclass(frozen=True)
class MetricDelta:
    metric: str
    baseline: float
    candidate: float
    delta: float
    regressed: bool


@dataclass(frozen=True)
class RegressionComparison:
    comparison_id: str
    baseline_run_id: str
    candidate_run_id: str
    deltas: tuple[MetricDelta, ...]
    regressions: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationGateEvidence:
    evidence_id: str
    tenant_id: str
    candidate_run_id: str
    baseline_run_id: str | None
    policy_id: str
    policy_version: int
    policy_snapshot: Mapping[str, Any]
    passed: bool
    failures: tuple[str, ...]
    comparison_id: str | None
    evidence_refs: tuple[str, ...]
    evaluated_at_utc: str
    content_sha256: str


def _finite_metric(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"metric {name} must be numeric")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"metric {name} must be finite")
    return number


def compare_metric_reports(
    baseline_run_id: str, candidate_run_id: str,
    baseline: Mapping[str, Any], candidate: Mapping[str, Any],
) -> RegressionComparison:
    if not baseline_run_id.strip() or not candidate_run_id.strip() or baseline_run_id == candidate_run_id:
        raise ValueError("distinct baseline and candidate run IDs are required")
    if not baseline or set(baseline) != set(candidate):
        raise ValueError("baseline and candidate must contain the same non-empty metric set")
    deltas: list[MetricDelta] = []
    regressions: list[str] = []
    for metric in sorted(baseline):
        before = _finite_metric(baseline[metric], metric)
        after = _finite_metric(candidate[metric], metric)
        delta = after - before
        regressed = (metric in HIGHER_IS_BETTER and delta < 0) or (
            metric in LOWER_IS_BETTER and delta > 0
        )
        deltas.append(MetricDelta(metric, before, after, delta, regressed))
        if regressed:
            regressions.append(metric)
    return RegressionComparison(
        str(uuid4()), baseline_run_id, candidate_run_id, tuple(deltas), tuple(regressions),
    )


def evaluate_thresholds(
    metrics: Mapping[str, Any], policy: EvaluationThresholdPolicy,
) -> tuple[bool, tuple[str, ...]]:
    failures: list[str] = []
    for metric, threshold in policy.minimums.items():
        if metric not in metrics or _finite_metric(metrics[metric], metric) < threshold:
            failures.append(f"{metric}:below_minimum:{threshold}")
    for metric, threshold in policy.maximums.items():
        if metric not in metrics or _finite_metric(metrics[metric], metric) > threshold:
            failures.append(f"{metric}:above_maximum:{threshold}")
    return not failures, tuple(failures)


def mask_reviewer_identity(reviewer_id: str) -> str:
    """Stable within the API response domain but does not disclose the account ID."""
    if not reviewer_id:
        return "reviewer"
    digest = hashlib.sha256(reviewer_id.encode("utf-8")).hexdigest()[:10]
    return f"reviewer-{digest}"


def redact_human_score_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["reviewer_alias"] = mask_reviewer_identity(str(item.pop("reviewer_id", "")))
        safe.append(item)
    return safe


class EvaluationGovernanceRepository(Protocol):
    async def load_report_for_tenant(self, *, tenant_id: str, eval_run_id: str) -> dict[str, Any]: ...
    async def save_comparison(self, *, tenant_id: str, comparison: RegressionComparison) -> None: ...
    async def save_gate_evidence(self, evidence: EvaluationGateEvidence) -> None: ...


class EvaluationGovernanceService:
    def __init__(self, repository: EvaluationGovernanceRepository) -> None:
        self.repository = repository

    async def compare(
        self, *, tenant_id: str, baseline_run_id: str, candidate_run_id: str,
    ) -> RegressionComparison:
        if not tenant_id.strip():
            raise ValueError("tenant_id is required")
        baseline = await self.repository.load_report_for_tenant(
            tenant_id=tenant_id, eval_run_id=baseline_run_id,
        )
        candidate = await self.repository.load_report_for_tenant(
            tenant_id=tenant_id, eval_run_id=candidate_run_id,
        )
        comparison = compare_metric_reports(
            baseline_run_id, candidate_run_id,
            baseline.get("metrics", {}), candidate.get("metrics", {}),
        )
        await self.repository.save_comparison(tenant_id=tenant_id, comparison=comparison)
        return comparison

    async def decide_release(
        self, *, tenant_id: str, candidate_run_id: str,
        policy: EvaluationThresholdPolicy, evidence_refs: tuple[str, ...],
        baseline_run_id: str | None = None,
    ) -> EvaluationGateEvidence:
        if not tenant_id.strip() or not candidate_run_id.strip():
            raise ValueError("tenant and candidate run are required")
        if not evidence_refs or any(not ref.strip() for ref in evidence_refs):
            raise ValueError("at least one non-empty evidence reference is required")
        candidate = await self.repository.load_report_for_tenant(
            tenant_id=tenant_id, eval_run_id=candidate_run_id,
        )
        metrics = candidate.get("metrics", {})
        passed, failures = evaluate_thresholds(metrics, policy)
        comparison = None
        if baseline_run_id:
            comparison = await self.compare(
                tenant_id=tenant_id, baseline_run_id=baseline_run_id,
                candidate_run_id=candidate_run_id,
            )
            if comparison.regressions:
                passed = False
                failures = (*failures, *(f"regression:{name}" for name in comparison.regressions))
        payload = {
            "evidence_id": str(uuid4()), "tenant_id": tenant_id,
            "candidate_run_id": candidate_run_id, "baseline_run_id": baseline_run_id,
            "policy_id": policy.policy_id, "policy_version": policy.version,
            "policy_snapshot": {"minimums": dict(policy.minimums), "maximums": dict(policy.maximums)},
            "passed": passed, "failures": list(failures),
            "comparison_id": comparison.comparison_id if comparison else None,
            "evidence_refs": list(evidence_refs),
            "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        evidence = EvaluationGateEvidence(
            **{**payload, "failures": tuple(failures), "evidence_refs": tuple(evidence_refs)},
            content_sha256=digest,
        )
        await self.repository.save_gate_evidence(evidence)
        return evidence
