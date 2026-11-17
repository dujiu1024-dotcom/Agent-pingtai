"""Bridge Model Eval experiment outcomes into Prompt Lab gray rollout policies."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EvaluationRolloutResult:
    experiment_id: str
    prompt_id: str
    tenant_id: str
    selected_variant: str
    weights: tuple[tuple[str, float], ...]
    policy_revision: int
    mode: str


class EvaluationRolloutBridge:
    """Resolve a tenant-owned immutable experiment report and apply an explicit rollout."""

    def __init__(self, *, experiment_repository: Any, rollout_service: Any) -> None:
        self.experiment_repository = experiment_repository
        self.rollout_service = rollout_service

    async def apply(
        self, *, tenant_id: str, experiment_id: str, actor: str,
        mode: str, variant: str | None = None, rollout_percentage: float = 10.0,
        minimum_score_gain: float = 0.0,
    ) -> EvaluationRolloutResult:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor is required")
        if mode not in {"automatic", "manual"}:
            raise ValueError("mode must be automatic or manual")
        if (
            isinstance(rollout_percentage, bool)
            or not isinstance(rollout_percentage, (int, float))
            or not math.isfinite(rollout_percentage)
            or not 1 <= rollout_percentage <= 100
        ):
            raise ValueError("rollout_percentage must be between 1 and 100")
        if (
            isinstance(minimum_score_gain, bool)
            or not isinstance(minimum_score_gain, (int, float))
            or not math.isfinite(minimum_score_gain)
            or minimum_score_gain < 0
        ):
            raise ValueError("minimum_score_gain must be finite and non-negative")

        report = await self.experiment_repository.get(tenant_id, experiment_id)
        if report is None:
            raise KeyError("experiment report not found")
        if report.tenant_id != tenant_id:
            raise PermissionError("experiment report tenant mismatch")
        if not report.prompt_id or not report.baseline_variant or len(report.metrics) < 2:
            raise ValueError("experiment report is missing rollout lineage or variant metrics")
        if any(getattr(row, "error", None) for row in report.matrix):
            raise ValueError("experiment contains failed cases; rollout is blocked")
        metrics = {item.variant: item for item in report.metrics}
        if len(metrics) != len(report.metrics) or report.baseline_variant not in metrics:
            raise ValueError("experiment variant metrics are incomplete or duplicated")
        expected_cases = {item.case_id for item in report.matrix}
        for item in report.metrics:
            if item.case_count < 1 or item.case_count != len(expected_cases):
                raise ValueError("every variant must have complete case coverage")

        if mode == "automatic":
            if variant is not None:
                raise ValueError("automatic mode does not accept a manual variant")
            baseline = metrics[report.baseline_variant]
            candidates = [item for name, item in metrics.items() if name != report.baseline_variant]
            winner = max(
                candidates,
                key=lambda item: (item.average_score, item.pass_rate, -item.average_latency_ms, item.variant),
            )
            if winner.average_score < baseline.average_score + minimum_score_gain:
                raise ValueError("no candidate meets the promotion score threshold")
            selected = winner.variant
        else:
            if not isinstance(variant, str) or not variant.strip() or variant not in metrics:
                raise ValueError("manual mode requires a variant present in the experiment")
            selected = variant

        if selected == report.baseline_variant or rollout_percentage == 100:
            weights = {selected: 100.0}
        else:
            weights = {
                report.baseline_variant: float(100.0 - rollout_percentage),
                selected: float(rollout_percentage),
            }
        policy = await self.rollout_service.configure(
            tenant_id=tenant_id, prompt_id=report.prompt_id,
            weights=weights, actor=actor,
        )
        return EvaluationRolloutResult(
            experiment_id=experiment_id, prompt_id=report.prompt_id,
            tenant_id=tenant_id, selected_variant=selected,
            weights=policy.weights, policy_revision=policy.revision, mode=mode,
        )
